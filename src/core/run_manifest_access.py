from __future__ import annotations

from _thread import LockType
from io import BufferedRandom
import os
from pathlib import Path
from threading import Lock
from typing import NamedTuple, NoReturn, Protocol

from core.compat import TypeAlias, override

from core.continuation_lock_identity import BoundedReadError, read_verified_bytes
from core.run_manifest_models import (
    RUN_MANIFEST_FILENAME,
    ManifestErrorKind,
    RunId,
    RunManifestError,
    RunStorageContext,
    Sha256Digest,
)


class RegisteredManifestHandle(Protocol):
    pass


class RunManifestHandleError(TypeError): ...


class ManifestOwnershipTransitionError(RunManifestHandleError): ...


def register_store(store: RegisteredManifestHandle, writable: bool) -> None:
    _ = store, writable
    raise ManifestOwnershipTransitionError(
        "manifest permissions are issued by the owning RunManifestStore factory"
    )


def deny_permission_reassignment(
    _owner: RegisteredManifestHandle,
    _writable: bool,
) -> NoReturn:
    raise ManifestOwnershipTransitionError("manifest permission cannot be reassigned")


class ManifestHandleIdentity(NamedTuple):
    context: RunStorageContext
    run_id: RunId
    expected_workflow_digest: Sha256Digest


_ManifestAttribute: TypeAlias = (
    bytes | BufferedRandom | LockType | Path | RunStorageContext | Sha256Digest | None
)


class RunManifestHandleBase:
    _allocation_owner: bytes | None
    _allocation_handle: BufferedRandom | None
    _context: RunStorageContext
    _expected_workflow_digest: Sha256Digest
    _manifest_path: Path
    _run_dir: Path
    _thread_lock: LockType

    def __init__(
        self,
        context: RunStorageContext,
        run_id: RunId,
        expected_workflow_digest: Sha256Digest,
    ) -> None:
        self._initialize_handle(
            ManifestHandleIdentity(context, run_id, expected_workflow_digest),
        )

    @override
    def __setattr__(self, name: str, value: _ManifestAttribute) -> None:
        if name == "_permission":
            raise ManifestOwnershipTransitionError(
                "manifest permission cannot be reassigned"
            )
        if hasattr(self, name):
            raise ManifestOwnershipTransitionError(
                "manifest handles are immutable after creation"
            )
        object.__setattr__(self, name, value)

    def _initialize_handle(
        self,
        identity: ManifestHandleIdentity,
    ) -> None:
        if hasattr(self, "_context"):
            raise ManifestOwnershipTransitionError(
                "manifest handle ownership is already initialized"
            )
        run_dir = identity.context.authoritative_root / str(identity.run_id)
        self._context = identity.context
        self._run_dir = run_dir
        self._manifest_path = run_dir / RUN_MANIFEST_FILENAME
        self._expected_workflow_digest = identity.expected_workflow_digest
        self._thread_lock = Lock()
        self._allocation_owner = None
        self._allocation_handle = None

    def __copy__(self) -> NoReturn:
        raise RunManifestHandleError("RunManifestStore cannot be copied")

    def __deepcopy__(self, _memo: dict[int, RunManifestHandleBase]) -> NoReturn:
        raise RunManifestHandleError("RunManifestStore cannot be copied")

    @override
    def __reduce__(self) -> NoReturn:
        raise RunManifestHandleError("RunManifestStore cannot be serialized")

    def _registered_writable(self) -> bool:
        owner = self._allocation_owner
        handle = self._allocation_handle
        if owner is None or handle is None or handle.closed:
            return False
        try:
            lock_owner_descriptor(handle)
            return (
                read_verified_bytes(self._run_dir / ".allocation-owner", 128) == owner
            )
        except (BoundedReadError, FileNotFoundError, OSError):
            return False

    def _revoke_write_access(self) -> None:
        handle = self._allocation_handle
        if handle is not None and not handle.closed:
            unlock_owner_descriptor(handle)
            handle.close()
        object.__setattr__(self, "_allocation_handle", None)
        object.__setattr__(self, "_allocation_owner", None)

    def _require_registered(self) -> None:
        if not hasattr(self, "_context"):
            raise RunManifestError(
                ManifestErrorKind.READ_ONLY,
                "manifest handle is not initialized",
            )


def lock_owner_descriptor(handle: BufferedRandom) -> None:
    if os.name == "nt":
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_owner_descriptor(handle: BufferedRandom) -> None:
    if os.name == "nt":
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
