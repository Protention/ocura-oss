# SPDX-License-Identifier: Apache-2.0

"""Summarize completed CI job attempts from a UTF-8 JSONL file."""

import argparse
import json
import sys


class ReportError(ValueError):
    """An invalid input row, with its physical line number."""


def reject_constant(value):
    raise ValueError(f"non-JSON constant {value}")


def parse_event(line, line_number):
    try:
        row = json.loads(line, parse_constant=reject_constant)
    except ValueError as exc:
        message = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise ReportError(f"line {line_number}: invalid JSON: {message}") from exc

    def invalid(message):
        raise ReportError(f"line {line_number}: {message}")

    if not isinstance(row, dict):
        invalid("expected a JSON object")
    for field in ("job", "attempt", "status", "duration_ms"):
        if field not in row:
            invalid(f"missing required field '{field}'")
    if not isinstance(row["job"], str) or not row["job"]:
        invalid("job must be a nonempty string")
    if type(row["attempt"]) is not int or row["attempt"] <= 0:
        invalid("attempt must be a positive integer")
    if row["status"] not in ("passed", "failed"):
        invalid("status must be 'passed' or 'failed'")
    if type(row["duration_ms"]) is not int or row["duration_ms"] < 0:
        invalid("duration_ms must be a nonnegative integer")
    return row


def summarize(path):
    """Count jobs; final status comes from the highest recorded attempt number."""
    jobs = {}
    total_duration = 0
    with open(path, encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = parse_event(line, line_number)
            attempts = jobs.setdefault(row["job"], {})
            if row["attempt"] in attempts:
                raise ReportError(f"line {line_number}: duplicate (job, attempt) pair")
            attempts[row["attempt"]] = row["status"]
            total_duration += row["duration_ms"]

    passed = retried = recovered = 0
    for attempts in jobs.values():
        final_passed = attempts[max(attempts)] == "passed"
        passed += final_passed
        retried += len(attempts) > 1
        recovered += final_passed and "failed" in attempts.values()
    return {
        "jobs": len(jobs),
        "passed": passed,
        "failed": len(jobs) - passed,
        "retried_jobs": retried,
        "recovered_jobs": recovered,
        "total_duration_ms": total_duration,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", metavar="INPUT", help="UTF-8 JSONL job attempts")
    args = parser.parse_args(argv)
    try:
        summary = summarize(args.input)
    except (ReportError, OSError, UnicodeError) as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
