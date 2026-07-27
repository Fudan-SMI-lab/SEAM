from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

from typing_extensions import assert_never

from .continuation_models import (
    ContinuationError,
    ContinuationErrorKind,
    ResolvedAuthority,
    ResolvedTerminalParent,
    RunSummaryDocument,
    TerminalParentStatus,
)
from .continuation_paths import (
    PathKind,
    canonical_existing_path,
    parse_explicit_summary,
)
from .continuation_resource import open_existing_resource_manifest
from .resource_manifest import ResourceManifestIdentity
from .run_manifest import (
    ManifestErrorKind,
    RunId,
    RunManifest,
    RunManifestError,
    RunManifestStore,
    RunStorageContext,
    Sha256Digest,
)


def _error(kind: ContinuationErrorKind, detail: str) -> ContinuationError:
    return ContinuationError(kind=kind, detail=detail)


def _terminal_status(summary: RunSummaryDocument) -> TerminalParentStatus:
    try:
        return TerminalParentStatus(summary.overall_status)
    except ValueError as exc:
        raise _error(
            ContinuationErrorKind.STATUS_INELIGIBLE,
            "parent summary status must be exactly PASS or FAIL",
        ) from exc


class _TerminalExpectation(NamedTuple):
    anchor_status: str
    lifecycle_statuses: frozenset[str]
    phase_statuses: frozenset[str]


def _terminal_expectation(status: TerminalParentStatus) -> _TerminalExpectation:
    match status:
        case TerminalParentStatus.PASS:
            return _TerminalExpectation(
                "passed",
                frozenset({"passed", "passed_with_reviews"}),
                frozenset({"passed", "skipped"}),
            )
        case TerminalParentStatus.FAIL:
            return _TerminalExpectation(
                "failed",
                frozenset({"failed"}),
                frozenset({"passed", "failed", "skipped"}),
            )
        case _ as unreachable:
            assert_never(unreachable)


def _require_summary_identity(summary_path: Path, summary: RunSummaryDocument) -> Path:
    report_dir = canonical_existing_path(
        summary_path.parent,
        PathKind.DIRECTORY,
        ContinuationErrorKind.UNSAFE_SUMMARY_PATH,
    )
    claimed_report = canonical_existing_path(
        Path(summary.output_dir),
        PathKind.DIRECTORY,
        ContinuationErrorKind.RUN_ID_MISMATCH,
    )
    if claimed_report != report_dir or report_dir.name != summary.run_id:
        raise _error(
            ContinuationErrorKind.RUN_ID_MISMATCH,
            "summary run identity differs from its report namespace",
        )
    return report_dir


def _require_phase_anchor(
    summary: RunSummaryDocument,
    anchor_phase: str,
    expectation: _TerminalExpectation,
) -> None:
    if not summary.phases:
        raise _error(
            ContinuationErrorKind.INCOMPLETE_PARENT,
            "terminal parent summary has no executed phases",
        )
    complete_statuses = {"passed", "failed", "skipped"}
    if any(phase.status not in complete_statuses for phase in summary.phases):
        raise _error(
            ContinuationErrorKind.INCOMPLETE_PARENT,
            "terminal parent summary contains an incomplete phase",
        )
    if any(phase.status not in expectation.phase_statuses for phase in summary.phases):
        raise _error(
            ContinuationErrorKind.INCOMPLETE_PARENT,
            "terminal parent summary contradicts its terminal outcome",
        )
    anchored = tuple(
        phase for phase in summary.phases if phase.phase_id == anchor_phase
    )
    if len(anchored) != 1:
        raise _error(
            ContinuationErrorKind.ANCHOR_INVALID,
            "authoritative terminal anchor is absent from the executed phases",
        )
    if anchored[0].status == expectation.anchor_status:
        return
    raise _error(
        ContinuationErrorKind.ANCHOR_INVALID,
        "terminal anchor status conflicts with the parent outcome",
    )


