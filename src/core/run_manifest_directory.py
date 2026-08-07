from __future__ import annotations

import os
import stat
from pathlib import Path

from core.run_manifest_models import ManifestErrorKind, RunManifestError


def _containment_error(detail: str) -> RunManifestError:
    return RunManifestError(kind=ManifestErrorKind.CONTAINMENT, detail=detail)


def _is_link_or_junction(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _containment_error(f"directory is unreadable: {path}: {exc}") from exc
    attributes = metadata.st_file_attributes if os.name == "nt" else 0
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def require_real_directory(path: Path, container: Path) -> Path:
    try:
        canonical_container = container.resolve(strict=True)
        canonical_path = path.resolve(strict=True)
    except OSError as exc:
        raise _containment_error(
            f"required directory is unavailable: {path}: {exc}"
        ) from exc
    lexical_path = Path(os.path.abspath(path))
    contained = (
        canonical_path == canonical_container
        or canonical_container in canonical_path.parents
    )
    ancestor = lexical_path
    while True:
        if _is_link_or_junction(ancestor):
            raise _containment_error(
                f"directory has a link or junction ancestor: {path}"
            )
        try:
            resolved_ancestor = ancestor.resolve(strict=True)
        except OSError as exc:
            raise _containment_error(
                f"directory ancestor changed during access: {ancestor}: {exc}"
            ) from exc
        if resolved_ancestor == canonical_container:
            break
        parent = ancestor.parent
        if parent == ancestor:
            raise _containment_error(f"directory escapes its container: {path}")
        ancestor = parent
    if not contained:
        raise _containment_error(f"directory escapes its canonical container: {path}")
    if not canonical_path.is_dir():
        raise _containment_error(f"path is not a directory: {path}")
    return canonical_path
