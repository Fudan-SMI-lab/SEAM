from __future__ import annotations

import os
import secrets
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
from .continuation_lock_identity import (
    LockIdentity,
    lock_identity,
    lock_identity_is_linked,
    read_lock_path_snapshot,
)


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

    def _acquire(self) -> tuple[LockIdentity, BinaryIO]:
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
            self._remove_partial(lock_identity(initial))
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "exclusive continuation owner publication failed",
            ) from exc
        return (
            lock_identity(metadata),
            handle,
        )

    def _remove_partial(self, identity: LockIdentity) -> None:
        try:
            metadata = self._path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "partial continuation owner cleanup failed",
            ) from exc

        current = lock_identity(metadata)
        if current != identity:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "partial continuation owner changed before cleanup",
            )
        quarantine = self._path.with_name(
            f".{self._path.name}.{secrets.token_hex(16)}.partial"
        )
        try:
            os.replace(self._path, quarantine)
            quarantined = read_lock_path_snapshot(
                quarantine,
                len(self._content) + 1,
            )
            if (
                lock_identity_is_linked(quarantined.identity)
                or quarantined.identity != identity
            ):
                _restore_quarantined_path(quarantine, self._path)
                raise _error(
                    ContinuationErrorKind.LOCK_IO,
                    "partial continuation owner changed during quarantine cleanup",
                )
            quarantine.unlink()
        except ContinuationError:
            raise
        except OSError as exc:
            raise _error(
                ContinuationErrorKind.LOCK_IO,
                "partial continuation owner cleanup failed",
            ) from exc

    @property
    def active(self) -> bool:
        if self._handle.closed:
            return False
        try:
            metadata = self._path.lstat()
            current = lock_identity(metadata)
            handle_metadata = os.fstat(self._handle.fileno())
            handle_identity = lock_identity(handle_metadata)
            _ = self._handle.seek(0)
            content_matches = self._handle.read(len(self._content) + 1) == self._content
            path_snapshot = read_lock_path_snapshot(
                self._path,
                len(self._content) + 1,
            )
        except OSError:
            return False
        return (
            not lock_identity_is_linked(current)
            and current == self._identity
            and handle_identity == self._identity
            and content_matches
            and path_snapshot.matches(self._identity, self._content)
        )

    def release(self) -> None:
        try:
            metadata = self._path.lstat()
            current = lock_identity(metadata)
            handle_metadata = os.fstat(self._handle.fileno())
            handle_identity = lock_identity(handle_metadata)
            _ = self._handle.seek(0)
            content_matches = self._handle.read(len(self._content) + 1) == self._content
            path_snapshot = read_lock_path_snapshot(
                self._path,
                len(self._content) + 1,
            )
            if (
                lock_identity_is_linked(current)
                or current != self._identity
                or not content_matches
                or not path_snapshot.matches(self._identity, self._content)
            ):
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
            final_identity = lock_identity(final_metadata)
            if final_identity != self._identity:
                raise _error(
                    ContinuationErrorKind.LOCK_RELEASE,
                    "continuation owner changed during deterministic release",
                )
            quarantine = self._path.with_name(
                f".{self._path.name}.{secrets.token_hex(16)}.release"
            )
            os.replace(self._path, quarantine)
            quarantined = read_lock_path_snapshot(
                quarantine,
                len(self._content) + 1,
            )
            if not quarantined.matches(self._identity, self._content):
                _restore_quarantined_path(quarantine, self._path)
                raise _error(
                    ContinuationErrorKind.LOCK_RELEASE,
                    "continuation owner changed during quarantine release",
                )
            quarantine.unlink()
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


def _restore_quarantined_path(quarantine: Path, target: Path) -> None:
    try:
        os.link(quarantine, target)
    except FileExistsError:
        return
    quarantine.unlink()


@dataclass(frozen=True, slots=True)
class ActiveProjectOwnerLock:
    parent_run_id: str
    child_run_id: str
    lineage_root_run_id: str
    output_project: Path
    _owner: _ExclusiveProjectLock = field(repr=False, compare=False)

    @property
    def active(self) -> bool:
        return _ACTIVE_PROJECT_OWNER.get() is self and self._owner.active


_ACTIVE_PROJECT_OWNER: ContextVar[ActiveProjectOwnerLock | None] = ContextVar(
    "seam_active_project_owner",
    default=None,
)


def current_project_owner_lock() -> ActiveProjectOwnerLock | None:
    return _ACTIVE_PROJECT_OWNER.get()


def project_owner_lock_is_active(lock: ActiveProjectOwnerLock) -> bool:
    return lock.active


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
    proof = ActiveProjectOwnerLock(
        parent_run_id=str(authority.parent.run_id),
        child_run_id=request.child_run_id,
        lineage_root_run_id=str(authority.parent.run_manifest.lineage_root_run_id),
        output_project=authority.parent.output_project,
        _owner=owner,
    )
    active_token = _ACTIVE_PROJECT_OWNER.set(proof)
    try:
        yield authority.parent
    finally:
        try:
            owner.release()
        finally:
            _ACTIVE_PROJECT_OWNER.reset(active_token)
