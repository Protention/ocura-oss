# Scripts and AI agents

Version 0.7.0.dev0.

The discovery, descriptive Python names, bounded readers, `run --from`, and
`--json-errors` sections describe unreleased work in this checkout. Published 0.6.0 does not
include those interfaces. See the [naming and compatibility guide](naming-migration.md).

Ocura OSS can be called from a shell, a Python program, or an AI agent's existing
command tool. Use it when you want a durable record connecting a baseline to later
variations, with reasons, outputs, and verification.

## Command workflow

1. Call `init --json` once and retain the project root and default pathway ID.
2. Execute the baseline through `run --json`; retain its branch point ID, returned
   under the compatible JSON key `chokepoint_id`.
3. Call `run --from ID --reason TEXT --json-errors` with the proposed parameter
   labels and `-- COMMAND`. This creates a child pathway and runs there; retain
   its `pathway_id` and the usual run result.
4. Read `verify --json` and `compare --json --from ID` before using the results.
5. Consume output through `Store.open_verified_log()` for bounded excerpts, or
   `Store.read_verified_log()` when the complete bytes fit in memory. Interpret
   metrics in the caller and cite the evidence used.

For example, with the baseline's branch point ID:

```console
ocura-oss run --root ./experiment --from <chokepoint-id> --reason "increase batch" --param batch=4 --json-errors -- python train.py --batch 4
```

`--from` requires a nonblank `--reason` and cannot be combined with `--pathway`.
Its `--param` values override the new child's inherited source-pathway labels;
labels declared only on the source run are not inherited. Ordinary `run --pathway`
overrides still apply only to that run. With neither selection option, `run` uses
the default pathway as before. Python offers the same combined operation through
`run(..., source_branch_point_id=ID, reason=TEXT)` and returns the usual
`RunExecution`.

To plan a variation before executing it, keep the two-step workflow: call
`branch --json --from ID --reason TEXT --param batch=4`, then pass its returned
`id` to `run --pathway ID --json -- COMMAND`. That run inherits the child's
labels; repeat a `--param` only for a run-specific override.

All invocations must use the intended `--root`. The returned IDs connect the steps
without scraping text. Pass them in full: the shortened IDs the CLI accepts are for
typing by hand, and a prefix that matches one record now can match two later.
Parameters label the record; set actual command arguments
separately, or pass `--substitute` and write `{KEY}` in the command so one
declaration does both. A program that builds the argument list itself needs no
quoting; a PowerShell command line must quote the placeholder. Handle nonzero exits: `run` still returns a recorded result for a failed
command, interruption, or launch failure. See [CLI](cli.md).

To stop a run early, send the `run` process `SIGTERM`, or Ctrl+Break on Windows:
it stops the command and still returns a recorded `interrupted` result. If a `run`
process is killed instead, its attempt remains unfinished. `attempts --json` lists it, `verify --json` reports it as a problem, and
`recover --json` closes it as `abandoned` once the command itself has stopped.

The [autoregressive example](../examples/autoregressive/README.md) is an executable
client of this interface, with PyTorch/JAX choices and an optional Ray Core executor.
The [Python API](python-api.md) provides the same operations with typed results.

## Consume saved output

A Python caller can read verified bytes directly from a retained run record ID:

```python
import json

from ocura_oss import Store

store = Store("experiment")
raw = store.read_verified_log(run_id="atom-<id>")
metrics = json.loads(raw.decode("utf-8"))  # For a workload that emits UTF-8 JSON.
```

The default stream is `stdout`; pass `stream="stderr"` to read diagnostics, including
output from a failed attempt. The method validates the stored run record and its lineage,
then checks containment, byte count, and SHA-256 for the exact bytes it returns.
Missing, unreadable, or inconsistent evidence raises `StoreError`.

This method reads the whole selected log into memory. It does not parse metrics,
verify the other stream, or verify the complete ledger. Keep the workflow's full
`verify` step; see [state and verification](state-and-verification.md) for the distinction.

## Example instruction for a caller

