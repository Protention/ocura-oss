# SPDX-License-Identifier: Apache-2.0
"""Use one ledger from an earlier release and from this version in turn.

Usage: python cross_version.py RELEASE. The release is installed from PyPI into its own
environment; this version is the copy installed for the Python running the script.
The ledger must stay usable from both until it is upgraded.
"""

import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ENVIRONMENT = {
    key: value
    for key, value in os.environ.items()
    if key != "PYTHONPATH" and not key.startswith("OCURA_OSS_")
}


def schemas(root: Path) -> set[int]:
    """Return the schema versions of the records under *root*."""
    records = (root / ".ocura-oss").rglob("*.json")
    return {
        json.loads(path.read_text(encoding="utf-8"))["schema_version"]
        for path in records
        if path.name != "settings.json"
    }


def main(release: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary).resolve()
        venv.create(work / "old", with_pip=True)
        python = str(work / "old" / ("Scripts" if os.name == "nt" else "bin") / "python")
        old = (python, "-m", "ocura_oss")
        package = f"ocura-oss=={release}"
        subprocess.run([python, "-m", "pip", "install", "--quiet", package], check=True)
        # This version keeps its index from the first run on, so the release meets one.
        indexed = "import sys; from ocura_oss import cli, index; index.SMALLEST = 1"
        new = (sys.executable, "-c", f"{indexed}; sys.exit(cli.main(sys.argv[1:]))")
        root = work / "project"
        root.mkdir()

        def text(version: tuple[str, ...], *arguments: str) -> str:
            done = subprocess.run(
                [*version, *arguments],
                cwd=root,
                env=ENVIRONMENT,
                capture_output=True,
                text=True,
                check=False,
            )
            assert done.returncode == 0, (version, arguments, done.stdout, done.stderr)
            return done.stdout

        def result(version: tuple[str, ...], *arguments: str) -> dict:
            return json.loads(text(version, arguments[0], "--json", *arguments[1:]))

        def record(version: tuple[str, ...], source: dict | None, value: str) -> dict:
            origin = ["--from", source["branch_point_id"], "--reason", "next"] if source else []
            workload = ["--", sys.executable, "-c", f"print({value})"]
            return result(version, "run", *origin, *workload)

        def check(version: tuple[str, ...], runs: list[dict]) -> None:
            assert result(version, "verify")["status"] == "ok"
            listed = [item["id"] for item in result(version, "runs", "--all")["runs"]]
            assert listed == [item["run_id"] for item in runs], listed
            for value, item in enumerate(runs, 1):
                assert text(version, "logs", item["run_id"]).split() == [str(value)]

        assert release in text(old, "--version"), "the earlier release is not the one asked for"
        result(old, "init", "--name", "shared")
        runs = [record(old, None, "1")]
        written = schemas(root)

        # This version records into the earlier release's ledger and leaves it readable there.
        runs.append(record(new, runs[-1], "2"))
        text(new, "note", runs[0]["run_id"], "kept")  # The release ignores notes.
        check(new, runs)
        assert schemas(root) == written, schemas(root)
        assert (root / ".ocura-oss" / "index.sqlite").is_file()
        check(old, runs)
        runs.append(record(old, runs[-1], "3"))
        (work / "manifest").write_text(text(old, "manifest"), encoding="utf-8")
        check(new, runs)
        text(new, "verify", "--against", str(work / "manifest"))

        # Upgrading keeps every run and its ID, and leaves all records in one schema.
        text(new, "settings", "upgrade=overwrite")
        runs.append(record(new, runs[-1], "4"))
        check(new, runs)
        assert len(schemas(root)) == 1, schemas(root)
        assert result(new, "show", runs[0]["run_id"])["note"]["text"] == "kept"
    print(f"ledger shared with ocura-oss {release}: passed")


if __name__ == "__main__":
    main(sys.argv[1])
