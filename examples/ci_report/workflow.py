# SPDX-License-Identifier: Apache-2.0

"""Record a CI-report fix, or inspect a settled run root without executing work."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from ocura_oss import Outcome, Store, StoreError, initialize, run

SOURCE_FILES = ("starter.py", "report.py", "checks.py", "events.jsonl", "workflow.py")
INTENT = "Count final job outcomes by highest attempt; validate rows before emitting a summary."
REASON = "Group attempts by job, account for retries and recovery, and reject invalid rows."


def run_example(root):
    """Create a new root and retain both versions before executing two bounded checks."""
    root = Path(root).expanduser().absolute()
    # mkdir is deliberately exclusive, including for an empty existing directory.
    # On failure later, leave everything retained; never delete or silently retry.
    root.mkdir(parents=True, exist_ok=False)
    sources = root / "sources"
    sources.mkdir()
    for name in SOURCE_FILES:
        shutil.copyfile(Path(__file__).with_name(name), sources / name)
    initialize(root, name="ci-report-example")
    context_files = [f"sources/{name}" for name in SOURCE_FILES]
    command = [sys.executable, "-B", "sources/checks.py"]
    fixture = ["--fixture", "sources/events.jsonl"]
    baseline = run(
        [*command, "sources/starter.py", *fixture],
        root=root,
        parameters={"example": "ci-report", "reporter": "starter", "intent": INTENT},
        context_files=context_files,
    )
    if baseline.record.outcome is not Outcome.FAILED or baseline.record.return_code != 1:
        raise ValueError(
            "Starter did not finish with the expected test failure; "
            "stopped before the corrected run. Inspect the retained ledger."
        )
    run(
        [*command, "sources/report.py", *fixture],
        root=root,
        source_branch_point_id=baseline.branch_point.id,
        reason=REASON,
        parameters={"example": "ci-report", "reporter": "completed", "intent": INTENT},
        context_files=context_files,
    )
    return report(root)


def report(root):
    """Read only: caller supplies a stable root with no writers or unfinished attempts."""
    store = Store(root, archive=True)
    verification = store.verify_state()
    if not verification.ok:
        raise StoreError(
            "Ledger verification failed: "
            + "; ".join(f"{identity}: {message}" for identity, message in verification.problems),
            code="integrity_error",
        )
    ledger = store.load_ledger()
    if ledger.name != "ci-report-example":
        raise ValueError("This root is not a CI-report example ledger")
    runs = []
    for listed in store.list_runs(limit=None).runs:
        inspected = store.inspect_run(run_id=listed.run.id)
        record, pathway = inspected.record, inspected.pathway
        context = record.context
        preserved = {}
        for name in SOURCE_FILES:
            relative = f"sources/{name}"
            if context is None or relative not in context.files:
                raise ValueError(f"{record.id}: missing recorded source hash for {relative}")
            path = (store.root / relative).resolve()
            if not path.is_relative_to(store.root):
                raise ValueError(f"Preserved source escapes the root: {relative}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != context.files[relative].sha256:
                raise ValueError(f"Preserved source hash mismatch: {relative}")
            preserved[relative] = digest
        # These logs are small. Bound returned excerpts while verifying the entire stream.
        with store.open_verified_log(run_id=record.id, stream="stdout", length=4096) as source:
            summary_bytes = source.read()
        if len(summary_bytes) != record.stdout_bytes:
            raise ValueError(f"{record.id}: check summary exceeds the example's read limit")
        summary = json.loads(summary_bytes)
        if not isinstance(summary, dict) or any(
            type(summary.get(key)) is not int for key in ("checks", "passed", "failed")
        ):
            raise ValueError(f"{record.id}: recorded check summary is unavailable or invalid")
        with store.open_verified_log(run_id=record.id, stream="stderr", length=2048) as source:
            excerpt = source.read()
        runs.append(
            {
                "run_id": record.id,
                "branch_point_id": listed.branch_point_id,
                "pathway_id": record.pathway_id,
                "source_branch_point_id": pathway.source_branch_point_id,
                "reason": pathway.reason,
                "declared_parameters": dict(record.declared_parameters),
                "outcome": record.outcome.value,
                "return_code": record.return_code,
                "checks": summary,
                "preserved_source_sha256": preserved,
                "stderr_excerpt": {
                    "reference": {
                        "ledger_id": ledger.id,
                        "run_id": record.id,
                        "stream": "stderr",
                        "sha256": record.stderr_sha256,
                        "offset": 0,
                        "length": len(excerpt),
                    },
                    "text": excerpt.decode("utf-8", errors="replace"),
                    "truncated": len(excerpt) < record.stderr_bytes,
                },
            }
        )
    starter = [item for item in runs if item["declared_parameters"].get("reporter") == "starter"]
    completed = [
        item for item in runs if item["declared_parameters"].get("reporter") == "completed"
    ]
    expected = (
        len(runs) == 2
        and len(starter) == len(completed) == 1
        and starter[0]["outcome"] == "failed"
        and starter[0]["checks"]["failed"] > 0
        and completed[0]["outcome"] == "passed"
        and completed[0]["checks"]["failed"] == 0
        and completed[0]["checks"]["checks"] > 0
        and completed[0]["source_branch_point_id"] == starter[0]["branch_point_id"]
    )
    return {
        "status": "complete" if expected else "unexpected_or_incomplete_results",
        "ledger_id": ledger.id,
        "runs": runs,
        "pathways": [
            {
                "pathway_id": pathway.id,
                "reason": pathway.reason,
                "source_branch_point_id": pathway.source_branch_point_id,
            }
            for pathway in store.list_pathways()
        ],
        "verification": {
            "mode": "stable_archive",
            "whole_ledger_checked": True,
            "logs_checked": verification.logs_checked,
            "preserved_sources": "match recorded SHA-256 hashes",
        },
        "uncertainty": [
            "Archive reading assumes a stable root with no concurrent writers.",
            "Passing these local fixture checks does not establish correctness for every input.",
            "Hashes detect inconsistencies; they do not authenticate authorship.",
            "The example copied source files; Ocura does not snapshot or restore them.",
        ],
    }


def format_report(result):
    lines = [f"CI report workflow: {result['status']}", f"ledger: {result['ledger_id']}"]
    for item in result["runs"]:
        labels = item["declared_parameters"]
        checks = item["checks"]
        lines.extend(
            [
                f"\n{labels.get('reporter', 'unlabelled')}: {item['outcome']} "
                f"({checks['passed']}/{checks['checks']} checks passed; "
                f"exit {item['return_code']})",
                f"  intent: {labels.get('intent', 'not recorded')}",
                f"  run_id: {item['run_id']}",
                f"  branch_point_id: {item['branch_point_id']}",
                f"  pathway_id: {item['pathway_id']}",
            ]
        )
        if item["source_branch_point_id"]:
            lines.extend(
                [
                    f"  source_branch_point_id: {item['source_branch_point_id']}",
                    f"  reason: {item['reason']}",
                ]
            )
        evidence = item["stderr_excerpt"]
        lines.append("  verified stderr excerpt:")
        lines.extend(f"    {line}" for line in evidence["text"].splitlines())
        if evidence["truncated"]:
            lines.append("    [excerpt truncated; inspect the recorded stderr for the full log]")
    checked = result["verification"]["logs_checked"]
    lines.append(f"\nVerified {checked} logs and preserved source hashes.")
    lines.extend(f"Note: {note}" for note in result["uncertainty"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "report"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="emit the example's structured report")
    args = parser.parse_args(argv)
    try:
        result = run_example(args.root) if args.action == "run" else report(args.root)
    except (StoreError, OSError, ValueError) as exc:
        error = {
            "message": str(exc),
            "execution": getattr(exc, "execution", "not_applicable"),
            "ids": getattr(exc, "ids", {}),
        }
        if isinstance(exc, StoreError):
            error["code"] = exc.code
        if args.json:
            print(json.dumps({"status": "unavailable", "error": error}))
        else:
            print(f"CI report workflow: {exc}", file=sys.stderr)
        print(
            "Inspect any retained evidence; this command did not retry or recover any workload.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2) if args.json else format_report(result))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
