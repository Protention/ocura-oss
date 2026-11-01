# Ocura OSS

A local execution ledger for recording, branching, and comparing command runs.

Record a baseline, explain the next variation, and keep its result connected to the
original run. Ocura OSS stores command arguments, outcomes, timing, output logs,
declared parameters, and branch lineage in your project. It verifies the relevant
records and logs before branching or comparing them.

Use it from your terminal, Python scripts, or an AI agent with a shell. The CLI
provides JSON output throughout the workflow; the Python API returns typed results.

## Unreleased usability work

This checkout adds descriptive Python names, `runs` and `show` for history
discovery, bounded verified log reads, and opt-in `--json-errors` for structured
failures. It also adds `run --from ID --reason TEXT` to create and execute a
variation in one call. These interfaces are absent from published 0.6.0. The
[history example](examples/history/README.md) starts with only a project root, retains failed
retries and empty variations, and returns verified excerpts with stable references.
The [naming guide](docs/naming-migration.md) maps descriptive names to compatible legacy names
and unchanged JSON fields. This is an unreleased development snapshot.

## Why use it

Ocura OSS is built to just work. It aims to be the easiest way to start recording
runs in a new project, especially for local development:

- **Dependency free.** It needs Python 3.11 or later and nothing else. Installing
  it adds no runtime dependencies to your project.
- **Easy setup.** `pip install ocura-oss`, then `ocura-oss init`. There is no
  server, database, account, or configuration file. Every command accepts `--json`
  and returns a documented exit code, so an AI agent with a shell can do the setup
  and run the whole workflow.
- **Easy to inspect and adapt.** Records are plain JSON files, and the typed
  Python API is available alongside the CLI. The Apache-2.0 license permits
  modifications without requiring you to publish them.

To remove the ledger, delete `.ocura-oss/` in your project. That directory holds
all ledger records and captured logs.

## Install and try

Python 3.11 or later is required. Ocura OSS uses only the standard library at runtime.

