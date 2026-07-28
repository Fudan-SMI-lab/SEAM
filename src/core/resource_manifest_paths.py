from __future__ import annotations

import os
import stat
import hashlib
from pathlib import Path
from typing import Final, NamedTuple

from .resource_manifest_models import (
    ResourceManifestError,
    ResourceManifestErrorKind,
)

_WINDOWS_REPARSE_POINT: Final = 0x400
_AUTHORITY_DIRECTORY: Final = ".seam-resource-authority"
_CAPABILITY_SIZE: Final = 32


class ResourceDirectoryBinding(NamedTuple):
    path: Path
    device: int
    inode: int
    mode: int
    attributes: int


def _is_link_or_junction(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _WINDOWS_REPARSE_POINT)


def _unsafe(detail: str) -> ResourceManifestError:
    return ResourceManifestError(ResourceManifestErrorKind.UNSAFE_PATH, detail)


def _read_capture_capability(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise _unsafe(
                "resource manifest authority capability is not a regular file"
            )
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise _unsafe(
                "resource manifest authority capability permissions are too broad"
            )
        secret = path.read_bytes()
    except ResourceManifestError:
        raise
    except OSError as exc:
        raise ResourceManifestError(
            ResourceManifestErrorKind.AUTHORITY_MISMATCH,
            f"resource manifest authority capability is unreadable: {exc}",
        ) from exc
    if len(secret) != _CAPABILITY_SIZE:
        raise ResourceManifestError(
            ResourceManifestErrorKind.AUTHORITY_MISMATCH,
            "resource manifest authority capability has an invalid length",
        )
    return secret


def _capture_capability_path(report_dir: Path) -> Path:
    root = report_dir.parent.parent / _AUTHORITY_DIRECTORY
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        metadata = root.lstat()
    except OSError as exc:
        raise _unsafe(
            f"resource manifest authority directory is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or _is_link_or_junction(root):
        raise _unsafe("resource manifest authority path is not a regular directory")
    if os.name != "nt" and metadata.st_mode & 0o077:
        raise _unsafe("resource manifest authority directory permissions are too broad")
    identity = hashlib.sha256(os.fspath(report_dir).encode()).hexdigest()
    return root / f"{identity}.key"


def load_internal_capture_secret(report_dir: Path, generated: bytes) -> bytes:
    path = _capture_capability_path(report_dir)
    try:
        return _read_capture_capability(path)
    except ResourceManifestError as exc:
        if (
            exc.kind is not ResourceManifestErrorKind.AUTHORITY_MISMATCH
            or path.exists()
        ):
            raise
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_capture_capability(path)
    except OSError as exc:
        raise ResourceManifestError(
            ResourceManifestErrorKind.WRITE_INTERRUPTED,
            f"resource manifest authority capability creation failed: {exc}",
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        cleanup_detail = ""
        try:
            path.unlink()
        except FileNotFoundError:
            cleanup_detail = "; partial capability already absent"
        except OSError as cleanup_exc:
            cleanup_detail = f"; partial capability cleanup failed: {cleanup_exc}"
        raise ResourceManifestError(
            ResourceManifestErrorKind.WRITE_INTERRUPTED,
            f"resource manifest authority capability write failed: {exc}{cleanup_detail}",
        ) from exc
    return generated


def require_resource_directory(path: Path, container: Path) -> Path:
    try:
        canonical_container = container.resolve(strict=True)
        canonical_path = path.resolve(strict=True)
        lexical_path = Path(os.path.abspath(path))
    except OSError as exc:
        raise _unsafe(f"required directory is unavailable: {path}: {exc}") from exc
    contained = (
        canonical_path == canonical_container
        or canonical_container in canonical_path.parents
    )
    ancestor = lexical_path
    while True:
        try:
            if _is_link_or_junction(ancestor):
                raise _unsafe(f"directory has a link or junction ancestor: {path}")
            resolved_ancestor = ancestor.resolve(strict=True)
        except ResourceManifestError:
            raise
        except OSError as exc:
            raise _unsafe(
                f"directory ancestor changed during access: {ancestor}: {exc}"
            ) from exc
        if resolved_ancestor == canonical_container:
            break
        parent = ancestor.parent
        if parent == ancestor:
            raise _unsafe(f"directory escapes its container: {path}")
        ancestor = parent
    if not contained:
        raise _unsafe(f"directory escapes its canonical container: {path}")
    if not canonical_path.is_dir():
        raise _unsafe(f"path is not a directory: {path}")
    return canonical_path


def bind_resource_directory(path: Path, container: Path) -> ResourceDirectoryBinding:
    canonical = require_resource_directory(path, container)
    metadata = canonical.lstat()
    return ResourceDirectoryBinding(
        path=canonical,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        attributes=getattr(metadata, "st_file_attributes", 0),
    )


def require_bound_resource_directory(binding: ResourceDirectoryBinding) -> Path:
    try:
        metadata = binding.path.lstat()
        canonical = binding.path.resolve(strict=True)
    except OSError as exc:
        raise _unsafe(f"bound report directory is unavailable: {exc}") from exc
    current = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT,
    )
    expected = (
        binding.device,
        binding.inode,
        binding.mode,
        binding.attributes & _WINDOWS_REPARSE_POINT,
    )
    if _is_link_or_junction(binding.path) or current != expected:
        raise _unsafe("bound report directory identity changed")
    if canonical != binding.path:
        raise _unsafe("bound report directory resolves to another location")
    return binding.path