def _open_run_manifest(
    context: RunStorageContext,
    run_id: RunId,
    workflow_digest: Sha256Digest,
) -> RunManifest:
    try:
        manifest = RunManifestStore.open_readonly(
            context, run_id, workflow_digest
        ).read()
    except RunManifestError as exc:
        match exc.kind:
            case ManifestErrorKind.WORKFLOW_MISMATCH:
                raise _error(
                    ContinuationErrorKind.WORKFLOW_MISMATCH,
                    f"authoritative run manifest is invalid: {exc.kind.value}",
                ) from exc
            case (
                ManifestErrorKind.DUPLICATE_RUN
                | ManifestErrorKind.MISSING_MANIFEST
                | ManifestErrorKind.MALFORMED
                | ManifestErrorKind.PARENT_MISMATCH
                | ManifestErrorKind.READ_ONLY
                | ManifestErrorKind.VERSION_MISMATCH
                | ManifestErrorKind.IMMUTABLE_FIELD
                | ManifestErrorKind.EVIDENCE_MUTATION
                | ManifestErrorKind.WRITE_INTERRUPTED
                | ManifestErrorKind.AUTHORITY_BOUNDARY
                | ManifestErrorKind.CONTAINMENT
                | ManifestErrorKind.CONCURRENT_WRITE
                | ManifestErrorKind.SEALED
            ):
                raise _error(
                    ContinuationErrorKind.AUTHORITY_INVALID,
                    f"authoritative run manifest is invalid: {exc.kind.value}",
                ) from exc
            case _ as unreachable:
                assert_never(unreachable)
    if not manifest.evidence_sealed:
        raise _error(
            ContinuationErrorKind.AUTHORITY_INVALID,
            "authoritative run evidence is not sealed",
        )
    return manifest


def resolve_authority(summary_path: Path) -> ResolvedAuthority:
    canonical_summary, summary = parse_explicit_summary(summary_path)
    status = _terminal_status(summary)
    expectation = _terminal_expectation(status)
    report_dir = _require_summary_identity(canonical_summary, summary)
    output_project = canonical_existing_path(
        Path(summary.temp_dir),
        PathKind.DIRECTORY,
        ContinuationErrorKind.OUTPUT_PROJECT_MISMATCH,
    )
    workflow_path = canonical_existing_path(
        Path(summary.workflow_path),
        PathKind.FILE,
        ContinuationErrorKind.WORKFLOW_MISMATCH,
    )
    workflow_digest = Sha256Digest(
        hashlib.sha256(workflow_path.read_bytes()).hexdigest()
    )
    try:
        context = RunStorageContext.bind(report_dir.parent, output_project)
    except RunManifestError as exc:
        raise _error(
            ContinuationErrorKind.AUTHORITY_INVALID,
            f"authoritative storage context is invalid: {exc.kind.value}",
        ) from exc
    run_id = RunId(summary.run_id)
    run_manifest = _open_run_manifest(context, run_id, workflow_digest)
    _require_phase_anchor(
        summary,
        str(run_manifest.terminal_anchor.phase_id),
        expectation,
    )
    identity = ResourceManifestIdentity(
        run_id=summary.run_id,
        workflow_digest=str(workflow_digest),
        workspace_digest=str(context.workspace_digest),
    )
    resource_manifest = open_existing_resource_manifest(report_dir, identity)
    lifecycle = tuple(
        fact.value
        for fact in resource_manifest.facts
        if fact.name == "lifecycle.status"
    )
    if (
        not resource_manifest.sealed
        or not lifecycle
        or lifecycle[-1] not in expectation.lifecycle_statuses
    ):
        raise _error(
            ContinuationErrorKind.AUTHORITY_INVALID,
            "resource lifecycle does not authorize the summary outcome",
        )
    return ResolvedAuthority(
        parent=ResolvedTerminalParent(
            run_id=run_id,
            status=status,
            output_project=output_project,
            workflow_path=workflow_path,
            workflow_digest=workflow_digest,
            terminal_anchor=run_manifest.terminal_anchor,
            run_manifest=run_manifest,
            resource_manifest=resource_manifest,
        ),
        authoritative_root=report_dir.parent,
        workspace_digest=context.workspace_digest,
    )


def resolve_terminal_parent(summary_path: Path) -> ResolvedTerminalParent:
    return resolve_authority(summary_path).parent
