# Ocura OSS

Version 0.7.0.dev0.

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
failures. These interfaces are absent from published 0.6.0. The
[history example](../examples/history/README.md) starts with only a project root, retains failed
retries and empty variations, and returns verified excerpts with stable references.
The [naming guide](naming-migration.md) maps descriptive names to compatible legacy names
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

## New in 0.6.0

- **Apache-2.0 license.** Version 0.6.0 and later are released under the Apache
  License 2.0. Versions 0.5.0 and earlier remain under MPL-2.0.
- **IDs can be shortened.** `branch --from`, `compare --from`, and `run --pathway`
  accept the first four or more hexadecimal characters of an ID when they match
  one record.
- **A run on a branch records the branch's parameters.** The child `run` no longer
  repeats them. A `--param` on the run still replaces the value for its key.
- **Stopping the recorder stops the command.** `ocura-oss run` handles `SIGTERM`,
  `SIGHUP`, and Ctrl+Break like Ctrl+C and records the run as `interrupted`. If
  the recorder is killed outright, Windows and Linux end the command; on macOS it
  keeps running.
- **The first error says what to do.** A command run where there is no state names
  `ocura-oss init`, or the directory above that already holds state.

## New in 0.5.0

- **Unfinished runs stay visible.** Each run is journaled before its command starts.
  If the recording process is interrupted twice or killed, `ocura-oss attempts` shows
  the attempt and `ocura-oss recover` closes it as `abandoned` without inventing an outcome.
- **Several runs can share one root.** Concurrent `run` processes are supported, and
  `verify` and `compare` work while runs are in flight.
- **Sensitive values can stay out of records.** `run --no-capture` writes no output
  to disk, and `run --mask-arg` stores a placeholder for a chosen command token.
- **One declaration can label and configure a run.** `run --substitute` fills `{KEY}`
  in the command from the declared parameters and records what it used.
- **Launch context is available on request.** `run --context` records the platform and
  git revision; `--context-file` records a file's digest.
- **Later rewrites can be detected.** `ocura-oss manifest` prints checksums to keep
  elsewhere, and `verify --against` checks the state against them.
- **Verification is stricter.** Every run record must have exactly one branch point.

The core runtime still uses only the standard library. See
[state and verification](state-and-verification.md) for the format, attempts,
concurrent use, and the limits of each option.

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

## Development example: explain a fix

The [CI-report example](../examples/ci_report/README.md) records a failing baseline,
the reason for a correction, and the passing result. Its report reconstructs the
history from verified logs without running tests again. It needs only Python and
this unreleased 0.7 checkout:

```console
python examples/ci_report/workflow.py run --root ./ci-report-demo
python examples/ci_report/workflow.py report --root ./ci-report-demo
```

The example owns the retained source versions and fixture. Ocura records execution
and lineage; it does not snapshot the workspace or infer the developer's reasoning.
See [use cases](use-cases.md) for development, training, and experimental replay.

## Autoregressive example: PyTorch, JAX, and Ray

The [autoregressive example](../examples/autoregressive/README.md) trains a small
character-level next-token model on an included text corpus. Choose PyTorch or
JAX, run a baseline and a longer-training variant, then reconstruct the comparison
from saved evidence. An optional Ray Core executor runs either backend as a local
task. The example stays in the source repository; neither the wheel nor the source
distribution includes its scripts or framework dependencies. Install only what you use.

From a repository checkout, after installing Ocura:

```console
python -m pip install -r examples/autoregressive/requirements-pytorch.txt
python examples/autoregressive/experiment.py run --backend pytorch --root ./ar-pytorch
python examples/autoregressive/experiment.py report --root ./ar-pytorch
```

The guide includes JAX and Ray commands. The script supplies metric reading and
interpretation; the package supplies recording, lineage, and verification.

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

Use `Store(root).read_verified_log(run_id=baseline.record.id, stream="stdout")` to consume verified
output bytes. The method also accepts `stream="stderr"`; use `verify(root)` for a
complete state check. See the [Python API](python-api.md#storeread_verified_log).

An agent can use the same CLI or Python workflow inside its existing execution
environment. The [automation guide](automation.md) explains JSON results,
exit codes, parameter labels, and how to inspect earlier attempts.

## Documentation

- [Scripts and AI agents](automation.md): JSON workflows and verified output consumption
- [CLI reference](cli.md): every command, JSON fields, and exit codes
- [Python API](python-api.md): functions, typed results, and `Store`
- [State and verification](state-and-verification.md): records and checks
- [Autoregressive example](../examples/autoregressive/README.md): PyTorch, JAX, and Ray
- [Migration tool](https://github.com/Protention/ocura-oss/blob/main/tools/migrations/README.md): optional import of 0.4 ledgers, downloaded separately

Repository references describe this checkout and remain readable directly on GitHub.
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

Ocura OSS is research software under the [Apache License 2.0](../LICENSE).
Versions 0.5.0 and earlier were released under MPL-2.0. The 0.x interface and record
format may change before 1.0, and there is no production support commitment.

Bug reports and focused pull requests are welcome. See [Contributing](../CONTRIBUTING.md)
and the [security policy](../SECURITY.md).

## Command help

The installed command also provides command-specific usage:

```console
ocura-oss --help
ocura-oss run --help
```
