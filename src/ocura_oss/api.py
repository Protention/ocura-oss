# SPDX-License-Identifier: Apache-2.0

"""Supported programmatic workflows for Ocura OSS."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ocura_oss import branching, logstorage, runner
from ocura_oss.demo import DemoReport
from ocura_oss.demo import run_demo as _run_demo
from ocura_oss.errors import classify_os_error
from ocura_oss.model import ComparisonResult, Ledger, Pathway
from ocura_oss.runner import RunExecution
from ocura_oss.store import RecoveredAttempt, StateVerification, Store, StoreError

_Root = os.PathLike[str] | str | None


@dataclass(frozen=True)
class Initialization:
    """State created by :func:`initialize`."""

    root: Path
    den: Ledger
    pathway: Pathway

    @property
    def ledger(self) -> Ledger:
        return self.den


def initialize(root: _Root = None, *, name: str = "ocura-oss") -> Initialization:
    """Create one ledger and its default pathway under a project root.

    The operation refuses to replace an existing ``.ocura-oss/`` directory.
    When *root* is omitted, the current working directory is used.
    """
    store = Store(root)
    den, pathway = store.initialize_state(name=name)
    return Initialization(root=store.root, den=den, pathway=pathway)


def run(
    command: Sequence[str],
    *,
    root: _Root = None,
    pathway_id: str | None = None,
    parameters: Mapping[str, str] | None = None,
    mirror: bool = False,
    capture: bool = True,
    compress: bool = True,
    compression_min_bytes: int = logstorage.DEFAULT_MIN_BYTES,
    masked_arguments: Iterable[int] = (),
    substitute: bool = False,
    context: bool = False,
    context_files: Iterable[os.PathLike[str] | str] = (),
) -> RunExecution:
    """Run a trusted local command and record terminal evidence.

    The den's default pathway is used when *pathway_id* is omitted. A command
    failure is represented by the returned atom's outcome and does not raise
    an exception. Invalid state or input raises :class:`StoreError`.

    Pass ``capture=False`` to retain no stdout or stderr; the atom then records
    that it has no logs. *masked_arguments* lists zero-based positions in
    *command* whose values are replaced by a placeholder in every record. The
    command still receives the real values.

    Large completed logs are losslessly compressed once per run when this
    saves at least ten percent. Pass ``compress=False`` for raw files, or set
    ``compression_min_bytes`` (default 1 MiB). Read original output with
    ``Store.read_verified_log()``. Storage errors appear in the returned
    ``storage_warnings`` and do not change the command's recorded outcome.

    The run records its pathway's effective parameters together with
    *parameters*, which wins for a key both name. With ``substitute=True``,
    ``{KEY}`` in a command token is replaced by the value recorded for that
    key. ``context=True`` records the
    platform and git state at launch; *context_files* names files whose size
    and digest are recorded. Neither is captured unless requested.
    """
    try:
        store = _required_store(root)
        selected_pathway = pathway_id or store.load_den().default_pathway_id
    except StoreError as exc:
        exc.execution = "not_started"
        raise
    except OSError as exc:
        raise StoreError(str(exc), code=classify_os_error(exc), execution="not_started") from exc
    return runner.run_command(
        store,
        pathway_id=selected_pathway,
        argv=command,
        declared_parameters={} if parameters is None else parameters,
        mirror=mirror,
        capture=capture,
        compress=compress,
        compression_min_bytes=compression_min_bytes,
        masked_arguments=masked_arguments,
        substitute=substitute,
        context=context,
        context_files=context_files,
    )


def branch(
    source_chokepoint_id: str | None = None,
    *,
    reason: str,
    root: _Root = None,
    parameters: Mapping[str, str] | None = None,
    source_branch_point_id: str | None = None,
) -> Pathway:
    """Create a metadata-only child pathway from verified terminal evidence.

    Pass the source positionally or as ``source_branch_point_id``. The legacy
    ``source_chokepoint_id`` keyword remains accepted; differing values conflict.
    """
    source_id = _source_id(source_chokepoint_id, source_branch_point_id)
    if source_id is None:
        raise StoreError(
            "a source branch point id is required", code="invalid_input", execution="not_started"
        )
    store = _required_store(root)
    source_chokepoint, _source_atom, source_pathway = branching.load_verified_source(
        store, source_id
    )
    return branching.create_child_pathway(
        store,
        source_chokepoint=source_chokepoint,
        source_pathway=source_pathway,
        reason=reason,
        overrides={} if parameters is None else parameters,
    )


def compare(
    source_chokepoint_id: str | None = None,
    *,
    root: _Root = None,
    source_branch_point_id: str | None = None,
) -> ComparisonResult:
    """Compare a verified source run with child pathway evidence.

    Omitting the source selects the newest branch point referenced by
    a child pathway after the complete state passes verification. A state with
    no child pathways selects its newest terminal branch point. Pass an explicit
    source positionally or as ``source_branch_point_id``. The legacy
    ``source_chokepoint_id`` keyword remains accepted; differing values conflict.
    """
    source_id = _source_id(source_chokepoint_id, source_branch_point_id)
    store = _required_store(root)
    return branching.compare(store, source_id)


def verify(root: _Root = None, *, against: Iterable[str] | None = None) -> StateVerification:
    """Recheck every state record and every log referenced by a recorded run.

    Pass *against* a manifest retained from an earlier verification to also
    require that each of its records is still present and unchanged.
    """
    return _required_store(root).verify_state(against=against)


def recover(root: _Root = None) -> tuple[RecoveredAttempt, ...]:
    """Close every attempt whose recording process stopped before finalizing it.

    An attempt with no recorded outcome becomes an ``abandoned`` atom. Attempts
    that a live process is still recording are left alone.
    """
    return _required_store(root).recover()


def run_demo(root: os.PathLike[str] | str) -> DemoReport:
    """Run the retained demonstration in a new directory."""
    return _run_demo(Path(root))


def _required_store(root: _Root) -> Store:
    store = Store(root)
    store.require()
    return store


def _source_id(legacy: str | None, canonical: str | None) -> str | None:
    if legacy is not None and canonical is not None and legacy != canonical:
        raise StoreError(
            "source_branch_point_id conflicts with source_chokepoint_id",
            code="invalid_input",
            execution="not_started",
        )
    return canonical if canonical is not None else legacy
