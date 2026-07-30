from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pydantic import ValidationError

from core.continuation_lock_identity import (
    BoundedReadError,
    read_lock_path_snapshot,
    read_verified_bytes,
    release_owned_file,
)
from core.evidence_limits import MAX_EVIDENCE_FILE_BYTES

from core.phase5_attempt_models import (
    ArtifactFileReceipt,
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    BackendExecution,
    BackendKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    EnvironmentVariable,
    Phase5AttemptId,
    Phase5AttemptReceipt,
    Phase5AttemptReservation,
    Phase5ReservationMarker,
    ReviewAcceptanceEvidence,
    Sha256Digest,
    ShellArtifactsReceipt,
    ShellAttemptExecution,
    ShellInvocation,
    is_attempt_acceptable,
)
from core.phase5_attempt_authority import (
    Phase5AttemptAuthority,
    receipt_matches_authority,
)
from core.phase5_attempt_receipt_persistence import (
    load_attempt_receipt,
    write_attempt_receipt,
)
from core.run_manifest_models import RunManifestError
from core.run_manifest_paths import read_real_file

__all__ = (
    "ArtifactFileReceipt",
    "AttemptReceiptError",
    "AttemptReceiptErrorKind",
    "BackendExecution",
    "BackendKind",
    "CustomOpGateEvidence",
    "CustomOpGateStatus",
    "EnvironmentVariable",
    "Phase5AttemptId",
    "Phase5AttemptAuthority",
    "Phase5AttemptReceipt",
    "Phase5AttemptReservation",
    "ReviewAcceptanceEvidence",
    "ShellArtifactsReceipt",
    "ShellAttemptExecution",
    "ShellInvocation",
    "accept_attempt_receipt",
    "artifact_file_receipt",
    "finalize_attempt_receipt",
    "load_attempt_receipt",
    "receipt_matches_authority",
    "require_receipt_artifact_integrity",
    "sha256_file",
)


def sha256_file(path: Path) -> Sha256Digest:
    try:
        content = read_verified_bytes(path, MAX_EVIDENCE_FILE_BYTES)
    except BoundedReadError as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.UNSAFE_PATH,
            str(path),
        ) from exc
    return Sha256Digest(hashlib.sha256(content).hexdigest())


def artifact_file_receipt(path: Path) -> ArtifactFileReceipt:
    try:
        content = read_verified_bytes(path, MAX_EVIDENCE_FILE_BYTES)
    except BoundedReadError as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.UNSAFE_PATH,
            str(path),
        ) from exc
    resolved = Path(os.path.abspath(path))
    return ArtifactFileReceipt(
        path=str(resolved),
        sha256=Sha256Digest(hashlib.sha256(content).hexdigest()),
        size_bytes=len(content),
        complete=True,
    )


def _updated_receipt(
    receipt: Phase5AttemptReceipt,
    custom_op_gate: CustomOpGateEvidence,
    review: ReviewAcceptanceEvidence,
    accepted: bool,
) -> Phase5AttemptReceipt:
    return Phase5AttemptReceipt(
        run_id=receipt.run_id,
        reservation_nonce=receipt.reservation_nonce,
        attempt_id=receipt.attempt_id,
        attempt_number=receipt.attempt_number,
        invocation=receipt.invocation,
        backend=receipt.backend,
        artifacts=receipt.artifacts,
        shell_exit_code=receipt.shell_exit_code,
        custom_op_gate=custom_op_gate,
        review=review,
        complete=True,
        accepted=accepted,
    )


def finalize_attempt_receipt(
    path: Path,
    *,
    custom_op_gate: CustomOpGateEvidence,
    review: ReviewAcceptanceEvidence,
) -> Phase5AttemptReceipt:
    receipt = load_attempt_receipt(path)
    if receipt.accepted:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.STALE_TRANSITION, str(receipt.attempt_id)
        )
    if receipt.complete:
        if receipt.custom_op_gate == custom_op_gate and receipt.review == review:
            return receipt
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.STALE_TRANSITION, str(receipt.attempt_id)
        )
    finalized = _updated_receipt(receipt, custom_op_gate, review, False)
    write_attempt_receipt(path, finalized, receipt)
    return finalized


def accept_attempt_receipt(
    path: Path, authority: Phase5AttemptAuthority
) -> Phase5AttemptReceipt:
    receipt = load_attempt_receipt(path)
    if str(path.resolve()) != authority.receipt_path or not receipt_matches_authority(
        receipt, authority
    ):
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH, str(authority.attempt_id)
        )
    marker_path = path.parent / f".{receipt.attempt_id}.reserved"
    try:
        marker_snapshot = read_lock_path_snapshot(marker_path, 4097)
        if len(marker_snapshot.content) > 4096:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.IDENTITY_MISMATCH,
                str(marker_path),
            )
        marker = Phase5ReservationMarker.model_validate_json(marker_snapshot.content)
    except (OSError, UnicodeError, ValidationError) as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH, str(marker_path)
        ) from exc
    if (
        marker.run_id != receipt.run_id
        or marker.attempt_id != receipt.attempt_id
        or marker.reservation_nonce != receipt.reservation_nonce
    ):
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH, str(marker_path)
        )
    if not is_attempt_acceptable(receipt):
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.NOT_ACCEPTABLE, str(receipt.attempt_id)
        )
    require_receipt_artifact_integrity(path, receipt)
    accepted = _updated_receipt(receipt, receipt.custom_op_gate, receipt.review, True)
    write_attempt_receipt(path, accepted, receipt)
    try:
        release_owned_file(
            marker_path,
            marker_snapshot.identity,
            marker_snapshot.content,
        )
    except OSError:
        return accepted
    return accepted


def require_receipt_artifact_integrity(
    path: Path,
    receipt: Phase5AttemptReceipt,
) -> None:
    workspace_root = path.parents[3]
    for artifact in (
        receipt.artifacts.stdout,
        receipt.artifacts.stderr,
        receipt.artifacts.metadata,
        receipt.custom_op_gate.report,
    ):
        if artifact is None:
            continue
        artifact_path = Path(artifact.path)
        try:
            _, artifact_content = read_real_file(
                artifact_path,
                workspace_root,
                MAX_EVIDENCE_FILE_BYTES,
            )
            valid = (
                artifact.complete
                and len(artifact_content) == artifact.size_bytes
                and Sha256Digest(hashlib.sha256(artifact_content).hexdigest())
                == artifact.sha256
            )
        except (OSError, RunManifestError) as exc:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.INTEGRITY_MISMATCH, artifact.path
            ) from exc
        if not valid:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.INTEGRITY_MISMATCH, artifact.path
            )