Install from [PyPI](https://pypi.org/project/ocura-oss/) in a virtual environment:

```console
python -m pip install ocura-oss
ocura-oss demo --root ./ocura-oss-demo
```

The demo runs the complete workflow and retains a new directory for inspection.
Its destination must not already exist. To install a repository checkout instead,
run `python -m pip install .` from its root.

## Compare two runs

```console
ocura-oss init --name example --json
ocura-oss run --json --param count=1 -- python -c "print(1)"
ocura-oss branch --json --from <chokepoint-id> --reason "try count 2" --param count=2
ocura-oss run --json --pathway <child-pathway-id> -- python -c "print(2)"
ocura-oss verify --json
ocura-oss compare --json
```

Use the baseline's `chokepoint_id` to branch and the branch result's `id` for the
child run. When typing an ID by hand, its first four or more hexadecimal characters
are enough if they match one record.

In this unreleased checkout, replace the separate `branch` and child `run` calls
with one command:

```console
ocura-oss run --json-errors --from <chokepoint-id> --reason "try count 2" --param count=2 -- python -c "print(2)"
```

This creates a child pathway and runs there, returning the usual run result and
its `pathway_id`. Its parameters override the source pathway's labels; labels
declared only on the source run are not inherited. A later storage failure can
leave an empty child. Inspect the error's known IDs and execution state before
continuing explicitly with `run --pathway CHILD_ID`; repeating `run --from`
creates another child. See [recovery guidance](docs/automation.md#handle-uncertain-execution-without-duplicate-work).

Without `--json`, `compare` prints:

```text
comparison: ready
source branch point: chokepoint-eb61ef56491042ae94e61d1387f3f0d5
source pathway: pathway-1378d1d28cb54987b9e80d59d61aec74
source run: passed 0.056063s
child pathway: pathway-ce4c71137dd449398fe2b4e0198c83c3
  reason: try count 2
  parameters: added count=2
  source run: passed 0.056063s
  child run: passed 0.061312s
  run parameters: changed count (1 -> 2)
```

`compare` reports each variant's relationship to its source, parameter changes,
outcomes, and timing. `parameters` is how the branch differs from the pathway it
came from; `run parameters` is how the labels the two runs recorded differ. With no
`--from`, it compares the newest baseline that has a branch. Read the recorded
output logs for workload-specific metrics, such as validation loss or accuracy.

**Parameters are recorded labels.** Set actual inputs in your command as well:
`--param batch=4 -- python train.py --batch 4`. A run on a branch records the
branch's parameters without repeating them, and can add or replace labels with its
own `--param`. To declare a value once, add `--substitute` and write `"{batch}"` in
the command.

Omit `--json` for terminal output. By default, `run` streams the command's output
while retaining stdout and stderr logs; `--json` or `--quiet` keeps that output in
the logs without streaming. A first Ctrl+C, or a `SIGTERM` sent to
`ocura-oss run`, stops the command and records an interrupted attempt; a second
Ctrl+C exits immediately and leaves the attempt for `ocura-oss recover` to close.

Logs stay raw unless you ask for lossless gzip storage: `run --compress` stores
that run's logs of at least 1 MiB as gzip when this saves at least 10%, and
`compress --dry-run` previews a pass over older logs; `decompress` restores raw
files. `ocura-oss logs <run-id>` prints the verified original bytes either way,
and `--info` shows sizes, the storage path, and a readable fingerprint. Ocura OSS
0.6 cannot read compressed logs; see [log compression](docs/autocompression.md).

## When to use something else

Ocura OSS records what ran, how runs relate, and whether those records are still
intact. It stops there on purpose:

- **You want dashboards, metric charts, or a tracking server shared by a team.**
  Use an experiment tracker such as [MLflow](https://mlflow.org/). Ocura OSS has no
  server and no user interface, and it keeps your output as logs without reading
  metrics from it.
- **You need to version datasets or model files, or rebuild a pipeline.** Use a
  tool such as [DVC](https://dvc.org/). A branch here is lineage metadata; it does
  not snapshot files.
- **You run one command and keep its output.** A log file is enough.

Ocura OSS sits between these: more structure than loose log files, and far less to
adopt than a tracking platform.

## Primary example: record and inspect

From this checkout, after `python -m pip install .`, create a small demonstration
and inspect its retained history:

```console
ocura-oss demo --root ./ocura-oss-demo
python examples/history/inspect_history.py --root ./ocura-oss-demo --archive
```

Use a new destination for the demo; if you already ran it above, run only the
inspection command. The [history guide](examples/history/README.md) explains the
JSON report and how to inspect your own ledger. No optional dependencies are needed.

The fuller CI-report, autoregressive, and Dream demonstrations live on the
semi-permanent [`examples/0.7` branch](https://github.com/Protention/ocura-oss/tree/examples/0.7).
See [use cases](docs/use-cases.md) for their requirements and checkout instructions.

## Python and automation

```python
import sys
from ocura_oss import branch, compare, initialize, run, verify

root = initialize("experiment", name="example").root
baseline = run([sys.executable, "-c", "print(1)"], root=root, parameters={"count": "1"})
child = branch(baseline.branch_point.id, root=root, reason="try count 2", parameters={"count": "2"})
run([sys.executable, "-c", "print(2)"], root=root, pathway_id=child.id)
assert verify(root).ok
comparison = compare(source_branch_point_id=baseline.branch_point.id, root=root)
```

The unreleased Python API can also replace the separate `branch()` and child
`run()` calls:

```python
variation = run(
    [sys.executable, "-c", "print(2)"],
    root=root,
    source_branch_point_id=baseline.branch_point.id,
    reason="try count 2",
    parameters={"count": "2"},
)
```

It returns the same `RunExecution`; the child's ID is `variation.record.pathway_id`.

Use `Store(root).read_verified_log(run_id=baseline.record.id, stream="stdout")` to consume recorded
output as bytes checked against the stored run record's byte count and SHA-256 digest.
Raw and gzip storage return the same original bytes.
The method also accepts `stream="stderr"`; decoding and metric interpretation
remain with your script. See the [Python API](https://github.com/Protention/ocura-oss/blob/main/docs/python-api.md#storeread_verified_log).

An agent can use the same CLI or Python workflow inside its existing execution
environment. The [automation guide](https://github.com/Protention/ocura-oss/blob/main/docs/automation.md) explains JSON results,
exit codes, parameter labels, and how to inspect earlier attempts.

## Documentation

- [Overview](https://github.com/Protention/ocura-oss/blob/main/docs/index.md): installation, concepts, and the complete workflow
- [CLI reference](https://github.com/Protention/ocura-oss/blob/main/docs/cli.md): every command, JSON fields, and exit codes
- [Python API](https://github.com/Protention/ocura-oss/blob/main/docs/python-api.md): functions, typed results, and `Store`
- [Scripts and AI agents](https://github.com/Protention/ocura-oss/blob/main/docs/automation.md): JSON workflows and verified output consumption
- [State and verification](https://github.com/Protention/ocura-oss/blob/main/docs/state-and-verification.md): records and checks
- [Log compression](docs/autocompression.md): opt-in gzip storage, verified reading, fingerprints, and compatibility
- [Examples](docs/use-cases.md): the primary walkthrough and demonstrations on `examples/0.7`
- [Migration tool](https://github.com/Protention/ocura-oss/blob/main/tools/migrations/README.md): optional import of 0.4 ledgers, downloaded separately

The full references are included in this checkout and remain readable directly on GitHub.
The documentation for the published release is also [available online](https://protention.dev/).

## Local state and operating model

State lives under `.ocura-oss/` in the project root. A **ledger** identifies that
state; a **pathway** groups runs in a lineage; a **run record** retains one command
attempt; a **branch point** identifies terminal evidence from which a branch can
be created. Older names (`Den`, `Atom`, and `Chokepoint`) remain Python aliases;
stored IDs, directories, and JSON keys retain their existing spellings.

Each branch records its source and reason. It represents a new line of work;
workspace files, process state, and model weights are managed by the workload.
Several processes may record runs under one state root at once. Delete `.ocura-oss/`
to discard that project's records.

Commands run with `shell=False` in the project root and inherit the invoking
environment's permissions. Use trusted, owner-authorized workloads. For generated
or otherwise untrusted commands, provide isolation and permissions through your
execution environment; Ocura OSS itself does not sandbox or restrict them.

Records retain command arguments, parameter labels, reasons, and output. Keep secrets
out of these fields; `run --mask-arg` and `run --no-capture` cover a command token or
output that cannot be avoided. SHA-256 checksums detect local inconsistencies; they do not
authenticate authorship or prevent a writer from replacing records and checksums.

## Project status

Ocura OSS is research software under the [Apache License 2.0](https://github.com/Protention/ocura-oss/blob/main/LICENSE).
Versions 0.5.0 and earlier were released under MPL-2.0. The 0.x interface and record
format may change before 1.0, and there is no production support commitment.

Bug reports and focused pull requests are welcome. See [Contributing](https://github.com/Protention/ocura-oss/blob/main/CONTRIBUTING.md)
and the [security policy](https://github.com/Protention/ocura-oss/blob/main/SECURITY.md).