> Run a baseline and one proposed variation in the selected project. Record both
> through Ocura OSS, with matching command inputs and parameter labels. Branch from
> the baseline branch point and explain the change. Verify the evidence, compare the
> attempts, and cite the output logs used to interpret the result. Report failed
> attempts and missing evidence explicitly.

An AI agent can follow that instruction inside its existing execution environment.
The caller supplies command permissions and isolation. Ocura records and checks
local evidence; its checksums detect inconsistency without authenticating the writer.
When the caller must be able to tell whether records were rewritten afterward, save
`manifest` output outside the agent's reach and check it with `verify --against`.

## Revisiting an experiment

Records persist beyond a script or conversation. A fresh caller need not know
private paths or retain IDs from an earlier chat:

```console
ocura-oss pathways --root ./experiment --json-errors
ocura-oss runs --root ./experiment --all --json-errors
ocura-oss attempts --root ./experiment --json-errors
ocura-oss show <run-id> --root ./experiment --json-errors
ocura-oss logs <run-id> --root ./experiment --offset 0 --length 256
```

`runs --all` keeps every finalized attempt, including failed retries; `pathways`
also exposes planned variations with no run. `show` returns the selected run,
its pathway, branch point, and child pathways. Discovery verifies records and
lineage, not log contents. A bounded log read verifies the entire selected stream
before returning the requested original-byte window; bounded output does not
mean bounded verification I/O. Use `verify` for a whole-ledger check.

Choose a baseline explicitly from the discovered history, then use
`compare --from ID`. Automatic comparison selects the newest source with child
pathways, which may be a different experiment once more work is added. Comparison
reports the newest run for each child pathway; use `runs` to retain earlier retries
in an account of the experiment.

For a large history, `runs --limit N --after ID` pages by branch point timestamp
and ID. A walk in which every page reports `unfinished_attempts: 0` lists every
run that finished before its last page; otherwise list again once those runs
finish or `recover` closes them. A series of reads is not a transaction or a frozen snapshot.
For a stable copy with no writers or unfinished attempts, pass `--archive` to read
without creating ledger locks.

The executable [history example](../examples/history/README.md) implements this
workflow using public Python readers. It accepts only a root, lists all pathways
and runs, preserves unfinished attempts, and returns exact byte references
(`ledger_id`, `run_id`, stream, digest, offset, length). Those references survive
raw/gzip representation changes. A readable UTF-8 preview is separate from exact
base64 bytes. An unavailable stream remains an error rather than an empty success.

## Handle uncertain execution without duplicate work

`--json-errors` opts into the [structured error contract](cli.md#structured-errors-unreleased).
It implies JSON results. Existing `--json` callers keep their original behavior.
Inspect the error's execution state and known IDs before deciding what to do next;
the recovery field describes an inspection or recovery step, not permission to
execute a workload again.

An error, caller timeout, lost terminal, or failed JSON parse does not prove that
the workload never ran. Use `runs`, `show`, and `attempts` at the same root to
reconcile evidence first. Retain a finalized failure as an attempt, and make any
retry a deliberate new run. Wait for a running attempt. Recover an abandoned one
only after its command has stopped; `recover` closes evidence and never restarts
the workload. Ocura supplies no idempotency key or exactly-once execution guarantee.

A combined `run --from` call checks its input, creates the child, then records the
command. A later storage failure can therefore leave an empty child even when
`error.execution` is `not_started`. Structured errors preserve the known child
`pathway_id` and `source_branch_point_id` in `error.ids`; Python exposes the same
fields through `StoreError`. Inspect `pathways`, `runs --pathway CHILD_ID --all`,
and `attempts` at that root, and `show RUN_ID` when a run ID is known. If the
evidence supports a deliberate continuation on that child, use
`run --pathway CHILD_ID -- COMMAND` (Python: `run(..., pathway_id=child_id)`).
Repeating `run --from` creates a new child; it does not resume the prior child or
automatically retry its workload. A known child ID alone does not prove that the
command never ran.

Several processes may record runs under one root at the same time, and `verify` and
`compare --from ID` work while they do. Ocura OSS keeps its own records apart; the
commands still share the project root as their working directory, so keep their
workload files apart or give independent experiments separate roots. Ray workers in
the supplied example return their metrics to one recorded driver command.
