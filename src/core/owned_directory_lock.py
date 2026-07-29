from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path
from typing import NamedTuple

from core.continuation_lock_identity import read_verified_bytes


class DirectoryLockIdentity(NamedTuple):
    device: int
    inode: int
    mode: int
    attributes: int


class OwnedDirectoryChangedError(OSError):
    pass


def directory_lock_identity(path: Path) -> DirectoryLockIdentity:
    metadata = path.lstat()
    return DirectoryLockIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _safe_directory(identity: DirectoryLockIdentity) -> bool:
    return stat.S_ISDIR(identity.mode) and not bool(identity.attributes & 0x400)


def _same_after_rename(
    expected: DirectoryLockIdentity,
    actual: DirectoryLockIdentity,
) -> bool:
    if os.name != "nt":
        return expected == actual
    return (
        expected.device,
        stat.S_IFMT(expected.mode),
        expected.attributes & 0x400,
    ) == (
        actual.device,
        stat.S_IFMT(actual.mode),
        actual.attributes & 0x400,
    )


def _restore_quarantine(quarantine: Path, public: Path) -> None:
    if public.exists():
        return
    os.rename(quarantine, public)


def release_owned_directory(
    path: Path,
    expected: DirectoryLockIdentity,
    owner_name: str | None = None,
    owner_content: bytes | None = None,
) -> None:
    current = directory_lock_identity(path)
    if current != expected or not _safe_directory(current):
        raise OwnedDirectoryChangedError("owned directory changed before release")
    quarantine = path.with_name(f".{path.name}.{secrets.token_hex(16)}.release")
    os.replace(path, quarantine)
    quarantined = directory_lock_identity(quarantine)
    if not _same_after_rename(expected, quarantined) or not _safe_directory(
        quarantined
    ):
        _restore_quarantine(quarantine, path)
        raise OwnedDirectoryChangedError("owned directory changed during release")
    if owner_name is not None and owner_content is not None:
        try:
            content = read_verified_bytes(quarantine / owner_name, len(owner_content))
        except OSError as exc:
            _restore_quarantine(quarantine, path)
            raise OwnedDirectoryChangedError(
                "lock owner changed during release"
            ) from exc
        if content != owner_content:
            _restore_quarantine(quarantine, path)
            raise OwnedDirectoryChangedError("lock owner changed during release")
    shutil.rmtree(quarantine)
