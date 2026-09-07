"""Focused crash-safe publisher for the direct-run root run-manifest.

This service implements the outcome-neutral ``--seal-manifest`` contract for
Wave-1 Todo 2 of the v1.2.1 remediation workplan. It builds the sealed
authority entirely in task-owned staging, publishes the complete
``sealed-artifacts`` directory first (fsynced), then atomically publishes
``run-manifest.v1.json`` LAST as the only continuation-authority commit
marker, reopens and verifies every sealed digest, and finally writes the
independent ``manifest-sealing.v1.json`` sidecar.

Any publication fault is converted into a typed failed result; the
exception never escapes into ``RunOutcome``. On failure only task-owned
staging paths are removed and pre-existing report data is preserved.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.atomic_directory import rename_directory_no_replace
from core.atomic_file import atomic_create_bytes, atomic_write_bytes
from core.continuation_lock_identity import fsync_parent
from core.manifest_sealing_models import (
    MANIFEST_SEALING_FILENAME,
    ManifestSealingError,
    ManifestSealingErrorKind,
    ManifestSealingFaultHooks,
    ManifestSealingResult,
    ManifestSealingStatus,
)
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    close_directory_identity,
    directory_lock_identity,
    empty_directory_identity,
    release_owned_directory,
)
from core.run_manifest import (
    RUN_MANIFEST_FILENAME,
    ResourceReference,
    RunId,
    RunManifest,
    RunManifestStore,
    RunStorageContext,
    Sha256Digest,
    SharedWorkspaceMarker,
)
from core.run_manifest_inventory import digest_inventory
from core.run_manifest_io import read_manifest
from core.run_manifest_models import ManifestErrorKind, RunManifestError
from core.run_outcome import TerminalAnchor

_SEALED_EVIDENCE_DIR = "sealed-artifacts"


def seal_root_manifest(
    *,
    report_dir: Path,
    run_id: str,
    project_dir: Path,
    workflow_path: Path,
    artifact_store: ArtifactStore | None,
    terminal_anchor: TerminalAnchor,
    hooks: ManifestSealingFaultHooks | None = None,
) -> ManifestSealingResult:
    """Seal a root run-manifest atomically for direct-run continuation eligibility."""
    bound_hooks = hooks if hooks is not None else ManifestSealingFaultHooks()
    sidecar_path = report_dir / MANIFEST_SEALING_FILENAME
    manifest_path = report_dir / RUN_MANIFEST_FILENAME
    evidence_path = report_dir / _SEALED_EVIDENCE_DIR

    if manifest_path.exists():
        return _publish_failed(
            sidecar_path=sidecar_path,
            run_id=run_id,
            kind=ManifestSealingErrorKind.AUTHORITY_ALREADY_PRESENT,
            detail="a root run-manifest.v1.json already exists; refusing to overwrite authority",
            manifest_path=None,
            evidence_path=None,
        )

    staging_root = report_dir.parent / f".manifest-sealing.{secrets.token_hex(8)}.tmp"
    staging_identity: DirectoryLockIdentity | None = None
    evidence_identity: DirectoryLockIdentity | None = None
    phase = ManifestSealingErrorKind.STAGING_FAILED
    published_evidence = False
    manifest_committed = False
    try:
        staging_root.mkdir(mode=0o700)
        staging_identity = empty_directory_identity(staging_root)
        staging_context = RunStorageContext.bind(staging_root, project_dir)
        manifest = _build_root_manifest(
            run_id=run_id,
            workflow_path=workflow_path,
            terminal_anchor=terminal_anchor,
            staging_context=staging_context,
        )
        store = RunManifestStore.create(staging_context, manifest)
        if artifact_store is not None:
            _ = store.seal_working_evidence(artifact_store)
        staging_run_dir = staging_root / run_id
        manifest_bytes = (staging_run_dir / RUN_MANIFEST_FILENAME).read_bytes()

        evidence_source = staging_run_dir / _SEALED_EVIDENCE_DIR
        evidence_identity = directory_lock_identity(evidence_source, retain=True)
        phase = ManifestSealingErrorKind.EVIDENCE_PUBLISH_FAILED
        bound_hooks.before_evidence_publish()
        rename_directory_no_replace(evidence_source, evidence_path)
        published_evidence = True
        fsync_parent(evidence_path)
        fsync_parent(report_dir)

        phase = ManifestSealingErrorKind.MANIFEST_PUBLISH_FAILED
        bound_hooks.before_manifest_commit()
        atomic_create_bytes(manifest_path, manifest_bytes)
        fsync_parent(manifest_path)
        manifest_committed = True

        phase = ManifestSealingErrorKind.VERIFICATION_FAILED
        _verify_sealed_authority(manifest_path, evidence_path, report_dir)

        phase = ManifestSealingErrorKind.SIDECAR_PUBLISH_FAILED
        bound_hooks.before_sidecar_publish()
        result = ManifestSealingResult(
            status=ManifestSealingStatus.SUCCEEDED,
            requested=True,
            continuation_eligible=True,
            run_id=run_id,
            sidecar_path=sidecar_path,
            manifest_path=manifest_path,
            evidence_dir_path=evidence_path,
        )
        _write_sidecar(sidecar_path, result)
        return result
    except Exception as exc:  # noqa: BLE001 - outcome-neutral service boundary
        if published_evidence and not manifest_committed and evidence_identity is not None:
            _ = _release_owned(evidence_path, evidence_identity)
        return _publish_failed(
            sidecar_path=sidecar_path,
            run_id=run_id,
            kind=phase,
            detail=str(exc),
            manifest_path=manifest_path if manifest_committed else None,
            evidence_path=evidence_path if manifest_committed else None,
        )
    finally:
        if staging_identity is not None:
            _ = _release_owned(staging_root, staging_identity)
        if evidence_identity is not None:
            close_directory_identity(evidence_identity)


def record_not_requested(*, report_dir: Path, run_id: str) -> ManifestSealingResult:
    """Record a ``not_requested`` sealing result and publish its sidecar.

    The ``not_requested`` disposition is independently true regardless of
    whether the status sidecar can be written, so a sidecar publication fault
    is swallowed and the typed ``not_requested`` result is still returned to
    the caller (the sidecar is one observability channel; the in-memory result
    is the other).
    """
    sidecar_path = report_dir / MANIFEST_SEALING_FILENAME
    result = ManifestSealingResult(
        status=ManifestSealingStatus.NOT_REQUESTED,
        requested=False,
        continuation_eligible=False,
        run_id=run_id,
        sidecar_path=sidecar_path,
    )
    _write_sidecar_best_effort(sidecar_path, result)
    return result


def _write_sidecar(sidecar_path: Path, result: ManifestSealingResult) -> None:
    atomic_write_bytes(sidecar_path, result.to_sidecar_json().encode("utf-8"))


def _write_sidecar_best_effort(
    sidecar_path: Path, result: ManifestSealingResult
) -> None:
    # The sidecar is a status report, not authority; a publication fault is
    # surfaced via the in-memory result rather than raised into the caller.
    try:
        _write_sidecar(sidecar_path, result)
    except OSError:
        pass


def _build_root_manifest(
    *,
    run_id: str,
    workflow_path: Path,
    terminal_anchor: TerminalAnchor,
    staging_context: RunStorageContext,
) -> RunManifest:
    workflow_bytes = workflow_path.read_bytes()
    digest = Sha256Digest(hashlib.sha256(workflow_bytes).hexdigest())
    rid = RunId(run_id)
    return RunManifest(
        run_id=rid,
        parent_run_id=None,
        lineage_root_run_id=rid,
        revision=1,
        terminal_anchor=terminal_anchor,
        workflow_digest=digest,
        inherited_canonical=(),
        shared_workspace=SharedWorkspaceMarker(
            workspace_digest=staging_context.workspace_digest
        ),
        resource_references=(
            ResourceReference(kind="environment", reference_id=f"resource-{rid}"),
        ),
        parent_evidence_digests=(),
        evidence_sealed=False,
        sealed_evidence=(),
    )


def _verify_sealed_authority(
    manifest_path: Path,
    evidence_path: Path,
    report_dir: Path,
) -> None:
    manifest = read_manifest(manifest_path)
    if not manifest.evidence_sealed:
        raise RunManifestError(
            ManifestErrorKind.EVIDENCE_MUTATION,
            "published manifest is not evidence-sealed",
        )
    inventory = digest_inventory(evidence_path, report_dir)
    if inventory != manifest.sealed_evidence:
        raise RunManifestError(
            ManifestErrorKind.EVIDENCE_MUTATION,
            "published sealed evidence differs from the manifest digest inventory",
        )


def _release_owned(path: Path, identity: DirectoryLockIdentity) -> str | None:
    """Release a task-owned directory only if its identity still matches.

    Returns ``None`` on success or already-missing, or the error string when
    ownership could not be proven (the directory was replaced or is unsafe);
    in the latter case the directory is left untouched. Mirrors the release
    pattern in :mod:`core.run_manifest_evidence_seal`.
    """
    try:
        release_owned_directory(path, identity)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return str(exc)
    return None


def _publish_failed(
    *,
    sidecar_path: Path,
    run_id: str,
    kind: ManifestSealingErrorKind,
    detail: str,
    manifest_path: Path | None,
    evidence_path: Path | None,
) -> ManifestSealingResult:
    result = ManifestSealingResult(
        status=ManifestSealingStatus.FAILED,
        requested=True,
        continuation_eligible=False,
        run_id=run_id,
        sidecar_path=sidecar_path,
        manifest_path=manifest_path,
        evidence_dir_path=evidence_path,
        error=ManifestSealingError(kind, detail),
    )
    _write_sidecar_best_effort(sidecar_path, result)
    return result
