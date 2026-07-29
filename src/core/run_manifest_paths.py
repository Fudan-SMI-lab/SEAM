from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Final, NamedTuple, final

from .run_manifest_directory import require_real_directory
from .run_manifest_models import (
    EvidenceDigest,
    ManifestErrorKind,
    RunManifestError,
    Sha256Digest,
)
from .evidence_limits import EvidenceBudget

_WINDOWS_REPARSE_POINT: Final = 0x400


class PathIdentity(NamedTuple):
    path: Path
    device: int
    inode: int
    mode: int
    attributes: int
    size: int
    modified_ns: int


@final
class RealTree:
    __slots__ = ("_root", "_directories", "_files")

    _root: PathIdentity
    _directories: tuple[PathIdentity, ...]
    _files: tuple[PathIdentity, ...]

    def __init__(
        self,
        root: PathIdentity,
        directories: tuple[PathIdentity, ...],
        files: tuple[PathIdentity, ...],
    ) -> None:
        self._root = root
        self._directories = directories
        self._files = files

    @property
    def root(self) -> PathIdentity:
        return self._root

    @property
    def directories(self) -> tuple[PathIdentity, ...]:
        return self._directories

    @property
    def files(self) -> tuple[PathIdentity, ...]:
        return self._files


def _containment_error(detail: str) -> RunManifestError:
    return RunManifestError(kind=ManifestErrorKind.CONTAINMENT, detail=detail)


def _path_identity(path: Path) -> PathIdentity:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _containment_error(f"tree entry is unreadable: {path}: {exc}") from exc
    attributes = metadata.st_file_attributes if os.name == "nt" else 0
    return PathIdentity(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        attributes=attributes,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
    )


def _is_reparse(identity: PathIdentity) -> bool:
    return stat.S_ISLNK(identity.mode) or bool(
        identity.attributes & _WINDOWS_REPARSE_POINT
    )


def _matches_metadata(identity: PathIdentity, metadata: os.stat_result) -> bool:
    attributes = metadata.st_file_attributes if os.name == "nt" else 0
    return (
        identity.device == metadata.st_dev
        and identity.inode == metadata.st_ino
        and identity.mode == metadata.st_mode
        and identity.attributes & _WINDOWS_REPARSE_POINT
        == attributes & _WINDOWS_REPARSE_POINT
        and identity.size == metadata.st_size
        and identity.modified_ns == metadata.st_mtime_ns
    )


def _matches_path_identity(expected: PathIdentity, actual: PathIdentity) -> bool:
    return (
        expected.device == actual.device
        and expected.inode == actual.inode
        and expected.mode == actual.mode
        and expected.attributes & _WINDOWS_REPARSE_POINT
        == actual.attributes & _WINDOWS_REPARSE_POINT
        and expected.size == actual.size
        and expected.modified_ns == actual.modified_ns
    )


def _require_identity(identity: PathIdentity) -> None:
    current = _path_identity(identity.path)
    if _is_reparse(current) or not _matches_path_identity(identity, current):
        raise _containment_error(f"tree entry changed during access: {identity.path}")


def inspect_real_tree(root: Path, container: Path) -> RealTree:
    canonical_root = require_real_directory(root, container)
    root_identity = _path_identity(canonical_root)
    pending = [root_identity]
    directories: list[PathIdentity] = []
    files: list[PathIdentity] = []
    budget = EvidenceBudget()
    while pending:
        directory = pending.pop()
        _require_identity(directory)
        for entry in directory.path.iterdir():
            identity = _path_identity(entry)
            if _is_reparse(identity):
                raise _containment_error(f"link or junction is forbidden: {entry}")
            try:
                resolved = entry.resolve(strict=True)
            except OSError as exc:
                raise _containment_error(
                    f"tree entry is unreadable: {entry}: {exc}"
                ) from exc
            if canonical_root not in resolved.parents or resolved != Path(
                os.path.abspath(entry)
            ):
                raise _containment_error(f"tree entry escapes evidence root: {entry}")
            resolved_identity = identity._replace(path=resolved)
            budget.charge(
                resolved.relative_to(canonical_root),
                identity.size if stat.S_ISREG(identity.mode) else 0,
            )
            if stat.S_ISDIR(identity.mode):
                directories.append(resolved_identity)
                pending.append(resolved_identity)
            elif stat.S_ISREG(identity.mode):
                files.append(resolved_identity)
            else:
                raise _containment_error(f"unsupported evidence entry: {entry}")
        _require_identity(directory)
    return RealTree(
        root=root_identity,
        directories=tuple(sorted(directories, key=lambda item: str(item.path))),
        files=tuple(sorted(files, key=lambda item: str(item.path))),
    )


