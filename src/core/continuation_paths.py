from __future__ import annotations

import os
import stat
import hashlib
from enum import Enum, unique
from pathlib import Path
from typing import Final, NamedTuple

from pydantic import ValidationError
from typing_extensions import assert_never

from .continuation_models import (
    ContinuationError,
    ContinuationErrorKind,
    RunSummaryDocument,
)
from .run_manifest import Sha256Digest

_WINDOWS_REPARSE_POINT: Final = 0x400
_MAX_SUMMARY_BYTES: Final = 2 * 1024 * 1024


@unique
class PathKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


def _error(kind: ContinuationErrorKind, detail: str) -> ContinuationError:
    return ContinuationError(kind=kind, detail=detail)


def _is_link_or_junction(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _WINDOWS_REPARSE_POINT)


def canonical_existing_path(
    path: Path,
    path_kind: PathKind,
    error_kind: ContinuationErrorKind,
) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise _error(error_kind, "path must be absolute and traversal-free")
    lexical = Path(os.path.abspath(path))
    current = lexical
    try:
        while True:
            if _is_link_or_junction(current):
                raise _error(error_kind, "path has a link or junction component")
            parent = current.parent
            if parent == current:
                break
            current = parent
        canonical = lexical.resolve(strict=True)
        metadata = lexical.lstat()
    except ContinuationError:
        raise
    except OSError as exc:
        raise _error(error_kind, f"path is unavailable: {exc}") from exc
    match path_kind:
        case PathKind.FILE:
            if not stat.S_ISREG(metadata.st_mode):
                raise _error(error_kind, "path is not a regular file")
            return canonical
        case PathKind.DIRECTORY:
            if not stat.S_ISDIR(metadata.st_mode):
                raise _error(error_kind, "path is not a regular directory")
            return canonical
        case _ as unreachable:
            assert_never(unreachable)


class ExplicitSummarySnapshot(NamedTuple):
    path: Path
    document: RunSummaryDocument
    digest: Sha256Digest


def read_explicit_summary_snapshot(path: Path) -> ExplicitSummarySnapshot:
    canonical = canonical_existing_path(
        path, PathKind.FILE, ContinuationErrorKind.UNSAFE_SUMMARY_PATH
    )
    if canonical.name != "summary.json":
        raise _error(
            ContinuationErrorKind.UNSAFE_SUMMARY_PATH,
            "continuation input must be an explicit summary.json",
        )
    try:
        with canonical.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > _MAX_SUMMARY_BYTES:
                raise _error(
                    ContinuationErrorKind.MALFORMED_SUMMARY,
                    "summary exceeds the bounded input size",
                )
            content = handle.read(_MAX_SUMMARY_BYTES + 1)
            if len(content) != size:
                raise _error(
                    ContinuationErrorKind.MALFORMED_SUMMARY,
                    "summary changed while being read",
                )
        summary = RunSummaryDocument.model_validate_json(content)
    except ContinuationError:
        raise
    except (OSError, ValidationError) as exc:
        raise _error(
            ContinuationErrorKind.MALFORMED_SUMMARY,
            f"summary is not a valid V3 run summary: {exc}",
        ) from exc
    return ExplicitSummarySnapshot(
        canonical,
        summary,
        Sha256Digest(hashlib.sha256(content).hexdigest()),
    )


def parse_explicit_summary(path: Path) -> tuple[Path, RunSummaryDocument]:
    snapshot = read_explicit_summary_snapshot(path)
    return snapshot.path, snapshot.document
