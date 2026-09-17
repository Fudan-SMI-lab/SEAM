"""Descriptor-relative receipt publication on POSIX systems without procfs.

All names stay relative to an open parent descriptor, including lock cleanup.
Renaming or substituting the public directory cannot redirect a write.
"""
from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable

from core.phase5_attempt_models import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    Phase5AttemptReceipt,
)


def _read_at(directory: int, name: str) -> tuple[bytes, os.stat_result]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AttemptReceiptError(AttemptReceiptErrorKind.UNSAFE_PATH, name)
        data = handle.read(1024 * 1024 + 1)
        after = os.fstat(handle.fileno())
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if (
        len(data) > 1024 * 1024
        or _identity(before) != _identity(after)
        or _identity(after) != _identity(current)
    ):
        raise AttemptReceiptError(AttemptReceiptErrorKind.IDENTITY_MISMATCH, name)
    return data, current


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)


def _remove_owned_at(directory: int, name: str, expected: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if _identity(current) == _identity(expected):
            os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def _create_at(directory: int, name: str, data: bytes) -> os.stat_result:
    fd = os.open(
        name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
        0o600, dir_fd=directory,
    )
    with os.fdopen(fd, "wb") as handle:
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            _remove_owned_at(directory, name, os.fstat(handle.fileno()))
            raise
        return os.fstat(handle.fileno())


def write_receipt_at(
    directory: int,
    name: str,
    receipt: Phase5AttemptReceipt,
    previous: Phase5AttemptReceipt | None,
    require_parent: Callable[[], None],
) -> None:
    """Publish a complete receipt under an exclusive transition lock."""
    lock = f".{name}.lock"
    lock_identity = None
    temporary = f".{name}.{secrets.token_hex(16)}.tmp"
    temporary_identity = None
    backup = f".{name}.{secrets.token_hex(16)}.bak"
    backup_identity = None
    published = False
    try:
        if previous is not None:
            try:
                lock_identity = _create_at(directory, lock, secrets.token_bytes(32))
            except FileExistsError as exc:
                raise AttemptReceiptError(AttemptReceiptErrorKind.STALE_TRANSITION, name) from exc
            content, _ = _read_at(directory, name)
            if Phase5AttemptReceipt.model_validate_json(content) != previous:
                raise AttemptReceiptError(AttemptReceiptErrorKind.STALE_TRANSITION, name)
        require_parent()
        payload = receipt.model_dump_json(indent=2).encode()
        temporary_identity = _create_at(directory, temporary, payload)
        actual, identity = _read_at(directory, temporary)
        if actual != payload or _identity(identity) != _identity(temporary_identity):
            raise AttemptReceiptError(AttemptReceiptErrorKind.IDENTITY_MISMATCH, name)
        require_parent()
        if previous is None:
            try:
                os.link(
                    temporary, name, src_dir_fd=directory,
                    dst_dir_fd=directory, follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise AttemptReceiptError(AttemptReceiptErrorKind.STALE_TRANSITION, name) from exc
        else:
            # Save exact old bytes independently so a failed directory sync can
            # restore the last durable transition without following a pathname.
            backup_identity = _create_at(directory, backup, content)
            os.fsync(directory)
            current, _ = _read_at(directory, name)
            if current != content:
                raise AttemptReceiptError(AttemptReceiptErrorKind.STALE_TRANSITION, name)
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        published = True
        os.fsync(directory)
        require_parent()
    except Exception:
        if published and temporary_identity is not None:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if _identity(current) == _identity(temporary_identity):
                if backup_identity is not None:
                    os.replace(backup, name, src_dir_fd=directory, dst_dir_fd=directory)
                    backup_identity = None
                else:
                    _remove_owned_at(directory, name, temporary_identity)
                try:
                    os.fsync(directory)
                except OSError:
                    pass  # Preserve the original publication error.
        raise
    finally:
        for candidate, identity in (
            (temporary, temporary_identity), (backup, backup_identity), (lock, lock_identity)
        ):
            if identity is not None:
                try:
                    _remove_owned_at(directory, candidate, identity)
                except OSError:
                    pass