def _directory_identity(tree: RealTree, path: Path) -> PathIdentity:
    if path == tree.root.path:
        return tree.root
    for identity in tree.directories:
        if identity.path == path:
            return identity
    raise _containment_error(f"unrecorded tree ancestor: {path}")


def _require_ancestors(tree: RealTree, path: Path) -> None:
    parent = path.parent
    while True:
        _require_identity(_directory_identity(tree, parent))
        if parent == tree.root.path:
            return
        if tree.root.path not in parent.parents:
            raise _containment_error(f"tree entry escapes evidence root: {path}")
        parent = parent.parent


def _require_entry(tree: RealTree, identity: PathIdentity) -> None:
    _require_ancestors(tree, identity.path)
    _require_identity(identity)


def _read_verified(tree: RealTree, identity: PathIdentity) -> bytes:
    _require_entry(tree, identity)
    try:
        with identity.path.open("rb") as handle:
            if not _matches_metadata(identity, os.fstat(handle.fileno())):
                raise _containment_error(
                    f"opened file differs from inspected entry: {identity.path}"
                )
            _require_ancestors(tree, identity.path)
            content = handle.read(identity.size + 1)
            if not _matches_metadata(identity, os.fstat(handle.fileno())):
                raise _containment_error(
                    f"opened file changed while reading: {identity.path}"
                )
            _require_ancestors(tree, identity.path)
    except RunManifestError:
        raise
    except OSError as exc:
        raise _containment_error(
            f"tree entry is unreadable: {identity.path}: {exc}"
        ) from exc
    _require_entry(tree, identity)
    if len(content) != identity.size:
        raise _containment_error(f"tree entry changed size: {identity.path}")
    return content


def read_real_tree_file(tree: RealTree, identity: PathIdentity) -> bytes:
    return _read_verified(tree, identity)


def require_real_tree_entry(tree: RealTree, identity: PathIdentity) -> None:
    _require_entry(tree, identity)


def require_real_tree(tree: RealTree) -> None:
    _require_tree(tree)


def read_real_file(
    path: Path,
    container: Path,
    maximum_bytes: int,
) -> tuple[PathIdentity, bytes]:
    parent = require_real_directory(path.parent, container)
    parent_identity = _path_identity(parent)
    identity = _path_identity(path)
    if (
        _is_reparse(identity)
        or not stat.S_ISREG(identity.mode)
        or identity.size > maximum_bytes
    ):
        raise _containment_error(f"file is unsafe or exceeds its limit: {path}")
    tree = RealTree(parent_identity, (), (identity,))
    return identity, _read_verified(tree, identity)


def _require_tree(tree: RealTree) -> None:
    _require_identity(tree.root)
    for identity in tree.directories:
        _require_entry(tree, identity)
    for identity in tree.files:
        _require_entry(tree, identity)


def copy_real_tree(source: Path, container: Path, destination: Path) -> None:
    tree = inspect_real_tree(source, container)
    destination.mkdir()
    for directory in tree.directories:
        _require_entry(tree, directory)
        target = destination / directory.path.relative_to(tree.root.path)
        target.mkdir(parents=True)
        _require_entry(tree, directory)
    for source_file in tree.files:
        content = _read_verified(tree, source_file)
        target = destination / source_file.path.relative_to(tree.root.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(content)
    _require_tree(tree)


def digest_inventory(root: Path, container: Path) -> tuple[EvidenceDigest, ...]:
    tree = inspect_real_tree(root, container)
    inventory: list[EvidenceDigest] = []
    for identity in tree.files:
        content = _read_verified(tree, identity)
        inventory.append(
            EvidenceDigest(
                relative_path=identity.path.relative_to(tree.root.path).as_posix(),
                digest=Sha256Digest(hashlib.sha256(content).hexdigest()),
                size_bytes=len(content),
            )
        )
    _require_tree(tree)
    return tuple(inventory)
