from __future__ import annotations

from pathlib import Path
import secrets
from types import TracebackType
import typing
from typing import Literal

from .resource_manifest_models import (
    ResourceManifestError,
    ResourceManifestErrorKind,
)
from .continuation_lock_identity import BoundedReadError, read_verified_bytes
from .owned_directory_lock import (
    DirectoryLockIdentity,
    OwnedDirectoryChangedError,
    directory_lock_identity,
    release_owned_directory,
)


class ResourceManifestLock:
    def __init__(self, report_dir: Path) -> None:
        self._path = report_dir / ".resource-manifest.lock"
        self._owner_path = self._path / "owner"
        self._owner_token: bytes | None = None
        self._lock_identity: DirectoryLockIdentity | None = None

    def __enter__(self) -> None:
        try:
            self._path.mkdir()
        except FileExistsError as exc:
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "another process owns the resource manifest lock",
            ) from exc
        token = secrets.token_hex(16).encode("ascii")
        try:
            with self._owner_path.open("xb") as handle:
                _ = handle.write(token)
                handle.flush()
        except OSError as exc:
            self._path.rmdir()
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "resource manifest lock ownership could not be established",
            ) from exc
        self._owner_token = token
        self._lock_identity = directory_lock_identity(self._path)

    def __exit__(
        self,
        exc_type: typing.Optional[typing.Type[BaseException]],
        exc_value: typing.Optional[BaseException],
        traceback: typing.Optional[TracebackType],
    ) -> Literal[False]:
        token = self._owner_token
        try:
            current = read_verified_bytes(self._owner_path, 128)
        except BoundedReadError as exc:
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "resource manifest lock ownership changed before release",
            ) from exc
        if token is None or current != token:
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "resource manifest lock ownership changed before release",
            )
        identity = self._lock_identity
        if identity is None:
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "resource manifest lock ownership changed before release",
            )
        try:
            release_owned_directory(self._path, identity, "owner", token)
        except (OSError, OwnedDirectoryChangedError) as exc:
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "resource manifest lock ownership changed before release",
            ) from exc
        self._owner_token = None
        self._lock_identity = None
        return False
