from __future__ import annotations

from .run_manifest_models import (
    ManifestErrorKind,
    RunId,
    RunManifest,
    RunManifestError,
    RunStorageContext,
    Sha256Digest,
)


def manifest_error(kind: ManifestErrorKind, detail: str) -> RunManifestError:
    return RunManifestError(kind=kind, detail=detail)


def require(condition: bool, kind: ManifestErrorKind, detail: str) -> None:
    if not condition:
        raise manifest_error(kind, detail)


def validate_initial(
    context: RunStorageContext, manifest: RunManifest, has_parent: bool
) -> None:
    require(
        manifest.revision == 1,
        ManifestErrorKind.VERSION_MISMATCH,
        "initial revision must be one",
    )
    require(
        not manifest.evidence_sealed and not manifest.sealed_evidence,
        ManifestErrorKind.SEALED,
        "initial manifests cannot claim sealed evidence",
    )
    require(
        manifest.shared_workspace.workspace_digest == context.workspace_digest,
        ManifestErrorKind.AUTHORITY_BOUNDARY,
        "manifest workspace digest does not match its physical workspace",
    )
    if not has_parent:
        require(
            manifest.parent_run_id is None,
            ManifestErrorKind.PARENT_MISMATCH,
            "child creation requires a parent reader",
        )


def validate_parent_access(
    context: RunStorageContext,
    parent_context: RunStorageContext,
    parent_writable: bool,
) -> None:
    require(
        not parent_writable,
        ManifestErrorKind.READ_ONLY,
        "parent must be read-only",
    )
    require(
        context.has_same_storage_as(parent_context),
        ManifestErrorKind.PARENT_MISMATCH,
        "child storage context differs from parent",
    )


def validate_parent_lineage(
    manifest: RunManifest, parent_manifest: RunManifest
) -> None:
    child_matches = (
        parent_manifest.evidence_sealed
        and manifest.parent_run_id == parent_manifest.run_id
        and manifest.lineage_root_run_id == parent_manifest.lineage_root_run_id
        and manifest.workflow_digest == parent_manifest.workflow_digest
        and manifest.parent_evidence_digests == parent_manifest.sealed_evidence
    )
    require(
        child_matches,
        ManifestErrorKind.PARENT_MISMATCH,
        "child lineage does not match parent",
    )


def validate_loaded(
    manifest: RunManifest,
    namespace: RunId,
    expected_workflow_digest: Sha256Digest,
    context: RunStorageContext,
) -> None:
    require(
        manifest.run_id == namespace,
        ManifestErrorKind.MALFORMED,
        "run_id differs from namespace",
    )
    require(
        manifest.workflow_digest == expected_workflow_digest,
        ManifestErrorKind.WORKFLOW_MISMATCH,
        "workflow digest differs from pinned value",
    )
    require(
        manifest.shared_workspace.workspace_digest == context.workspace_digest,
        ManifestErrorKind.AUTHORITY_BOUNDARY,
        "manifest is bound to another physical workspace",
    )


def validate_update(current: RunManifest, candidate: RunManifest) -> None:
    require(
        candidate.revision == current.revision + 1,
        ManifestErrorKind.VERSION_MISMATCH,
        "revision must advance by exactly one",
    )
    immutable = (
        candidate.run_id == current.run_id
        and candidate.parent_run_id == current.parent_run_id
        and candidate.lineage_root_run_id == current.lineage_root_run_id
        and candidate.workflow_digest == current.workflow_digest
        and candidate.inherited_canonical == current.inherited_canonical
        and candidate.shared_workspace == current.shared_workspace
        and candidate.parent_evidence_digests == current.parent_evidence_digests
        and candidate.evidence_sealed == current.evidence_sealed
        and candidate.sealed_evidence == current.sealed_evidence
    )
    require(
        immutable,
        ManifestErrorKind.IMMUTABLE_FIELD,
        "immutable manifest fields changed",
    )
