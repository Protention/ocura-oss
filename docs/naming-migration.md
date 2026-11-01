# Descriptive names and compatibility

Version 0.7.0.dev0.

This guide describes unreleased work on top of 0.6.0. Use a checkout containing
these changes; the published 0.6.0 package exposes the legacy names.

A **ledger** identifies the project-local state. A **run record** retains one
command attempt and its outcome. A **branch point** identifies terminal evidence
from which a new **pathway** can begin. A pathway groups related runs and records
the source and reason for a variation. It does not copy project files or rerun work.

## Python names

Use the descriptive names for new code. The legacy names remain supported aliases
and properties; this change does not migrate or rewrite existing state.

| Preferred interface | Compatible legacy interface |
| --- | --- |
| `Ledger` | `Den` |
| `RunRecord` | `Atom` |
| `BranchPoint` | `Chokepoint` |
| `initialize(...).ledger` | `.den` |
| `run(...).record` | `.atom` |
| `run(...).branch_point` | `.chokepoint` |
| `pathway.ledger_id` | `.den_id` |
| `pathway.source_branch_point_id` | `.source_chokepoint_id` |
| `branch_point.run_id` | `.atom_id` |
| `branch(source_branch_point_id=ID, ...)` | `branch(ID, ...)` or `branch(source_chokepoint_id=ID, ...)` |
| `compare(source_branch_point_id=ID, ...)` | `compare(ID, ...)` or `compare(source_chokepoint_id=ID, ...)` |
| `Store.load_ledger()` | `Store.load_den()` |
| `Store.load_run(run_id)` | `Store.load_atom(atom_id)` |
| `Store.load_branch_point(branch_point_id)` | `Store.load_chokepoint(chokepoint_id)` |
| `Store.list_run_records()` | `Store.list_atoms()` |
| `Store.list_branch_points()` | `Store.list_chokepoints()` |
| `Store.verify_run_evidence(record)` | `Store.verify_atom_evidence(atom)` |

`inspect_run`, `read_verified_log`, `open_verified_log`, and `log_info` accept
`run_id=ID` alongside the original positional argument and `atom_id=ID` keyword.
`compress_logs` and `decompress_logs` also accept `run_id`. Supplying both aliases
with the same value is accepted; conflicting values raise `StoreError` with code
`invalid_input`. Prefer one spelling per call. `Store.resolve_id` accepts the canonical
kinds `run` and `branch_point` as well as legacy `atom` and `chokepoint`.

The classes share the existing constructor field names. For example,
`BranchPoint` still accepts `atom_id`, and `Pathway` still accepts `den_id` and
`source_chokepoint_id`; their descriptive properties provide a read interface.
Dataclass serialization retains those constructor fields. Prefer the workflow
functions and public readers over constructing records to write state.

`Store.list_runs()` is the discovery operation: it returns finalized runs and
their branch points, supports filters, and reports unfinished work. It differs
from `list_run_records()`, which exposes all recorded run records in start order.
Use `list_runs(limit=None)` on settled history or a stable archive for complete
reconstruction. Live reads are observations, not an atomic snapshot.

The legacy public names are transitional compatibility aliases. No removal date
has been announced; a future removal would require a documented API transition.
Retiring stored kinds, prefixes, paths, or JSON fields would be a separate format
change with its own versioned migration. A Python naming change does not authorize
either change implicitly.

## CLI and stored data

| Preferred CLI spelling | Compatible legacy spelling |
| --- | --- |
| `ocura-oss branch-points` | `ocura-oss chokepoints` |
| `ocura-oss compress --run ID` | `ocura-oss compress --atom ID` |
| `ocura-oss decompress --run ID` | `ocura-oss decompress --atom ID` |

These aliases retain the existing success JSON shapes. Both run selectors accept
full IDs or unique prefixes. When `--run` and `--atom` are both supplied, equal
resolved IDs are accepted; different resolved IDs are rejected before mutation
with `invalid_input` under `--json-errors`.

Success JSON keys, record envelope kinds, schema version, IDs, directories, and
manifest entries keep their existing spellings. For example, a run result still
contains `atom_id` and `chokepoint_id`; an initialization result still contains
`den_id`; and a branch point ID still begins with `chokepoint-`. Keep those complete
IDs when passing data between commands. Do not rename files, edit prefixes, or
rewrite JSON to adopt the Python terminology.

`--json` retains its existing success and error behavior. The separate
`--json-errors` option opts into structured failure output and implies JSON
results. See the [CLI error contract](cli.md#structured-errors-unreleased) before
changing a caller's failure handling. Neither flag makes retries idempotent.

Physical gzip storage is a separate compatibility question. Unchanged record
names and schema do not make compressed logs readable by older versions; see
[storage compatibility](autocompression.md#space-and-compatibility-limits).
