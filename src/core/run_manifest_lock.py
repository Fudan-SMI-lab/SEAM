from __future__ import annotations

from pathlib import Path
import secrets
from types import TracebackType
from typing import Literal, final

from .atomic_file import atomic_create_bytes
from .continuation_lock_identity import BoundedReadError, read_verified_bytes
from .owned_directory_lock import (
    DirectoryLockIdentity,
    OwnedDirectoryChangedError,
    close_directory_identity,
    empty_directory_identity,
    release_owned_directory,
)
from .run_manifest_models import ManifestErrorKind, RunManifestError


@final
class RunFileLock:
    def __init__(self, run_dir: Path) -> None:
        self._lock_dir = run_dir / ".manifest-write.lock"
        self._owner_path = self._lock_dir / "owner"
        self._owner_token: bytes | None = None
        self._lock_identity: DirectoryLockIdentity | None = None

    def __enter__(self) -> None:
        try:
            self._lock_dir.mkdir()
        except FileExistsError as exc:
            raise RunManifestError(
                ManifestErrorKind.CONCURRENT_WRITE,
                "another process owns the run lock",
            ) from exc
        token = secrets.token_hex(16).encode("ascii")
        identity: DirectoryLockIdentity | None = None
        try:
            identity = empty_directory_identity(self._lock_dir)
            atomic_create_bytes(self._owner_path, token)
        except OSError as exc:
            cleanup_detail = ""
            if identity is not None:
                try:
                    release_owned_directory(self._lock_dir, identity)
                except OSError as cleanup_error:
                    cleanup_detail = f"; cleanup failed: {cleanup_error}"
            raise RunManifestError(
                ManifestErrorKind.CONCURRENT_WRITE,
                f"run lock ownership could not be established{cleanup_detail}",
            ) from exc
        self._owner_token = token
        self._lock_identity = identity

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        identity = self._lock_identity
        token = self._owner_token
        try:
            try:
                current = read_verified_bytes(self._owner_path, 128)
            except BoundedReadError as exc:
                raise RunManifestError(
                    ManifestErrorKind.CONCURRENT_WRITE,
                    "run lock ownership changed before release",
                ) from exc
            if token is None or current != token or identity is None:
                raise RunManifestError(
                    ManifestErrorKind.CONCURRENT_WRITE,
                    "run lock ownership changed before release",
                )
            try:
                release_owned_directory(self._lock_dir, identity, "owner", token)
            except (OSError, OwnedDirectoryChangedError) as exc:
                raise RunManifestError(
                    ManifestErrorKind.CONCURRENT_WRITE,
                    "run lock ownership changed before release",
                ) from exc
            self._owner_token = None
            self._lock_identity = None
            return False
        finally:
            if identity is not None:
                close_directory_identity(identity)
