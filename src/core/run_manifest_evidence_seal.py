from __future__ import annotations

import os
import secrets
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.atomic_directory import rename_directory_no_replace
from core.continuation_lock_identity import fsync_parent
from core.evidence_limits import EvidenceBudget
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    close_directory_identity,
    empty_directory_identity,
    release_owned_directory,
)
from core.phase5_attempt_models import AttemptReceiptError, AttemptReceiptErrorKind
from core.phase5_attempt_receipt import (
    load_attempt_receipt,
    receipt_matches_authority,
    require_receipt_artifact_integrity,
)
from core.run_manifest_inventory import digest_inventory
from core.run_manifest_models import EvidenceDigest, ManifestErrorKind, RunManifestError
from core.run_manifest_paths import copy_real_tree_into
from core.run_manifest_validation import manifest_error


def seal_evidence(
    artifact_store: ArtifactStore,
    workspace_root: Path,
    run_dir: Path,
) -> tuple[EvidenceDigest, ...]:
    with artifact_store.phase5_transaction():
        return _seal_evidence(artifact_store, workspace_root, run_dir)


def _seal_evidence(
    artifact_store: ArtifactStore,
    workspace_root: Path,
    run_dir: Path,
) -> tuple[EvidenceDigest, ...]:
    sealed_dir = run_dir / "sealed-artifacts"
    source = Path(artifact_store.artifact_dir)
    _require_phase5_authority(artifact_store)
    expected = digest_inventory(source, workspace_root)
    if not sealed_dir.exists():
        _copy_seal(artifact_store, workspace_root, run_dir, sealed_dir)
    current = digest_inventory(source, workspace_root)
    sealed = digest_inventory(sealed_dir, run_dir)
    if current != expected or sealed != expected:
        raise manifest_error(
            ManifestErrorKind.EVIDENCE_MUTATION,
            "sealed evidence differs from the stable working artifact source",
        )
    return sealed


def _copy_seal(
    artifact_store: ArtifactStore,
    workspace_root: Path,
    run_dir: Path,
    sealed_dir: Path,
) -> None:
    staging = run_dir / f".sealed-artifacts.{secrets.token_hex(8)}.tmp"
    staging.mkdir(mode=0o700)
    staging_identity = empty_directory_identity(staging)
    published = False
    try:
        copy_real_tree_into(Path(artifact_store.artifact_dir), workspace_root, staging)
        _require_phase5_authority(artifact_store)
        rename_directory_no_replace(staging, sealed_dir)
        published = True
        fsync_parent(sealed_dir)
    except RunManifestError:
        _ = _release_staging(sealed_dir if published else staging, staging_identity)
        raise
    except OSError as exc:
        cleanup_error = _release_staging(
            sealed_dir if published else staging,
            staging_identity,
        )
        cleanup_detail = (
            f"; evidence seal cleanup failed: {cleanup_error}"
            if cleanup_error is not None
            else ""
        )
        raise manifest_error(
            ManifestErrorKind.WRITE_INTERRUPTED,
            f"evidence seal interrupted: {exc}{cleanup_detail}",
        ) from exc
    close_directory_identity(staging_identity)


def _release_staging(path: Path, identity: DirectoryLockIdentity) -> str | None:
    try:
        release_owned_directory(path, identity)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return str(exc)
    return None


def _require_phase5_authority(artifact_store: ArtifactStore) -> None:
    try:
        artifact_root = Path(artifact_store.artifact_dir)
        expected = set(artifact_store.accepted_phase5_receipt_paths())
        observed: set[str] = set()
        if artifact_root.exists():
            budget = EvidenceBudget()
            for directory, child_directories, filenames in os.walk(artifact_root):
                current = Path(directory)
                relative = current.relative_to(artifact_root)
                for name in (*child_directories, *filenames):
                    budget.charge(relative / name)
                for name in filenames:
                    if not name.endswith(".receipt.json"):
                        continue
                    receipt_path = current / name
                    receipt = load_attempt_receipt(receipt_path)
                    if receipt.accepted:
                        observed.add(str(receipt_path.resolve()))
                        authority = artifact_store.phase5_attempt_authority(
                            str(receipt_path)
                        )
                        if authority is None or not receipt_matches_authority(
                            receipt, authority
                        ):
                            raise AttemptReceiptError(
                                AttemptReceiptErrorKind.IDENTITY_MISMATCH,
                                str(receipt.attempt_id),
                            )
                        require_receipt_artifact_integrity(receipt_path, receipt)
        if observed != expected:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.IDENTITY_MISMATCH,
                "accepted Phase 5 receipt set changed before sealing",
            )
    except (AttemptReceiptError, OSError) as exc:
        raise manifest_error(
            ManifestErrorKind.EVIDENCE_MUTATION,
            f"accepted Phase 5 evidence lost its issuing authority: {exc}",
        ) from exc
