# Python API reference

Version 0.7.0.dev0.

This draft builds on 0.6.0. Opt-in compression, storage metadata, and conversion
methods below are absent from the released package. See
[log compression](autocompression.md).

Descriptive names, discovery, structured diagnostics, and creating and running a
variation in one call are also unreleased.
New code uses `Ledger`, `RunRecord`, and `BranchPoint`, with `Pathway` unchanged.
`Den`, `Atom`, and `Chokepoint` remain class aliases. Constructor field names and
JSON serialization retain their existing spellings; descriptive properties are
listed below. See the [complete naming map](naming-migration.md) for compatibility
and transition policy.

The `ocura_oss` package exposes typed workflow functions, read-oriented state access, frozen result and record types, enums, and public exceptions.

Names listed in `ocura_oss.__all__` form the documented top-level API. Internal record writers and implementation helpers remain package internals.

## API status

The Python API is provisional during the 0.x series. Minor releases may refine signatures, result fields, and record semantics before 1.0.

Ocura OSS supports CPython 3.11 through 3.14 and includes a `py.typed` marker. The runtime uses only the Python standard library.

Optional root parameters accept a path-like object, a string, or `None`. `None` resolves to the current working directory. Relative paths and `~` are resolved without changing the process working directory.

File operations can propagate `OSError` when a rejected read, write, replacement, or directory operation remains an operating-system error. `run_demo()` wraps stage failures in `DemoError`.

A crafted pathway lineage that exceeds Python's recursion limit can propagate `RecursionError` from programmatic loading, verification, branching, or comparison. The CLI converts that condition to exit status 2.

Declared parameter mappings require nonempty string values. Keys must begin with a letter or underscore and may then contain letters, digits, underscores, periods, or hyphens. Values are stored without type conversion.

