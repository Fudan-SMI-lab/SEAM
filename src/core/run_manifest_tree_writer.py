from __future__ import annotations

import os
from pathlib import Path, PurePath

from core.run_manifest_durability import fsync_tree, write_durable_bytes

destination_open = os.open


def _open_directory(root: int, relative: PurePath) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current = os.dup(root)
    succeeded = False
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
            except FileExistsError as existing:
                _ = existing
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        succeeded = True
        return current
    finally:
        if not succeeded:
            os.close(current)


def _write_file(root: int, relative: PurePath, content: bytes) -> None:
    parent = _open_directory(root, relative.parent)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = destination_open(relative.name, flags, 0o600, dir_fd=parent)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise OSError("destination write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent)
    finally:
        os.close(parent)


def _write_link(root: int, relative: PurePath, target: str) -> None:
    parent = _open_directory(root, relative.parent)
    try:
        os.symlink(target, relative.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _write_posix(
    destination: Path,
    directories: tuple[PurePath, ...],
    files: tuple[tuple[PurePath, bytes], ...],
    links: tuple[tuple[PurePath, str], ...],
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root = os.open(destination, flags)
    try:
        for relative in directories:
            descriptor = _open_directory(root, relative)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for relative, content in files:
            _write_file(root, relative, content)
        for relative, target in links:
            _write_link(root, relative, target)
        os.fsync(root)
    finally:
        os.close(root)


def write_real_tree(
    destination: Path,
    directories: tuple[PurePath, ...],
    files: tuple[tuple[PurePath, bytes], ...],
    links: tuple[tuple[PurePath, str], ...] = (),
) -> None:
    if os.name != "nt":
        _write_posix(destination, directories, files, links)
        return
    for relative in directories:
        (destination / relative).mkdir(parents=True, mode=0o700)
    for relative, content in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_durable_bytes(target, content)
    for relative, target in links:
        link_path = destination / relative
        link_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.symlink(target, link_path)
    fsync_tree(destination)
