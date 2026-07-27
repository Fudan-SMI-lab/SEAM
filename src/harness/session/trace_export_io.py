from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import uuid4

from harness.session.opencode_contract import JsonValue
from harness.session.trace_export_models import (
    OverflowCapture,
    OverflowCopyRequest,
    OverflowStatus,
    StoredArtifact,
    TraceWriteError,
)
from harness.session.trace_export_paths import (
    has_unsafe_ancestry,
    resolve_local_reference,
)


atomic_replace = os.replace
_COPY_CHUNK_BYTES = 1024 * 1024


def _open_source(path: Path, mode: Literal["rb"]) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return os.fdopen(descriptor, mode)


source_open = _open_source


def encode_json(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def write_atomic(path: Path, content: bytes) -> StoredArtifact:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    if has_unsafe_ancestry(path.parent):
        raise TraceWriteError(path, "artifact parent is linked or a reparse point")
    try:
        with temporary.open("xb") as handle:
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, path)
    except OSError as exc:
        cleanup_detail = _remove_temporary(temporary)
        raise TraceWriteError(
            path, f"atomic write interrupted: {exc}{cleanup_detail}"
        ) from exc
    return StoredArtifact(path, len(content), hashlib.sha256(content).hexdigest())


def copy_overflow(request: OverflowCopyRequest) -> OverflowCapture:
    classified = resolve_local_reference(request.reference, request.allowed_roots)
    if isinstance(classified, OverflowCapture):
        return classified
    source = classified
    try:
        source_stat = source.stat()
    except FileNotFoundError:
        return OverflowCapture(
            OverflowStatus.MISSING, None, "source expired or missing"
        )
    except OSError as exc:
        return OverflowCapture(OverflowStatus.READ_ERROR, None, str(exc))
    if not stat.S_ISREG(source_stat.st_mode):
        return OverflowCapture(
            OverflowStatus.NOT_REGULAR, None, "source is not a regular file"
        )
    if source_stat.st_size > request.max_bytes:
        return OverflowCapture(
            OverflowStatus.OVERSIZED,
            None,
            f"source size {source_stat.st_size} exceeds limit {request.max_bytes}",
        )
    return _copy_bounded(source, request, source_stat)


def artifact_value(artifact: StoredArtifact, root: Path) -> JsonValue:
    return {
        "path": artifact.path.relative_to(root).as_posix(),
        "size": artifact.size,
        "sha256": artifact.sha256,
    }


def inventory_sha256(artifacts: tuple[StoredArtifact, ...], root: Path) -> str:
    digest = hashlib.sha256()
    for artifact in sorted(artifacts, key=lambda item: item.path.as_posix()):
        digest.update(artifact.path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(artifact.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(artifact.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _copy_bounded(
    source: Path,
    request: OverflowCopyRequest,
    expected: os.stat_result,
) -> OverflowCapture:
    destination = request.destination
    if has_unsafe_ancestry(destination.parent):
        return OverflowCapture(
            OverflowStatus.WRITE_INTERRUPTED,
            None,
            "overflow destination parent is linked or a reparse point",
        )
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    failure: OverflowCapture | None = None
    final_stat: os.stat_result | None = None
    try:
        source_handle = source_open(source, "rb")
    except FileNotFoundError:
        return OverflowCapture(
            OverflowStatus.MISSING, None, "source expired before open"
        )
    except OSError as exc:
        return OverflowCapture(OverflowStatus.READ_ERROR, None, str(exc))
    try:
        with source_handle:
            opened_stat = os.fstat(source_handle.fileno())
            if not _same_file(expected, opened_stat):
                return OverflowCapture(
                    OverflowStatus.UNSAFE,
                    None,
                    "source identity changed before open",
                )
            with temporary.open("xb") as output_handle:
                while failure is None:
                    try:
                        chunk = source_handle.read(_COPY_CHUNK_BYTES)
                    except OSError as exc:
                        failure = OverflowCapture(
                            OverflowStatus.READ_ERROR, None, str(exc)
                        )
                        continue
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > request.max_bytes:
                        failure = OverflowCapture(
                            OverflowStatus.OVERSIZED,
                            None,
                            f"captured bytes exceed limit {request.max_bytes}",
                        )
                        continue
                    digest.update(chunk)
                    try:
                        _ = output_handle.write(chunk)
                    except OSError as exc:
                        failure = OverflowCapture(
                            OverflowStatus.WRITE_INTERRUPTED,
                            None,
                            str(exc),
                        )
                if failure is None:
                    output_handle.flush()
                    os.fsync(output_handle.fileno())
                    final_stat = os.fstat(source_handle.fileno())
    except OSError as exc:
        cleanup_detail = _remove_temporary(temporary)
        return OverflowCapture(
            OverflowStatus.WRITE_INTERRUPTED,
            None,
            f"overflow copy interrupted: {exc}{cleanup_detail}",
        )
    if failure is not None:
        _ = _remove_temporary(temporary)
        return failure
    try:
        current_stat = source.stat()
        stable_path = source.resolve(strict=True) == source
    except (FileNotFoundError, OSError, RuntimeError):
        stable_path = False
        current_stat = None
    if (
        final_stat is None
        or current_stat is None
        or not stable_path
        or not _stable_file(expected, final_stat)
        or not _stable_file(expected, current_stat)
    ):
        _ = _remove_temporary(temporary)
        return OverflowCapture(
            OverflowStatus.UNSAFE,
            None,
            "source identity or content changed while copying",
        )
    try:
        atomic_replace(temporary, destination)
    except OSError as exc:
        cleanup_detail = _remove_temporary(temporary)
        return OverflowCapture(
            OverflowStatus.WRITE_INTERRUPTED,
            None,
            f"overflow replace interrupted: {exc}{cleanup_detail}",
        )
    return OverflowCapture(
        OverflowStatus.COPIED,
        StoredArtifact(destination, size, digest.hexdigest()),
        None,
    )


def _same_file(expected: os.stat_result, actual: os.stat_result) -> bool:
    return (
        expected.st_dev == actual.st_dev
        and expected.st_ino == actual.st_ino
        and expected.st_mode == actual.st_mode
    )


def _stable_file(expected: os.stat_result, actual: os.stat_result) -> bool:
    return (
        _same_file(expected, actual)
        and expected.st_size == actual.st_size
        and expected.st_mtime_ns == actual.st_mtime_ns
    )


def _remove_temporary(path: Path) -> str:
    try:
        path.unlink()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"; temporary cleanup failed: {exc}"
    return ""
