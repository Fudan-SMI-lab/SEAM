from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from core.continuation_lock_identity import (
    LockIdentity,
    OwnedFileChangedError,
    lock_identity,
    lock_identity_is_linked,
    read_lock_path_snapshot,
    release_owned_file,
)

atomic_replace = os.replace
directory_fsync = os.fsync
AtomicReplace = Callable[[Path, Path], None]
ParentSync = Callable[[Path], None]


def _fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        directory_fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_owned(path: Path, expected: LockIdentity) -> None:
    try:
        release_owned_file(path, expected)
    except FileNotFoundError:
        return


def _remove_owned_best_effort(path: Path, expected: LockIdentity) -> None:
    try:
        _remove_owned(path, expected)
    except OSError:
        return


def open_private_exclusive(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb")


def _write_private(path: Path, content: bytes) -> LockIdentity:
    with open_private_exclusive(path) as handle:
        identity = lock_identity(os.fstat(handle.fileno()))
        _ = handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return identity


def _require_owned(path: Path, expected: LockIdentity, content: bytes) -> None:
    snapshot = read_lock_path_snapshot(path, len(content) + 1)
    if not snapshot.matches(expected, content):
        raise OwnedFileChangedError("atomic temporary changed before publication")


def _path_is_owned(
    path: Path,
    expected: LockIdentity,
    content: bytes,
) -> bool:
    try:
        snapshot = read_lock_path_snapshot(path, len(content) + 1)
    except OSError:
        return False
    return snapshot.matches(expected, content)


def _restore_previous(
    path: Path,
    backup: Path | None,
    backup_identity: LockIdentity | None,
    published_identity: LockIdentity,
    content: bytes,
    publication_authenticated: bool,
    sync_parent: ParentSync,
) -> bool:
    published_is_owned = _path_is_owned(path, published_identity, content)
    if published_is_owned:
        try:
            release_owned_file(path, published_identity, content)
        except OSError:
            return False
    elif publication_authenticated:
        return True
    elif backup is None:
        return False
    if backup is None:
        sync_parent(path)
        return True
    if backup_identity is None:
        return False
    try:
        backup_current = lock_identity(backup.lstat())
    except OSError:
        return False
    if backup_current != backup_identity or not stat.S_ISREG(backup_current.mode):
        return False
    try:
        if published_is_owned:
            os.link(backup, path, follow_symlinks=False)
        else:
            os.replace(backup, path)
        sync_parent(path)
    except OSError:
        return False
    return True


def atomic_write_bytes(path: Path, content: bytes) -> None:
    atomic_write_bytes_with(path, content, atomic_replace, _fsync_parent)


def atomic_write_bytes_with(
    path: Path,
    content: bytes,
    replace: AtomicReplace,
    sync_parent: ParentSync,
) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    backup: Path | None = None
    backup_identity: LockIdentity | None = None
    identity: LockIdentity | None = None
    published = False
    publication_authenticated = False
    try:
        identity = _write_private(temporary, content)
        _require_owned(temporary, identity, content)
        try:
            previous = path.lstat()
        except FileNotFoundError:
            previous = None
        if previous is not None:
            previous_identity = lock_identity(previous)
            if lock_identity_is_linked(previous_identity) or not stat.S_ISREG(
                previous_identity.mode
            ):
                raise OwnedFileChangedError("atomic destination is unsafe")
            backup = path.parent / f".{path.name}.{uuid4().hex}.bak"
            os.link(path, backup, follow_symlinks=False)
            backup_identity = lock_identity(backup.lstat())
            sync_parent(path)
        replace(temporary, path)
        published = True
        _require_owned(path, identity, content)
        publication_authenticated = True
        sync_parent(path)
    except OSError:
        if identity is not None:
            published = published or _path_is_owned(path, identity, content)
            if published:
                backup_disposable = _restore_previous(
                    path,
                    backup,
                    backup_identity,
                    identity,
                    content,
                    publication_authenticated,
                    sync_parent,
                )
                if not backup_disposable:
                    backup = None
            _remove_owned_best_effort(temporary, identity)
        if backup is not None and backup_identity is not None:
            _remove_owned_best_effort(backup, backup_identity)
        raise
    if backup is not None and backup_identity is not None:
        _remove_owned_best_effort(backup, backup_identity)
        sync_parent(path)


def atomic_create_bytes(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    identity: LockIdentity | None = None
    published = False
    try:
        identity = _write_private(temporary, content)
        _require_owned(temporary, identity, content)
        os.link(temporary, path, follow_symlinks=False)
        published = True
        _fsync_parent(path)
    except OSError:
        if identity is not None:
            if published:
                _remove_owned_best_effort(path, identity)
            _remove_owned_best_effort(temporary, identity)
        raise
    _remove_owned_best_effort(temporary, identity)
