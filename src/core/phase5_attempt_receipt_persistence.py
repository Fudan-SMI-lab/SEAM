from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from core.atomic_file import atomic_create_bytes, atomic_write_bytes
from core.continuation_lock_identity import (
    BoundedReadError,
    BoundedReadErrorKind,
    LockIdentity,
    lock_identity,
    read_verified_bytes,
    release_owned_file,
)
from core.phase5_attempt_models import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    Phase5AttemptReceipt,
    ReviewAcceptanceEvidence,
    ShellArtifactsReceipt,
    ShellAttemptExecution,
    ShellInvocation,
)
from core.run_outcome import ReviewOutcome

_MAX_RECEIPT_BYTES: Final = 1024 * 1024


def draft_attempt_receipt(
    execution: ShellAttemptExecution,
    invocation: ShellInvocation,
    artifacts: ShellArtifactsReceipt,
    exit_code: int,
) -> Phase5AttemptReceipt:
    reservation = execution.reservation
    return Phase5AttemptReceipt(
        run_id=reservation.run_id,
        reservation_nonce=reservation.reservation_nonce,
        attempt_id=reservation.attempt_id,
        attempt_number=reservation.attempt_number,
        invocation=invocation,
        backend=execution.backend,
        artifacts=artifacts,
        shell_exit_code=exit_code,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.NOT_RUN),
        review=ReviewAcceptanceEvidence(enabled=True, outcome=ReviewOutcome.UNKNOWN),
        complete=False,
        accepted=False,
    )


def _release_lock_best_effort(
    lock: Path,
    identity: LockIdentity,
    content: bytes,
) -> None:
    try:
        release_owned_file(lock, identity, content)
    except OSError:
        return


def write_attempt_receipt(
    path: Path,
    receipt: Phase5AttemptReceipt,
    previous: Phase5AttemptReceipt | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise AttemptReceiptError(AttemptReceiptErrorKind.UNSAFE_PATH, str(path))
    if os.name == "nt":
        parent_identity = lock_identity(path.parent.lstat())
        _write_attempt_receipt(
            path,
            receipt,
            previous,
            path.parent,
            parent_identity,
        )
        _require_parent_identity(path.parent, parent_identity)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor = os.open(path.parent, flags)
    try:
        parent_identity = lock_identity(os.fstat(parent_descriptor))
        _require_parent_identity(path.parent, parent_identity)
        anchored = Path(f"/proc/self/fd/{parent_descriptor}") / path.name
        _write_attempt_receipt(
            anchored,
            receipt,
            previous,
            path.parent,
            parent_identity,
        )
        _require_parent_identity(path.parent, parent_identity)
    finally:
        os.close(parent_descriptor)


def _write_attempt_receipt(
    path: Path,
    receipt: Phase5AttemptReceipt,
    previous: Phase5AttemptReceipt | None,
    public_parent: Path,
    parent_identity: LockIdentity,
) -> None:
    payload = receipt.model_dump_json(indent=2).encode()
    if previous is None:
        try:
            atomic_create_bytes(path, payload)
        except FileExistsError as exc:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.STALE_TRANSITION, str(path)
            ) from exc
        return

    lock = path.with_name(f".{path.name}.lock")
    lock_content = secrets.token_bytes(32)
    lock_file_identity: LockIdentity | None = None
    try:
        lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.STALE_TRANSITION, str(path)
        ) from exc
    try:
        lock_file_identity = lock_identity(os.fstat(lock_descriptor))
        with os.fdopen(lock_descriptor, "wb") as lock_handle:
            _ = lock_handle.write(lock_content)
            lock_handle.flush()
            os.fsync(lock_handle.fileno())
    except OSError:
        if lock_file_identity is None:
            os.close(lock_descriptor)
        else:
            release_owned_file(lock, lock_file_identity)
        raise
    try:
        if load_attempt_receipt(path) != previous:
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.STALE_TRANSITION, str(path)
            )
        _require_parent_identity(public_parent, parent_identity)
        atomic_write_bytes(path, payload)
    finally:
        _release_lock_best_effort(lock, lock_file_identity, lock_content)


def _require_parent_identity(parent: Path, expected: LockIdentity) -> None:
    try:
        current = lock_identity(parent.lstat())
    except OSError as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH,
            str(parent),
        ) from exc
    if (
        current != expected
        or not stat.S_ISDIR(current.mode)
        or bool(current.attributes & 0x400)
    ):
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH,
            str(parent),
        )


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
