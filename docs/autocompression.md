# Log compression

Version 0.7.0.dev0.

Ocura OSS can store captured stdout and stderr losslessly as gzip. Compression is
opt-in: logs stay raw unless a run or an explicit `compress` pass asks for it.
Records, IDs, byte counts, SHA-256 digests, and retained manifests stay unchanged,
and every Ocura OSS reader returns the original bytes. The runtime remains
standard-library only. Compression manages captured logs; it does not manage
workload checkpoints or impose a disk quota.

Raw is the default because raw logs stay readable at their recorded paths by
standard tools and by Ocura OSS 0.6, and because compressing a large log adds time
before `run` returns. Turn compression on when disk space matters more.

## Compressing a run's logs

`run --compress` considers that run's captured logs once, after its run record
and branch point are final. A log is eligible at **1,048,576 original bytes
(1 MiB)** by default. It is stored as gzip at compression level 6 only if doing so
saves at least **10%**. Smaller logs and logs that compress poorly stay raw.
Stdout and stderr are considered separately, regardless of the command's outcome.

```console
ocura-oss run --compress -- python train.py
ocura-oss run --compress-min-bytes 4194304 -- python train.py
```

`--compress-min-bytes N` sets the threshold and implies `--compress`. It takes a
nonnegative byte count; `0` considers every captured log, while still requiring
the savings threshold. `--no-capture` retains no logs and has nothing to compress.
The Python equivalent is `run(compress=True, compression_min_bytes=N)`.

There is no timer, background service, or scan of old runs. The work happens
before `run` returns and grows with the log's size; high-entropy output costs the
most and may then stay raw. Compression errors are reported as storage warnings
without replacing the command's actual outcome. JSON records and workload-owned
artifacts, datasets, and checkpoints are never compressed.

## Reading a compressed log

Use the run ID returned by `run`, or listed by `runs`:

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

`run` and `show` print each log's current file, and their JSON reports it as
`log_files`: the recorded path, plus `.gz` when stored as gzip. That file is
ordinary gzip, so standard tools such as `zcat`, `zgrep`, `zless`, and `gzip -dc`
read it directly, and `rg -z PATTERN .ocura-oss/logs` searches raw and compressed
logs together. These tools do not check the recorded SHA-256; `ocura-oss logs` and
`verify` do. Windows PowerShell has no built-in gzip reader, so use
`ocura-oss logs` there.

The metadata includes a readable fingerprint, in this form:

```text
stdout/<short-atom-id>/sha256:0123456789ab
```

The stream and short atom ID help identify the log, and the final part is a
prefix of its original SHA-256. A hash cannot tell you what a log says or recreate
the log. This descriptor helps you recognize and refer to a verified log; the
full 64-character digest remains the integrity check. The fingerprint stays the
same when the physical storage changes. A semantic summary would be additional,
lossy information and is not provided.

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

Records keep **schema 2**, but gzip backing is a **physical storage
extension**. Ocura OSS 0.6 and earlier do not understand `.log.gz` files: their
`verify` reports the log as missing and the `.gz` file as orphaned, and they
refuse to branch from or compare those runs. Before opening a ledger with an
older version, run `ocura-oss decompress`, then `ocura-oss verify`. A ledger that
never uses compression stays readable by them.

Retained manifests cover the original records and digests, so they remain valid
across compression and decompression. The hash still checks local consistency;
it does not authenticate who produced the log.
