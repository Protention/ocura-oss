# Names before 0.8

Version 0.8.0.

A **ledger** identifies the project-local state. A **run record** retains one
command attempt and its outcome. A **branch point** identifies terminal evidence
from which a new **pathway** can begin. A pathway groups related runs and records
the source and reason for a variation. It does not copy project files or rerun work.

Releases before 0.8 stored these as `den`, `atom`, and `chokepoint`. Version 0.7
added the descriptive names to Python and the CLI as aliases; 0.8 uses them
everywhere, including stored state, and removes the old names. This page maps each
old name to its replacement for updating scripts and callers.

## Stored data and JSON

| Before 0.8 | 0.8 and later |
| --- | --- |
| `.ocura-oss/den.json` | `.ocura-oss/ledger.json` |
| `.ocura-oss/atoms/` | `.ocura-oss/runs/` |
| `.ocura-oss/chokepoints/` | `.ocura-oss/branch-points/` |
| `den-<hex>`, `atom-<hex>`, `chokepoint-<hex>` | `ledger-<hex>`, `run-<hex>`, `branch-point-<hex>` |
| logs `atom-<hex>.stdout.log` | logs `run-<hex>.stdout.log` |
| record and manifest kinds `den`, `atom`, `chokepoint` | `ledger`, `run`, `branch_point` |
| JSON keys `den_id`, `atom_id`, `chokepoint_id`, `source_chokepoint_id` | `ledger_id`, `run_id`, `branch_point_id`, `source_branch_point_id` |
| `verify --json` keys `den`, `counts.atoms`, `counts.chokepoints` | `ledger`, `counts.runs`, `counts.branch_points` |
| `chokepoints --json` key `chokepoints` | `branch-points --json` key `branch_points` |
| `show --json` keys `atom`, `chokepoint` | `run`, `branch_point` |
| schema version 2 | schema version 3 |

Pathway IDs, the `pathways/`, `attempts/`, and `logs/` directories, and all other
keys are unchanged. Version 0.8 does not read or convert state from 0.7 or
earlier; see [upgrading from earlier versions](state-and-verification.md#upgrading-from-earlier-versions).

## CLI

| Before 0.8 | 0.8 and later |
| --- | --- |
| `ocura-oss chokepoints` | `ocura-oss branch-points` |
| `compress --atom ID`, `decompress --atom ID` | `compress --run ID`, `decompress --run ID` |

## Python

| Before 0.8 | 0.8 and later |
| --- | --- |
| `Den`, `Atom`, `Chokepoint` | `Ledger`, `RunRecord`, `BranchPoint` |
| `initialize(...).den` | `.ledger` |
| `run(...).atom`, `run(...).chokepoint` | `.record`, `.branch_point` |
| `den_id`, `atom_id`, `chokepoint_id`, `source_chokepoint_id` fields and keywords | `ledger_id`, `run_id`, `branch_point_id`, `source_branch_point_id` |
| `Store.load_den()`, `load_atom()`, `load_chokepoint()` | `load_ledger()`, `load_run()`, `load_branch_point()` |
| `Store.list_atoms()`, `list_chokepoints()` | `list_run_records()`, `list_branch_points()` |
| `Store.verify_atom_evidence()` | `verify_run_evidence()` |
| `StateVerification.atoms`, `.chokepoints` | `.run_records`, `.branch_points` |
| `RunInspection.atom`, `.chokepoint` | `.record`, `.branch_point` |
| `Store.resolve_id(value, "atom")`, `"chokepoint"` | `"run"`, `"branch_point"` |

Constructor fields, dataclass fields, and keyword arguments use the new names only.
Code already written against 0.7's descriptive names needs no change beyond reading
the new IDs, paths, and JSON keys.
