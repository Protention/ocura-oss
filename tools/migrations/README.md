# Migrate a 0.4 ledger

`migrate_0_4.py` is an optional standalone tool for copying a complete Ocura OSS
0.4 `.ocura-oss/` ledger from schema 1 into schema 2. Python 3.11 or later is
required. It uses only the standard library; Ocura OSS does not need to be installed
to run it. The script, its tests, and its fixtures are repository-only and are
excluded from both pip distributions.

[Download the standalone script](migrate_0_4.py?raw=1) from a reviewed repository
revision, or run it from a checkout.
No account or network connection is needed when migrating. Recorded commands are
never executed. Stop all processes writing to the source ledger first.

## Check before copying

From this repository's root, with your original project in `../my-project`:

```console
python tools/migrations/migrate_0_4.py --source ../my-project --destination ../my-project-migrated --dry-run --json
```

The source and destination arguments are **project directories**, rather than
paths to `.ocura-oss/` itself. The destination must not exist, its parent must
exist, and the project directories must not overlap. `--dry-run` verifies the
input and reports the counts and source digest without creating any output.

The tool accepts a complete schema-1 ledger with the 0.4 record fields. It rejects
mixed formats, unknown fields or files, missing or corrupt logs, broken lineage,
duplicate JSON keys, links/junctions, and atoms without exactly one chokepoint.
Interrupted recording can leave unreferenced logs or incomplete records; those
need separate inspection, rather than conversion that invents missing evidence.
Existing schema-2 ledgers do not need this conversion and are rejected as input.

## Create the migrated copy

Run the same command without `--dry-run`:

```console
python tools/migrations/migrate_0_4.py --source ../my-project --destination ../my-project-migrated --json
```

The output is:

```text
my-project-migrated/
  .ocura-oss/            converted records, unchanged log bytes, empty attempts/
  migration-receipt.json
```

IDs, branch reasons and relationships, declared parameters, commands, outcomes,
timestamps, durations, and log bytes are retained. Every envelope changes to
schema 2 and receives a checksum for the new representation. Atoms additionally
state `output_capture: full` and `masked_arguments: []`: 0.4 captured both streams
and had no argument masking. No historical attempts or launch context are added.

The tool verifies the destination and checks that the source inventory has not
changed before writing a completion receipt. The receipt records the script digest,
original and converted file digests, corresponding record checksums, and the
conversion rules.
These support local consistency checking, not authentication or proof that a
command executed. Keep the original ledger and the receipt.

Validation failure leaves the original as it was. Copy failure or Ctrl+C removes
only the new output reserved by this invocation. If that directory was replaced
or gained unexpected files, it is retained for inspection. A hard kill or power
loss can leave an incomplete destination. Inspect a valid completion receipt and
verify the ledger before using it. A later run refuses an existing destination.

## Inspect and activate

With Ocura OSS 0.5 or a later release that reads schema 2 installed:

```console
ocura-oss verify --root ../my-project-migrated --json
ocura-oss compare --root ../my-project-migrated --json
```

The migrated project contains ledger evidence only. Workload code, datasets,
environments, and model checkpoints are managed separately. To use the converted
ledger in the original project, first verify the copy, stop all its writers, and
retain the original `.ocura-oss/` under a backup name. Then copy the converted
`.ocura-oss/` into that project. The script never performs this activation or
removes the original for you. To return to 0.4, use the original schema-1 ledger
with `ocura-oss==0.4.0`; 0.4 cannot read the converted copy.

Format conversion also does not restore an older release's execution defaults.
In particular, it preserves the parameters actually recorded by 0.4 instead of
retroactively filling them from pathway parameters.

## Development checks

```console
python -m unittest discover -s tools/migrations/tests
python -m mypy --strict tools/migrations/migrate_0_4.py
```

The frozen source fixture was written and verified by the actual 0.4.0 package.
Tests cover conversion, binary logs, all four old outcomes, standalone execution
with site packages disabled, rejection paths, failed-copy cleanup, receipts, and
reading/comparing/extending the output with the package in this checkout. Normal
package release checks reject migration files in either distribution.
