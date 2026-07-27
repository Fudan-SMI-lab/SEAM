from __future__ import annotations

import os
import secrets
import socket
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import BinaryIO, final

from .continuation_models import (
    ContinuationError,
    ContinuationErrorKind,
    ContinuationRequest,
    OwnerMetadata,
    OwnerToken,
    ResolvedAuthority,
    ResolvedTerminalParent,
)
from .continuation_paths import PathKind, canonical_existing_path
from .continuation_resolver import resolve_authority


def _error(kind: ContinuationErrorKind, detail: str) -> ContinuationError:
    return ContinuationError(kind=kind, detail=detail)


@final
class _ExclusiveProjectLock:
    __slots__ = ("_content", "_handle", "_identity", "_path")

    def __init__(self, authority: ResolvedAuthority, child_run_id: str) -> None:
        lock_dir = authority.authoritative_root / "locks"
        try:
            lock_dir.mkdir(exist_ok=True)
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "external continuation lock directory is unavailable",
            ) from exc
        canonical_lock_dir = canonical_existing_path(
            lock_dir, PathKind.DIRECTORY, ContinuationErrorKind.LOCK_IO
        )
        self._path = canonical_lock_dir / f"{authority.workspace_digest}.lock"
        metadata = OwnerMetadata(
            parent_run_id=str(authority.parent.run_id),
            child_run_id=child_run_id,
            pid=os.getpid(),
            hostname=socket.gethostname() or "unknown-host",
            acquired_at_utc=datetime.now(timezone.utc).isoformat(),
            owner_token=str(OwnerToken(secrets.token_hex(16))),
        )
        self._content = (metadata.model_dump_json(by_alias=True) + "\n").encode("utf-8")
        self._identity, self._handle = self._acquire()

    def _acquire(self) -> tuple[tuple[int, int, int, int], BinaryIO]:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError as exc:
            raise _error(
                ContinuationErrorKind.PROJECT_LOCKED,
                "output project already has an exclusive continuation owner",
            ) from exc
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "exclusive continuation owner creation failed",
            ) from exc
        try:
            initial = os.fstat(descriptor)
        except OSError as exc:
            os.close(descriptor)
            try:
                self._path.unlink()
            except OSError as cleanup_error:
                raise _error(
                    ContinuationErrorKind.LOCK_IO,
                    "unidentified continuation owner cleanup failed",
                ) from cleanup_error
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "exclusive continuation owner identity could not be captured",
            ) from exc
        handle: BinaryIO | None = None
        try:
            handle = os.fdopen(descriptor, "w+b")
            _ = handle.write(self._content)
            handle.flush()
            os.fsync(handle.fileno())
            metadata = os.fstat(handle.fileno())
        except OSError as exc:
            if handle is not None:
                handle.close()
            else:
                os.close(descriptor)
            self._remove_partial(
                (
                    initial.st_dev,
                    initial.st_ino,
                    initial.st_mode,
                    getattr(initial, "st_file_attributes", 0),
                )
            )
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "exclusive continuation owner publication failed",
            ) from exc
        return (
            (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                getattr(metadata, "st_file_attributes", 0),
            ),
            handle,
        )

    def _remove_partial(self, identity: tuple[int, int, int, int]) -> None:
        try:
            metadata = self._path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "partial continuation owner cleanup failed",
            ) from exc
        current = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            getattr(metadata, "st_file_attributes", 0),
        )
        if current != identity:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "partial continuation owner changed before cleanup",
            )
        try:
            self._path.unlink()
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "partial continuation owner cleanup failed",
            ) from exc

    def release(self) -> None:
        try:
            metadata = self._path.lstat()
            current = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                getattr(metadata, "st_file_attributes", 0),
            )
            linked = stat.S_ISLNK(metadata.st_mode) or bool(current[3] & 0x400)
            handle_metadata = os.fstat(self._handle.fileno())
            handle_identity = (
                handle_metadata.st_dev,
                handle_metadata.st_ino,
                handle_metadata.st_mode,
                getattr(handle_metadata, "st_file_attributes", 0),
            )
            self._handle.seek(0)
            content_matches = self._handle.read(len(self._content) + 1) == self._content
            if linked or current != self._identity or not content_matches:
                raise _error(
                    ContinuationErrorKind.LOCK_RELEASE,
                    "continuation owner changed before deterministic release",
                )
            if handle_identity != self._identity:
                raise _error(
                    ContinuationErrorKind.LOCK_RELEASE,
                    "continuation owner handle changed before deterministic release",
                )
            self._handle.close()
            final_metadata = self._path.lstat()
            final_identity = (
                final_metadata.st_dev,
                final_metadata.st_ino,
                final_metadata.st_mode,
                getattr(final_metadata, "st_file_attributes", 0),
            )
            if final_identity != self._identity:
                raise _error(
                    ContinuationErrorKind.LOCK_RELEASE,
                    "continuation owner changed during deterministic release",
                )
            self._path.unlink()
        except ContinuationError:
            raise
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_RELEASE,
                "continuation owner could not be released",
            ) from exc
        finally:
            if not self._handle.closed:
                self._handle.close()


@contextmanager
def claim_terminal_parent(
    request: ContinuationRequest,
) -> Iterator[ResolvedTerminalParent]:
    authority = resolve_authority(request.summary_path)
    if request.child_run_id == authority.parent.run_id:
        raise _error(
            ContinuationErrorKind.CHILD_RUN_ID_REUSED,
            "continuation child run ID must differ from its parent",
        )
    owner = _ExclusiveProjectLock(authority, request.child_run_id)
    try:
        yield authority.parent
    finally:
        owner.release()
