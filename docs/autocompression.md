# Automatic log compression

Version 0.7.0.dev0.

This draft branch prototypes lossless compression for captured stdout and stderr.
It uses Python's standard library and leaves the original execution records,
byte counts, SHA-256 digests, and retained manifests unchanged. It has not been
published as a package release. This development snapshot includes the prototype;
its rollout policy remains unsettled. Keep the pull request in draft
for future consideration until the decisions below are settled.

## Decisions before merge

- **Default policy:** this prototype compresses new eligible logs automatically.
  Decide whether the eventual feature should instead require an explicit opt-in.
- **Compatibility:** unchanged JSON and retained manifests do not make gzip
  backing readable by earlier versions. Settle how storage capabilities are
  declared before promising schema compatibility or a downgrade path.
- **Reading large logs:** `logs` and `open_verified_log()` use bounded snapshots,
  retaining only requested windows. Large windows need temporary disk space. Review
  that cost and interface; `read_verified_log()` still returns a complete in-memory
  byte string.
- **Read-only ledgers:** plain reads skip log locks on write-protected storage,
  and `--archive` reads without any locks or writes. Archive mode assumes stable
  storage with no writers or unfinished attempts. Review this contract before
  adopting it as the archive interface.
- **Minimal interface:** review the three added CLI commands, run flags, and
  `Store` methods. Result types remain in `ocura_oss.store`, with no new top-level
  exports. These draft names and shapes are not a release commitment.

The runtime remains standard-library only. Compression manages captured logs;
it does not manage workload checkpoints or impose a disk quota. The controls
below describe a runnable prototype, rather than a completed rollout policy.

## When compression runs

After a run has finalized its atom and chokepoint, it considers only that run's
captured logs, once. A log is eligible at **1,048,576 original bytes (1 MiB)** by
default. It is stored as gzip at compression level 6 only if doing so saves at
least **10%**. Smaller logs and logs that compress poorly stay raw. Stdout and
stderr are considered separately, regardless of the command's outcome.

There is no timer, background service, or scan of old runs on every new command.
This keeps the cost proportional to the new logs. Compression errors are reported
as storage warnings without replacing the command's actual outcome. The recorded
evidence remains available; `verify` still reports any damaged evidence it finds.

```console
ocura-oss run -- python train.py
ocura-oss run --compress-min-bytes 4194304 -- python train.py
ocura-oss run --no-compress -- python train.py
```

`--no-compress` applies to this run. `--compress-min-bytes N` takes a nonnegative
byte count; `0` considers every captured log, while still requiring the savings
threshold. `--no-capture` retains no logs and has nothing to compress. The Python
equivalents are `run(compress=False)` and `run(compression_min_bytes=N)`.

The current prototype changes storage for captured logs only. JSON records and
workload-owned artifacts, datasets, and checkpoints are outside its scope.

## Reading a compressed log

Use the atom ID returned by `run`, or listed by `chokepoints`:

```console
ocura-oss logs atom-<id>
ocura-oss logs atom-<id> --stream stderr
ocura-oss logs atom-<id> --info
ocura-oss logs atom-<id> --json
```

Without `--info` or `--json`, `logs` writes the exact verified original bytes to
stdout, with no extra newline or summary. It handles raw and compressed storage
transparently. The terminal or receiving tool decides how to display those bytes.
`Store(root).read_verified_log(atom_id, stream="stdout")` returns the same bytes
to Python; decoding and metric interpretation remain the caller's responsibility.

The complete selected stream is verified before any content is released. `logs`
and `Store.open_verified_log()` retain only the requested window: up to 1 MiB in
memory, or a larger snapshot in OS temporary storage. One pass decodes and verifies
the original stream. A short requested range avoids a complete decoded disk copy,
but still scans the entire stream for verification. The existing
`read_verified_log()` API returns the complete log in memory.

`logs ID --offset N --length N` reads original byte positions regardless of raw or
gzip backing. `--archive` reads stable storage without creating ledger locks; it
requires no concurrent writers and rejects unfinished attempts. Large snapshots
need writable temporary storage outside the archive. See
[stable archives](cli.md#stable-archives).

`--info` shows storage metadata, including the run's outcome and start time,
original and stored sizes, actual backing path, and full original SHA-256.
`--json` emits this metadata as JSON and never includes log contents or commands.
The atom's recorded `stdout_log` and `stderr_log` remain logical paths; after
compression, the physical file has an additional `.gz` suffix. Use `logs --info`
to see which file currently holds the evidence.

The metadata includes a readable fingerprint, in this form:

```text
stdout/<short-atom-id>/sha256:0123456789ab
```

The stream and short atom ID help identify the log, and the final part is a
prefix of its original SHA-256. A hash cannot tell you what a log says or recreate
the log. This descriptor helps you recognize and refer to a verified log; the
full 64-character digest remains the integrity check. The fingerprint stays the
same when the physical storage changes. A semantic summary would be additional,
lossy information and is not part of this prototype.

## Existing logs and reversible storage

Preview a pass over finalized logs, then apply it explicitly:

```console
ocura-oss compress --dry-run
ocura-oss compress
ocura-oss compress --run atom-<id> --min-bytes 262144 --json
ocura-oss decompress --dry-run
ocura-oss decompress
ocura-oss verify
```

`--run` limits either operation to one finalized run record. The legacy `--atom`
option remains accepted; supplying both requires them to resolve to the same run.
These commands accept
unique ID prefixes, `--root PATH`, and `--json`; Python `Store.compress_logs()`
and `Store.decompress_logs()` take full atom IDs. A dry run verifies the selected
evidence and calculates prospective changes without replacing stored files.
Compression is lossless, and decompression restores the original log bytes.
Each stream is converted independently. If a later stream fails, earlier
successful conversions remain in place; the pass is not a transaction over the
whole ledger. Rerunning it safely reuses the completed work.

Only finalized captured logs are eligible. Active attempts keep their raw files,
and compression does not discard failures or partial evidence. Local advisory
locks coordinate storage changes with readers and verification. Compressed data
is written to a temporary file and checked before replacing the raw storage.
Log files and their gzip counterparts must be regular files rather than symlinks.
Ordinary errors clean up temporary files. A forcibly killed converter may leave
`.ocura-log-*.tmp` files directly under `.ocura-oss/`; verification ignores these
incomplete conversion files and preserves the original evidence. They can occupy
space until separately inspected and removed.

## Space and compatibility limits

Compression reduces retained size when logs contain repeated data. It does not
set a hard disk budget: a running command's entire output is captured raw, and
conversion needs room for its temporary compressed copy alongside that raw file.
Decompression likewise needs space for restored bytes while compressed storage
still exists. Already compressed or high-entropy output may save nothing.

The prototype retains record **schema 2**, but adds a **physical storage
compatibility extension**. Earlier versions do not understand the `.log.gz`
backing files, so an unchanged schema number does not make compressed state
readable by them. Before opening this ledger with an older version, use this
branch's `ocura-oss decompress`, then `ocura-oss verify`. Keep `--no-compress` on
new runs if the ledger must remain readable by those older versions.

Retained manifests cover the original records and digests, so they remain valid
across compression and decompression. The hash still checks local consistency;
it does not authenticate who produced the log.
