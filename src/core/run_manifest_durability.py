from __future__ import annotations

import os
from pathlib import Path


def write_durable_bytes(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        _ = handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def fsync_tree(root: Path) -> None:
    directories = [Path(path) for path, _, _ in os.walk(root, topdown=False)]
    for directory in directories:
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
