from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from core.continuation_lock_identity import (
    BoundedReadError,
    BoundedReadErrorKind,
    read_verified_bytes,
)

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

_MAX_RECEIPT_BYTES: Final = 1024 * 1024

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
    "sha256_file",
)


def sha256_file(path: Path) -> Sha256Digest:
    if path.is_symlink():
        raise AttemptReceiptError(AttemptReceiptErrorKind.UNSAFE_PATH, str(path))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return Sha256Digest(digest.hexdigest())


def artifact_file_receipt(path: Path) -> ArtifactFileReceipt:
    resolved = path.resolve()
    return ArtifactFileReceipt(
        path=str(resolved),
        sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        complete=True,
    )


def write_attempt_receipt(
    path: Path,
    receipt: Phase5AttemptReceipt,
    previous: Phase5AttemptReceipt | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise AttemptReceiptError(AttemptReceiptErrorKind.UNSAFE_PATH, str(path))
    payload = receipt.model_dump_json(indent=2).encode()
    if previous is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.STALE_TRANSITION, str(path)
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                _ = handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return

    lock = path.with_name(f".{path.name}.lock")
    try:
        lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.STALE_TRANSITION, str(path)
        ) from exc
    os.close(lock_descriptor)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    try:
        if load_attempt_receipt(path) != previous:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.STALE_TRANSITION, str(path)
            )
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def load_attempt_receipt(path: Path) -> Phase5AttemptReceipt:
    try:
        content = read_verified_bytes(path, _MAX_RECEIPT_BYTES)
        return Phase5AttemptReceipt.model_validate_json(content)
    except BoundedReadError as exc:
        kind = (
            AttemptReceiptErrorKind.MISSING
            if exc.kind is BoundedReadErrorKind.MISSING
            else AttemptReceiptErrorKind.UNSAFE_PATH
            if exc.kind
            in {
                BoundedReadErrorKind.UNSAFE,
                BoundedReadErrorKind.CHANGED,
            }
            else AttemptReceiptErrorKind.MALFORMED
        )
        raise AttemptReceiptError(kind, str(path)) from exc
    except (UnicodeError, ValidationError) as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.MALFORMED, f"{path}: {exc}"
        ) from exc


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
        marker_content = read_verified_bytes(marker_path, 4096)
        marker = Phase5ReservationMarker.model_validate_json(marker_content)
    except (BoundedReadError, UnicodeError, ValidationError) as exc:
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
            valid = (
                artifact.complete
                and artifact_path.is_file()
                and artifact_path.stat().st_size == artifact.size_bytes
                and sha256_file(artifact_path) == artifact.sha256
            )
        except OSError as exc:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.INTEGRITY_MISMATCH, artifact.path
            ) from exc
        if not valid:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.INTEGRITY_MISMATCH, artifact.path
            )
    accepted = _updated_receipt(receipt, receipt.custom_op_gate, receipt.review, True)
    write_attempt_receipt(path, accepted, receipt)
    try:
        current_marker = read_verified_bytes(marker_path, 4096)
    except BoundedReadError as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH, str(marker_path)
        ) from exc
    if current_marker != marker_content:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH, str(marker_path)
        )
    marker_path.unlink()
    return accepted
