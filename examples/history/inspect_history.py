# SPDX-License-Identifier: Apache-2.0

"""Inspect an existing ledger using public interfaces; never execute or recover runs."""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import json
from collections.abc import Iterator
from pathlib import Path

from ocura_oss import OutputCapture, Store, StoreError


def _error_details(
    error: StoreError | OSError | ValueError, ids: dict[str, str] | None = None
) -> dict:
    """Use public diagnostics; keep the example independent of CLI internals."""
    known_ids = dict(ids or {})
    execution, recovery = "not_applicable", None
    if isinstance(error, StoreError):
        code, execution, recovery = error.code, error.execution, error.recovery
        known_ids.update(error.ids)
    elif isinstance(error, OSError):
        denied = isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM)
        code = "permission_denied" if denied else "io_error"
    else:
        code = "invalid_input"
    if not recovery:
        if execution == "unknown":
            recovery = "inspect_attempts_and_runs"
        elif execution == "recorded":
            recovery = "inspect_recorded_run"
        else:
            # These defaults follow the documented CLI error contract. They are
            # suggestions to a caller; this example performs none of the actions.
            recovery = {
                "invalid_input": "correct_input",
                "state_not_found": "initialize_state",
                "record_not_found": "select_record",
                "ambiguous_id": "select_record",
                "unsupported_schema": "use_supported_reader",
                "integrity_error": "verify_state",
                "permission_denied": "check_permissions",
                "io_error": "check_storage",
                "invalid_state": "verify_state",
            }.get(code, "inspect_state")
    return {
        "code": code,
        "message": str(error),
        "execution": execution,
        "ids": known_ids,
        "recovery": recovery,
    }


@contextlib.contextmanager
def _read_context(ids: dict[str, str]) -> Iterator[None]:
    """Retain identities already obtained through public readers if a read fails."""
    try:
        yield
    except StoreError as exc:
        exc.ids = {**ids, **exc.ids}
        raise
    except OSError as exc:
        details = _error_details(exc, ids)
        raise StoreError(str(exc), code=details["code"], ids=details["ids"]) from exc


def report(root: Path, *, archive: bool = False, snippet_bytes: int = 256) -> dict:
    """Return observed history and verified log excerpts without choosing a winner."""
    if not 0 <= snippet_bytes <= 65_536:
        raise ValueError("snippet_bytes must be between 0 and 65536")
    store = Store(root, archive=archive)
    ledger = store.load_ledger()
    ledger_ids = {"ledger_id": ledger.id}
    with _read_context(ledger_ids):
        before = store.list_attempts()
        page = store.list_runs(limit=None)
        pathways = store.list_pathways()
    runs = []
    runs_by_pathway: dict[str, list[str]] = {}
    read_errors = 0
    checked_streams = 0
    for listed in page.runs:
        record_ids = {
            **ledger_ids,
            "run_id": listed.run.id,
            "branch_point_id": listed.branch_point_id,
            "pathway_id": listed.run.pathway_id,
        }
        with _read_context(record_ids):
            record = store.load_run(listed.run.id)
        runs_by_pathway.setdefault(record.pathway_id, []).append(record.id)
        evidence = []
        if record.output_capture is OutputCapture.FULL:
            for stream in ("stdout", "stderr"):
                digest = getattr(record, f"{stream}_sha256")
                original_bytes = getattr(record, f"{stream}_bytes")
                reference = {
                    "ledger_id": ledger.id,
                    "run_id": record.id,
                    "stream": stream,
                    "sha256": digest,
                    "offset": 0,
                }
                try:
                    with store.open_verified_log(
                        run_id=record.id, stream=stream, offset=0, length=snippet_bytes
                    ) as source:
                        data = source.read()
                except (StoreError, OSError) as exc:
                    read_errors += 1
                    evidence.append(
                        {
                            "reference": reference,
                            "status": "unavailable",
                            "error": str(exc),
                            "error_details": _error_details(exc, record_ids),
                        }
                    )
                    continue
                checked_streams += 1
                evidence.append(
                    {
                        "reference": {**reference, "length": len(data)},
                        "status": "verified",
                        "verification_scope": "entire selected stream against recorded digest",
                        "original_bytes": original_bytes,
                        "truncated": len(data) < original_bytes,
                        "content_base64": base64.b64encode(data).decode("ascii"),
                        "text_preview_utf8": data.decode("utf-8", errors="replace"),
                    }
                )
        runs.append(
            {
                "run_id": record.id,
                "branch_point_id": listed.branch_point_id,
                "pathway_id": record.pathway_id,
                "outcome": record.outcome.value,
                "return_code": record.return_code,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
                "parameters": dict(record.declared_parameters),
                "output_capture": record.output_capture.value,
                "evidence": evidence,
            }
        )
    with _read_context(ledger_ids):
        after = store.list_attempts()
    attempts = {
        attempt.id: {
            "run_id": attempt.id,
            "branch_point_id": attempt.branch_point_id,
            "pathway_id": attempt.pathway_id,
            "state": state.value,
            "started_at": attempt.started_at,
        }
        for attempt, state in [*before, *after]
    }
    unfinished = bool(before or after or page.unfinished_attempts)
    return {
        "root": str(store.root),
        "ledger_id": ledger.id,
        "default_pathway_id": ledger.default_pathway_id,
        "selection": "all observed pathways and finalized runs; no baseline selected",
        "pathways": [
            {
                "pathway_id": pathway.id,
                "parent_pathway_id": pathway.parent_pathway_id,
                "source_branch_point_id": pathway.source_branch_point_id,
                "reason": pathway.reason,
                "parameters": dict(pathway.parameters),
                "run_ids": runs_by_pathway.get(pathway.id, []),
            }
            for pathway in pathways
        ],
        "runs": runs,
        "unfinished_attempts": sorted(attempts.values(), key=lambda item: item["run_id"]),
        "discovery": {
            "mode": "stable_archive" if archive else "live_observation",
            "unfinished_at_run_scan": page.unfinished_attempts,
            "completeness": "stable_archive" if archive else "observed_only",
            "note": (
                "Archive completeness requires a stable copy with no writers."
                if archive
                else "Separate reads are not an atomic snapshot; new work may appear during them."
            ),
        },
        "verification": {
            "scope": "returned records and selected captured streams",
            "streams_checked": checked_streams,
            "stream_errors": read_errors,
            "whole_ledger_checked": False,
        },
        "guidance": (
            "Unfinished work was observed. Inspect attempts; wait for live recorders or recover "
            "abandoned attempts on a writable copy after their commands stop. Then inspect again. "
            "This report never reruns a command."
            if unfinished
            else "Select a source branch point explicitly from this history before comparing "
            "or branching. This report does not infer a baseline or a best result."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="existing project root")
    parser.add_argument("--archive", action="store_true", help="inspect a stable, settled copy")
    parser.add_argument(
        "--snippet-bytes", type=int, default=256, help="0 to 65536 bytes per stream"
    )
    args = parser.parse_args(argv)
    try:
        result = report(args.root, archive=args.archive, snippet_bytes=args.snippet_bytes)
    except (StoreError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "error_details": _error_details(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 2 if result["verification"]["stream_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
