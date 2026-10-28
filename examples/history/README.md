# Inspect an existing history

This repository-only example uses the unreleased discovery and naming interfaces.
Install this checkout with `python -m pip install .` before running it. It needs no
optional dependencies and is excluded from the package distributions.

Given only an existing project root, a fresh caller can inspect its history:

```console
python examples/history/inspect_history.py --root ./experiment
```

The JSON report includes every observed pathway, including variations with no run,
and every finalized run observed by the scan, including failed retries. Parent
pathway IDs, source branch point IDs, reasons, and parameter labels preserve the relationships. It does not
guess which run is the baseline or choose a best result. Select a branch point from
the report explicitly when calling `compare --from ID` or `branch --from ID` later.

Each captured stdout/stderr stream is verified in full while retaining only a
bounded excerpt, 256 bytes by default. Set `--snippet-bytes 1024` for a larger
excerpt. References contain the ledger ID, run ID, stream, original SHA-256, byte
offset, and returned length. These identify original bytes across raw/gzip storage;
base64 preserves exact bytes and the UTF-8 preview can contain replacement characters.
The report states its verification scope. It does not perform whole-ledger verification
or interpret workload metrics. Missing or inconsistent streams remain visible and
make the command exit 2; they are never replaced with a successful result.

Live reads are observations, not an atomic snapshot. Unfinished attempts stay
visible, with guidance to inspect them and wait or recover as appropriate. An
attempt seen before discovery may have finalized by the end of the report. The
script never executes, resubmits, or recovers a command. Ordinary reads may create
coordination lock files; for a stable copy with no writers or unfinished attempts:

```console
python examples/history/inspect_history.py --root ./settled-copy --archive
```

Archive mode creates no ledger files. This example keeps selected excerpts bounded
in memory. A new run, retry, or code change remains the caller's decision.
