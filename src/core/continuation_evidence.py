from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .artifact_store import ArtifactStore
from .continuation_evidence_authority import (
    verify_external_evidence_root,
    verify_parent_evidence,
)
from .continuation_evidence_io import (
    allocate_child_evidence_namespace,
    archive_migration_reports,
    snapshot_project_baseline,
    verify_record,
    write_exclusive_record,
)
from .continuation_evidence_lineage import build_child_manifest
from .continuation_evidence_models import (
    ChildEvidenceRequest as ChildEvidenceRequest,
    ContinuationEvidenceError as ContinuationEvidenceError,
    ContinuationEvidenceErrorKind as ContinuationEvidenceErrorKind,
    ContinuationEvidenceRoot,
    MigrationReportArchive,
    PreparedChildEvidence as PreparedChildEvidence,
    ProjectBaseline,
    VerifiedChildEvidence as VerifiedChildEvidence,
)
from .continuation_models import ResolvedTerminalParent
from .run_manifest import (
    ManifestErrorKind,
    RunId,
    RunManifest,
    RunManifestError,
    RunManifestStore,
)
from .run_manifest_paths import digest_inventory
from .run_outcome import TerminalAnchor


_error = ContinuationEvidenceError


def prepare_child_evidence(
    parent: ResolvedTerminalParent,
    request: ChildEvidenceRequest,
) -> PreparedChildEvidence:
    parent_authority = verify_parent_evidence(parent, request)
    context = parent_authority.context
    parent_store = parent_authority.store
    try:
        child_store = RunManifestStore.create(
            context,
            build_child_manifest(parent, request),
            parent_store,
        )
    except RunManifestError as exc:
        kind = (
            ContinuationEvidenceErrorKind.NAMESPACE_EXISTS
            if exc.kind is ManifestErrorKind.DUPLICATE_RUN
            else ContinuationEvidenceErrorKind.NAMESPACE_FAILED
        )
        raise _error(kind, "child report namespace allocation failed") from exc
    child_run_id = RunId(request.continuation.child_run_id)
    report_dir = context.authoritative_root / str(child_run_id)
    try:
        namespace = allocate_child_evidence_namespace(report_dir)
    except FileExistsError as exc:
        raise _error(
            ContinuationEvidenceErrorKind.NAMESPACE_EXISTS,
            "child trace or artifact namespace already exists",
        ) from exc
    except OSError as exc:
        raise _error(
            ContinuationEvidenceErrorKind.NAMESPACE_FAILED,
            "child trace or artifact namespace allocation failed",
        ) from exc
    try:
        project_snapshot = snapshot_project_baseline(context.workspace_root)
        baseline = ProjectBaseline(
            parent_run_id=str(parent.run_id),
            child_run_id=str(child_run_id),
            files=project_snapshot.files,
            links=project_snapshot.links,
        )
        baseline_receipt = write_exclusive_record(namespace.baseline_path, baseline)
    except (OSError, RunManifestError, ValidationError) as exc:
        raise _error(
            ContinuationEvidenceErrorKind.SNAPSHOT_FAILED,
            "lineage-shared project baseline could not be recorded",
        ) from exc
    try:
        archive_files = archive_migration_reports(
            context.workspace_root,
            namespace.migration_archive_dir,
        )
        archive = MigrationReportArchive(
            parent_run_id=str(parent.run_id),
            child_run_id=str(child_run_id),
            files=archive_files,
        )
        archive_receipt = write_exclusive_record(
            namespace.migration_archive_manifest_path,
            archive,
        )
    except (OSError, RunManifestError, ValidationError) as exc:
        raise _error(
            ContinuationEvidenceErrorKind.ARCHIVE_FAILED,
            "mutable migration_reports could not be archived",
        ) from exc
    parent_store = verify_parent_evidence(
        parent,
        request,
        parent_authority.report_inventory,
    ).store
    try:
        artifact_store = ArtifactStore.create_exclusive(
            str(context.workspace_root),
            str(child_run_id),
        )
    except FileExistsError as exc:
        raise _error(
            ContinuationEvidenceErrorKind.WORKING_NAMESPACE_EXISTS,
            "child working artifact namespace already exists",
        ) from exc
    except OSError as exc:
        raise _error(
            ContinuationEvidenceErrorKind.NAMESPACE_FAILED,
            "child working artifact namespace creation failed",
        ) from exc
    return PreparedChildEvidence(
        request=request,
        parent=parent,
        context=context,
        parent_store=parent_store,
        child_store=child_store,
        artifact_store=artifact_store,
        namespace=namespace,
        project_baseline=baseline,
        migration_archive=archive,
        baseline_receipt=baseline_receipt,
        archive_manifest_receipt=archive_receipt,
        parent_report_inventory=parent_authority.report_inventory,
    )


def seal_child_evidence(
    prepared: PreparedChildEvidence,
    *,
    terminal_anchor: TerminalAnchor | None = None,
) -> RunManifest:
    try:
        current = prepared.child_store.read()
        if current.evidence_sealed:
            return current
        if terminal_anchor is not None and current.terminal_anchor != terminal_anchor:
            current = prepared.child_store.write(
                current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "terminal_anchor": terminal_anchor,
                    }
                )
            )
        precontinuation = digest_inventory(
            prepared.namespace.precontinuation_dir,
            prepared.namespace.report_dir,
        )
        trace = (
            digest_inventory(
                prepared.namespace.trace_dir,
                prepared.namespace.report_dir,
            )
            if prepared.namespace.trace_dir.exists()
            else ()
        )
        root = ContinuationEvidenceRoot(
            child_run_id=str(current.run_id),
            precontinuation_files=precontinuation,
            trace_files=trace,
        )
        _ = write_exclusive_record(
            Path(prepared.artifact_store.validated_dir)
            / "continuation_evidence_root.json",
            root,
        )
        return prepared.child_store.seal_working_evidence(prepared.artifact_store)
    except (OSError, RunManifestError, ValidationError) as exc:
        raise _error(
            ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
            "child working evidence could not be sealed",
        ) from exc


def verify_final_child_evidence(
    prepared: PreparedChildEvidence,
) -> VerifiedChildEvidence:
    parent_store = verify_parent_evidence(
        prepared.parent,
        prepared.request,
        prepared.parent_report_inventory,
    ).store
    try:
        parent_manifest = parent_store.read()
        child_manifest = prepared.child_store.read()
        archived = digest_inventory(
            prepared.namespace.migration_archive_dir,
            prepared.namespace.precontinuation_dir,
        )
        verify_external_evidence_root(
            prepared.namespace.report_dir,
            child_manifest,
            required=True,
        )
    except ContinuationEvidenceError:
        raise
    except (OSError, RunManifestError) as exc:
        raise _error(
            ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
            "final child evidence digest verification failed",
        ) from exc
    records_match = verify_record(
        prepared.namespace.baseline_path,
        prepared.baseline_receipt,
    ) and verify_record(
        prepared.namespace.migration_archive_manifest_path,
        prepared.archive_manifest_receipt,
    )
    lineage_matches = (
        child_manifest.evidence_sealed
        and child_manifest.parent_run_id == parent_manifest.run_id
        and child_manifest.parent_evidence_digests == parent_manifest.sealed_evidence
        and child_manifest.inherited_canonical == prepared.request.inherited_canonical
    )
    if (
        not records_match
        or not lineage_matches
        or archived != prepared.migration_archive.files
    ):
        raise _error(
            ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
            "final child evidence is incomplete or changed",
        )
    return VerifiedChildEvidence(
        namespace=prepared.namespace,
        parent_manifest=parent_manifest,
        child_manifest=child_manifest,
    )
