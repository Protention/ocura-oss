# Fix a CI job report and retain the evidence

This repository-only example uses **0.7.0.dev0 (unreleased)**. It needs Python 3.11+
with Ocura installed; the reporter and its checks use only the standard library.
The example is copied separately and is not bundled in the wheel or source distribution.

A small CI job reporter starts by counting input rows. That gives the wrong answer
when a job has several attempts, and it accepts invalid fields. The example runs
the same functional checks against that retained starter and a completed version,
recording the failure, the reason for the change, and the passing result.

## Run from a checkout

From the repository root, in a virtual environment:

```console
python -m pip install .
python examples/ci_report/workflow.py run --root ./ci-report-run
python examples/ci_report/workflow.py report --root ./ci-report-run --json
```

The run destination **must not exist**, even as an empty directory. The driver
copies its own sources and fixture into `ci-report-run/sources/`, creates the
ledger, then executes two bounded checks. Each run records hashes of those copied
files. An error leaves the root intact for inspection; the driver never overwrites,
retries, resumes, deletes, or recovers it.

Only a completed starter test failure (outcome `failed`, exit 1) proceeds to the
corrected run. An interruption, launch failure, abandoned attempt, or unexpected
exit stops the workflow and retains the evidence for inspection.

The readable output includes:

```text
CI report workflow: complete
...
starter: failed (0/31 checks passed; exit 1)
  intent: Count final job outcomes by highest attempt; validate rows before emitting a summary.
  run_id: atom-<starter-run>
  branch_point_id: chokepoint-<starter-branch-point>
  verified stderr excerpt:
    FAIL out-of-order attempts: expected ... "jobs": 5 ...; got ... "jobs": 8 ...
...
completed: passed (31/31 checks passed; exit 0)
  source_branch_point_id: chokepoint-<starter-branch-point>
  reason: Group attempts by job, account for retries and recovery, and reject invalid rows.
...
Verified 4 logs and preserved source hashes.
```

This is an illustrative excerpt; actual runs print complete generated IDs. It is
a local executable example, not evidence of human adoption or general agent quality.
The stderr excerpts are taken from verified saved bytes. The JSON report includes
stable ledger/run/stream/hash/offset/length references and says when an excerpt is
truncated. Normal reports omit absolute host paths and interpreter arguments;
those arguments remain available in the ledger's run records.

## Follow the IDs to the evidence

The Python driver uses the 0.7 combined operation:

```python
baseline = run(command, root=root, context_files=source_files)
variation = run(
    corrected_command,
    root=root,
    source_branch_point_id=baseline.branch_point.id,
    reason="Group attempts by job and validate input rows",
    context_files=source_files,
)
```

`baseline.branch_point.id` is the source for the next call. The new run is
`variation.record.id`, and its branch is `variation.record.pathway_id`.
The example declares the intent and reporter version on both calls: labels set
only on the source run are not inherited by a child pathway.

The CLI keeps compatible JSON and storage names. `run --json` returns `atom_id`
(the run ID), `chokepoint_id` (the branch point), and `pathway_id`; `show --json`
uses `atom` and `chokepoint` objects. The example's report uses descriptive names
without changing package JSON, existing IDs, or storage. Use the *branch point*
for `--from`, and the *run ID* for `show` and `logs`:

```powershell
# Replace these placeholders with the actual report fields.
$sourcePoint = 'chokepoint-<starter-branch-point>'
$starterRun = 'atom-<starter-run>'
$completedRun = 'atom-<completed-run>'
ocura-oss compare --root ./ci-report-run --from $sourcePoint --archive
ocura-oss show $completedRun --root ./ci-report-run --command --json --archive
ocura-oss logs $starterRun --root ./ci-report-run --stream stderr --archive
ocura-oss logs $completedRun --root ./ci-report-run --stream stderr --archive
```

`run --json` retains workload stdout/stderr in the ledger, so `logs --stream stderr`
is the next diagnostic command after a failing test run. A CLI variation would
use `run --from $sourcePoint --reason "..." -- COMMAND`; this creates and executes
another child, so it is deliberately absent from the inspection commands above.

## Report later using only the retained root

After all writers stop, this also works if the root has been copied elsewhere:

```console
python ci-report-run/sources/workflow.py report --root ./ci-report-run
```

`report` uses public `Store(root, archive=True)` readers. It verifies ledger
records and logs, inspects lineage, checks the preserved sources against their
recorded hashes, and reads bounded verified log excerpts. It never launches a
workload or calls recovery, and does not create ledger locks. The integration test
also checks an unchanged file inventory and timestamps on a read-only copy.

Archive mode assumes a stable root with no concurrent writers; unfinished attempts
are rejected. Missing or inconsistent evidence produces a nonzero exit, and an
incomplete workflow remains explicitly incomplete. Recorded hashes detect
inconsistency, not authorship. Ocura does **not** snapshot or restore source: this
example owns its copied `sources/` directory. Passing these fixture checks does
not establish correctness for every possible input.

## Install a wheel and copy the example separately

PowerShell commands from a checkout of this same development version, using new
destination names:

```powershell
python -m pip wheel . --no-deps --wheel-dir ./dist
python -m venv .venv-example
.\.venv-example\Scripts\python.exe -m pip install --no-index --no-deps ./dist/ocura_oss-0.7.0.dev0-py3-none-any.whl
Copy-Item -Recurse ./examples/ci_report ./ci-report-example
$env:PYTHONPATH = ''
.\.venv-example\Scripts\python.exe ./ci-report-example/workflow.py run --root ./ci-report-wheel-run
.\.venv-example\Scripts\python.exe ./ci-report-wheel-run/sources/workflow.py report --root ./ci-report-wheel-run --json
```

The first command builds the development wheel; it may install build-time
requirements. An already supplied matching wheel can be installed directly instead.
The wheel has no runtime dependencies. On POSIX, use `.venv-example/bin/python`
and `cp -R` in place of the Windows executable path and `Copy-Item`.

## Use the reporter independently

```console
python examples/ci_report/report.py examples/ci_report/events.jsonl
python examples/ci_report/checks.py examples/ci_report/report.py
```

The supplied fixture produces:

```json
{"failed": 2, "jobs": 5, "passed": 3, "recovered_jobs": 2, "retried_jobs": 3, "total_duration_ms": 1885}
```

Each nonblank JSONL line must contain a nonempty string `job`, a positive integer
`attempt`, status `passed` or `failed`, and a nonnegative integer `duration_ms`.
Booleans and floating-point values are not integers here. Each `(job, attempt)`
pair must be unique. Extra fields are ignored; job names are not trimmed.

Final status comes from the highest attempt number, regardless of row order.
A retried job has several recorded attempts; a recovered job failed earlier and
passed finally. Duration includes all attempts. Empty input produces six zeros.
Invalid rows produce a line-numbered stderr error, exit 1, and no summary on stdout.

The repository integration tests cover real baseline failure, final behavior,
root-only read-only reporting, exact excerpt identities, changed logs/sources,
incomplete roots, and refusal to overwrite existing files or directories:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest tests.test_ci_report_example -v
```
