# Changelog

This file records user-visible changes to Ocura OSS.

## 0.7.0.dev0 - Unreleased

- Record a stop request arriving between process-polling timeouts as an interrupted
  run, stopping the command and retaining its evidence through the existing result
  interface.
- Add canonical public names `Ledger`, `RunRecord`, and `BranchPoint`; retain
  `Pathway` and the legacy `Den`, `Atom`, and `Chokepoint` class aliases. Add
  descriptive result properties, Store readers, source keywords, and the
  `branch-points` CLI alias and `--run` storage selector. Existing constructor
  fields, success JSON, schema-2 records, IDs, checksums, and retained manifests
  remain compatible.
- Add opt-in `--json-errors` for JSON results and structured failures, including
  argument errors, stable error categories, known run IDs, execution state, and
  recovery guidance. Existing `--json` behavior and exit statuses remain unchanged.
  Python `StoreError` exposes diagnostic fields too. A failure after launch directs
  callers to inspect retained attempts and runs before considering resubmission.
- Add an executable history inspection example for a fresh caller with only a
  ledger root. It includes failed retries, empty variations, unfinished attempts,
  and verified bounded log excerpts with original-content references. It never
  executes or recovers a workload. Update terminology and migration guidance.
- **Fixed:** validating a pathway no longer doubles its work for every branch
  generation. A history built by branching each run from the previous one made
  `run`, `branch`, `compare`, and `verify` take seconds after about ten generations
  and far longer after that. Each record is now validated once per operation.
- `ocura-oss --version` prints the installed version.
- `pathways` and `compare` read the atom records once per command, instead of
  once for every pathway or child pathway.
- Add `ocura-oss runs` and `Store.list_runs()`: finalized runs ordered by terminal
  record timestamp and ID, including failed retries, each with the `chokepoint_id`
  to branch from.
  Filter by pathway or outcome, page with `--limit` and `--after`, or list every
  match with `--all`. Live pagination is observational: records can appear behind
  a cursor even when no unfinished attempts are observed. Read settled history or
  a stable archive when completeness is required.
- Add `ocura-oss show` and `Store.inspect_run()`: one run with its pathway, terminal
  chokepoint, and the pathways branched from it. Command arguments are included
  only with `--command`. Neither command reads log contents.
- Add `--archive` to the commands that only read, and `Store(root, archive=True)`,
  to read a stable copy of a ledger without creating lock files. Archive mode
  refuses every change, and refuses a ledger with unfinished attempts, saying how
  to list and recover them.

## 0.6.0 - 2026-10-22

- **License:** Ocura OSS is now released under the Apache License 2.0. Versions 0.5.0
  and earlier remain under MPL-2.0. Contributions are accepted under Apache-2.0.
- `branch --from`, `compare --from`, and `run --pathway` accept a shortened ID: at
  least four of its leading hexadecimal characters, with or without the kind word.
  The prefix must match exactly one record; an ambiguous one is rejected with the
  matches listed. Output and records still carry full IDs. Add `Store.resolve_id()`.
  The Python workflow functions still take full IDs.
- **Changed:** a run now records its pathway's effective parameters together with
  its own, so a run on a branch no longer repeats the branch's `--param` values. A
  parameter declared on the run replaces the pathway's value for the same key. Before
  this release a run recorded only what it declared, plus any pathway parameter that
  `--substitute` used. `compare` therefore reports a child run's inherited labels
  under `run_parameters` where it previously reported none. Atoms already recorded
  are not changed.
- A command run where there is no state now names `ocura-oss init` in its error.
  When a directory above the root holds state, the error names that directory
  instead. State is never created implicitly.
- `ocura-oss run` now handles `SIGTERM`, `SIGHUP`, and Ctrl+Break on Windows as it
  handles the first Ctrl+C: the command is stopped and the run is recorded as
  `interrupted` with exit status 1. Before this release those requests ended the
  recorder, left an abandoned attempt, and left the command running. A signal the
  caller ignores, as under `nohup`, stays ignored. The Python `run()` function
  installs no signal handlers.
- **Changed:** a command no longer outlives a killed recorder on Windows or Linux.
  On Windows the operating system ends the command and the processes it started,
  and an interrupted run now ends those processes too. On Linux the kernel kills
  the command itself. On macOS the command still keeps running. A command that
  finishes on its own is not affected. See "When the recorder stops" in
  `docs/state-and-verification.md`.
- Documentation: the README and overview show what `compare` prints, say when a
  different tool is the better choice, and state how to remove the package's data.
