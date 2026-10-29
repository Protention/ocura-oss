# SPDX-License-Identifier: Apache-2.0

"""Retained starter: counts rows, ignores attempt ordering, and lacks field validation.

This is executable unfinished application code, not a command forced to exit 1.
The same functional checks run against this file and the completed report.py.
"""

import json
import sys


def summarize(path):
    with open(path, encoding="utf-8") as stream:
        events = [json.loads(line) for line in stream if line.strip()]
    return {
        "jobs": len(events),
        "passed": sum(event["status"] == "passed" for event in events),
        "failed": sum(event["status"] == "failed" for event in events),
        "total_duration_ms": sum(event["duration_ms"] for event in events),
    }


if __name__ == "__main__":
    print(json.dumps(summarize(sys.argv[1]), sort_keys=True))
