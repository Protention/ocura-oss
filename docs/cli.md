# Command-line reference

Version 0.7.0.dev0.

This draft builds on 0.6.0. Compression options and the `logs`, `compress`, and
`decompress` commands below are proposed branch behavior, absent from the released
package. See [open design decisions](autocompression.md#decisions-before-merge).

Discovery commands, `branch-points`, descriptive Python names, `run --from`, and
`--json-errors` are also unreleased. A ledger contains run records and pathways; a branch point
identifies terminal evidence for a variation. Existing JSON keys and stored IDs
retain their legacy spellings. See the [naming guide](naming-migration.md).

The `ocura-oss` command records trusted local command attempts, creates metadata branches, compares branch evidence, and verifies project-local state.

```text
ocura-oss COMMAND [OPTIONS]
```

Commands run against one project root. Unless `--root PATH` is supplied, the root is the current working directory. State is stored below that root in `.ocura-oss/`.

## Command summary

| Command | Purpose |
| --- | --- |
| `init` | Create one ledger and its default pathway |
| `run` | Run one command and retain a run record, logs, and a branch point |
| `runs` | Page recorded run metadata, including failed retries |
| `show` | Inspect one run, its branch point, and its branches without reading logs |
| `pathways` | List pathway lineage and evidence status |
| `branch-points` (`chokepoints`) | List terminal evidence boundaries |
| `branch` | Create a metadata-only child pathway |
| `compare` | Compare a source run with runs on its child pathways |
| `verify` | Recheck records, relationships, and referenced logs |
| `manifest` | Print one checksum line per verified record, to keep elsewhere |
| `attempts` | List runs that have started and are not yet finalized |
| `recover` | Close attempts whose recording process stopped |
| `logs` | Read verified original log bytes or inspect storage metadata |
| `compress` | Compress eligible finalized captured logs losslessly |
| `decompress` | Restore finalized compressed logs to their original bytes |
| `demo` | Run the complete workflow in a new retained directory |

Run `ocura-oss COMMAND --help` for command-specific help, and `ocura-oss --version` to print the installed version.

## Conventions

### Project root

`--root PATH` identifies the directory that contains `.ocura-oss/`. Relative paths and `~` are resolved before use. The command does not change the parent process working directory.

`init` and `demo` create state. All other commands require initialized state. Without it they exit with status 2 and name `ocura-oss init`. When a directory above the root already holds state, the message names that directory, so that a command run from a subdirectory is not answered by creating a second ledger.

### Identifiers

Every record has an identifier made of its kind and 32 hexadecimal characters, such as `chokepoint-447ca3002f0b4d7c9a1e5b6c7d8e9f01`. Output, JSON results, and records always carry the full identifier.

`branch --from`, `compare --from`, `run --from`, `run --pathway`, `logs ID`, `compress --run`, and `decompress --run` also accept a prefix: at least four of the identifier's leading hexadecimal characters, with or without the kind word, such as `447c` or `chokepoint-447ca3`. A prefix must match exactly one stored record of that kind. One that matches several is rejected with the matching identifiers listed, and one that matches none is rejected, both with exit status 2 and before anything is written or run. The compatible `compress --atom` and `decompress --atom` selectors use the same resolution.

Prefixes are for typing by hand. A script or agent already holds the full identifier and should pass it, because a prefix that is unique today can match a second record later.

### Declared parameters

`--param KEY=VALUE` records a string label. The option is repeatable. Keys must begin with a letter or underscore and may then contain letters, digits, underscores, periods, or hyphens. Keys and values must not be empty. A key may appear only once in one invocation.

A run records its pathway's effective parameters together with its own declarations. A run on a branch therefore carries the branch's parameters without repeating them; a `--param` on the run replaces the pathway's value for the same key in that run's record and adds any other key. A pathway's parameters are fixed when it is created.

With `run --from`, `--param` instead supplies overrides for the new child pathway,
which the run then inherits. The child inherits the source pathway's parameters;
labels declared only on the source run are not inherited.

Declared parameters do not configure the child process. Pass process arguments after `--` in `run`. To declare a value once and also pass it, use `run --substitute` and write `{KEY}` in the command, quoted in PowerShell.

### JSON output

Every command accepts `--json`. JSON mode writes one result document to standard output, with Unicode escaped so it also works on legacy Windows encodings. Diagnostic messages go to standard error.

`run --json` retains child stdout and stderr in their logs without streaming them. A recorded command failure, interruption, or launch failure still returns a JSON result with its existing nonzero exit status. Invalid input or a state error returns no result document.

### Structured errors (unreleased)

Pass `--json-errors` before the command or among its options to opt into structured
failures. This flag also enables normal JSON results, so it replaces `--json` for
callers adopting this contract. Existing `--json` output and error behavior are
unchanged. A failed workload that was successfully recorded still returns its
normal run result and nonzero exit status; it is not a recorder error.

An operation error is one JSON document with this shape:

```json
{"error":{"code":"state_not_found","message":"no initialized state","execution":"not_started","ids":{},"recovery":"initialize_state"}}
```

The example message is illustrative. Branch on `code` and `execution`, not message
wording. `ids` carries identifiers known when the error occurs and may be empty.
It does not guarantee that every referenced record was finalized.

| `execution` | Meaning for the requested workload |
| --- | --- |
| `not_started` | The workload was not launched |
| `recorded` | A terminal execution record exists; inspect it |
| `unknown` | Work may have started; reconcile attempts and runs before deciding whether to retry |
| `not_applicable` | The operation did not request workload execution |

These states do not describe whether ledger files changed. A lost caller response
or timeout has no error envelope and leaves execution uncertain. No state is a
promise of idempotency or authorization to repeat a command.

| `code` | Default `recovery` hint |
| --- | --- |
| `invalid_input` | `correct_input` |
| `state_not_found` | `initialize_state` |
| `record_not_found`, `ambiguous_id` | `select_record` |
| `unsupported_schema` | `use_supported_reader` |
| `integrity_error`, `invalid_state` | `verify_state` |
| `permission_denied` | `check_permissions` |
| `io_error` | `check_storage` |

`unknown` execution takes precedence and returns `inspect_attempts_and_runs`;
`recorded` returns `inspect_recorded_run`. Recovery hints are actions for the
caller to consider, not automatic operations. Inspect an unexpected root before
initializing it. Recovery closes abandoned evidence without rerunning a workload.
See the [fresh caller workflow](automation.md#revisiting-an-experiment).

### Summary data

Default summaries and listing output omit command arguments, environment values, and log contents. `run` streams child output unless `--quiet` or `--json` is present, so streamed output is separate from the final summary.

`logs ID` explicitly reads log contents as verified original bytes. Its `--info`
and `--json` modes emit metadata only.

Raw run records retain command arguments, and log files retain command output. Keep secrets out of command arguments, declared parameters, branch reasons, and output. When a run cannot avoid one, `run --mask-arg` keeps a command token out of the records and `run --no-capture` keeps output off disk; see [keeping sensitive values out of records](state-and-verification.md#keeping-sensitive-values-out-of-records).

## `init`

Create one ledger and one default pathway.

```text
ocura-oss init [--root PATH] [--name NAME] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root in which `.ocura-oss/` is created |
| `--name NAME` | string | `ocura-oss` | Nonblank name stored in the ledger record |
| `--json` | flag | false | Emit project paths and identifiers as JSON |

#### Behavior

`init` creates `.ocura-oss/`, one ledger record, and one unbranched default pathway. Initialization fails if `.ocura-oss/` already exists, including when the directory contains incomplete state.

The text response identifies the ledger, default pathway, and state directory.

#### JSON output

| Field | Type | Description |
| --- | --- | --- |
| `root` | string | Resolved project root |
| `state_dir` | string | Resolved `.ocura-oss` directory |
| `den_id` | string | Persisted ledger identifier |
| `default_pathway_id` | string | Persisted default pathway identifier |

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | State was initialized |
| 2 | Input was invalid, state already existed, or a filesystem operation failed |

#### Example

```console
ocura-oss init --root ./experiment --name "batch study"
```

## `run`

Run one trusted local command and record terminal evidence.

```text
ocura-oss run [--root PATH] [--pathway ID | --from BRANCH_POINT_ID --reason TEXT]
              [--param KEY=VALUE] [--quiet] [--json] [--json-errors]
              [--no-capture] [--mask-arg POSITION] [--substitute]
              [--no-compress] [--compress-min-bytes N]
              [--context] [--context-file PATH] -- COMMAND...
```

The `--` separator is required. Ocura OSS options belong before it. Every token after it is passed to the child command as one argument token.

#### Arguments and options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--pathway ID` | pathway ID or unique prefix | ledger default when `--from` is absent | Existing pathway that receives the run; mutually exclusive with `--from` |
| `--from BRANCH_POINT_ID` | branch point ID or unique prefix | none | Create a child from this verified source and run there; requires `--reason` |
| `--reason TEXT` | string | none | Nonblank explanation stored on the new child pathway; requires `--from` |
| `--param KEY=VALUE` | string pair | none | Override or add a label for this run; with `--from`, set the new child pathway's labels instead; repeatable |
| `--quiet` | flag | false | Retain output without mirroring it to the terminal |
| `--json` | flag | false | Emit one result as JSON; retain child output in logs without streaming |
| `--json-errors` | flag | false | Emit JSON results and structured errors with execution state and known IDs |
| `--no-capture` | flag | false | Retain no stdout or stderr; the record states that output was not captured |
| `--no-compress` | flag | false | Keep this run's captured logs raw after finalization |
| `--compress-min-bytes N` | nonnegative integer | `1048576` | Minimum original size for automatic gzip compression; logs must also save at least 10% |
| `--mask-arg POSITION` | integer | none | Record `<masked>` in place of the `COMMAND` token at this zero-based position; repeatable |
| `--substitute` | flag | false | Replace `{KEY}` in `COMMAND` tokens with that parameter's value |
| `--context` | flag | false | Record the platform and the git revision and dirty state at launch |
| `--context-file PATH` | path | none | Record the size and SHA-256 of this file at launch; repeatable |
| `COMMAND...` | argument tokens | required | Executable and arguments placed after `--` |

#### Create and run a variation

Use an explicit source branch point and reason to create a child pathway and
record its first command in one call:

```console
ocura-oss run --from chokepoint-<id> --reason "increase batch" --param batch=4 --json-errors -- python train.py --batch 4
```

This verifies the source using the same checks as `branch`, creates the child,
and runs the command there. It returns the usual run result, with `pathway_id`
identifying the new child; there is no additional result type or JSON field.
`--from` and `--pathway` cannot be combined. A source and a nonblank reason must
be supplied together. Without either option, `run` still uses the default pathway.

The child's parameters inherit from the source pathway and apply this call's
`--param` overrides. Labels declared only on the source run are not copied. These
parameters persist on the new pathway and are recorded by its runs; ordinary `run --pathway`
parameters still affect only that individual run. Actual command inputs remain
explicit unless `--substitute` is used.

The operation is sequential. Later run validation or execution failure can leave
the child in place, including an empty child if no run began. With `--json-errors`,
known `pathway_id` and `source_branch_point_id` are included in `error.ids`;
`error.execution` describes the workload, so `not_started` does not mean that no
pathway was created. Inspect the retained attempts and runs before continuing
explicitly with `run --pathway CHILD_ID`. Repeating `run --from` creates another
child; it never resumes or retries the previous operation automatically. See
[uncertain-execution recovery](automation.md#handle-uncertain-execution-without-duplicate-work).

The separate `branch` followed by `run --pathway` remains available when planning
a variation before executing it.

#### Execution

The command runs with `shell=False` and the project root as its working directory. It inherits the invoking process environment. Environment keys and values are not serialized.

Stdout and stderr are captured as separate files under `.ocura-oss/logs/`. Unless `--quiet` or `--json` is present, the same bytes are also streamed to the terminal.

After finalization, captured logs of at least 1 MiB are considered once for
lossless gzip compression. Each must save at least 10%; small and incompressible
logs remain raw. No older runs are scanned. `--no-compress` disables this step for
the run, and `--compress-min-bytes N` changes its size threshold. Compression
errors emit storage warnings on stderr without changing the command's recorded
outcome. See the [local prototype's policy and compatibility limits](autocompression.md).

With `--no-capture`, no log files are written. Output still streams to the terminal unless `--quiet` or `--json` is present, and is otherwise discarded.

`--mask-arg POSITION` counts `COMMAND` tokens from zero, so position 0 is the executable. The command receives the real token; the attempt and run records store `<masked>` there and list the position. Masking does not alter output, so a command that prints the value still writes it to a captured log. A position outside `COMMAND` is rejected before anything runs.

With `--substitute`, each `{KEY}` in a `COMMAND` token is replaced by that parameter's value before the command launches. A value comes from this run's `--param` declarations first and then from the pathway's effective parameters, so a run on a branch can use the branch's values without restating them. These are the same labels the run records, and the recorded command is the substituted one. Write `{{` or `}}` for a literal brace. A placeholder with no value, or an unbalanced brace, is rejected before anything runs. Without `--substitute`, braces are passed through untouched. Quote a placeholder, as in `"{batch}"`, when the shell is PowerShell: it reads bare braces as a script block and does not pass them to the command.

`--context` and `--context-file` add an optional `context` object to the record; see [optional launch context](state-and-verification.md#optional-launch-context). Neither runs or reads anything unless it is present.

One run produces:

- one run record containing timing, outcome, the pathway's and the run's declared parameters, command arguments, and log metadata
- one stdout log and one stderr log, unless `--no-capture` is present
- one branchable terminal branch point

The attempt is journaled under `.ocura-oss/attempts/` before the command launches, and that entry is removed once the run record and branch point are written.

A passing command records `passed`. A nonzero return code records `failed`. A launch error records `launch_failed`. The first Ctrl+C stops the command and records `interrupted` with partial output retained. So does a request to stop the `run` process: `SIGTERM` or `SIGHUP` on Linux and macOS, Ctrl+Break on Windows. A second Ctrl+C exits immediately; so does a killed `run` process. Either leaves the attempt unfinished until [`recover`](#recover) closes it as `abandoned`. The operating system then ends the command on Windows and Linux; on macOS the command keeps running. See [when the recorder stops](state-and-verification.md#when-the-recorder-stops).

#### Text output

The final text summary includes the run record, branch point, pathway, outcome, duration, log paths, and the return code or launch category when available. With `--no-capture`, it states `output: not captured` in place of the log paths. It does not repeat command arguments or parameter values; `pathways` lists a pathway's parameters and `compare` lists what each run recorded.

Log paths in the run summary and run record are logical paths; compressed physical
storage appends `.gz`. Use `logs ID` to read either representation or `logs ID
--info` to find its current backing path. The text summary includes a read command.

#### JSON output

| Field | Type | Description |
| --- | --- | --- |
| `atom_id` | string | Recorded command attempt |
| `chokepoint_id` | string | Terminal evidence for branching |
| `pathway_id` | string | Pathway containing the run |
| `outcome` | string | `passed`, `failed`, `interrupted`, or `launch_failed` |
| `duration_seconds` | number | Recorded elapsed command time |
| `return_code` | integer or null | Child exit status when available |
| `launch_error_category` | string or null | Error category or `interrupted` |
| `output_capture` | string | `full`, or `none` when `--no-capture` was used |
| `stdout_log` | string or null | Project-relative stdout log path; null without capture |
| `stderr_log` | string or null | Project-relative stderr log path; null without capture |
| `storage_warnings` | array of strings, optional | Post-finalization compression errors, present only when nonempty; also printed on stderr |

Use returned IDs to continue the [automated workflow](automation.md). The [training example](../examples/autoregressive/README.md) provides an executable client.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The child command returned 0 and `passed` evidence was recorded |
| 1 | The child returned nonzero or the attempt was interrupted; evidence was recorded |
| 2 | Input or state was invalid, or evidence could not be persisted |
| 3 | The child process could not be launched; `launch_failed` evidence was recorded |

#### Examples

```console
ocura-oss run -- python script.py --epochs 4
```

```console
ocura-oss run --pathway pathway-<id> --quiet -- python script.py --batch 4
```

Declare a value once and pass it to the command:

```console
ocura-oss run --param batch=4 --substitute -- python train.py --batch "{batch}"
```

Record the revision, the platform, and the digest of a lock file with the run:

```console
ocura-oss run --context --context-file requirements.lock -- python train.py
```

Keep a token out of the records and its output off disk. Position 3 is the value after `--token`:

```console
ocura-oss run --no-capture --mask-arg 3 -- python upload.py --token <value>
```

## Stable archives

`--archive` is available on `pathways`, `chokepoints`, `runs`, `show`,
`compare`, `verify`, `manifest`, `attempts`, and `logs`. It reads without creating
ledger lock files and refuses every change. Stop all writers and finalize or recover
unfinished attempts before making the archive. The mode rejects archives containing
attempt records, saying how to list and recover them, and assumes the storage
remains unchanged while reading; it does not coordinate with active writers.

Normal mode takes a lock for each log it reads. When the storage is write-protected
it reads without one, as 0.6.0 did; every byte is still verified, so a concurrent
change makes the read fail rather than return wrong data. `compress` and
`decompress` still require the lock. Unlike archive mode, this also reads ledgers
with unfinished attempts and lists them.

Content snapshots may need writable OS temporary storage outside the archive.
Archive mode does not introduce an export format or snapshot an active ledger.

## `logs`

Read one finalized run record's verified original log bytes or inspect its metadata.

```text
ocura-oss logs ID [--stream stdout|stderr] [--offset N] [--length N]
                [--info] [--json] [--archive] [--root PATH]
```

| Name | Default | Description |
| --- | --- | --- |
| `ID` | required | Full run record ID or unique prefix |
| `--stream` | `stdout` | Select `stdout` or `stderr` |
| `--info` | false | Emit verified metadata instead of contents |
| `--json` | false | Emit metadata as JSON; implies `--info` |
| `--root PATH` | current directory | Project root containing initialized state |

`--offset` starts at an original-byte position (default 0); `--length` returns at
most that many bytes (default the remaining stream). Values must be nonnegative,
and an offset beyond the recorded size is rejected. An offset at EOF or a zero
length returns empty output after verification. Ranges cannot be combined with
metadata modes. Verification still checks the complete selected stream, including
bytes outside the range, before releasing any output.

Only the returned window is retained. Windows of at most 1 MiB stay in memory;
larger windows use OS temporary storage, removed on close. RAM stays bounded for
large content reads, with one decoding pass. A short range still scans the entire
original stream for SHA-256 verification.

The default writes the exact original bytes, with no summary or added newline,
only after the byte count and SHA-256 verify. Gzip storage is decoded
transparently. A missing log, damaged evidence, or an run recorded without
capture returns status 2 without printing log contents. Success returns 0.

Metadata fields in both `--info` and `--json` are `atom_id`, `stream`, `outcome`,
`started_at`, `encoding` (`raw` or `gzip`), `original_bytes`, `stored_bytes`,
`sha256`, `fingerprint`, and `path`. The path is the project-relative physical
backing path. The fingerprint identifies the stream, short run record ID, and a prefix
of the original digest; it is a readable reference, not a summary of the log.
Neither metadata mode includes commands or log contents.

```console
ocura-oss logs atom-<id> --stream stderr
ocura-oss logs atom-<id> --info
```

## `compress` and `decompress`

Change finalized captured logs' physical storage while retaining original bytes,
recorded sizes and digests, JSON records, and retained manifests.

```text
ocura-oss compress [--run ID] [--min-bytes N] [--dry-run] [--json] [--root PATH]
ocura-oss decompress [--run ID] [--dry-run] [--json] [--root PATH]
```

| Option | Default | Description |
| --- | --- | --- |
| `--run ID` | all finalized run records | Preferred selector for one full run record ID or unique prefix |
| `--atom ID` | all finalized run records | Compatible alias of `--run` |
| `--min-bytes N` | `1048576` | `compress` only: minimum original size, as a nonnegative integer; require at least 10% savings as well |
| `--dry-run` | false | Verify and preview prospective changes without replacing files |
| `--json` | false | Emit `{"dry_run": false, "changes": [...]}` instead of text |
| `--root PATH` | current directory | Project root containing initialized state |

If both `--run` and `--atom` are supplied, they must resolve to the same full ID.
A full ID and its unique prefix are accepted together. Different resolved IDs
are rejected before any storage change, with `invalid_input` under `--json-errors`.

Each change entry contains `atom_id`, `stream`, `action`, `original_bytes`,
`stored_bytes`, and project-relative physical `path`. Actions are `compressed`,
`would-compress`, `restored`, `would-restore`, `unchanged`, `small`, or
`no-savings`. Active attempts and run records
without captured output are not converted. Both commands return 0 on success and
2 for invalid input, unverifiable evidence, or a failed storage operation. A pass
can finish earlier conversions before a later log fails; it is not a transaction
over the whole ledger.

Use `--dry-run` to inspect older logs before an explicit pass. Conversion needs
temporary disk headroom. Earlier readers do not support gzip backing despite
the unchanged record schema; use this branch's `decompress` and `verify` before
returning to one. See [automatic log compression](autocompression.md).

## `runs` and `show`

```text
ocura-oss runs [--pathway ID] [--outcome OUTCOME] [--after ID] [--limit N | --all]
               [--json] [--root PATH]
ocura-oss show ID [--command] [--json] [--root PATH]
```

`runs` lists finalized runs by terminal record timestamp, then ID, including earlier
failed attempts on a pathway. Each entry carries the run record ID and the `chokepoint_id`
to pass to `branch --from`. `--limit` defaults to 100 and accepts 1 through 1000;
`--all` returns every match instead. `--pathway` selects one pathway; `--outcome`
accepts the recorded outcomes listed under `run`. `--after` resumes strictly after
a branch point, normally the previous page's `next_after`.

JSON contains `runs` (the run summaries used by `compare`, plus `chokepoint_id`),
`next_after` (the last returned branch point ID when more matches exist, otherwise
null), `unfinished_attempts`, and `verification: {"scope": "records", "logs_checked": 0}`.
Every branch point record's envelope and fields must validate, even when filters
exclude it, and each returned run's records and lineage are checked. Log contents
are not read. There is no persistent index: each page scans the branch point records
once, so walking a long history page by page repeats that scan, and `--all` does it
once.

Each page reads the clock and counts unfinished attempts before it scans, and
includes only terminal records timestamped before that reading. These reads are
not an atomic snapshot: a record can become visible behind the cursor, where
`--after` will not return it. `unfinished_attempts` is an observation of attempt
files, not a completeness certificate; even zero on every page does not prove
that a live walk found every run. Clock changes can also affect which records
are visible to a page.

For complete reconstruction, stop writers and resolve unfinished attempts, then
restart discovery without `--after`; `--all` returns the matches from one scan.
A page walk over unchanged, settled history is also stable. Use `--archive` for
a stable copy with no writers or unfinished attempts. The caller is responsible
for that stability; archive mode does not freeze a live ledger.

`show` selects one run record, validates its record and lineage, and returns `atom`,
`pathway`, `chokepoint`, `children`, and
`verification: {"scope": "record_and_lineage", "logs_checked": 0}`. The run record includes
declared parameters, timing, outcome, and recorded log paths, sizes, and digests.
The pathway includes the reason and source relationship. `chokepoint` is the run's
terminal branch point, or null while the run is unfinished, and `children` lists the
pathways branched from it, oldest first. Finding them scans the branch point and
pathway records. Command arguments and masking positions are omitted unless
`--command` is supplied. No log contents are read.

Both commands accept unique ID prefixes in their ID options. Agents should retain
and reuse the full returned IDs. A successful view returns 0, and invalid input
or records return 2 without a partial JSON result.

## `pathways`

List structurally valid pathway records in creation order, oldest first.

```text
ocura-oss pathways [--root PATH] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit one machine-readable JSON document |

#### Output

Each entry contains:

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Pathway identifier |
| `parent_pathway_id` | string or null | Parent pathway for a branch |
| `source_chokepoint_id` | string or null | Chokepoint from which the branch was created |
| `created_at` | string | UTC ISO 8601 timestamp |
| `reason` | string | Branch reason; `default pathway` for the default pathway |
| `parameters` | object | Effective declared pathway parameters |
| `has_terminal_evidence` | boolean | Whether any run record is recorded on the pathway |

JSON mode returns `{"pathways": [...]}`.

The listing is fail-closed. A malformed or misnamed record causes exit status 2 instead of producing a partial list. This command does not verify pathway relationships or logs; use `verify` for complete state verification.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The validated list was emitted |
| 2 | State was missing, malformed, inconsistent, or unreadable |

<a id="chokepoints"></a>

## `branch-points` (legacy alias: `chokepoints`)

Both command names accept the same arguments and retain the existing success
JSON shape. `branch-points` is the descriptive spelling in this unreleased checkout.

List structurally valid terminal branch point records in reverse creation order, newest first.

```text
ocura-oss branch-points [--root PATH] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit one machine-readable JSON document |

#### Output

Each entry contains:

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Chokepoint identifier |
| `pathway_id` | string | Pathway that owns the source run record |
| `atom_id` | string | Recorded command attempt |
| `outcome` | string | `passed`, `failed`, `launch_failed`, `interrupted`, or `abandoned` |
| `created_at` | string | UTC ISO 8601 timestamp |
| `branchable` | boolean | Whether the branch point can serve as a branch source |

JSON mode returns `{"chokepoints": [...]}`. Command arguments and log contents are not included.

The listing is fail-closed. A malformed or misnamed record causes exit status 2. This command does not verify run record relationships or logs; use `verify` for complete state verification.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The validated list was emitted |
| 2 | State was missing, malformed, inconsistent, or unreadable |

## `branch`

Create a metadata-only child pathway from verified terminal evidence.

```text
ocura-oss branch --from CHOKEPOINT_ID --reason TEXT [--param KEY=VALUE] [--root PATH] [--json]
```

#### Arguments and options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--from CHOKEPOINT_ID` | branch point ID or unique prefix | required | Terminal branchable source branch point |
| `--reason TEXT` | string | required | Nonblank explanation recorded on the child pathway |
| `--param KEY=VALUE` | string pair | none | Override applied to the parent's effective parameters; repeatable |
| `--root PATH` | path | current directory | Project root containing the source |
| `--json` | flag | false | Emit the child pathway summary as JSON |

#### Verification and state effects

Before writing, `branch` validates the branch point, its run record, its pathway, their relationships, and the logs the run recorded. Missing, checksum-mismatched, nonterminal, nonbranchable, or otherwise unverifiable sources are rejected.

The child inherits the parent's effective parameters and applies the supplied overrides. The operation records the parent pathway, source branch point, reason, creation time, and effective parameters.

It does not copy a workspace, process, memory image, checkpoint, artifact, source run record, or external state. It does not run a command. Use `run --pathway CHILD_ID` to attach later evidence; that run records the child's effective parameters.

To create the child and immediately run its first command, use
[`run --from CHOKEPOINT_ID --reason TEXT`](#create-and-run-a-variation).

#### JSON output

JSON mode returns the fields documented for a pathway listing. `has_terminal_evidence` is `false` for the new child.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The child pathway was created |
| 2 | Input, source records, source logs, or state were invalid |

#### Example

```console
ocura-oss branch --from chokepoint-<id> --reason "increase batch" --param batch=4
```

## `compare`

Compare one verified source run with the newest recorded run on each child pathway created from its branch point.

```text
ocura-oss compare [--from CHOKEPOINT_ID] [--root PATH] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--from CHOKEPOINT_ID` | branch point ID or unique prefix | newest branched source, or newest terminal source when no branches exist | Explicit source for targeted verification |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit a structured comparison document |

#### Source selection

With `--from`, comparison verifies the selected source and the child evidence used in the result.

Without `--from`, the complete state must pass verification before Ocura OSS selects the newest branch point referenced by a child pathway. A state with no child pathways selects its newest terminal branch point and reports `no_branch`. A malformed record, broken reference, unverified log, orphaned log file, unexpected log directory, run record without a branch point, or abandoned attempt anywhere in state blocks automatic selection. Attempts that another process is still recording do not.

#### Comparison state

| State | Meaning |
| --- | --- |
| `ready` | Every child pathway has terminal evidence |
| `partial` | At least one child pathway lacks terminal evidence |
| `no_branch` | No child pathway was created from the source branch point |

For a child with multiple run records, comparison uses the newest run record by start time and identifier. It reports pathway parameter differences and, when child evidence exists, run-level declared parameter differences. The pathway delta compares the two pathways' effective parameters. The run delta compares the labels the source run and the child run recorded, which include each run's own declarations.

Parameter deltas contain inherited, added, and changed values. Removed parameters are outside the comparison schema.

Comparison reads stored records and logs. It does not rerun commands.

#### JSON output

The top-level document contains:

| Field | Type | Description |
| --- | --- | --- |
| `state` | string | `ready`, `partial`, or `no_branch` |
| `source_chokepoint_id` | string | Selected source branch point |
| `source_pathway_id` | string | Pathway containing the source run |
| `source_run` | object | Reduced source run record summary |
| `children` | array | One comparison for each child pathway |

Each child contains its pathway ID, reason, source branch point, pathway parameter delta, source run, optional child run, optional run parameter delta, and `missing_evidence` flag.

A run summary contains `id`, `pathway_id`, `outcome`, `started_at`, `duration_seconds`, and `return_code`. `duration_seconds` is null for an `abandoned` run, and the text view prints `abandoned (duration unknown)`.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | A comparison was emitted, including `partial` or `no_branch` |
| 2 | Selection, state, source evidence, or child evidence was invalid |

## `verify`

Recheck every state record and every log referenced by a recorded run.

```text
ocura-oss verify [--root PATH] [--json] [--against FILE]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit counts and the complete problem list as JSON |
| `--against FILE` | path | none | Manifest retained from an earlier `manifest`; every record it lists must still be present and unchanged |

#### Checks

Verification covers:

- record envelopes, kinds, schema versions, and canonical checksums
- payload identifiers and agreement between identifiers and filenames
- required fields and outcome invariants
- ledger, pathway, run record, and branch point references
- pathway lineage cycles and source-parent agreement
- that every run record has exactly one branch point
- referenced log containment, existence, byte count, and SHA-256 digest
- unreferenced files and unexpected directories under `.ocura-oss/logs/`
- unfinished attempts
- with `--against`, that every retained manifest entry is still present with the same checksum

`logs_checked` counts individual logs that passed verification. A valid run record normally contributes two logs, and none when it was recorded without capture.

Verification may run while other processes record runs under the same root. An attempt that a live process is still recording is listed under `attempts.running` and is not a problem; its logs are not checked until it is finalized. An abandoned attempt is a problem until [`recover`](#recover) closes it.

#### JSON output

| Field | Type | Description |
| --- | --- | --- |
| `den` | string | Den identifier |
| `status` | string | `ok` or `failed` |
| `digest` | string or null | SHA-256 of the manifest when the state is intact; null otherwise |
| `counts.pathways` | integer | Pathway records found |
| `counts.atoms` | integer | Atom records found |
| `counts.chokepoints` | integer | Chokepoint records found |
| `counts.logs_checked` | integer | Referenced logs that passed verification |
| `attempts.running` | array | Identifiers of attempts a live process is still recording |
| `attempts.abandoned` | array | Identifiers of attempts whose recorder stopped; each is also a problem |
| `problems` | array | Objects containing `record` and `problem` strings |

The text view prints the digest on its own line when the state is intact.

Verification establishes consistency among local records and logs. It does not establish authorship or reproduce external execution conditions. Checksums kept inside the state cannot show that a record was rewritten together with its checksum; `--against` with a manifest kept elsewhere can.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | No verification problems were found |
| 2 | State was missing, unreadable, malformed, inconsistent, or failed verification; or the `--against` file was unreadable, malformed, or listed no records |

## `manifest`

Verify the complete state, then print one checksum line for every record.

```text
ocura-oss manifest [--root PATH] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit the digest and entries as JSON |

#### Output

Each line is `KIND ID CHECKSUM`, where `KIND` is `den`, `pathway`, `atom`, or `chokepoint` and `CHECKSUM` is that record's stored SHA-256. Lines are sorted. JSON mode returns `{"digest": "...", "entries": [...]}`; the digest is the SHA-256 of the lines, each terminated by a newline.

Keep the output where this state's writers cannot change it and pass it to `verify --against` later. See [retained manifests](state-and-verification.md#retained-manifests) for what that detects and its limits.

A manifest is printed only for a state that verifies completely. Attempts still being recorded are not records yet and are not listed.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The state verified and the manifest was emitted |
| 2 | State was missing or failed verification; no manifest was emitted |

## `attempts`

List runs that have started and are not yet finalized, oldest first.

```text
ocura-oss attempts [--root PATH] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit one machine-readable JSON document |

#### Output

Each entry contains:

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Identifier of the run record the attempt becomes when finalized |
| `pathway_id` | string | Pathway the run was started on |
| `started_at` | string | UTC ISO 8601 timestamp |
| `state` | string | `running` while a live process is recording it; `abandoned` once that process has stopped |

JSON mode returns `{"attempts": [...]}`. Command arguments and log contents are not included. A finalized run is an run record and is not listed, so the list is empty when nothing is running and nothing was abandoned.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The validated list was emitted |
| 2 | State was missing, malformed, inconsistent, or unreadable |

## `recover`

Close every attempt whose recording process stopped before finalizing it.

```text
ocura-oss recover [--root PATH] [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | current directory | Project root containing initialized state |
| `--json` | flag | false | Emit the closed attempts as JSON |

#### Behavior

Each abandoned attempt is closed according to how far its recorder got:

| Action | When | Result |
| --- | --- | --- |
| `abandoned` | No outcome had been recorded | An run record with outcome `abandoned` and its terminal branch point |
| `completed` | The run record existed without its branch point | The missing branch point, with the run record's recorded outcome |
| `cleared` | The run record and branch point both existed | The leftover attempt entry is removed |

An `abandoned` run record has no finish time, duration, or return code; none is invented. Output captured before the recorder stopped is retained and measured, so it verifies afterward.

Running attempts are left alone, so `recover` is safe to call while other runs are in flight. Run it only after the abandoned command itself has stopped: a command that outlived its recorder can keep writing to logs that recovery has already measured.

#### JSON output

JSON mode returns `{"recovered": [...]}`. Each entry contains:

| Field | Type | Description |
| --- | --- | --- |
| `atom_id` | string | Atom the attempt was closed as |
| `chokepoint_id` | string | That run record's terminal branch point |
| `pathway_id` | string | Pathway containing the run record |
| `action` | string | `abandoned`, `completed`, or `cleared` |

The text view prints one line per closed attempt and a final `recovered: N` count.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | Every abandoned attempt was closed, including when there were none |
| 2 | State was missing, or an attempt record or its evidence was malformed or unreadable |

## `demo`

Run the complete workflow in a new retained directory.

```text
ocura-oss demo --root PATH [--json]
```

#### Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--root PATH` | path | required | Destination directory; it must not exist |
| `--json` | flag | false | Emit completed stages and comparison as JSON |

#### Behavior

The demonstration performs:

- initialization
- a passing baseline run with declared `batch=1`
- selection of the resulting terminal branch point
- a metadata branch with an explicit reason and effective `batch=4`
- a passing run explicitly attached to the child pathway
- complete state verification
- a `ready` comparison

Both commands must pass and verification must report no problems before the demonstration reports completion.

The destination is retained on success and failure. An existing destination produces exit status 2. If a stage fails, the diagnostic names that stage.

#### JSON output

The document contains the resolved `root`, an ordered `steps` array, and the complete `comparison` object.

#### Exit status

| Status | Meaning |
| --- | --- |
| 0 | Every demonstration stage completed |
| 2 | The destination existed, a stage failed, or resulting state did not verify |

## Exit status summary

| Status | Commands | Meaning |
| --- | --- | --- |
| 0 | all | Requested operation completed; for `run`, the child passed |
| 1 | `run` | Child failed or was interrupted and terminal evidence was recorded |
| 2 | all | Invalid input, missing or invalid state, verification failure, or demo failure |
| 3 | `run` | Child could not launch and launch-failed evidence was recorded |

Argument parsing errors also use status 2.

## Security and execution boundary

`run` executes command tokens directly with `shell=False`. Ocura OSS does not sandbox commands, restrict network access, contain hostile code, or guarantee process-tree isolation.

Use owner-authorized workloads inside an execution environment with suitable permissions and isolation. See [state and verification](state-and-verification.md) and [Python API reference](python-api.md) for the corresponding programmatic contract.
