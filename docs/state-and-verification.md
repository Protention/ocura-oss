# State and verification

Version 0.9.0.

Ocura OSS stores records and command output under `.ocura-oss/` in one project directory. It does not use a service or a global project index.

The descriptive names are **ledger**, **run record**, and **branch point**.
Their Python types are `Ledger`, `RunRecord`, and `BranchPoint`, and since 0.8 the
directory layout, record kinds, ID prefixes, JSON keys, and manifest entries below
use the same names. The [naming guide](naming-migration.md) maps the names used
before 0.8.

## Directory layout

```text
.ocura-oss/
  ledger.json
  settings.json
  index.sqlite
  pathways/
    <pathway-id>.json
  runs/
    <run-id>.json
  branch-points/
    <branch-point-id>.json
  attempts/
    <run-id>.json
    <run-id>.lock
  log-locks/
    <run-id>.stdout.log.lock
    <run-id>.stderr.log.lock
  logs/
    <run-id>.stdout.log
    <run-id>.stderr.log
    <run-id>.stdout.log.gz
    <run-id>.stderr.log.gz
```

`attempts/` holds one entry for each run that has started and is not yet finalized. It is empty when nothing is running and nothing was abandoned.

`settings.json` exists only after [`ocura-oss settings`](cli.md#settings) has stored a value. It is a plain JSON object, not a record: it has no envelope and is not part of verification or the manifest.

### The index

`index.sqlite` appears once a ledger holds 500 finished runs. It is a cache, not part
of the ledger: verification and the manifest ignore it, and deleting it loses nothing.

It records where each branch point falls in finish order, so `runs` and `show` open
only the records they return instead of every one. For example, `runs --limit 20` on
a ledger of 20,000 runs reads about 20 branch point records where a scan reads 20,000.
Every run those commands return is still read from its JSON record and checked, so a
wrong index cannot change what a run says.

Each row is tied to its record file's size and modification time. A record that is
new or changed is read again the next time `runs` or `show` is used; `run` itself
never touches the index. An index that is damaged, locked by another process, or
written by a later version is not used, and the command scans the records as it does
in a small ledger. The same happens under `--archive`, on a read-only project, and
when Python was built without SQLite.

`ocura-oss settings index=off` stops a project from keeping or using one. The file's
layout can change between releases, so read the ledger through the commands or the
Python API. Keep the file out of version control and out of copies you sign or
compare; earlier releases ignore it.

Each finalized captured stream normally has either its raw `.log` file or a
losslessly compressed `.log.gz` backing file. A conversion interrupted after
publication may leave both; readers verify both representations and prefer raw
storage, and a later conversion can finish removing the duplicate.
`log-locks/` holds local advisory locks that coordinate reads and conversion.
The run record continues to record the logical
`.log` path and the original size and SHA-256. `ocura-oss logs ID` and
`Store.read_verified_log()` transparently return the verified original bytes.
`logs ID --info` reports the current physical path and sizes. Logs stay raw
unless compression is requested; see [log compression](autocompression.md).

## Record envelope

Each JSON record contains an envelope:

```json
{
  "schema_version": 4,
  "kind": "run",
  "payload": {},
  "checksum": {
    "algorithm": "sha256",
    "value": "<64 lowercase hexadecimal characters>"
  }
}
```

The checksum covers the schema version, kind, and canonical payload. It provides local change detection. It is not an authenticated signature and does not establish authorship: anyone who can write the directory can replace a record and its checksum together. A [retained manifest](#retained-manifests) detects that.

## Format compatibility

This release writes schema version 4 and reads schemas 3 and 4. Version 0.8 wrote schema 3; see [ledgers written by 0.8](#ledgers-written-by-08). State written by Ocura OSS 0.5 to 0.7 carries schema 2, and state from 0.4 and earlier carries schema 1; both are rejected, see [upgrading from 0.7 and earlier](#upgrading-from-07-and-earlier).

Schema 4 differs from schema 3 in one place. A run record names its captured output in one `streams` object, where schema 3 used six separate fields:

```json
"streams": {
  "stdout": {"log": ".ocura-oss/logs/<run-id>.stdout.log", "bytes": 10, "sha256": "<digest>"},
  "stderr": {"log": ".ocura-oss/logs/<run-id>.stderr.log", "bytes": 0, "sha256": "<digest>"}
}
```

A run recorded without capture has an empty `streams`. Command output and the Python API are the same for both schemas: `show --json` and `RunRecord` keep `stdout_log`, `stdout_bytes`, `stdout_sha256` and the `stderr` equivalents.

From schema 4 on, a record changes in one of three ways:

- **An optional field is added.** A reader ignores payload fields it does not recognize, so the record stays readable by earlier releases. The field must be safe to ignore.
- **The record lists what it needs.** A record that an earlier release would misread carries a `requires` array in its payload naming each capability a reader must have. No record written by this release has one.
- **The schema version increases.** This is for a change that neither of the first two can express, such as a removed or renamed field.

The envelope's four keys are fixed, and every file a record refers to is named under `streams`.

The repository keeps a small ledger in the 0.8.0 layout as a test fixture. Every later release that reads schema 3 must verify it unchanged.

This describes how the format changes. It is not a commitment that schema 4 is final: the 0.x status in the [overview](index.md#project-status) still applies.

### Records from a later version

A record this version cannot read is named and left alone; it is not reported as damage. That is a record with a higher schema version, or one whose `requires` names something this version does not have.

| Command | What it does with such a record |
| --- | --- |
| `verify` | Checks its checksum, prints `newer record: FILE: WHAT IT NEEDS`, and still reports `integrity: ok`. Its logs are not reported as orphaned files, and their contents are not checked |
| `runs` | Leaves the run out and reports how many were left out |
| `show`, `logs`, `branch --from`, `run --from`, `compare` | Stop with exit status 2 and `needs a newer ocura-oss` when they need that record's contents |

A captured log stored in an encoding this version does not read, such as one a later version adds beside [gzip](autocompression.md#space-and-compatibility-limits), is handled the same way: `verify` names it and does not check its contents, and `logs` stops with `needs a newer ocura-oss`.

Runs branched from such a run are read, listed, and verified as usual. A record edited after it was written still fails its checksum, whatever its schema.

### Ledgers written by 0.8

This release reads a 0.8 ledger without changing it, and records into it in schema 3, the schema 0.8 wrote. Version 0.8 goes on reading, verifying, and recording into the same ledger, so a project can be used from both versions for as long as it is not upgraded. A manifest retained under 0.8 still verifies.

A new ledger created by `ocura-oss init` starts in schema 4.

`run`, `branch`, `recover`, and `verify` print a note while the ledger record is still schema 3:

```text
note: this ledger uses schema 3, which ocura-oss 0.8 also reads, and new records keep it. To upgrade it in place: `ocura-oss settings upgrade=overwrite` (for every project: set OCURA_OSS_UPGRADE=overwrite). To keep schema 3 and stop this note: `ocura-oss settings upgrade=off`
```

Staying on schema 3 is a supported way to use a project. `ocura-oss settings upgrade=off` stores that choice: the note stops, and the project keeps schema 3 even where `OCURA_OSS_UPGRADE=overwrite` is set.

To move the ledger to schema 4, opt in with either of these. A value stored for the project takes precedence over the environment variable.

```text
ocura-oss settings upgrade=overwrite     # this project, stored in .ocura-oss/settings.json
OCURA_OSS_UPGRADE=overwrite              # every project used from this environment
```

The next `run`, `branch`, or `recover` then rewrites each schema 3 record in schema 4 and reports how many it converted. From then on new records are written in schema 4. The upgrade happens as that command starts, so it is done even if the command then stops on a bad argument. Read commands and `--archive` never convert anything.

- Record IDs, log files, and what each record says are unchanged. `compare` gives the same result before and after.
- Each record is replaced in one step and the ledger record is converted last. An upgrade that is interrupted leaves a ledger that verifies, and the next recording command finishes it.
- While any process is still recording a run in schema 3, the ledger record is held back, and a later recording command converts what that process wrote.
- A manifest retained before the upgrade lists the old checksums and no longer matches. Save a new one with `ocura-oss manifest`.

**Upgrade a project only once every environment that uses it has this release.** Version 0.8.0 stops at any schema it does not know, and that cannot be changed in a release that is already installed:

| The ledger | What 0.8.0 does |
| --- | --- |
| Has not been upgraded, whichever version recorded into it | Works as before |
| Has been upgraded, or an upgrade has started | Every command stops with `unsupported schema version 4`, or `verify` reports the converted records as unsupported |

A record that needs schema 4 cannot be written into a schema 3 ledger. No feature in this release needs one; a later feature that does will ask for the upgrade first.

### Compressed-storage extension

Optional log compression changes only physical log storage; JSON records and the
schema version stay the same.

`run --compress` considers only that run's own finalized captured logs, once, at
1 MiB or larger, with gzip level 6 and a minimum 10% saving. A manual `compress`
pass handles existing finalized logs. Storage conversion preserves original
bytes, original hashes, record checksums, and retained manifests. It does not
compress active streams or enforce a disk quota, and needs room for temporary
storage alongside the source file. JSON records and workload artifacts are never
compressed.

### Upgrading from 0.7 and earlier

Version 0.8 renamed the stored names (see the [naming guide](naming-migration.md)), so versions 0.8 and later cannot read a `.ocura-oss/` directory written by 0.7 or earlier, and they do not convert one. Every command pointed at such a directory, including `verify`, stops with exit status 2 and a message naming the cause:

```text
error: unsupported schema version 2 in ledger record den.json; Ocura OSS 0.5 to 0.7 wrote this state, and this version cannot read or convert it. Keep using "ocura-oss<0.8" for it, or move .ocura-oss aside and run `ocura-oss init`
```

State from 0.4 and earlier gets the same message with schema version 1 and `"ocura-oss<0.5"`. The old directory is never modified. Decide per project before upgrading:

| You want to | Do this |
| --- | --- |
| Keep working with the existing records | Stay on the release line that wrote them: `python -m pip install "ocura-oss<0.8"` in its environment, or `"ocura-oss<0.5"` for 0.4 state. It reads and extends the directory as before |
| Start recording with a current version in the same project | Move the old directory aside, then run `ocura-oss init`. For example `mv .ocura-oss .ocura-oss-0.7`, or in PowerShell `Rename-Item .ocura-oss .ocura-oss-0.7` |
| Look at old records after upgrading | The logs under the moved directory's `logs/` are plain files, and the records are plain JSON. To use the old commands on them again, move the directory back to `.ocura-oss` in an environment that has the old version |

A new state directory starts empty. Identifiers from the old one are unknown to it, so a branch cannot name an old branch point as its source: record the baseline again before branching from it. If you need comparisons or listings from the old records later, save them with the old version first, for example `ocura-oss compare --json --from ID` and `ocura-oss branch-points --json`.

The optional [repository-only migration script](https://github.com/Protention/ocura-oss/blob/main/tools/migrations/README.md) converts complete 0.4 state to schema 2, for use with 0.5 to 0.7. There is no conversion to schema 3.

## Record relationships

- The ledger identifies one default pathway.
- Every pathway belongs to that ledger.
- A child pathway identifies both its parent and the source branch point on that parent.
- Every run record belongs to an existing pathway.
- Every run record has exactly one branch point.
- Every branch point belongs to the same pathway as its run record and has the same outcome.
- Record filenames match their payload IDs.

A branch records lineage metadata only. A later run must name the child pathway to attach evidence to it.

## Attempts and recovery

A run is journaled before its command launches, so an attempt stays visible even when the process recording it never gets to finish:

1. `run` takes an operating-system lock on `attempts/<run-id>.lock`, then writes `attempts/<run-id>.json`. That attempt record holds the pathway, the identifiers reserved for the run record and branch point, the start time, the recorded command, and the declared parameters.
2. The command runs.
3. `run` writes the run record, then the branch point, then removes the attempt record and releases the lock.

The operating system releases the lock when the recording process exits for any reason, including a forced kill. The recorder holds the lock exclusively; anything that only asks whether a recorder is alive takes it shared, so concurrent verifiers and listings never mistake one another for a recorder. An attempt record is therefore in one of two states:

| State | Meaning |
| --- | --- |
| `running` | A live process holds the attempt's lock and is still recording it |
| `abandoned` | No process holds the lock; the recorder stopped before finalizing the attempt |

`ocura-oss attempts` and `Store.list_attempts()` list unfinished attempts with their state. `ocura-oss recover` and `recover()` close each abandoned attempt according to how far its recorder got:

| The recorder had written | Action | Result |
| --- | --- | --- |
| Only the attempt record | `abandoned` | A run record with outcome `abandoned` and its terminal branch point |
| The run record | `completed` | The missing branch point, carrying the run record's recorded outcome |
| The run record and branch point | `cleared` | Nothing new; the leftover attempt record is removed |

An `abandoned` run record states what is known and nothing else. It keeps the start time, recorded command, and declared parameters, and has no finish time, duration, return code, or launch category. Whatever output had been captured is retained: recovery measures each log as it finds it and records that size and digest, so the logs verify from then on. An abandoned run record's branch point is branchable, like any other.

Running attempts are never touched by recovery. Recovery needs each abandoned attempt's lock to itself, and waits about a quarter of a second for a reader that is checking the same attempt. If a reader holds on longer than that, recovery leaves that attempt for the next call; verification keeps reporting it as abandoned in the meantime.

### When the command finishes

For captured runs, wait for child processes that inherit stdout or stderr before the command returns. If a child must keep running, launch it with both streams redirected elsewhere; that output is outside the captured logs. A command that finishes normally leaves any surviving child processes running.

With streamed CLI output, an inherited output stream left open by a child can keep the recorder waiting after the command exits. With `--quiet` or `--json`, the recorder can instead finalize while a child still holds the log files open. If that child writes afterward, later verification reports the log as changed. These modes do not remove the need to finish captured output before the command returns.

### When the recorder stops

`ocura-oss run` treats a request to stop it as it treats the first Ctrl+C: it stops the command, records the run as `interrupted`, and exits with status 1. The requests are `SIGTERM` and `SIGHUP` on Linux and macOS, and Ctrl+Break on Windows. A signal the caller already ignores stays ignored, so a run started under `nohup` survives its terminal. On Windows, stopping the command also ends the processes it started; on Linux and macOS only the command itself is signaled.

The Python `run()` function installs no signal handlers. It records an interruption when `KeyboardInterrupt` is raised in the calling thread, so a program that wants the same behavior can set `signal.default_int_handler` for those signals.

A recorder that is killed outright records nothing, and its attempt becomes `abandoned`. What happens to the command depends on the operating system:

| System | The command when its recorder is killed |
| --- | --- |
| Windows | The operating system ends it, together with the processes it started |
| Linux | The kernel kills it; processes it started keep running |
| macOS | It keeps running |

On Linux this applies when the recording process has a single thread as it launches the command, which is always true of `ocura-oss run`.

These operating-system mechanisms are best effort: a command still runs when one is unavailable. On Linux the kernel request is also skipped in hosts that cannot use a pre-exec hook, such as Python subinterpreters.

Limits:

- Where a command or a process it started outlives a killed recorder, it can keep writing to its logs. Recover only after it has stopped; otherwise later verification reports its log as changed.
- The locks are advisory operating-system file locks. They are reliable on local filesystems. On a network filesystem, a running attempt may be reported as abandoned or the reverse. Where the filesystem refuses file locks, `run`, `recover`, `compress`, and `decompress` stop with an error, and reads and `verify` continue without locks.

## Concurrent use

Several processes may record runs under one state root at the same time. Each run writes only files named with its own freshly generated identifiers, every record appears atomically, and nothing written by one run is rewritten by another.

`verify` and `compare` may run while other runs are in flight. A running attempt is reported as in progress and is not a problem; its logs are not checked until it is finalized.

Ocura OSS does not coordinate anything outside its own records:

- Concurrent commands share the project root as their working directory. Keeping their workspace files apart is the workload's job.
- Listings order records by recorded timestamp and then identifier. Runs that overlap in time have no other ordering.
- `compare` without an explicit source selects the newest branched source at the moment it runs. Pass the source branch point when other work may be adding branches.
- Of several processes initializing the same root at once, exactly one succeeds.

## Verification

`verify()` and `Store.verify_state()` check:

- envelope shape, record kind, and schema version
- canonical payload checksum
- identifier and filename agreement
- required fields and semantic outcome invariants
- ledger, pathway, run record, and branch point references
- lineage cycles and source-parent agreement
- that every run record has exactly one branch point
- referenced log containment, existence, size, and SHA-256 digest
- unreferenced files or unexpected directories under `logs/`
- unfinished attempts: a running attempt is listed, and an abandoned attempt is a problem until it is recovered

The report counts records that were structurally readable and logs that passed evidence verification. Problems identify the affected record or log.

Automatic source selection for `compare()` requires the complete state to verify. Supplying a source branch point ID performs targeted verification of that source and the child evidence included in its comparison.

Verification establishes consistency among local records and logs. It does not capture or prove the external conditions needed to reproduce a command.

### Retained manifests

When the state is intact, verification also produces a manifest and a digest:

- The manifest has one `KIND ID CHECKSUM` line for every verified record, sorted. `ocura-oss manifest` prints it, and `StateVerification.manifest` holds it.
- The digest is the SHA-256 of those lines, each terminated by a newline. `verify` reports it, and `StateVerification.digest` holds it.

A run record's checksum covers its recorded log sizes and digests, and verification has just matched the logs to them, so a manifest line for a run record also pins that run's output.

Checksums stored inside `.ocura-oss/` cannot show that a record was rewritten by someone able to write there. A manifest kept elsewhere can. Save it where this state's writers cannot change it, such as a commit in another repository or a build artifact, then pass it back:

```console
ocura-oss manifest > ../trusted/experiment.manifest
ocura-oss verify --against ../trusted/experiment.manifest
```

Every retained entry must still be present with the same checksum. Records added since are allowed, so a manifest stays useful while work continues. A manifest that lists no records is rejected rather than treated as a pass: it would check nothing, and an empty file is what a failed export leaves behind. A rewritten record, a replaced log, or a removed run is reported as a retained record that is missing or was changed.

The protection is exactly as strong as the place the manifest is kept. A manifest stored beside the ledger can be replaced along with it. The digest alone identifies one exact set of records; it changes with every new run, so use it to confirm that two copies of a finished ledger match, and use the manifest to check a ledger that is still growing.

## Reading verified output

`Store.read_verified_log(run_id, stream="stdout")` loads and validates the stored
run record and its lineage, checks the selected log's containment, and verifies the exact
bytes it returns against the run record's recorded byte count and SHA-256 digest. Pass
`stream="stderr"` for the other stream. Both streams can be read for failed attempts.

The complete selected log is held in memory and returned as bytes. Decoding and
metric interpretation belong to the caller. Unlike resolving a path and reading it
later, this method verifies the same bytes your code receives. Raw and gzip
storage both return the original bytes; `ocura-oss logs ID` exposes this read at
the CLI, while `logs ID --info` returns verified storage metadata.

The read is scoped to the selected stream: it does not verify the other stream,
unrelated records, or the whole ledger. Continue to use `verify()` or
`Store.verify_state()` for a complete check. Local advisory locks coordinate
readers with compression and decompression, without providing an atomic snapshot
of the ledger; returned bytes remain unchanged if the file changes afterward.
A run recorded without output capture has no log, and the read raises `StoreError`.
See the [Python API](python-api.md#storeread_verified_log) for usage and errors.

## Stored information

Run records retain command arguments, outcomes, timing, return codes, declared parameters, log paths, byte counts, and log digests. Logs retain command output. Pathways retain branch reasons and effective declared parameters. An unfinished attempt retains the same command arguments and declared parameters as the run record it becomes.

Environment values are inherited by the child process but are not serialized. Dependency versions, workspace contents, process memory, network activity, and external-system state are not recorded. The source revision is recorded only when [launch context](#optional-launch-context) is requested.

### Optional launch context

A run records nothing about its surroundings unless asked. Two options add a `context` object to the attempt and the run record:

| Option | Records |
| --- | --- |
| `run --context`, or `context=True` | `platform`: the operating system name, release, and machine type. `git`: the checked-out revision and whether the work tree differs from it, or null when the project root is not inside a work tree with at least one commit or git is unavailable |
| `run --context-file PATH`, or `context_files=[...]` | `files`: the size and SHA-256 of each named file, read just before launch. A relative path is resolved against the project root. A file that cannot be read stops the run before the command launches |

The work tree is `dirty` when it has modified or untracked files other than `.ocura-oss/` itself. Untracked files and changes inside submodules count whatever the user's or repository's git settings say about showing them. Files matched by ignore rules do not count. Reading git state runs `git` twice in the project root; that is the only extra process, and only with `--context`.

Context is a description, not a guarantee:

- It records the moment of launch. Verification does not recheck it, and a named file may change afterward.
- It does not record installed package versions. Those belong to whichever interpreter the command uses, which need not be the one running Ocura OSS. Name a lock file with `--context-file` to pin them.
- A revision and file digests narrow down what ran. They do not make a command reproducible or restore a workspace.

The `context` field is absent from a record written without these options.

### Keeping sensitive values out of records

Two options limit what a run stores. Both are stated in the record, so a reader can tell an omission from an absence:

| Option | Effect | Recorded as |
| --- | --- | --- |
| `run --no-capture`, or `capture=False` | No stdout or stderr is written to disk | `output_capture` is `none`, and the six log fields are null |
| `run --mask-arg POSITION`, or `masked_arguments=[...]` | The command token at that zero-based position is stored as `<masked>` in the attempt and the run record; the command still receives the real value | `masked_arguments` lists the masked positions |

`output_capture` is `full` for an ordinary run. A command token that happens to equal `<masked>` is not listed in `masked_arguments`, so it is not mistaken for a masked one.

These options are explicit, and nothing is detected automatically:

- Masking covers command tokens in records. It does not alter output: a command that prints a masked value still writes it to a captured log. Combine it with `--no-capture` when that matters.
- Declared parameters and branch reasons are stored as given. Keep secrets out of them. That includes any parameter passed to the command with `--substitute`: its value is recorded as a label even when its command token is masked.
- A masked value remains visible to the operating system as an argument of the running process.

## Execution boundary

Commands run directly on the local machine with `shell=False` and the project root as the working directory. Ocura OSS does not sandbox commands, restrict network access, or isolate process trees. Use it only for trusted, same-owner local workloads.