Identifier parameters take full identifiers. The shortened identifiers that the CLI accepts are expanded by [`Store.resolve_id`](#storeresolve_id).

Use these operations from a script or an AI agent inside its existing execution environment. The [automation guide](automation.md) describes caller responsibilities; the [training example](https://github.com/Protention/ocura-oss/blob/examples/0.7/examples/autoregressive/README.md) on the separate `examples/0.7` branch supplies PyTorch/JAX workloads with an optional local Ray executor.

## Workflow example

```python
import sys
from pathlib import Path

from ocura_oss import ComparisonState, branch, compare, initialize, run, verify

root = Path("experiment")
initial = initialize(root, name="batch study")

baseline = run(
    [sys.executable, "-c", "print('baseline')"],
    root=root,
    pathway_id=initial.pathway.id,
    parameters={"batch": "1"},
)

child = branch(
    baseline.branch_point.id,
    root=root,
    reason="increase batch",
    parameters={"batch": "4"},
)

run(
    [sys.executable, "-c", "print('child')"],
    root=root,
    pathway_id=child.id,
)

comparison = compare(source_branch_point_id=baseline.branch_point.id, root=root)

assert comparison.state is ComparisonState.READY
assert verify(root).ok
```

To create and execute the variation in one call, replace the `branch()` and child
`run()` calls above with:

```python
variation = run(
    [sys.executable, "-c", "print('child')"],
    root=root,
    source_branch_point_id=baseline.branch_point.id,
    reason="increase batch",
    parameters={"batch": "4"},
)
```

This returns the same `RunExecution`, with the new child ID in
`variation.record.pathway_id`. Keep the two-step form when planning a variation
before its command is ready. Neither form copies workspace files or process state.

## Workflow functions

### `ocura_oss.initialize`

```python
initialize(
    root: os.PathLike[str] | str | None = None,
    *,
    name: str = "ocura-oss",
) -> Initialization
```

Create one ledger and one default pathway under a project root.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `root` | path-like, string, or None | `None` | Project root that will contain `.ocura-oss/`; `None` selects the current working directory |
| `name` | string | `ocura-oss` | Nonblank name stored in the ledger record |

#### Returns

`Initialization` containing the resolved root, created `.ledger`, and default pathway.
The `.den` property spelling remains supported.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | `.ocura-oss/` already exists, `name` is blank or invalid, or state cannot be written |

#### Notes

Initialization fails if `.ocura-oss/` already exists, including when the directory contains incomplete state. All created state remains under the selected project root.

#### Example

```python
from ocura_oss import initialize

initial = initialize("experiment", name="optimizer study")
print(initial.pathway.id)
```

### `ocura_oss.run`

```python
run(
    command: Sequence[str],
    *,
    root: os.PathLike[str] | str | None = None,
    pathway_id: str | None = None,
    source_branch_point_id: str | None = None,
    reason: str | None = None,
    parameters: Mapping[str, str] | None = None,
    mirror: bool = False,
    capture: bool = True,
    compress: bool = False,
    compression_min_bytes: int = 1_048_576,
    masked_arguments: Iterable[int] = (),
    substitute: bool = False,
    context: bool = False,
    context_files: Iterable[os.PathLike[str] | str] = (),
) -> RunExecution
```

Run one trusted local command and record terminal evidence.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `command` | sequence of strings | required | Executable followed by its argument tokens; a plain string or bytes object is rejected |
| `root` | path-like, string, or None | `None` | Project root containing initialized state |
| `pathway_id` | string or None | `None` | Existing pathway that receives the run; cannot be combined with `source_branch_point_id`. When both are `None`, use the ledger's default pathway |
| `source_branch_point_id` | string or None | `None` | Full ID of a source to verify, create a child pathway from, and run on; requires `reason` |
| `reason` | string or None | `None` | Nonblank explanation stored on the new child pathway; requires `source_branch_point_id` |
| `parameters` | mapping of string to string, or None | `None` | Add or replace labels for this run; with `source_branch_point_id`, set the new child's parameter overrides instead. Labels do not configure the child process |
| `mirror` | boolean | `False` | Stream stdout and stderr to the current terminal while retaining the same bytes in logs |
| `capture` | boolean | `True` | Retain stdout and stderr as logs; `False` writes no output to disk and records that it was not captured |
| `compress` | boolean | `False` | Consider only this run's finalized captured logs once for lossless gzip storage |
| `compression_min_bytes` | nonnegative integer | `1048576` | Minimum original size to compress when `compress` is true; require at least 10% savings as well |
| `masked_arguments` | iterable of integers | `()` | Zero-based positions in `command` whose tokens are stored as `<masked>`; the command still receives the real values |
| `substitute` | boolean | `False` | Replace `{KEY}` in command tokens with the value the run records for that key |
| `context` | boolean | `False` | Record the platform and the git revision and dirty state at launch |
| `context_files` | iterable of paths | `()` | Files whose size and SHA-256 are recorded at launch; relative paths are resolved against the project root |

#### Returns

`RunExecution` exposing the recorded `RunRecord` as `.record`, terminal
`BranchPoint` as `.branch_point`, and
`storage_warnings`, a tuple of post-finalization compression errors (empty on
success). Compression warnings do not replace the command's actual outcome.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Command tokens, source/reason selection, masked positions, placeholders, parameter data, state, pathway, source evidence, or evidence persistence is invalid, or a context file cannot be read |

A `StoreError` for invalid parameter data or an operating-system failure is also a
`ValidationError` or `OSError`, the type `run()` raised for it before.

Child command failures and launch failures are returned as run record outcomes rather than raised as exceptions.

#### Outcome mapping

| Outcome | `return_code` | `launch_error_category` | Meaning |
| --- | --- | --- | --- |
| `Outcome.PASSED` | `0` | `None` | Process launched and returned zero |
| `Outcome.FAILED` | nonzero integer | `None` | Process launched and returned nonzero |
| `Outcome.LAUNCH_FAILED` | `None` | category string | Process could not be launched |
| `Outcome.INTERRUPTED` | `None` | `interrupted` | First Ctrl+C interrupted the attempt and partial output was retained |

Launch categories include `executable_not_found`, `permission_denied`, `not_a_directory`, `invalid_argument`, and `os_error`.

`run()` never returns `Outcome.ABANDONED`. That outcome is written by [`recover()`](#ocura_ossrecover) for an attempt whose recording process stopped first.

#### Notes

With `source_branch_point_id`, a nonblank `reason` is required and `pathway_id`
must be omitted. The source is verified with the same checks as `branch()` before
creating the child. `parameters` overrides the source pathway's effective
parameters on that new child; labels declared only on the source run are not
inherited. The child's labels persist for later runs. Without `source_branch_point_id`,
`parameters` continues to affect only this run's record.

The child is created only after the run's input is checked. A later storage or
execution failure can leave the child pathway in place, even with no run recorded. `StoreError.ids` retains the known child `pathway_id` and
`source_branch_point_id`, alongside any known attempt or run IDs. Inspect
`execution` and the retained evidence before deciding whether to continue with
`run(..., pathway_id=child_id)`. `not_started` describes the workload and does not
imply that no child was created. Repeating the combined call creates another
child; no automatic retry occurs. See [recovery guidance](automation.md#handle-uncertain-execution-without-duplicate-work).

The child runs with `shell=False` and the project root as its working directory. It inherits the invoking process environment, while the inherited environment remains outside recorded state.

Stdout and stderr are stored separately under `.ocura-oss/logs/`. With `capture=False`, no log files are written: the returned run record has `output_capture` set to `OutputCapture.NONE` and `None` in its six log fields, and `mirror=True` still streams the output.

Logs stay raw by default. With `compress=True`, each captured log of at least
`compression_min_bytes` (1 MiB) is considered for gzip compression at level 6
after this run is finalized. It must save at least 10%; smaller or incompressible
logs remain raw. Original bytes and hashes remain unchanged, and no older logs are
scanned. See [log compression](autocompression.md) for storage compatibility and
temporary disk space requirements.

Each position in `masked_arguments` must index `command`; position 0 is the executable. The attempt and run records store `<masked>` at those positions and list them. Masking does not alter output, so a command that prints a masked value still writes it to a captured log.

The returned run record's `declared_parameters` are the pathway's effective parameters with `parameters` laid over them, so a run on a branch carries the branch's labels without repeating them. With `substitute=True`, placeholders take their values from those labels, and the run record's `command` is the substituted command. `{{` and `}}` are literal braces; a placeholder without a value, or an unbalanced brace, raises `StoreError` before anything runs. Braces are untouched when `substitute` is false.

`context` and `context_files` fill the returned run record's `context` with a `RunContext`; it is `None` when neither is used. Nothing is queried, executed, or read for context otherwise. See [optional launch context](state-and-verification.md#optional-launch-context) for what is recorded and what it does not establish.

The attempt is journaled under `.ocura-oss/attempts/` before the command launches. One run record and one branchable terminal branch point are written after the command ends, and the journal entry is then removed. A second Ctrl+C, or a killed process, leaves the entry in place; `recover()` closes it. `run()` installs no signal handlers, and what happens to the command when the calling process is killed depends on the operating system; see [when the recorder stops](state-and-verification.md#when-the-recorder-stops). Several processes may call `run()` against one root at the same time.

The host machine, network, and process tree form the execution context. The trust model is trusted, same-owner local work.

#### Example

```python
import sys

from ocura_oss import Outcome, run

execution = run(
    [sys.executable, "-c", "print('recorded')"],
    root="experiment",
    parameters={"batch": "1"},
    mirror=True,
)

assert execution.record.outcome is Outcome.PASSED
```

### `ocura_oss.branch`

```python
branch(
    source_chokepoint_id: str | None = None,
    *,
    source_branch_point_id: str | None = None,
    reason: str,
    root: os.PathLike[str] | str | None = None,
    parameters: Mapping[str, str] | None = None,
) -> Pathway
```

Create a metadata-only child pathway from verified terminal evidence.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `source_branch_point_id` | string or None | `None` | Preferred keyword for a terminal, branchable source; supply this or the legacy argument |
| `source_chokepoint_id` | string or None | `None` | Compatible positional argument or legacy keyword; conflicting aliases are rejected |
| `reason` | string | required | Nonblank branch reason stored on the child pathway |
| `root` | path-like, string, or None | `None` | Project root containing the source evidence |
| `parameters` | mapping of string to string, or None | `None` | Overrides applied to the parent pathway's effective parameters |

#### Returns

The created child `Pathway`. It has no run record until a later `run()` explicitly names its ID.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | State or input is invalid; the source is missing, nonterminal, nonbranchable, or unverifiable; or the child cannot be written |

#### Notes

Source verification covers the branch point, run record, pathway, their relationships, and both referenced logs before the write occurs.

Leading and trailing whitespace is removed from `reason` before storage. The resulting pathway contains lineage metadata only: parent, source branch point, reason, creation time, and effective parameters.

#### Example

```python
from ocura_oss import branch

child = branch(
    "chokepoint-<id>",
    root="experiment",
    reason="increase batch",
    parameters={"batch": "4"},
)
```

### `ocura_oss.compare`

```python
compare(
    source_chokepoint_id: str | None = None,
    *,
    source_branch_point_id: str | None = None,
    root: os.PathLike[str] | str | None = None,
) -> ComparisonResult
```

Compare a verified source run with the newest run on each child pathway created from its branch point.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `source_branch_point_id` | string or None | `None` | Preferred source keyword; conflicting aliases are rejected |
| `source_chokepoint_id` | string or None | `None` | Compatible positional argument or legacy keyword; `None` selects the newest branch point referenced by a child pathway after full-state verification, or the newest terminal branch point when no branches exist |
| `root` | path-like, string, or None | `None` | Project root containing initialized state |

#### Returns

`ComparisonResult` with the source summary, ordered child comparisons, and a `ComparisonState`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | State, source evidence, selected child evidence, or automatic source selection is invalid |

#### Notes

Explicit selection verifies the pinned source and relevant child evidence. Automatic selection requires complete state verification and selects the newest branch point referenced by a child pathway. A state with no child pathways selects its newest terminal branch point and reports `NO_BRANCH`. Any verification problem causes automatic selection to fail.

Comparison is a read-only operation over recorded state. For a child with more than one run record, it selects the newest run record by start time and identifier.

`ComparisonState.READY` means every child has evidence. `PARTIAL` means at least one child lacks evidence. `NO_BRANCH` means the source has no child pathways.

Parameter deltas contain inherited, added, and changed values.

#### Example

```python
from ocura_oss import ComparisonState, compare

result = compare("chokepoint-<id>", root="experiment")
if result.state is ComparisonState.PARTIAL:
    missing = [child.pathway_id for child in result.children if child.missing_evidence]
```

### `ocura_oss.verify`

```python
verify(
    root: os.PathLike[str] | str | None = None,
    *,
    against: Iterable[str] | None = None,
) -> StateVerification
```

Recheck every state record and every log referenced by a recorded run.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `root` | path-like, string, or None | `None` | Project root containing initialized state |
| `against` | iterable of strings, or None | `None` | Manifest lines retained from an earlier verification; each must still be present unchanged. Blank lines and lines starting with `#` are skipped; a manifest left with no entries is an error |

#### Returns

`StateVerification`. `report.ok` is true when `report.problems` is empty. The report contains record counts, the number of individual logs that passed verification, and the identifiers of running and abandoned attempts.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Initialized state is absent; the ledger record is missing, unreadable, malformed, or unverifiable; or `against` has a malformed entry or lists no records |

Problems in other records and logs are normally collected in the returned report instead of raised.

#### Notes

Verification checks record envelopes, checksums, identifiers, filenames, required fields, semantic relationships, lineage, that every run record has exactly one branch point, referenced log containment, byte counts, log digests, orphaned log files, unexpected directories under `logs/`, and unfinished attempts.

It may run while other processes record runs under the same root. An attempt that a live process is still recording appears in `running_attempts` and is not a problem. An abandoned attempt is a problem until `recover()` closes it.

When the state is intact, `report.manifest` holds one `KIND ID CHECKSUM` line per verified record and `report.digest` is the SHA-256 of those lines. Retain the manifest where this state's writers cannot change it and pass it back as `against` to detect a record that was rewritten together with its checksum. Records added since are allowed. See [retained manifests](state-and-verification.md#retained-manifests).

Verification establishes local consistency among records and logs. Authorship and execution-environment reproduction require separate evidence.

#### Example

```python
from pathlib import Path

from ocura_oss import verify

report = verify("experiment")
for record, problem in report.problems:
    print(record, problem)

retained = Path("trusted/experiment.manifest")
if report.ok:
    retained.write_text("".join(f"{line}\n" for line in report.manifest))

# Later, possibly after more runs:
assert verify("experiment", against=retained.read_text().splitlines()).ok
```

### `ocura_oss.recover`

```python
recover(
    root: os.PathLike[str] | str | None = None,
) -> tuple[RecoveredAttempt, ...]
```

Close every attempt whose recording process stopped before finalizing it.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `root` | path-like, string, or None | `None` | Project root containing initialized state |

#### Returns

A tuple with one `RecoveredAttempt` for each attempt that was closed. It is empty when nothing was abandoned.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Initialized state is absent, or an attempt record or its evidence is malformed or unreadable |

#### Notes

What recovery writes depends on how far the recorder got:

| `RecoveredAttempt.action` | When | Result |
| --- | --- | --- |
| `RecoveryAction.ABANDONED` | No outcome had been recorded | An run record with `Outcome.ABANDONED` and its terminal branch point |
| `RecoveryAction.COMPLETED` | The run record existed without its branch point | The missing branch point, with the run record's recorded outcome |
| `RecoveryAction.CLEARED` | The run record and branch point both existed | The leftover attempt record is removed |

An abandoned run record has `None` for `finished_at`, `duration_seconds`, `return_code`, and `launch_error_category`. Output captured before the recorder stopped is retained and measured, so it verifies afterward.

Attempts that a live process is still recording are left alone, so recovery is safe while other runs are in flight. Call it only after the abandoned command itself has stopped: a command that outlived its recorder can keep writing to logs that recovery has measured.

#### Example

```python
from ocura_oss import recover, verify

for closed in recover("experiment"):
    print(closed.atom_id, closed.action.value)

assert verify("experiment").ok
```

### `ocura_oss.run_demo`

```python
run_demo(
    root: os.PathLike[str] | str,
) -> DemoReport
```

Run the complete retained demonstration in a new directory.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `root` | path-like or string | required | Destination directory; it must not exist |

#### Returns

`DemoReport` containing the retained root, completed stages, and a ready comparison.

#### Raises

| Exception | Condition |
| --- | --- |
| `DemoError` | The destination exists or any demonstration stage fails |

#### Notes

The directory is retained on success and failure. Both demonstration commands must pass and the final state must verify before a report is returned.

Stages are `initialize`, `baseline-run`, `chokepoint`, `branch`, `rerun`, `verify`, and `compare`.

#### Example

```python
from ocura_oss import run_demo

report = run_demo("ocura-oss-demo")
print(report.comparison.state.value)
```

## Store

### Preferred readers (unreleased)

Start with these descriptive interfaces. Their validation and ordering match the
compatible legacy methods documented in detail below.

```python
store.load_ledger() -> Ledger
store.load_run(run_id: str) -> RunRecord
store.load_branch_point(branch_point_id: str) -> BranchPoint
store.list_run_records() -> list[RunRecord]
store.list_branch_points() -> list[BranchPoint]
store.verify_run_evidence(record: RunRecord) -> None
```

These correspond to `load_den`, `load_atom`, `load_chokepoint`, `list_atoms`,
`list_chokepoints`, and `verify_atom_evidence`. `list_runs()` is the higher-level
discovery interface for finalized runs, branch points, filtering, and unfinished
attempt counts; `list_run_records()` lists all retained run records in start order.
Neither metadata list verifies log contents.

`inspect_run`, `read_verified_log`, `open_verified_log`, and `log_info` accept the
preferred `run_id=ID` keyword alongside their compatible positional argument and
`atom_id=ID` keyword. `compress_logs` and `decompress_logs` accept `run_id` too.
Equal aliases are accepted; conflicting aliases raise `StoreError` with code
`invalid_input`. `Store.resolve_id()` accepts kinds `run` and `branch_point`,
returning unchanged `atom-...` and `chokepoint-...` IDs.

The [history example](../examples/history/README.md) demonstrates a fresh caller
discovering every observed attempt and reading bounded, verified excerpts from an
existing root. It leaves report composition and interpretation with the caller.

### `ocura_oss.Store`

```python
Store(
    root: os.PathLike[str] | str | None = None,
    *,
    archive: bool = False,
)
```

Read and verify Ocura OSS state under one project root.

The documented constructor, attributes, loading methods, listing methods, initialization method, verification methods, recovery method, and log storage methods form the provisional low-level API. Direct record writers are internal.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `root` | path-like, string, or None | `None` | Project root; `None` selects the current working directory |
| `archive` | bool | `False` | Read stable archived storage without ledger locks or mutations; stop writers and finalize all attempts first |

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `root` | `pathlib.Path` | Resolved project root |
| `state_dir` | `pathlib.Path` | Resolved `.ocura-oss/` directory |
| `pathways_dir` | `pathlib.Path` | Pathway record directory |
| `atoms_dir` | `pathlib.Path` | RunRecord record directory |
| `chokepoints_dir` | `pathlib.Path` | BranchPoint record directory |
| `attempts_dir` | `pathlib.Path` | Unfinished attempt records and their lock files |
| `logs_dir` | `pathlib.Path` | Recorded log directory |
| `den_path` | `pathlib.Path` | Ledger record path |

Constructing a `Store` resolves paths only. `exists()`, `require()`, and `initialize_state()` provide the state-presence operations.

#### Example

```python
from ocura_oss import Store

store = Store("experiment")
if store.exists():
    print(store.load_den().name)
```

### `Store.exists`

```python
store.exists() -> bool
```

Return whether the `.ocura-oss/` state directory exists.

#### Returns

`True` when the state directory exists, including when its contents are incomplete. Use `require()` or `verify_state()` for stronger checks.

### `Store.require`

```python
store.require() -> None
```

Require an initialized ledger under the project root.

#### Returns

`None`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | `den.json` is not a file |

This method checks `den.json` presence only. `load_den()` parses the ledger, and `verify_state()` performs complete verification.

When the root has no `.ocura-oss/` directory, the message names `ocura-oss init`. When a directory above the root holds state, the message names the nearest one instead.

### `Store.resolve_id`

```python
store.resolve_id(
    value: str,
    kind: Literal["pathway", "atom", "chokepoint"],
) -> str
```

Return the full identifier that a full or shortened identifier names.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `value` | string | A full identifier, or at least four of its leading hexadecimal characters with or without the kind word |
| `kind` | string | Record kind to search: `pathway`, `atom`, or `chokepoint` |

#### Returns

The full identifier. A full identifier is returned as given, without checking that its record exists.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | `value` is neither a full identifier nor a valid prefix, or the prefix matches no stored record or more than one |

A prefix is matched against record file names only; no record is read. The error for an ambiguous prefix lists up to five matching identifiers. The CLI calls this method for `branch --from`, `compare --from`, `run --from`, `run --pathway`, `logs ID`, `compress --run`, and `decompress --run`. The legacy `--atom` storage selector also remains supported.

### `Store.load_den`

```python
store.load_den() -> Ledger
```

Load and validate the ledger record.

#### Returns

The parsed `Ledger`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | The record is missing, unreadable, malformed, checksum-mismatched, or fails payload validation |

### `Store.load_pathway`

```python
store.load_pathway(
    pathway_id: str,
) -> Pathway
```

Load a pathway and validate its ledger and lineage references.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `pathway_id` | string | Identifier of the pathway record to load |

#### Returns

The parsed `Pathway`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | The identifier or record is invalid; the ledger, parent, or source reference is invalid; or lineage is cyclic |
| `RecursionError` | A crafted lineage exceeds the Python recursion limit |

Child pathways must set parent and source fields together, and their source branch point must belong to the parent pathway.

### `Store.load_atom`

```python
store.load_atom(
    atom_id: str,
) -> RunRecord
```

Load an run record and validate its pathway reference.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `atom_id` | string | Identifier of the run record to load |

#### Returns

The parsed `RunRecord`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | The identifier, record, payload, outcome invariants, or referenced pathway is invalid |

This method validates the run record and its log metadata fields. `verify_atom_evidence()` reads and verifies the referenced files.

### `Store.load_chokepoint`

```python
store.load_chokepoint(
    chokepoint_id: str,
) -> BranchPoint
```

Load a branch point and validate its run record, pathway, and outcome.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `chokepoint_id` | string | Identifier of the branch point record to load |

#### Returns

The parsed `BranchPoint`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | The identifier or record is invalid; the run record or pathway is invalid; or run record and branch point fields disagree |

`verify_atom_evidence()` provides the corresponding log-file verification.

### `Store.list_pathways`

```python
store.list_pathways() -> list[Pathway]
```

Return pathway records in deterministic creation order.

#### Returns

A list sorted by `created_at`, then `id`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Any listed JSON record has an invalid envelope, checksum, payload, kind, or filename-to-ID relationship |

The method fails closed instead of skipping an invalid record. Use `load_pathway()` or `verify_state()` when reference validation is required.

### `Store.list_atoms`

```python
store.list_atoms() -> list[RunRecord]
```

Return run records in deterministic start order.

#### Returns

A list sorted by `started_at`, then `id`.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Any listed JSON record has an invalid envelope, checksum, payload, outcome invariants, kind, or filename-to-ID relationship |

`load_atom()` adds pathway validation. `verify_state()` adds complete relationship and log verification.

### `Store.list_runs` and `Store.inspect_run`

```python
store.list_runs(
    *, pathway_id: str | None = None, outcome: Outcome | None = None,
    after: str | None = None, limit: int | None = 100,
) -> RunPage
store.inspect_run(atom_id: str | None = None, *, run_id: str | None = None) -> RunInspection
```

`RunPage` contains `runs: tuple[ListedRun, ...]`, `next_after: str | None`, and
`unfinished_attempts: int`. Each `ListedRun` holds `chokepoint_id: str` and
`run: RunSummary`. `RunInspection` contains `atom: RunRecord`, `pathway: Pathway`,
`chokepoint: BranchPoint | None`, and `children: tuple[Pathway, ...]`. These result
types live in `ocura_oss.store`. Descriptive properties are `ListedRun.branch_point_id`,
`RunInspection.record`, and `RunInspection.branch_point`; their legacy constructor
fields and JSON keys remain unchanged. Their
`to_dict()` methods include verification scope. `RunInspection.to_dict(include_command=True)`
includes command arguments, which are omitted by default.

Listing orders runs by branch point timestamp and ID, checks every scanned branch point record,
retains earlier failures, and keeps at most `limit + 1` candidates; `limit=None`
returns every match from one scan. *after* is a branch point ID, normally a page's
`next_after`. Inspection validates one selected run and its lineage and scans for
its branch point and child pathways. Neither reads log contents. Ordering,
completeness, filters and failure behavior follow the [CLI contract](cli.md#runs-and-show). Python callers
supply full IDs and `Outcome` values. Invalid limits, selectors or records raise
`StoreError`.

### `Store.list_chokepoints`

```python
store.list_chokepoints() -> list[BranchPoint]
```

Return branch point records in reverse deterministic creation order.

#### Returns

A list sorted by `created_at`, then `id`, both descending.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Any listed JSON record has an invalid envelope, checksum, payload, kind, or filename-to-ID relationship |

`load_chokepoint()` adds run record relationship validation. `verify_state()` adds complete relationship and log verification.

### `Store.has_terminal_evidence`

```python
store.has_terminal_evidence(
    pathway_id: str,
) -> bool
```

Return whether any listed run records the supplied pathway ID.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `pathway_id` | string | Pathway identifier to match against run records |

#### Returns

`True` when at least one run record names the pathway, otherwise `False`.

#### Raises

`StoreError` if run record listing fails.

The result reflects run record membership only. Pathway loading and log verification are separate operations.

### `Store.resolve_log_path`

```python
store.resolve_log_path(
    atom: RunRecord,
    stream: Literal["stdout", "stderr"],
) -> pathlib.Path
```

Resolve one recorded log path after containment checks.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `atom` | `RunRecord` | RunRecord whose recorded log path is resolved |
| `stream` | `stdout` or `stderr` | Selects the corresponding log field |

#### Returns

The resolved physical `pathlib.Path` directly inside this store's
`.ocura-oss/logs/` directory: raw `.log` storage if present, otherwise its `.log.gz`
backing path when compressed. Compressed file contents are encoded; use
`read_verified_log()` for original bytes. If both exist the raw path is preferred.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | `stream` is invalid; the run record was recorded without output capture; or the recorded path is empty, absolute, escaping, or outside the direct logs directory |

The returned path has passed containment checks only. Use `read_verified_log()` to consume bytes that have been checked against the stored run record; `verify_atom_evidence()` checks both logs without returning their contents.

### `Store.log_files`

```python
store.log_files(atom: RunRecord) -> dict[str, str]
```

Return each captured stream's current file relative to the project root, keyed
`stdout` and `stderr`: the recorded path, plus `.gz` when the log is stored as
gzip. A run recorded without output capture returns an empty dictionary. Only the
paths and the files' presence are checked; nothing is read, verified, or locked.
`ocura-oss run --json` and `show --json` report this as `log_files`. Raises
`StoreError` for unsafe or unreadable storage paths, including a recorded path
that escapes the logs directory.

### `Store.read_verified_log`

```python
store.read_verified_log(
    atom_id: str | None = None,
    *,
    run_id: str | None = None,
    stream: Literal["stdout", "stderr"] = "stdout",
) -> bytes
```

Read one complete recorded log and verify the exact bytes returned. Added in 0.4.0.

#### Parameters

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `run_id` | string or None | `None` | Preferred keyword identifying the run whose output is read |
| `atom_id` | string or None | `None` | Compatible positional argument or legacy keyword |
| `stream` | `stdout` or `stderr` | `stdout` | Selects the recorded output stream; keyword-only |

#### Returns

The selected log as `bytes`, unchanged, after loading and validating the stored run record and its lineage, checking path containment, and verifying the returned bytes' count and SHA-256 digest against the run record.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | The run record or lineage is invalid; the stream is invalid; the run record was recorded without output capture; or the selected log is missing, unreadable, outside containment, or inconsistent with its recorded byte count or digest |
| `RecursionError` | A crafted lineage exceeds the Python recursion limit |

#### Notes

Raw and gzip backing are read transparently and verified against the original
byte count and SHA-256. The complete original log is read into memory. Binary
output is preserved, and output from failed commands can be read. Decoding and
metric interpretation belong to the caller.

This is a targeted check of one stream. It does not verify the other stream or
the whole ledger; use `verify_state()` for the complete state check. Local
advisory locks coordinate reading with compression and decompression, without
creating a snapshot of the whole ledger. The returned bytes remain the verified
contents even if the log file later changes.

#### Example

```python
import json

from ocura_oss import Store

store = Store("experiment")
raw = store.read_verified_log("atom-<id>", stream="stdout")
metrics = json.loads(raw.decode("utf-8"))  # For a workload that emits UTF-8 JSON.

diagnostics = store.read_verified_log("atom-<id>", stream="stderr")
```

### `Store.open_verified_log`

```python
with store.open_verified_log(
    run_id=run_id,
    stream="stdout",
    offset=0,
    length=None,
) as source:
    chunk = source.read(4096)
```

This context manager returns a seekable binary snapshot after verifying the full
selected stream. Only the requested original-byte window is retained; seeking is
relative to that window. A window up to 1 MiB stays in memory. Larger windows use
OS temporary storage and bounded buffers, with one source decoding pass. The
snapshot closes on exit, including when the caller raises, and remains valid if
backing storage changes after verification.

Offsets and lengths follow [`logs`](cli.md#logs). Corruption outside a requested
range still fails before the context yields. The other stream and the whole
ledger are not checked. Invalid ranges or evidence raise `StoreError`.
`read_verified_log()` retains its existing bytes-returning behavior and whole-log
memory allocation.

`Store(root, archive=True)` permits these reads and `verify_state()` on stable,
read-only archives without creating ledger locks. It rejects unfinished attempts
and mutation operations. This mode assumes there are no concurrent writers; see
[stable archives](cli.md#stable-archives).

### `Store.log_info`

```python
store.log_info(
    atom_id: str | None = None,
    *,
    run_id: str | None = None,
    stream: Literal["stdout", "stderr"] = "stdout",
) -> LogInfo
```

Verify one captured log and return storage metadata without returning its
contents. `atom_id` must be a full identifier. Invalid lineage, stream, capture
mode, or evidence raises `StoreError`, as for `read_verified_log()`.

`LogInfo` is defined in `ocura_oss.store`, without adding it to
`ocura_oss.__all__`. It has these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `run_id` | string | Preferred property for the full run ID |
| `atom_id` | string | Compatible constructor field for the full run ID |
| `stream` | string | `stdout` or `stderr` |
| `outcome` | string | Recorded outcome |
| `started_at` | string | Recorded UTC start time |
| `encoding` | string | `raw` or `gzip` |
| `original_bytes` | integer | Recorded original byte count |
| `stored_bytes` | integer | Current physical byte count |
| `sha256` | string | Full SHA-256 of the verified original bytes |
| `fingerprint` | string | Readable stream/short atom/digest-prefix reference |
| `path` | string | Project-relative physical backing path |

The fingerprint remains stable across conversion. It identifies the log; it
does not describe its contents, act as an ID lookup key, or replace full-digest verification. Neither
command arguments nor log contents are included in this metadata.

### `Store.compress_logs` and `Store.decompress_logs`

```python
store.compress_logs(
    *,
    atom_id: str | None = None,
    run_id: str | None = None,
    min_bytes: int = 1_048_576,
    dry_run: bool = False,
) -> tuple[LogStorageChange, ...]

store.decompress_logs(
    *,
    atom_id: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
) -> tuple[LogStorageChange, ...]
```

Convert finalized captured logs after verifying their original bytes. `None`
selects finalized run records across the ledger; a supplied `atom_id` must be full.
Active attempts and run records without capture are not converted. `min_bytes` is a
nonnegative original byte threshold. Compression uses gzip level 6 and requires
at least 10% savings. `dry_run=True` calculates prospective changes without
replacing files.

Each returned `LogStorageChange` exposes the preferred `run_id` property and contains `atom_id`, `stream`, `action`,
`original_bytes`, `stored_bytes`, and `path` (the project-relative backing path).
This result type also lives in `ocura_oss.store`, without a top-level export.
Actions are `compressed`, `would-compress`, `restored`, `would-restore`,
`unchanged`, `small`, or `no-savings`.
The records and original digests remain unchanged, as do retained manifests.
Local advisory locks coordinate readers and storage conversion; temporary disk
headroom is required. A pass can finish earlier conversions before a later log
raises `StoreError` or an operating-system error; it is not a transaction over
the whole ledger.

```python
from ocura_oss import Store

store = Store("experiment")
preview = store.compress_logs(dry_run=True)
changes = store.compress_logs()
info = store.log_info("atom-<id>")
assert len(store.read_verified_log(info.atom_id)) == info.original_bytes
store.decompress_logs()
assert store.verify_state().ok
```

Compressed storage extends physical storage despite retaining record schema 2;
Ocura OSS 0.6 and earlier require decompressed logs. See
[log compression](autocompression.md).

### `Store.verify_atom_evidence`

```python
store.verify_atom_evidence(
    atom: RunRecord,
) -> None
```

Verify the log files referenced by one run record.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `atom` | `RunRecord` | RunRecord containing stdout and stderr paths, byte counts, and SHA-256 digests |

#### Returns

`None` when both logs pass verification. An run recorded without output capture has no logs and passes.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | A path escapes containment, a log is missing, or a byte count or SHA-256 digest differs |

The checksum supports local change detection. Authorship requires separate authenticated evidence.

### `Store.initialize_state`

```python
store.initialize_state(
    *,
    name: str,
) -> tuple[Ledger, Pathway]
```

Create one ledger and one default pathway.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `name` | string | Nonblank name stored in the ledger |

#### Returns

A `(den, pathway)` tuple.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | State already exists, the name is invalid, or state cannot be written |

Use top-level `initialize()` when the resolved root should be included in the result.

### `Store.verify_state`

```python
store.verify_state(
    *,
    against: Iterable[str] | None = None,
) -> StateVerification
```

Verify every state record and every log referenced by a valid run record.

#### Returns

`StateVerification`. Problems outside the ledger are collected by record name when possible. `logs_checked` counts individual log files that passed verification. `running_attempts` and `abandoned_attempts` name unfinished attempts. `manifest` and `digest` are filled when the state is intact. `against` behaves as described for top-level [`verify()`](#ocura_ossverify).

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | The ledger is missing, unreadable, malformed, checksum-mismatched, or invalid |

#### Notes

The scan reports invalid record files, broken relationships, invalid lineage, source-parent disagreement, run records without exactly one branch point, missing or changed logs, orphaned files, unexpected directories under `logs/`, and abandoned attempts.

Other processes may record runs under the same root during the scan. A running attempt is not a problem, and its logs are not checked until it is finalized.

### `Store.list_attempts`

```python
store.list_attempts() -> list[tuple[Attempt, AttemptState]]
```

Return unfinished attempts with their state, ordered by start time and then identifier.

#### Returns

A list of `(attempt, state)` pairs. The state is `AttemptState.RUNNING` while a live process is recording the attempt and `AttemptState.ABANDONED` once that process has stopped without finalizing it. A finalized attempt is an run record and is not listed.

#### Raises

`StoreError` if an attempt record is malformed, checksum-mismatched, misnamed, or unreadable.

### `Store.recover`

```python
store.recover() -> tuple[RecoveredAttempt, ...]
```

Close every unfinished attempt whose recording process has stopped. Top-level [`recover()`](#ocura_ossrecover) calls this method and documents the actions, results, and limits.

#### Raises

| Exception | Condition |
| --- | --- |
| `StoreError` | Initialized state is absent, or an attempt record or its evidence is malformed or unreadable |

## Record types

Public records and results are frozen dataclasses. Their fields cannot be reassigned. Mapping fields should be treated as read-only values.

The dataclasses describe values returned by workflows and `Store`. Direct construction creates an in-memory value only; workflow functions and `Store` loading provide persistence and validation.

Identifiers use a kind prefix followed by 32 lowercase hexadecimal characters. Timestamps are normalized UTC ISO 8601 strings with offsets.

<a id="ocura_ossden"></a>

### `ocura_oss.Ledger`

```python
Ledger(
    id: str,
    name: str,
    created_at: str,
    default_pathway_id: str,
)
```

Project-local state identity and default pathway.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `id` | string | Ledger identifier |
| `name` | string | User-supplied state name |
| `created_at` | string | UTC ISO 8601 creation timestamp |
| `default_pathway_id` | string | Pathway selected when `run()` omits `pathway_id` |

### `ocura_oss.Pathway`

```python
Pathway(
    id: str,
    den_id: str,
    created_at: str,
    parent_pathway_id: str | None,
    source_chokepoint_id: str | None,
    reason: str,
    parameters: Mapping[str, str],
)
```

One lineage of declared parameters and recorded evidence.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `id` | string | Pathway identifier |
| `ledger_id` | string | Preferred property for the owning ledger identifier |
| `den_id` | string | Owning ledger identifier |
| `created_at` | string | UTC ISO 8601 creation timestamp |
| `parent_pathway_id` | string or None | Parent for a branch; `None` on the default pathway |
| `source_branch_point_id` | string or None | Preferred property for the source boundary |
| `source_chokepoint_id` | string or None | Source boundary for a branch; `None` on the default pathway |
| `reason` | string | Branch reason or `default pathway` |
| `parameters` | mapping of string to string | Effective pathway parameters after inheritance and overrides |

Parent and source fields are either both set or both `None`.

<a id="ocura_ossatom"></a>

### `ocura_oss.RunRecord`

```python
RunRecord(
    id: str,
    pathway_id: str,
    started_at: str,
    finished_at: str | None,
    duration_seconds: float | None,
    outcome: Outcome,
    return_code: int | None,
    launch_error_category: str | None,
    declared_parameters: Mapping[str, str],
    command: tuple[str, ...],
    stdout_log: str | None,
    stderr_log: str | None,
    stdout_bytes: int | None,
    stderr_bytes: int | None,
    stdout_sha256: str | None,
    stderr_sha256: str | None,
    output_capture: OutputCapture = OutputCapture.FULL,
    masked_arguments: tuple[int, ...] = (),
    context: RunContext | None = None,
)
```

One recorded command attempt and its referenced output logs.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `id` | string | RunRecord identifier |
| `pathway_id` | string | Pathway receiving this evidence |
| `started_at` | string | UTC ISO 8601 start timestamp |
| `finished_at` | string or None | UTC ISO 8601 finish timestamp; `None` for an abandoned run record |
| `duration_seconds` | float or None | Nonnegative finite elapsed duration, rounded to six decimal places for package-created run records; `None` for an abandoned run record |
| `outcome` | `Outcome` | Terminal command outcome |
| `return_code` | integer or None | Process return code for passed and failed outcomes |
| `launch_error_category` | string or None | Normalized launch category, or `interrupted` |
| `declared_parameters` | mapping of string to string | Labels recorded for this run: its pathway's effective parameters with the run's own declarations laid over them |
| `command` | tuple of strings | Executable and argument tokens retained in the raw record, with `<masked>` at each masked position |
| `stdout_log` | string or None | Project-relative logical stdout log path; physical gzip storage adds `.gz` |
| `stderr_log` | string or None | Project-relative logical stderr log path; physical gzip storage adds `.gz` |
| `stdout_bytes` | integer or None | Recorded stdout byte count |
| `stderr_bytes` | integer or None | Recorded stderr byte count |
| `stdout_sha256` | string or None | Lowercase SHA-256 digest of stdout bytes |
| `stderr_sha256` | string or None | Lowercase SHA-256 digest of stderr bytes |
| `output_capture` | `OutputCapture` | Whether stdout and stderr were retained as logs |
| `masked_arguments` | tuple of integers | Ascending positions in `command` that hold the masked placeholder |
| `context` | `RunContext` or None | Launch context, when it was requested |

Outcome, return code, and launch category must satisfy the invariants described by `run()`. An abandoned run record carries neither a return code nor a launch category.

The six log fields are all set when `output_capture` is `OutputCapture.FULL` and all `None` when it is `OutputCapture.NONE`. For an abandoned run record, the byte counts and digests describe each log as recovery found it.

### `ocura_oss.Attempt`

```python
Attempt(
    id: str,
    pathway_id: str,
    chokepoint_id: str,
    started_at: str,
    declared_parameters: Mapping[str, str],
    command: tuple[str, ...],
    stdout_log: str | None,
    stderr_log: str | None,
    output_capture: OutputCapture = OutputCapture.FULL,
    masked_arguments: tuple[int, ...] = (),
    context: RunContext | None = None,
)
```

One run that has started and is not yet finalized, as returned by `Store.list_attempts()`.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `id` | string | Identifier of the run record this attempt becomes when finalized |
| `run_id` | string | Descriptive property for `id` |
| `pathway_id` | string | Pathway the run was started on |
| `branch_point_id` | string | Preferred property for the reserved terminal boundary |
| `chokepoint_id` | string | Identifier reserved for the run record's terminal branch point |
| `started_at` | string | UTC ISO 8601 start timestamp |
| `declared_parameters` | mapping of string to string | Labels recorded for this run: its pathway's effective parameters with the run's own declarations laid over them |
| `command` | tuple of strings | Executable and argument tokens, with `<masked>` at each masked position |
| `stdout_log` | string or None | Project-relative stdout log path; `None` without capture |
| `stderr_log` | string or None | Project-relative stderr log path; `None` without capture |
| `output_capture` | `OutputCapture` | Whether stdout and stderr are being retained as logs |
| `masked_arguments` | tuple of integers | Ascending positions in `command` that hold the masked placeholder |
| `context` | `RunContext` or None | Launch context, when it was requested |

An attempt has no outcome, byte counts, or digests: its logs may still be growing.

### `ocura_oss.RunContext`

```python
RunContext(
    platform: Mapping[str, str] | None,
    git_revision: str | None,
    git_dirty: bool | None,
    files: Mapping[str, FileDigest],
)
```

Facts about where a command attempt started, recorded only on request.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `platform` | mapping of string to string, or None | `system`, `release`, and `machine` of the recording machine; `None` unless `context=True` |
| `git_revision` | string or None | Full object name of the checked-out commit; `None` unless `context=True` found a work tree with a commit |
| `git_dirty` | boolean or None | Whether the work tree had modified or untracked files other than `.ocura-oss/`; `None` whenever `git_revision` is |
| `files` | mapping of string to `FileDigest` | One entry per file named in `context_files`, keyed by the path as given with forward slashes |

Context describes the moment of launch. Verification does not recheck it.

### `ocura_oss.FileDigest`

```python
FileDigest(
    bytes: int,
    sha256: str,
)
```

Size and lowercase SHA-256 digest of one file named for context capture.

### `ocura_oss.RecoveredAttempt`

```python
RecoveredAttempt(
    atom_id: str,
    chokepoint_id: str,
    pathway_id: str,
    action: RecoveryAction,
)
```

One unfinished attempt closed by `recover()`.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `run_id` | string | Preferred property for the finalized run identifier |
| `atom_id` | string | Compatible constructor field for the run identifier |
| `branch_point_id` | string | Preferred property for the terminal boundary identifier |
| `chokepoint_id` | string | That run record's terminal branch point |
| `pathway_id` | string | Pathway containing the run record |
| `action` | `RecoveryAction` | What closing the attempt had to do |

<a id="ocura_osschokepoint"></a>

### `ocura_oss.BranchPoint`

```python
BranchPoint(
    id: str,
    pathway_id: str,
    atom_id: str,
    created_at: str,
    kind: str,
    outcome: Outcome,
    branchable: bool,
)
```

Terminal evidence boundary that can serve as a branch source.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `id` | string | BranchPoint identifier |
| `pathway_id` | string | Pathway shared with the source run record |
| `run_id` | string | Preferred property for the source run identifier |
| `atom_id` | string | Source run record identifier |
| `created_at` | string | UTC ISO 8601 creation timestamp |
| `kind` | string | `terminal` in schema version 2 |
| `outcome` | `Outcome` | Outcome that must agree with the source run record |
| `branchable` | boolean | Whether branching may use this boundary |

### `ocura_oss.Initialization`

```python
Initialization(
    root: pathlib.Path,
    den: Ledger,
    pathway: Pathway,
)
```

State created by `initialize()`.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `root` | `pathlib.Path` | Resolved project root |
| `ledger` | `Ledger` | Preferred property for the created ledger |
| `den` | `Ledger` | Compatible constructor field and property |
| `pathway` | `Pathway` | Created default pathway |

### `ocura_oss.RunExecution`

```python
RunExecution(
    atom: RunRecord,
    chokepoint: BranchPoint,
    storage_warnings: tuple[str, ...] = (),
)
```

Records produced by one command attempt.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `record` | `RunRecord` | Preferred property for the recorded attempt and log metadata |
| `atom` | `RunRecord` | Compatible constructor field and property |
| `branch_point` | `BranchPoint` | Preferred property for the terminal evidence boundary |
| `chokepoint` | `BranchPoint` | Compatible constructor field and property |
| `storage_warnings` | tuple of strings | Post-finalization compression errors; actual command outcome remains recorded on the run record |

### `ocura_oss.RunSummary`

```python
RunSummary(
    id: str,
    pathway_id: str,
    outcome: Outcome,
    started_at: str,
    duration_seconds: float | None,
    return_code: int | None,
)
```

Reduced command fields used in comparisons and public summaries.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `id` | string | Source run record identifier |
| `pathway_id` | string | Pathway containing the run record |
| `outcome` | `Outcome` | Terminal outcome |
| `started_at` | string | UTC ISO 8601 start timestamp |
| `duration_seconds` | float or None | Recorded elapsed duration; `None` for an abandoned run |
| `return_code` | integer or None | Process return code when applicable |

#### Methods

`to_dict() -> dict` returns these fields with `outcome` converted to its string value.

### `ocura_oss.ParameterDelta`

```python
ParameterDelta(
    inherited: Mapping[str, str],
    added: Mapping[str, str],
    changed: Mapping[str, Mapping[str, str]],
)
```

Declared parameter relationship between source and child values.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `inherited` | mapping of string to string | Keys present with the same value in source and child |
| `added` | mapping of string to string | Keys present only in the child |
| `changed` | nested string mapping | Changed keys, each with `source` and `child` values |

Removed source keys are outside the parameter-delta representation.

#### Methods

| Method | Returns | Description |
| --- | --- | --- |
| `to_dict()` | dictionary | Deterministically sorted `inherited`, `added`, and `changed` mappings |
| `phrase()` | string | Concise text summary, or `(none)` when the delta has no represented entries |

### `ocura_oss.ChildComparison`

```python
ChildComparison(
    pathway_id: str,
    reason: str,
    source_chokepoint_id: str,
    parameters: ParameterDelta,
    source_run: RunSummary,
    child_run: RunSummary | None,
    run_parameters: ParameterDelta | None,
    missing_evidence: bool,
)
```

Comparison of one child pathway with its source run.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `pathway_id` | string | Child pathway identifier |
| `reason` | string | Recorded branch reason |
| `source_chokepoint_id` | string | Shared source branch point |
| `parameters` | `ParameterDelta` | Parent-to-child pathway parameter delta |
| `source_run` | `RunSummary` | Source run record summary |
| `child_run` | `RunSummary` or None | Newest child run record summary, or `None` when evidence is missing |
| `run_parameters` | `ParameterDelta` or None | Source-atom-to-child-atom parameter delta when child evidence exists |
| `missing_evidence` | boolean | Whether the child has no run record |

#### Methods

`to_dict() -> dict` returns a nested JSON-compatible representation.

### `ocura_oss.ComparisonResult`

```python
ComparisonResult(
    state: ComparisonState,
    source_chokepoint_id: str,
    source_pathway_id: str,
    source_run: RunSummary,
    children: tuple[ChildComparison, ...],
)
```

Comparison state and every child associated with one source branch point.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `state` | `ComparisonState` | Evidence completeness across children |
| `source_chokepoint_id` | string | Selected source boundary |
| `source_pathway_id` | string | Pathway containing the source run record |
| `source_run` | `RunSummary` | Source run record summary |
| `children` | tuple of `ChildComparison` | Child comparisons in creation order |

#### Methods

`to_dict() -> dict` returns a nested JSON-compatible representation with enum values converted to strings.

### `ocura_oss.StateVerification`

```python
StateVerification(
    pathways: int,
    atoms: int,
    chokepoints: int,
    logs_checked: int,
    problems: tuple[tuple[str, str], ...],
    running_attempts: tuple[str, ...] = (),
    abandoned_attempts: tuple[str, ...] = (),
    manifest: tuple[str, ...] = (),
    digest: str | None = None,
)
```

Result of verifying records and referenced logs under one state directory.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `pathways` | integer | Valid pathway records collected by the scan |
| `run_records` | integer | Preferred property for valid run records collected by the scan |
| `atoms` | integer | Compatible constructor field for the same count |
| `branch_points` | integer | Preferred property for valid branch point records collected by the scan |
| `chokepoints` | integer | Compatible constructor field for the same count |
| `logs_checked` | integer | Individual referenced logs that passed verification |
| `problems` | tuple of pairs | `(record, problem)` entries collected during verification |
| `running_attempts` | tuple of strings | Attempts a live process is still recording; not problems |
| `abandoned_attempts` | tuple of strings | Attempts whose recorder stopped; each also appears in `problems` |
| `manifest` | tuple of strings | Sorted `KIND ID CHECKSUM` lines for every verified record; empty when there are problems |
| `digest` | string or None | SHA-256 of the manifest lines, each newline-terminated; `None` when there are problems |

#### Properties

`ok -> bool` is `True` when `problems` is empty.

### `ocura_oss.DemoStep`

```python
DemoStep(
    step: str,
    detail: str,
)
```

One completed stage in a demonstration report.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `step` | string | Stable stage label |
| `detail` | string | Recorded summary of that stage |

### `ocura_oss.DemoReport`

```python
DemoReport(
    root: pathlib.Path,
    steps: tuple[DemoStep, ...],
    comparison: ComparisonResult,
)
```

Retained demonstration location, completed stages, and comparison.

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `root` | `pathlib.Path` | Resolved retained destination |
| `steps` | tuple of `DemoStep` | Completed stages in execution order |
| `comparison` | `ComparisonResult` | Final ready comparison |

#### Methods

`to_dict() -> dict` returns a JSON-compatible representation. `root` is converted to a string.

## Enumerations

### `ocura_oss.Outcome`

String enum describing one terminal command attempt.

| Member | Value | Meaning |
| --- | --- | --- |
| `Outcome.PASSED` | `passed` | Process returned zero |
| `Outcome.FAILED` | `failed` | Process returned nonzero |
| `Outcome.LAUNCH_FAILED` | `launch_failed` | Process could not be launched |
| `Outcome.INTERRUPTED` | `interrupted` | Attempt was interrupted and partial output was recorded |
| `Outcome.ABANDONED` | `abandoned` | The recording process stopped before an outcome was recorded; the attempt was closed by `recover()` |

`Outcome` derives from `enum.StrEnum`, so members also behave as strings.

### `ocura_oss.OutputCapture`

String enum stating whether a run's stdout and stderr were retained as logs.

| Member | Value | Meaning |
| --- | --- | --- |
| `OutputCapture.FULL` | `full` | Both streams were written to logs |
| `OutputCapture.NONE` | `none` | No output was written to disk |

### `ocura_oss.AttemptState`

String enum describing an unfinished attempt.

| Member | Value | Meaning |
| --- | --- | --- |
| `AttemptState.RUNNING` | `running` | A live process is still recording the attempt |
| `AttemptState.ABANDONED` | `abandoned` | The recording process stopped without finalizing the attempt |

### `ocura_oss.RecoveryAction`

String enum describing what closing an unfinished attempt had to do.

| Member | Value | Meaning |
| --- | --- | --- |
| `RecoveryAction.ABANDONED` | `abandoned` | No outcome had been recorded; an abandoned run record and its branch point were written |
| `RecoveryAction.COMPLETED` | `completed` | The run record existed; its missing branch point was written |
| `RecoveryAction.CLEARED` | `cleared` | The run record and branch point existed; the leftover attempt record was removed |

### `ocura_oss.ComparisonState`

String enum describing evidence completeness for one comparison.

| Member | Value | Meaning |
| --- | --- | --- |
| `ComparisonState.NO_BRANCH` | `no_branch` | Source has no child pathways |
| `ComparisonState.PARTIAL` | `partial` | At least one child lacks evidence |
| `ComparisonState.READY` | `ready` | Every child has evidence |

`ComparisonState` derives from `enum.StrEnum`.

## Exceptions

### `ocura_oss.StoreError`

In this unreleased checkout, `StoreError` also exposes `code`, `execution`, `ids`,
and `recovery` for machine-readable diagnostics. Its string remains the human
message. Execution states and recovery hints follow the
[CLI error contract](cli.md#structured-errors-unreleased). A caller must inspect
`unknown` execution before deciding to repeat work; these fields do not provide
idempotency or exactly-once execution. Ordinary `OSError` can still propagate
from the Python API where documented.

```python
StoreError(message)
```

Raised for invalid workflow input and missing, malformed, checksum-mismatched, semantically inconsistent, unverifiable, or unwritable state.

`run()` returns normal command failures and launch failures as `Outcome` values. It raises `StoreError` when the workflow or evidence persistence fails.

### `ocura_oss.DemoError`

```python
DemoError(
    step: str,
    message: str,
)
```

Raised when a retained demonstration cannot complete.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `step` | string | Stage that failed |
| `message` | string | Failure detail |

#### Attributes

| Name | Type | Description |
| --- | --- | --- |
| `step` | string | Stage that failed |
| `message` | string | Failure detail |

The destination is retained when it was created before the failure.

## Module metadata

### `ocura_oss.__version__`

```python
ocura_oss.__version__: str
```

Installed package version. This development snapshot reports `"0.7.0.dev0"`.

## Typing

The wheel includes `py.typed`, so package annotations are visible to compatible static type checkers.

```python
from pathlib import Path

from ocura_oss import Initialization, Store, initialize

initial: Initialization = initialize(Path("experiment"))
store: Store = Store(initial.root)
```

Dataclass fields use concrete record and result types. Parameter inputs use `collections.abc.Sequence` and `Mapping` so callers can supply compatible typed containers.

## State and concurrency

The supported programmatic workflows write project-local `.ocura-oss/` state. See [state and verification](state-and-verification.md) for record details and upgrade tools.

Several processes may record runs under one state root at the same time, and verification and comparison may run while they do. Public workflow functions perform record mutation; direct record writers remain internal. The liveness of an unfinished attempt is read from an operating-system file lock, which is reliable on local filesystems only.

Raw run records retain command arguments and logs retain command output. Keep secrets out of command arguments, parameter mappings, branch reasons, and output. Where one cannot be avoided, `masked_arguments` keeps a command token out of the records and `capture=False` keeps output off disk.

For command syntax and exit status, see the [CLI reference](cli.md). For the record layout and execution boundary, see the [documentation overview](index.md).
