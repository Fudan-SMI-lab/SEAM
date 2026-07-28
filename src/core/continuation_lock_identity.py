from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

LockIdentity = tuple[int, int, int, int]


def lock_identity(metadata: os.stat_result) -> LockIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_file_attributes", 0),
    )


def lock_identity_is_linked(identity: LockIdentity) -> bool:
    return stat.S_ISLNK(identity[2]) or bool(identity[3] & 0x400)


@dataclass(frozen=True, slots=True)
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
