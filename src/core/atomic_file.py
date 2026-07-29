from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple
from uuid import uuid4

atomic_replace = os.replace
directory_fsync = os.fsync


def _identity(metadata: os.stat_result) -> Tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


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


def _remove_owned(path: Path, expected: Tuple[int, int]) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if _identity(current) == expected:
        path.unlink()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    identity: Tuple[int, int] | None = None
    try:
        with temporary.open("xb") as handle:
            identity = _identity(os.fstat(handle.fileno()))
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, path)
        _fsync_parent(path)
    except OSError:
        if identity is not None:
            _remove_owned(temporary, identity)
        raise
