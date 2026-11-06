# SPDX-License-Identifier: Apache-2.0

"""Source validation, metadata-only branching, and comparison states."""

from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping

from ocura_oss import model
from ocura_oss.store import Store, StoreError


def load_verified_source(
    store: Store, branch_point_id: str
) -> tuple[model.BranchPoint, model.RunRecord, model.Pathway]:
    """Load and verify a branch point, its run, its pathway, and its evidence.

    The source is rejected before any write when the records are missing,
    malformed, checksum-mismatched, nonterminal, nonbranchable, or when the
    run's recorded logs are missing or fail integrity verification.
    """
    branch_point = store.load_branch_point(branch_point_id)
    if branch_point.kind != model.TERMINAL_KIND:
        raise StoreError(f"source branch point {branch_point_id} is not terminal")
    if not branch_point.branchable:
        raise StoreError(f"source branch point {branch_point_id} is not branchable")
    record = store.load_run(branch_point.run_id)
    pathway = store.load_pathway(branch_point.pathway_id)
    store.verify_run_evidence(record)
    return branch_point, record, pathway


def create_child_pathway(
    store: Store,
    *,
    source_branch_point: model.BranchPoint,
    source_pathway: model.Pathway,
    reason: str,
    overrides: Mapping[str, str],
    now: Callable[[], datetime.datetime] | None = None,
    new_id: Callable[[str], str] | None = None,
) -> model.Pathway:
    """Create one child pathway containing lineage metadata only.

    No workspace, process, memory image, checkpoint, artifact, or source run
    is copied. Effective parameters are the parent's effective parameters plus
    the declared overrides.
    """
    text = reason.strip() if isinstance(reason, str) else ""
    if not text:
        raise StoreError("branch reason must not be blank")
    override_parameters = model.validate_parameters(dict(overrides))
    clock = now or model.utc_now
    identifier = new_id or model.make_id
    ledger = store.load_ledger()
    effective = dict(source_pathway.parameters)
    effective.update(override_parameters)
    child = model.Pathway(
        id=identifier("pathway"),
        ledger_id=ledger.id,
        created_at=model.format_timestamp(clock()),
        parent_pathway_id=source_pathway.id,
        source_branch_point_id=source_branch_point.id,
        reason=text,
        parameters=effective,
    )
    store._save_pathway(child)
    return child


def select_source_branch_point(
    store: Store,
) -> tuple[model.BranchPoint, model.RunRecord, model.Pathway]:
    """Choose the newest branched source under a global fail-closed policy.

    The entire state is verified first: any malformed record, broken
    reference, unverified log, or orphaned file anywhere under the state
    directory prevents automatic selection. When the state has no child
    pathways, the newest terminal branch point preserves the ``no_branch`` view.
    """
    report = store.verify_state()
    if not report.ok:
        record, problem = report.problems[0]
        raise StoreError(
            f"automatic source selection requires a fully verified state ({record}: {problem})"
        )
    branch_points = store.list_branch_points()
    if not branch_points:
        raise StoreError("no branchable branch point found")
    branched_sources = {
        pathway.source_branch_point_id
        for pathway in store.list_pathways()
        if pathway.source_branch_point_id is not None
    }
    selected = next(
        (branch_point for branch_point in branch_points if branch_point.id in branched_sources),
        branch_points[0],
    )
    return load_verified_source(store, selected.id)


def compare(store: Store, branch_point_id: str | None = None) -> model.ComparisonResult:
    """Compare a source run with every child pathway created from its branch point."""
    if branch_point_id is None:
        source = select_source_branch_point(store)
    else:
        source = load_verified_source(store, branch_point_id)
    branch_point, record, pathway = source
    source_run = model.run_summary_from_run(record)

    children = []
    for candidate in store.list_pathways():
        if (
            candidate.parent_pathway_id == pathway.id
            and candidate.source_branch_point_id == branch_point.id
        ):
            children.append(store.load_pathway(candidate.id))
    children.sort(key=lambda item: (item.created_at, item.id))

    newest_by_pathway: dict[str, model.RunRecord] = {}
    if children:
        child_ids = {child.id for child in children}
        for candidate_run in store.list_run_records():
            if candidate_run.pathway_id not in child_ids:
                continue
            previous = newest_by_pathway.get(candidate_run.pathway_id)
            if previous is None or (candidate_run.started_at, candidate_run.id) > (
                previous.started_at,
                previous.id,
            ):
                newest_by_pathway[candidate_run.pathway_id] = candidate_run
    comparisons = []
    any_missing = False
    for child in children:
        child_run: model.RunSummary | None = None
        run_parameters: model.ParameterDelta | None = None
        newest = newest_by_pathway.get(child.id)
        if newest is not None:
            loaded = store.load_run(newest.id)
            store.verify_run_evidence(loaded)
            child_run = model.run_summary_from_run(loaded)
            run_parameters = model.parameter_delta(
                record.declared_parameters, loaded.declared_parameters
            )
        else:
            any_missing = True
        delta = model.parameter_delta(pathway.parameters, child.parameters)
        comparisons.append(
            model.ChildComparison(
                pathway_id=child.id,
                reason=child.reason,
                source_branch_point_id=branch_point.id,
                parameters=delta,
                source_run=source_run,
                child_run=child_run,
                run_parameters=run_parameters,
                missing_evidence=child_run is None,
            )
        )

    if not children:
        state = model.ComparisonState.NO_BRANCH
    elif any_missing:
        state = model.ComparisonState.PARTIAL
    else:
        state = model.ComparisonState.READY
    return model.ComparisonResult(
        state=state,
        source_branch_point_id=branch_point.id,
        source_pathway_id=pathway.id,
        source_run=source_run,
        children=tuple(comparisons),
    )
