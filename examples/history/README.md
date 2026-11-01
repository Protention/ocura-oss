# Record and inspect a small history

This repository-only example uses the unreleased discovery and naming interfaces.
Install this checkout with `python -m pip install .` before running it. It needs no
optional dependencies and is excluded from the package distributions.

From the checkout root, create a small demonstration and inspect it:

```console
ocura-oss demo --root ./ocura-oss-demo
python examples/history/inspect_history.py --root ./ocura-oss-demo --archive
```

The demo records a baseline and a variation in a new directory. Its destination
must not already exist. Keep that directory: the second command reads its saved
records and verified output without running the workload again. If you already
ran the demo, use only the second command. Archive mode requires a stable ledger
with no writers or unfinished attempts.

This is the core repository's primary example. Fuller development, training, and
replay demonstrations live on [`examples/0.7`](https://github.com/Protention/ocura-oss/tree/examples/0.7);
see the [use-case catalog](../../docs/use-cases.md).

## Inspect your own ledger

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

Failures keep the existing `error` string and add an `error_details` object with
`code`, `message`, `execution`, `ids`, and `recovery`. This applies both to an
unavailable stream within a report and to a failure that prevents the report.
Use these fields with the [CLI error contract](../../docs/cli.md#structured-errors-unreleased)
instead of parsing English messages. Known ledger, run, branch point, and pathway
IDs are retained when available; source error diagnostics take precedence.
Raw operating-system errors are classified by type and error number. Invalid
excerpt bounds produce `invalid_input`; parser errors retain normal argparse behavior.

The report requests no workload execution, so ordinary read failures have
`execution: "not_applicable"`. Source error execution states and explicit recovery
hints remain intact. A recovery hint is guidance for the caller, not an action
the example performs. Success reports and exit statuses are unchanged.

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
