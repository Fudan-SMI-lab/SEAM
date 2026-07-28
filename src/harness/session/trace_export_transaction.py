from __future__ import annotations

import os
import shutil
from secrets import token_urlsafe
from types import TracebackType
from typing import Literal, final

from harness.session.trace_export_models import TraceExportError, TraceExportRequest
from harness.session.trace_export_paths import has_unsafe_ancestry

atomic_directory_rename = os.rename
directory_fsync = os.fsync
DIRECTORY_SYNC_SUPPORTED = os.name != "nt"


@final
class TraceExportTransaction:
    """Stage one export privately and atomically publish it after its manifest."""

    def __init__(self, request: TraceExportRequest) -> None:
        self.final = request.destination
        self.staging = self.final.parent / f".trace.{token_urlsafe(8)}.tmp"
        self.request = TraceExportRequest(
            destination=self.staging,
            seeds=request.seeds,
            overflow_roots=request.overflow_roots,
            captured_at=request.captured_at,
            max_overflow_bytes=request.max_overflow_bytes,
            correlation=request.correlation,
        )
        self._committed = False
        self._parent_identity: tuple[int, int, int] | None = None
        self._staging_identity: tuple[int, int, int] | None = None

    def __enter__(self) -> TraceExportTransaction:
        try:
            self._prepare()
        except TraceExportError as exc:
            cleanup_error = self._remove_staging()
            if cleanup_error is not None:
                raise TraceExportError(
                    self.staging,
                    f"{exc.detail}; staging cleanup failed: {cleanup_error}",
                ) from exc
            raise
        return self

    def commit(self) -> None:
        if not self._directories_stable() or self.final.exists():
            raise TraceExportError(self.final, "trace staging identity changed")
        try:
            _sync_directory(self.staging / "sessions")
            _sync_directory(self.staging / "overflows")
            _sync_directory(self.staging)
            atomic_directory_rename(self.staging, self.final)
        except OSError as exc:
            raise TraceExportError(
                self.final, f"trace commit interrupted: {exc}"
            ) from exc
        self._committed = True
        try:
            _sync_directory(self.final.parent)
        except OSError as exc:
            raise TraceExportError(
                self.final, f"trace directory sync interrupted: {exc}"
            ) from exc

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._committed:
            return False
        cleanup_error = self._remove_staging()
        if cleanup_error is not None:
            error = TraceExportError(
                self.staging,
                f"trace staging cleanup failed: {cleanup_error}",
            )
            if _exception is not None:
                raise error from _exception
            raise error
        return False

    def _prepare(self) -> None:
        destination = self.final
        if (
            not destination.is_absolute()
            or ".." in destination.parts
            or str(destination).startswith(("\\\\", "//"))
        ):
            raise TraceExportError(
                destination, "destination must be an absolute local path"
            )
        parent = destination.parent
        try:
            if has_unsafe_ancestry(parent):
                raise TraceExportError(destination, "destination ancestry is unsafe")
            _ = parent.resolve(strict=True)
            if destination.exists():
                raise TraceExportError(destination, "destination already exists")
            self.staging.mkdir(mode=0o700)
            (self.staging / "sessions").mkdir(mode=0o700)
            (self.staging / "overflows").mkdir(mode=0o700)
            self._parent_identity = _directory_identity(parent)
            self._staging_identity = _directory_identity(self.staging)
        except TraceExportError:
            raise
        except (OSError, RuntimeError) as exc:
            raise TraceExportError(destination, f"unsafe destination: {exc}") from exc

    def _directories_stable(self) -> bool:
        if has_unsafe_ancestry(self.staging):
            return False
        try:
            return self._parent_identity == _directory_identity(
                self.final.parent
            ) and self._staging_identity == _directory_identity(self.staging)
        except OSError:
            return False

    def _remove_staging(self) -> str | None:
        try:
            shutil.rmtree(self.staging)
        except FileNotFoundError:
            return None
        except OSError as exc:
            return str(exc)
        return None


def _directory_identity(path: os.PathLike[str]) -> tuple[int, int, int]:
    metadata = os.lstat(path)
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _sync_directory(path: os.PathLike[str]) -> None:
    if not DIRECTORY_SYNC_SUPPORTED:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        directory_fsync(descriptor)
    finally:
        os.close(descriptor)
