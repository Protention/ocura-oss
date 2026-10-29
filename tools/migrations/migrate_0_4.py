# SPDX-License-Identifier: Apache-2.0

"""Copy a complete Ocura OSS 0.4 ledger into schema 2 using only the standard library.

Source and destination are project directories. The destination must not exist.
This standalone, repository-only tool never runs recorded commands.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

TOOL_VERSION = "1"
STATE = ".ocura-oss"
RECEIPT = "migration-receipt.json"
DIRECTORIES = {"pathways", "atoms", "chokepoints", "logs"}
ENVELOPE_FIELDS = {"schema_version", "kind", "payload", "checksum"}
FIELDS = {
    "den": {"id", "name", "created_at", "default_pathway_id"},
    "pathway": {
        "id",
        "den_id",
        "created_at",
        "parent_pathway_id",
        "source_chokepoint_id",
        "reason",
        "parameters",
    },
    "atom": {
        "id",
        "pathway_id",
        "started_at",
        "finished_at",
        "duration_seconds",
        "outcome",
        "return_code",
        "launch_error_category",
        "declared_parameters",
        "command",
        "stdout_log",
        "stderr_log",
        "stdout_bytes",
        "stderr_bytes",
        "stdout_sha256",
        "stderr_sha256",
    },
    "chokepoint": {
        "id",
        "pathway_id",
        "atom_id",
        "created_at",
        "kind",
        "outcome",
        "branchable",
    },
}
OUTCOMES = {"passed", "failed", "launch_failed", "interrupted"}
PARAMETER_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")


class MigrationError(Exception):
    """The input or destination cannot be migrated without losing evidence."""


@dataclass
class Ledger:
    root: Path
    records: dict[str, dict[str, Any]]
    files: dict[str, dict[str, Any]]

    @property
    def counts(self) -> dict[str, int]:
        kinds = Counter(record["kind"] for record in self.records.values())
        return {
            "pathways": kinds["pathway"],
            "atoms": kinds["atom"],
            "chokepoints": kinds["chokepoint"],
            "logs": sum(name.startswith("logs/") for name in self.files),
        }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MigrationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def checksum(envelope: dict[str, Any]) -> dict[str, str]:
    material = {key: envelope[key] for key in ("schema_version", "kind", "payload")}
    return {"algorithm": "sha256", "value": hashlib.sha256(canonical(material)).hexdigest()}


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise MigrationError(f"non-finite JSON number: {value}")


def read_json(path: Path, *, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    plain(path, directory=False)
    raw = path.read_bytes()
    if expected is not None:
        require(
            {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} == expected,
            f"{path.name}: record changed while being verified; stop its writers and retry",
        )
    try:
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MigrationError(f"unreadable JSON: {path.name}") from exc
    if not isinstance(result, dict):
        raise MigrationError(f"{path.name}: JSON must be an object")
    return result


def plain(path: Path, *, directory: bool) -> os.stat_result:
    metadata = path.lstat()
    reparse = getattr(metadata, "st_file_attributes", 0) & getattr(
        stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        0x400,
    )
    require(not path.is_symlink() and not reparse, f"links and junctions are unsupported: {path}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    require(
        expected(metadata.st_mode),
        f"expected a regular {'directory' if directory else 'file'}: {path}",
    )
    return metadata


def file_digest(path: Path) -> dict[str, Any]:
    plain(path, directory=False)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def inventory(state: Path, schema: int) -> dict[str, dict[str, Any]]:
    plain(state, directory=True)
    allowed_directories = DIRECTORIES | ({"attempts"} if schema == 2 else set())
    found_directories: set[str] = set()
    files: dict[str, dict[str, Any]] = {}
    for entry in sorted(state.iterdir()):
        if entry.name in allowed_directories:
            plain(entry, directory=True)
            found_directories.add(entry.name)
            if entry.name == "attempts":
                require(not list(entry.iterdir()), "converted attempts directory must be empty")
            for child in sorted(entry.iterdir()):
                name = f"{entry.name}/{child.name}"
                files[name] = file_digest(child)
        else:
            require(
                entry.name == "den.json", f"unsupported file or directory in ledger: {entry.name}"
            )
            files[entry.name] = file_digest(entry)
    require(found_directories == allowed_directories, "ledger is missing a required directory")
    require("den.json" in files, "ledger is missing den.json")
    return files


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MigrationError(f"{label} must be a nonempty string")
    return value


def identifier(value: Any, kind: str, label: str) -> str:
    result = text(value, label)
    require(re.fullmatch(rf"{kind}-[0-9a-f]{{32}}", result) is not None, f"invalid {label}")
    return result


def timestamp(value: Any, label: str) -> None:
    value = text(value, label)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise MigrationError(f"invalid {label}") from exc
    require(
        parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{label} needs a timezone"
    )


def integer(value: Any, label: str, *, minimum: int | None = None) -> None:
    require(type(value) is int, f"{label} must be an integer")
    require(minimum is None or value >= minimum, f"{label} is below its minimum")


def parameters(value: Any, label: str) -> None:
    require(isinstance(value, dict), f"{label} must be an object")
    for key, item in value.items():
        require(
            isinstance(key, str) and PARAMETER_KEY.fullmatch(key) is not None,
            f"invalid {label} key",
        )
        text(item, f"{label} value")


def validate_payload(kind: str, payload: Any, schema: int) -> None:
    expected = FIELDS[kind] | (
        {"output_capture", "masked_arguments"} if kind == "atom" and schema == 2 else set()
    )
    require(isinstance(payload, dict) and set(payload) == expected, f"unexpected {kind} fields")
    identifier(payload["id"], kind, f"{kind} id")
    if kind != "atom":
        timestamp(payload["created_at"], f"{kind} created_at")
    if kind == "den":
        text(payload["name"], "den name")
        identifier(payload["default_pathway_id"], "pathway", "default pathway")
    elif kind == "pathway":
        identifier(payload["den_id"], "den", "pathway den")
        for key, target_kind in (
            ("parent_pathway_id", "pathway"),
            ("source_chokepoint_id", "chokepoint"),
        ):
            if payload[key] is not None:
                identifier(payload[key], target_kind, key)
        require(
            (payload["parent_pathway_id"] is None) == (payload["source_chokepoint_id"] is None),
            "pathway needs both a parent and a source, or neither",
        )
        text(payload["reason"], "pathway reason")
        parameters(payload["parameters"], "pathway parameters")
    elif kind == "atom":
        validate_atom(payload, schema)
    else:
        identifier(payload["pathway_id"], "pathway", "chokepoint pathway")
        identifier(payload["atom_id"], "atom", "chokepoint atom")
        require(payload["kind"] == "terminal", "chokepoint must be terminal")
        require(
            isinstance(payload["outcome"], str) and payload["outcome"] in OUTCOMES,
            "invalid chokepoint outcome",
        )
        require(type(payload["branchable"]) is bool, "branchable must be a boolean")


def validate_atom(payload: dict[str, Any], schema: int) -> None:
    identifier(payload["pathway_id"], "pathway", "atom pathway")
    timestamp(payload["started_at"], "atom started_at")
    timestamp(payload["finished_at"], "atom finished_at")
    duration = payload["duration_seconds"]
    require(
        type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
        "invalid duration_seconds",
    )
    parameters(payload["declared_parameters"], "run parameters")
    command = payload["command"]
    require(isinstance(command, list) and bool(command), "command must be a nonempty array")
    for token in command:
        text(token, "command token")
    outcome, code, category = (
        payload[key] for key in ("outcome", "return_code", "launch_error_category")
    )
    require(isinstance(outcome, str) and outcome in OUTCOMES, "invalid atom outcome")
    if code is not None:
        integer(code, "return_code")
    if category is not None:
        text(category, "launch_error_category")
    if outcome == "launch_failed":
        require(code is None and category is not None, "invalid launch_failed result")
    elif outcome == "interrupted":
        require(code is None and category == "interrupted", "invalid interrupted result")
    else:
        require(code is not None and category is None, "invalid launched result")
        require((code == 0) == (outcome == "passed"), "return_code and outcome disagree")
    for stream in ("stdout", "stderr"):
        text(payload[f"{stream}_log"], f"{stream} log")
        integer(payload[f"{stream}_bytes"], f"{stream}_bytes", minimum=0)
        require(
            SHA256.fullmatch(text(payload[f"{stream}_sha256"], f"{stream} digest")) is not None,
            "invalid log digest",
        )
    if schema == 2:
        require(
            payload["output_capture"] == "full" and payload["masked_arguments"] == [],
            "unexpected converted capture fields",
        )


def read_record(path: Path, kind: str, schema: int, expected: dict[str, Any]) -> dict[str, Any]:
    envelope = read_json(path, expected=expected)
    require(set(envelope) == ENVELOPE_FIELDS, f"{path.name}: unexpected envelope fields")
    require(
        type(envelope["schema_version"]) is int and envelope["schema_version"] == schema,
        f"{path.name}: expected schema {schema}; only 0.4 schema-1 ledgers are supported",
    )
    require(envelope["kind"] == kind, f"{path.name}: record kind mismatch")
    validate_payload(kind, envelope["payload"], schema)
    require(envelope["checksum"] == checksum(envelope), f"{path.name}: checksum mismatch")
    if kind != "den":
        require(path.stem == envelope["payload"]["id"], f"{path.name}: id does not match filename")
    return envelope


def validate_graph(records: dict[str, dict[str, Any]]) -> None:
    groups: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in FIELDS}
    for record in records.values():
        payload = record["payload"]
        groups[record["kind"]][payload["id"]] = payload
    den = records["den.json"]["payload"]
    pathways, atoms, chokepoints = (groups[kind] for kind in ("pathway", "atom", "chokepoint"))
    default = den["default_pathway_id"]
    require(default in pathways, "default pathway is missing")
    for point in chokepoints.values():
        require(point["atom_id"] in atoms, "chokepoint references a missing atom")
        atom = atoms[point["atom_id"]]
        require(
            point["pathway_id"] == atom["pathway_id"] and point["outcome"] == atom["outcome"],
            "chokepoint disagrees with its atom",
        )
    point_counts = Counter(point["atom_id"] for point in chokepoints.values())
    for atom in atoms.values():
        require(atom["pathway_id"] in pathways, "atom references a missing pathway")
        require(
            point_counts[atom["id"]] == 1,
            "each atom needs exactly one chokepoint; incomplete state cannot be migrated",
        )
    for pathway in pathways.values():
        require(pathway["den_id"] == den["id"], "pathway references another den")
        parent, source = pathway["parent_pathway_id"], pathway["source_chokepoint_id"]
        if parent is None:
            require(pathway["id"] == default, "unexpected root pathway")
        else:
            require(
                parent in pathways and source in chokepoints, "pathway lineage reference is missing"
            )
            point = chokepoints[source]
            require(point["pathway_id"] == parent and point["branchable"], "invalid branch source")
        seen: set[str] = set()
        current: str | None = pathway["id"]
        while current is not None:
            require(current not in seen, "pathway lineage contains a cycle")
            require(current in pathways, "pathway lineage reference is missing")
            seen.add(current)
            current = pathways[current]["parent_pathway_id"]
        require(default in seen, "pathway is disconnected from the default pathway")


def verify_logs(ledger: Ledger) -> None:
    references: set[str] = set()
    for record in ledger.records.values():
        if record["kind"] != "atom":
            continue
        atom = record["payload"]
        for stream in ("stdout", "stderr"):
            relative = atom[f"{stream}_log"]
            parts = PurePosixPath(relative).parts
            require(
                "\\" not in relative
                and ":" not in relative
                and len(parts) == 3
                and parts[:2] == (STATE, "logs")
                and parts[2] not in (".", ".."),
                "log path must be directly inside .ocura-oss/logs/",
            )
            name = f"logs/{parts[2]}"
            require(name not in references, "a log is referenced more than once")
            references.add(name)
            expected = {"bytes": atom[f"{stream}_bytes"], "sha256": atom[f"{stream}_sha256"]}
            require(
                name in ledger.files and ledger.files[name] == expected,
                f"{name}: log size or checksum mismatch",
            )
    require(
        references == {name for name in ledger.files if name.startswith("logs/")},
        "unreferenced logs indicate incomplete or unsupported state",
    )


def read_ledger(root: Path, schema: int) -> Ledger:
    state = root / STATE
    files = inventory(state, schema)
    records = {"den.json": read_record(state / "den.json", "den", schema, files["den.json"])}
    for name in files:
        directory, _, filename = name.partition("/")
        if directory in ("pathways", "atoms", "chokepoints"):
            kind = {"pathways": "pathway", "atoms": "atom", "chokepoints": "chokepoint"}[directory]
            require(
                re.fullmatch(rf"{kind}-[0-9a-f]{{32}}\.json", filename) is not None,
                f"unsupported record filename: {name}",
            )
            records[name] = read_record(state / name, kind, schema, files[name])
    ledger = Ledger(root, records, files)
    validate_graph(records)
    verify_logs(ledger)
    require(
        inventory(state, schema) == files,
        "ledger changed while being verified; stop its writers and retry",
    )
    return ledger


def manifest(files: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"path": name, **digest} for name, digest in sorted(files.items())]


def manifest_digest(files: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(canonical(manifest(files))).hexdigest()


def locations(source: Path, destination: Path) -> tuple[Path, Path]:
    source = source.expanduser().absolute()
    plain(source, directory=True)
    source = source.resolve(strict=True)
    destination = destination.expanduser().absolute()
    require(
        not os.path.lexists(destination),
        "destination already exists; choose a new project directory",
    )
    destination = destination.resolve()
    require(
        not destination.is_relative_to(source) and not source.is_relative_to(destination),
        "source and destination project directories must not overlap",
    )
    plain(destination.parent, directory=True)
    return source, destination


def converted(envelope: dict[str, Any]) -> dict[str, Any]:
    payload = dict(envelope["payload"])
    if envelope["kind"] == "atom":
        payload.update(output_capture="full", masked_arguments=[])
    result = {"schema_version": 2, "kind": envelope["kind"], "payload": payload}
    result["checksum"] = checksum(result)
    return result


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def copy_ledger(ledger: Ledger, destination: Path) -> None:
    state = destination / STATE
    state.mkdir()
    for directory in sorted(DIRECTORIES | {"attempts"}):
        (state / directory).mkdir()
    for name in sorted(ledger.files):
        target = state / name
        if name in ledger.records:
            write_json(target, converted(ledger.records[name]))
        else:
            plain(ledger.root / STATE / name, directory=False)
            with (ledger.root / STATE / name).open("rb") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def cleanup_owned(
    destination: Path, identity: tuple[int, int], files: dict[str, dict[str, Any]]
) -> None:
    """Remove only the explicitly named output directory reserved by this invocation."""
    metadata = plain(destination, directory=True)
    require(
        destination.resolve(strict=True) == destination
        and (metadata.st_dev, metadata.st_ino) == identity,
        "output identity changed; incomplete destination retained",
    )
    allowed_directories = {STATE} | {
        f"{STATE}/{directory}" for directory in DIRECTORIES | {"attempts"}
    }
    allowed_files = {f"{STATE}/{name}" for name in files} | {RECEIPT}
    for root, directories, children in os.walk(destination, followlinks=False):
        for name in directories:
            path = Path(root) / name
            require(
                path.relative_to(destination).as_posix() in allowed_directories,
                "unexpected directory appeared in output; incomplete destination retained",
            )
            plain(path, directory=True)
        for name in children:
            path = Path(root) / name
            require(
                path.relative_to(destination).as_posix() in allowed_files,
                "unexpected file appeared in output; incomplete destination retained",
            )
            plain(path, directory=False)
    shutil.rmtree(destination)


def migrate(source: Path, destination: Path, *, dry_run: bool = False) -> dict[str, Any]:
    source, destination = locations(source, destination)
    ledger = read_ledger(source, 1)
    summary = {
        "status": "ready" if dry_run else "migrated",
        "tool": "migrate_0_4",
        "tool_version": TOOL_VERSION,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source": str(source),
        "destination": str(destination),
        "source_schema": 1,
        "destination_schema": 2,
        "counts": ledger.counts,
        "source_digest": manifest_digest(ledger.files),
    }
    if dry_run:
        return summary
    destination.mkdir()
    metadata = plain(destination, directory=True)
    identity = (metadata.st_dev, metadata.st_ino)
    try:
        copy_ledger(ledger, destination)
        output = read_ledger(destination, 2)
        require(output.counts == ledger.counts, "conversion changed record or log counts")
        require(set(output.records) == set(ledger.records), "conversion changed record identities")
        for name, original in ledger.records.items():
            require(
                output.records[name] == converted(original),
                f"{name}: conversion changed historical data",
            )
        for name in ledger.files:
            if name.startswith("logs/"):
                require(output.files[name] == ledger.files[name], f"{name}: copied log changed")
        require(
            inventory(source / STATE, 1) == ledger.files,
            "source changed during migration; stop its writers and retry",
        )
        receipt = {
            **summary,
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "destination_digest": manifest_digest(output.files),
            "source_manifest": manifest(ledger.files),
            "destination_manifest": manifest(output.files),
            "record_checksums": [
                {
                    "path": name,
                    "source": record["checksum"]["value"],
                    "destination": output.records[name]["checksum"]["value"],
                }
                for name, record in sorted(ledger.records.items())
            ],
            "conversion": {
                "all_records": "schema 1 -> 2; envelope checksums recalculated",
                "atoms": "output_capture=full; masked_arguments=[]; historical fields preserved",
                "attempts": "empty directory; historical attempts are not reconstructed",
                "logs": "copied without changes",
            },
        }
        write_json(destination / RECEIPT, receipt)
        return {
            **summary,
            "destination_digest": receipt["destination_digest"],
            "receipt": str(destination / RECEIPT),
        }
    except BaseException as error:
        try:
            cleanup_owned(destination, identity, ledger.files)
        except (OSError, MigrationError) as cleanup_error:
            raise MigrationError(
                f"migration failed; incomplete destination retained: {destination} "
                f"({cleanup_error})"
            ) from error
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="project containing a complete 0.4 .ocura-oss ledger",
    )
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="new project directory; its parent must exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify and describe the conversion without creating anything",
    )
    parser.add_argument(
        "--json", action="store_true", help="return a JSON result; errors go to stderr"
    )
    arguments = parser.parse_args(argv)
    if sys.version_info < (3, 11):  # noqa: UP036 - standalone users may invoke older Python.
        parser.error("Python 3.11 or later is required")
    try:
        result = migrate(arguments.source, arguments.destination, dry_run=arguments.dry_run)
    except (MigrationError, OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
        message = str(error).encode("ascii", "backslashreplace").decode("ascii")
        print(f"error: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: migration interrupted", file=sys.stderr)
        return 130
    if arguments.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"migration: {result['status']}")
        print("schema: 1 -> 2")
        print("counts: " + ", ".join(f"{name}={count}" for name, count in result["counts"].items()))
        print(f"source digest: {result['source_digest']}")
        if "receipt" in result:
            print(
                f"receipt: {result['receipt']}".encode("ascii", "backslashreplace").decode("ascii")
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
