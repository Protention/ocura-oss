# SPDX-License-Identifier: Apache-2.0

"""Run the same deterministic CLI checks against either reporter version."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED = {
    "jobs": 5,
    "passed": 3,
    "failed": 2,
    "retried_jobs": 3,
    "recovered_jobs": 2,
    "total_duration_ms": 1885,
}


def event(**changes):
    return {"job": "build", "attempt": 1, "status": "passed", "duration_ms": 10, **changes}


def cases(fixture):
    yield "out-of-order attempts", fixture, EXPECTED, None
    yield "reversed input", "\n".join(reversed(fixture.splitlines())), EXPECTED, None
    zeros = dict.fromkeys(EXPECTED, 0)
    yield "empty input", "", zeros, None
    yield "blank input", "\n \t\n", zeros, None
    rows = [
        event(attempt=9),
        event(attempt=2, status="failed"),
        event(job="other", status="failed"),
    ]
    expected = dict(
        jobs=2, passed=1, failed=1, retried_jobs=1, recovered_jobs=1, total_duration_ms=30
    )
    yield "gaps in attempts", "\n".join(map(json.dumps, rows)), expected, None
    rows = [event(attempt=1, status="failed"), event(attempt=2), event(attempt=3, status="failed")]
    expected = dict(
        jobs=1, passed=0, failed=1, retried_jobs=1, recovered_jobs=0, total_duration_ms=30
    )
    yield "final failure is not recovery", "\n".join(map(json.dumps, rows)), expected, None
    invalid = {
        "job": ["", 5, None],
        "attempt": [0, -1, True, 1.0, "1"],
        "status": ["running", None, []],
        "duration_ms": [-1, True, 1.5, "10"],
    }
    prefix = json.dumps(event(job="valid")) + "\n"
    for field, values in invalid.items():
        for index, value in enumerate(values):
            text = prefix + json.dumps(event(**{field: value}))
            yield f"invalid {field} {index + 1}", text, None, field
    for field in event():
        row = event()
        del row[field]
        yield f"missing {field}", prefix + json.dumps(row), None, field
    for index, text in enumerate(('{"job":', "[]", "null", "42")):
        yield f"invalid JSON row {index + 1}", prefix + text, None, None
    yield "nonstandard JSON constant", prefix + json.dumps(event())[:-1] + ',"x":NaN}', None, None
    yield "duplicate attempt", prefix + json.dumps(event(job="valid")), None, "duplicate"


def check(reporter, text, expected, field, directory):
    source = directory / "input.jsonl"
    source.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", str(reporter), str(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if expected is not None:
        if result.returncode != 0 or result.stderr:
            return "valid input did not produce a clean success"
        try:
            actual = json.loads(result.stdout)
        except ValueError:
            return "stdout is not a JSON summary"
        if actual != expected:
            return f"expected {json.dumps(expected, sort_keys=True)}; got {result.stdout.strip()}"
    else:
        if result.returncode == 0:
            return "invalid input was accepted"
        if result.stdout:
            return "invalid input produced stdout"
        if "Traceback" in result.stderr:
            return "stderr is an unhandled traceback, not a row diagnostic"
        if "line 2" not in result.stderr.lower():
            return "stderr did not identify input line 2"
        if field is not None and field not in result.stderr:
            return f"stderr did not identify {field}"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reporter", type=Path)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_name("events.jsonl"))
    args = parser.parse_args(argv)
    reporter = args.reporter.resolve()
    results = []
    with tempfile.TemporaryDirectory(prefix="ci-report-checks-", dir=Path.cwd()) as directory:
        for name, text, expected, field in cases(args.fixture.read_text(encoding="utf-8")):
            detail = check(reporter, text, expected, field, Path(directory))
            results.append({"name": name, "passed": detail is None})
            print(
                f"{'PASS' if detail is None else 'FAIL'} {name}"
                + (f": {detail}" if detail else ""),
                file=sys.stderr,
            )
    failed = sum(not result["passed"] for result in results)
    print(
        json.dumps(
            {"checks": len(results), "passed": len(results) - failed, "failed": failed},
            sort_keys=True,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
