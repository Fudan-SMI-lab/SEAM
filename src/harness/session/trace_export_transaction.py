from __future__ import annotations

import os
from secrets import token_urlsafe
from types import TracebackType
from typing import Literal, final

from harness.session.trace_export_models import TraceExportError, TraceExportRequest
from harness.session.trace_export_paths import has_unsafe_ancestry
from core.atomic_file import atomic_create_bytes
from core.atomic_directory import rename_directory_no_replace
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    OwnedDirectoryChangedError,
    close_directory_identity,
    directory_lock_identity,
    empty_directory_identity,
    release_owned_directory,
)

atomic_directory_rename = rename_directory_no_replace
private_file_create = atomic_create_bytes
directory_fsync = os.fsync
DIRECTORY_SYNC_SUPPORTED = os.name != "nt"


@final
class TraceExportTransaction:
    """Stage one export privately and atomically publish it after its manifest."""

    def __init__(self, request: TraceExportRequest) -> None:
        self.final = request.destination
        self.staging = self.final.parent / f".trace.{token_urlsafe(8)}.tmp"
        self._publish_lock = self.final.parent / f".{self.final.name}.publish.lock"
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
        self._staging_identity: DirectoryLockIdentity | None = None
        self._publish_lock_identity: DirectoryLockIdentity | None = None
        self._publish_lock_owned = False
        self._publish_lock_token = token_urlsafe(24).encode("ascii")

    def __enter__(self) -> TraceExportTransaction:
        try:
            self._prepare()
        except TraceExportError as exc:
            staging_error = self._remove_staging()
            lock_error = self._remove_publish_lock()
            cleanup_error = staging_error or lock_error
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
        renamed = False
        try:
            _sync_directory(self.staging / "sessions")
            _sync_directory(self.staging / "overflows")
            _sync_directory(self.staging)
            atomic_directory_rename(self.staging, self.final)
            renamed = True
            published_identity = directory_lock_identity(self.final, retain=False)
            if published_identity != self._staging_identity:
                replacement_identity = directory_lock_identity(self.final)
                release_owned_directory(self.final, replacement_identity)
                raise OwnedDirectoryChangedError(
                    "published trace differs from the owned staging directory"
                )
            _sync_directory(self.final.parent)
            cleanup_error = self._remove_publish_lock()
            if cleanup_error is not None:
                raise OSError(cleanup_error)
            _sync_directory(self.final.parent)
        except OSError as exc:
            cleanup_detail = ""
            if renamed and self._staging_identity is not None:
                try:
                    release_owned_directory(self.final, self._staging_identity)
                    _sync_directory(self.final.parent)
                except (OSError, OwnedDirectoryChangedError) as cleanup_error:
                    cleanup_detail = (
                        f"; committed trace cleanup failed: {cleanup_error}"
                    )
            _ = self._remove_publish_lock()
            raise TraceExportError(
                self.final, f"trace commit interrupted: {exc}{cleanup_detail}"
            ) from exc
        self._committed = True
        if self._staging_identity is not None:
            close_directory_identity(self._staging_identity)

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._committed:
            return False
        staging_error = self._remove_staging()
        lock_error = self._remove_publish_lock()
        cleanup_error = staging_error or lock_error
        if _exception is not None:
            return False
        if cleanup_error is not None:
            error = TraceExportError(
                self.staging,
                f"trace staging cleanup failed: {cleanup_error}",
            )
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
            self._publish_lock.mkdir(mode=0o700)
            self._publish_lock_identity = empty_directory_identity(self._publish_lock)
            private_file_create(self._publish_lock / "owner", self._publish_lock_token)
            self._publish_lock_owned = True
            if destination.exists():
                raise TraceExportError(destination, "destination already exists")
            self.staging.mkdir(mode=0o700)
            self._staging_identity = empty_directory_identity(self.staging)
            (self.staging / "sessions").mkdir(mode=0o700)
            (self.staging / "overflows").mkdir(mode=0o700)
            self._parent_identity = _directory_identity(parent)
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
            ) and self._staging_identity == directory_lock_identity(
                self.staging, retain=False
            )
        except OSError:
            return False

    def _remove_staging(self) -> str | None:
        identity = self._staging_identity
        if identity is None:
            return None
        try:
            release_owned_directory(self.staging, identity)
        except FileNotFoundError:
            close_directory_identity(identity)
            self._staging_identity = None
            return None
        except (OSError, OwnedDirectoryChangedError) as exc:
            return str(exc)
        self._staging_identity = None
        return None

    def _remove_publish_lock(self) -> str | None:
        identity = self._publish_lock_identity
        if identity is None:
            return None
        try:
            release_owned_directory(
                self._publish_lock,
                identity,
                "owner" if self._publish_lock_owned else None,
                self._publish_lock_token if self._publish_lock_owned else None,
            )
        except FileNotFoundError:
            close_directory_identity(identity)
            self._publish_lock_identity = None
            return None
        except (OSError, OwnedDirectoryChangedError) as exc:
            return str(exc)
        self._publish_lock_identity = None
        self._publish_lock_owned = False
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
