from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import NamedTuple, NoReturn, final

from typing_extensions import override


class LockIdentity(NamedTuple):
    device: int
    inode: int
    mode: int
    attributes: int


@unique
class BoundedReadErrorKind(str, Enum):
    MISSING = "missing"
    UNSAFE = "unsafe"
    TOO_LARGE = "too_large"
    CHANGED = "changed"
    READ_FAILED = "read_failed"


@final
class BoundedReadError(OSError):
    __slots__ = ("detail", "kind")

    def __init__(self, kind: BoundedReadErrorKind, detail: str) -> None:
        super().__init__(kind.value, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


def _raise_bounded(kind: BoundedReadErrorKind, path: Path) -> NoReturn:
    raise BoundedReadError(kind, str(path))


def _same_identity_and_size(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
    )


def _same_descriptor_state(left: os.stat_result, right: os.stat_result) -> bool:
    return _same_identity_and_size(left, right) and (
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def read_verified_bytes(path: Path, max_bytes: int) -> bytes:
    if max_bytes < 1:
        _raise_bounded(BoundedReadErrorKind.TOO_LARGE, path)
    try:
        initial = path.lstat()
    except FileNotFoundError:
        _raise_bounded(BoundedReadErrorKind.MISSING, path)
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _raise_bounded(BoundedReadErrorKind.UNSAFE, path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        _raise_bounded(BoundedReadErrorKind.MISSING, path)
    except OSError as exc:
        raise BoundedReadError(BoundedReadErrorKind.READ_FAILED, str(path)) from exc
    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same_identity_and_size(
                initial, opened
            ):
                _raise_bounded(BoundedReadErrorKind.CHANGED, path)
            if opened.st_size > max_bytes:
                _raise_bounded(BoundedReadErrorKind.TOO_LARGE, path)
            content = handle.read(max_bytes + 1)
            final = os.fstat(handle.fileno())
    except BoundedReadError:
        raise
    except OSError as exc:
        raise BoundedReadError(BoundedReadErrorKind.READ_FAILED, str(path)) from exc
    if len(content) > max_bytes or final.st_size > max_bytes:
        _raise_bounded(BoundedReadErrorKind.TOO_LARGE, path)
    if len(content) != opened.st_size or not _same_descriptor_state(opened, final):
        _raise_bounded(BoundedReadErrorKind.CHANGED, path)
    try:
        current = path.lstat()
    except FileNotFoundError:
        _raise_bounded(BoundedReadErrorKind.CHANGED, path)
    if not _same_identity_and_size(opened, current):
        _raise_bounded(BoundedReadErrorKind.CHANGED, path)
    return content


def fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def lock_identity(metadata: os.stat_result) -> LockIdentity:
    return LockIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def lock_identity_is_linked(identity: LockIdentity) -> bool:
    return stat.S_ISLNK(identity[2]) or bool(identity[3] & 0x400)


@dataclass(frozen=True)
class LockPathSnapshot:
    identity: LockIdentity
    content: bytes

    def matches(self, expected_identity: LockIdentity, expected_content: bytes) -> bool:
        return (
            not lock_identity_is_linked(self.identity)
            and self.identity == expected_identity
            and self.content == expected_content
        )


def read_lock_path_snapshot(path: Path, maximum_bytes: int) -> LockPathSnapshot:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        identity = lock_identity(os.fstat(descriptor))
        content = os.read(descriptor, maximum_bytes)
    finally:
        os.close(descriptor)
    return LockPathSnapshot(identity, content)