- Add an optional, standalone tool to copy complete 0.4 ledgers into schema 2.
  It verifies the source, preserves IDs and log bytes, and writes a separate copy
  with a migration receipt. Download it from `tools/migrations/`; it is excluded
  from both the wheel and source distribution and adds no runtime dependencies.

## 0.5.0 - 2026-10-07

- **Breaking:** change the record format to schema version 2. State written by 0.4 and
  earlier is rejected with exit status 2 and left untouched; there is no migration. To
  keep using existing records, stay on 0.4 for that project (`ocura-oss<0.5`). To record
  with 0.5, move the old `.ocura-oss/` aside and run `ocura-oss init`. See "Upgrading
  from 0.4" in `docs/state-and-verification.md`. `Atom` log fields, `finished_at`, and `duration_seconds`
  may now be `None`, and `RunSummary.duration_seconds` may be `None`.
- Journal each run under `.ocura-oss/attempts/` before its command launches. An attempt
  whose recording process is interrupted twice or killed stays visible instead of leaving
  orphaned logs. Add `ocura-oss attempts` and `Store.list_attempts()` to list unfinished
  attempts as `running` or `abandoned`.
- Add `ocura-oss recover`, `recover()`, and `Store.recover()` to close abandoned attempts.
  One with no recorded outcome becomes an atom with the new `abandoned` outcome: its
  captured output is retained, and no finish time, duration, or return code is invented.
- Support several processes recording runs under one state root at once. `verify` and
  `compare` work while runs are in flight and report them as running rather than as damage.
  Of several concurrent `init` calls, exactly one succeeds.
- Report an atom that does not have exactly one chokepoint as a verification problem.
- Add `run --no-capture` and `run(capture=False)` to retain no stdout or stderr. The atom
  records `output_capture: none` instead of log paths.
- Add `run --mask-arg POSITION` and `run(masked_arguments=[...])` to store a placeholder for
  chosen command tokens while the command still receives the real values.
- Add `run --substitute` and `run(substitute=True)` to replace `{KEY}` in command tokens
  with declared or pathway parameter values and record every parameter used, so one
  declaration both labels and configures a run.
- Add opt-in launch context: `run --context` records the platform and the git revision and
  dirty state, and `run --context-file PATH` records a file's size and SHA-256. Nothing is
  queried, executed, or read for context unless requested.
- Add `ocura-oss manifest`, `verify --against FILE`, and `verify(against=...)`. A manifest
  kept outside the state detects records later rewritten together with their checksums.
  `verify` also reports a digest of the manifest.
- State how the format evolves: readers ignore payload fields they do not recognize, so a
  schema version can gain optional fields without another break.
- Publish releases from GitHub Actions through PyPI Trusted Publishing.
- Keep the core runtime dependency-free.

## 0.4.0 - 2026-09-16

- Add `Store.read_verified_log()` to read stdout or stderr as bytes and verify
  their exact byte count and SHA-256 digest against the stored atom. Update the
  autoregressive report to use this method when consuming its saved output.

## 0.3.0 - 2026-09-08

- Add `init --json` and `run --json` for complete machine-readable command workflows.
  Execution JSON retains the existing exit codes and keeps subprocess output in logs.
- Escape Unicode in CLI JSON so paths and labels round-trip on legacy Windows encodings.
- Add a small repository-only autoregressive character-model example with optional PyTorch and JAX
  backends, an optional local Ray Core executor, a baseline/variant experiment,
  and a report reconstructed from saved evidence. Example scripts, requirements, and
  tests are excluded from both package distributions; documentation links to the repository.
- Explain local experiment workflows for terminal users, scripts, and AI agents;
  clarify recorded parameter labels and provide a repository CLI reference.
- Keep the core runtime dependency-free and preserve the existing state format.

## 0.2.2 - 2026-08-24

- Make automatic comparison select the newest chokepoint that has child pathways, while preserving `no_branch` for states with no branches.
- Retain valid log references during verification so checksum-mismatched logs receive one accurate problem report.

## 0.2.1 - 2026-08-24

- Point package metadata and repository documentation to the hosted Ocura OSS reference at `https://ocuna-ai.com/docs`.

## 0.2.0 - 2026-08-24

- Add a provisional typed Python API for initialization, execution, branching, comparison, verification, and demonstration.
- Export `Store` as the low-level state initialization, inspection, log-resolution, and verification interface; direct record writers remain internal.
- Publish a `py.typed` marker and package API reference.

## 0.1.1 - 2026-08-24

- Correct the installation guidance for the published PyPI package.

## 0.1.0 - 2026-08-24

- Initial public research package for recording, branching, verifying, and comparing trusted local command runs.
