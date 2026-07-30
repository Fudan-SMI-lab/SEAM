from __future__ import annotations

import errno
import os
import secrets
import shutil
import stat
from pathlib import Path
from threading import Lock
from typing import NamedTuple, final

from core.atomic_directory import rename_directory_no_replace
from core.continuation_lock_identity import fsync_parent, read_verified_bytes


@final
class _DirectoryDescriptorLease:
    __slots__: tuple[str, ...] = ("descriptor",)

    def __init__(self, descriptor: int) -> None:
        self.descriptor: int | None = descriptor

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is not None:
            self.descriptor = None
            os.close(descriptor)


class DirectoryLockIdentity(NamedTuple):
    device: int
    inode: int
    mode: int
    attributes: int

    def __copy__(self) -> DirectoryLockIdentity:
        return self

    def __deepcopy__(
        self,
        _memo: dict[int, DirectoryLockIdentity],
    ) -> DirectoryLockIdentity:
        return self


_LEASES: dict[int, tuple[DirectoryLockIdentity, _DirectoryDescriptorLease]] = {}
_LEASES_LOCK = Lock()


def _retain_descriptor(identity: DirectoryLockIdentity, descriptor: int) -> None:
    with _LEASES_LOCK:
        _LEASES[id(identity)] = (identity, _DirectoryDescriptorLease(descriptor))


def _duplicate_retained_descriptor(identity: DirectoryLockIdentity) -> int | None:
    with _LEASES_LOCK:
        retained = _LEASES.get(id(identity))
        if retained is None or retained[0] is not identity:
            return None
        descriptor = retained[1].descriptor
        return os.dup(descriptor) if descriptor is not None else None


class OwnedDirectoryChangedError(OSError):
    pass


def directory_lock_identity(
    path: Path,
    *,
    retain: bool = True,
) -> DirectoryLockIdentity:
    if not retain or os.name == "nt":
        return _directory_identity(path.lstat())
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    retained = False
    try:
        identity = _directory_identity(os.fstat(descriptor))
        current = _directory_identity(path.lstat())
        if identity != current or not _safe_directory(identity):
            raise OwnedDirectoryChangedError(
                "directory changed during ownership capture"
            )
        _retain_descriptor(identity, descriptor)
        retained = True
        return identity
    finally:
        if not retained:
            os.close(descriptor)


def close_directory_identity(identity: DirectoryLockIdentity) -> None:
    with _LEASES_LOCK:
        retained = _LEASES.pop(id(identity), None)
    if retained is not None and retained[0] is identity:
        retained[1].close()


def _directory_identity(metadata: os.stat_result) -> DirectoryLockIdentity:
    return DirectoryLockIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def empty_directory_identity(path: Path) -> DirectoryLockIdentity:
    before = directory_lock_identity(path, retain=True)
    retained = False
    try:
        with os.scandir(path) as entries:
            populated = next(entries, None) is not None
        if populated:
            raise OwnedDirectoryChangedError(
                "new directory was replaced before capture"
            )
        after = directory_lock_identity(path, retain=False)
        if not _same_after_rename(before, after) or not _safe_directory(after):
            raise OwnedDirectoryChangedError("new directory changed before capture")
        retained = True
        return before
    finally:
        if not retained:
            close_directory_identity(before)


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
    try:
        _ = public.lstat()
    except FileNotFoundError:
        rename_directory_no_replace(quarantine, public)
        fsync_parent(public)
        return


def release_owned_directory(
    path: Path,
    expected: DirectoryLockIdentity,
    owner_name: str | None = None,
    owner_content: bytes | None = None,
) -> None:
    descriptor = _duplicate_retained_descriptor(expected)
    try:
        if os.name != "nt" and descriptor is None:
            raise OwnedDirectoryChangedError("directory ownership lease is unavailable")
        retained = (
            _directory_identity(os.fstat(descriptor))
            if descriptor is not None
            else expected
        )
        current = directory_lock_identity(path, retain=False)
        if current != retained or not _safe_directory(current):
            raise OwnedDirectoryChangedError("owned directory changed before release")
        quarantine = path.with_name(f".{path.name}.{secrets.token_hex(16)}.release")
        os.replace(path, quarantine)
        try:
            fsync_parent(path)
        except OSError:
            _restore_quarantine(quarantine, path)
            raise
        quarantined = directory_lock_identity(quarantine, retain=False)
        if not _same_after_rename(retained, quarantined) or not _safe_directory(
            quarantined
        ):
            _restore_quarantine(quarantine, path)
            raise OwnedDirectoryChangedError("owned directory changed during release")
        if owner_name is not None and owner_content is not None:
            try:
                content = read_verified_bytes(
                    quarantine / owner_name, len(owner_content)
                )
            except OSError as exc:
                _restore_quarantine(quarantine, path)
                raise OwnedDirectoryChangedError(
                    "lock owner changed during release"
                ) from exc
            if content != owner_content:
                _restore_quarantine(quarantine, path)
                raise OwnedDirectoryChangedError("lock owner changed during release")
        if os.name == "nt":
            shutil.rmtree(quarantine)
        else:
            _remove_tree_by_descriptor(quarantine, retained, descriptor)
        fsync_parent(path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        close_directory_identity(expected)


def _remove_tree_by_descriptor(
    path: Path,
    expected: DirectoryLockIdentity,
    retained_descriptor: int | None = None,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = (
        retained_descriptor if retained_descriptor is not None else os.open(path, flags)
    )
    try:
        actual = _directory_identity(os.fstat(descriptor))
        if not _same_after_rename(expected, actual):
            raise OwnedDirectoryChangedError(
                "owned directory changed before descriptor removal"
            )
        _clear_directory(descriptor, flags)
    finally:
        if retained_descriptor is None:
            os.close(descriptor)
    current = directory_lock_identity(path, retain=False)
    if not _same_after_rename(expected, current):
        raise OwnedDirectoryChangedError("owned directory changed before final removal")
    parent_descriptor = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.rmdir(path.name, dir_fd=parent_descriptor)
        if os.fstat(descriptor).st_nlink != 0:
            try:
                os.mkdir(path.name, dir_fd=parent_descriptor)
            except FileExistsError as existing:
                _ = existing
            raise OwnedDirectoryChangedError(
                "path successor was restored after removal race"
            )
    finally:
        os.close(parent_descriptor)


def _clear_directory(descriptor: int, flags: int) -> None:
    for name in os.listdir(descriptor):
        observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        try:
            child = os.open(name, flags, dir_fd=descriptor)
        except OSError as exc:
            if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
                raise
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if _directory_identity(observed) != _directory_identity(current):
                raise OwnedDirectoryChangedError(
                    "directory child changed before unlink"
                ) from exc
            os.unlink(name, dir_fd=descriptor)
            continue
        try:
            child_identity = _directory_identity(os.fstat(child))
            _clear_directory(child, flags)
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not _same_after_rename(child_identity, _directory_identity(current)):
                raise OwnedDirectoryChangedError(
                    "child directory changed before removal"
                )
            os.rmdir(name, dir_fd=descriptor)
            if os.fstat(child).st_nlink != 0:
                try:
                    os.mkdir(name, dir_fd=descriptor)
                except FileExistsError as existing:
                    _ = existing
                raise OwnedDirectoryChangedError(
                    "child successor was restored after removal race"
                )
        finally:
            os.close(child)
