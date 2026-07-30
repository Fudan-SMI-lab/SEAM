from __future__ import annotations

import logging
import subprocess
from typing import Protocol
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from typing_extensions import override

from core.run_outcome import TerminalOutcome
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    close_directory_identity,
    directory_lock_identity,
    release_owned_directory,
)
from harness.server.lifecycle import stop_server

from .models import EMPTY_ARTIFACT_UPDATE, FinalizationHookError, RunArtifactUpdate

logger = logging.getLogger("harness.run.cleanup")


class CleanupObserver(Protocol):
    def record_cleanup_requested(self) -> None: ...

    def cleanup_sessions(self) -> int: ...

    def record_cleaned_sessions(self, count: int, /) -> None: ...

    def record_cleanup_failure(
        self,
        resource: str,
        error_type: str,
        detail: str,
    ) -> None: ...


@unique
class CleanupResource(str, Enum):
    CLEANUP_REQUESTED = "cleanup requested bookkeeping"
    SESSIONS = "session cleanup"
    CLEANED_SESSIONS = "cleaned sessions bookkeeping"
    SERVER = "server cleanup"
    TEMP_DIRECTORY = "temporary directory cleanup"


@dataclass(frozen=True)
class ResourceCleanupFailure:
    resource: CleanupResource
    error_type: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.resource.value} ({self.error_type}): {self.detail}"


@dataclass(frozen=True)
class CleanupContext:
    temp_dir: Path | None
    keep_temp_dir: bool
    owns_temp_dir: bool
    observer: CleanupObserver | None
    server_process: subprocess.Popen[bytes] | None
    owned_temp_identity: DirectoryLockIdentity | None = None

    def __post_init__(self) -> None:
        if (
            self.temp_dir is not None
            and self.owns_temp_dir
            and self.owned_temp_identity is None
        ):
            object.__setattr__(
                self,
                "owned_temp_identity",
                directory_lock_identity(self.temp_dir, retain=True),
            )


@dataclass(frozen=True)
class ResourceCleanup:
    context: CleanupContext

    def __call__(self, _outcome: TerminalOutcome) -> RunArtifactUpdate:
        try:
            return self._run_cleanup()
        finally:
            identity = self.context.owned_temp_identity
            if identity is not None:
                close_directory_identity(identity)

    def _run_cleanup(self) -> RunArtifactUpdate:
        failures: list[ResourceCleanupFailure] = []
        observer = self.context.observer
        if self.context.temp_dir is not None and observer is not None:
            try:
                observer.record_cleanup_requested()
            except Exception as exc:
                failures.append(self._failure(CleanupResource.CLEANUP_REQUESTED, exc))
            cleaned_sessions: int | None = None
            try:
                cleaned_sessions = observer.cleanup_sessions()
            except Exception as exc:
                failures.append(self._failure(CleanupResource.SESSIONS, exc))
            if cleaned_sessions is not None:
                try:
                    observer.record_cleaned_sessions(cleaned_sessions)
                except Exception as exc:
                    failures.append(
                        self._failure(CleanupResource.CLEANED_SESSIONS, exc)
                    )
        if self.context.server_process is not None:
            try:
                _ = stop_server(self.context.server_process)
            except Exception as exc:
                failures.append(self._failure(CleanupResource.SERVER, exc))
        if (
            self.context.temp_dir is not None
            and not self.context.keep_temp_dir
            and self.context.owns_temp_dir
            and self.context.owned_temp_identity is not None
        ):
            try:
                release_owned_directory(
                    self.context.temp_dir,
                    self.context.owned_temp_identity,
                )
            except Exception as exc:
                failures.append(self._failure(CleanupResource.TEMP_DIRECTORY, exc))
        if failures:
            raise FinalizationHookError(detail="; ".join(map(str, failures)))
        return EMPTY_ARTIFACT_UPDATE

    def _failure(
        self,
        resource: CleanupResource,
        error: Exception,
    ) -> ResourceCleanupFailure:
        failure = ResourceCleanupFailure(resource, type(error).__name__, str(error))
        logger.warning(
            "Resource %s failed with %s: %s",
            resource.value,
            failure.error_type,
            failure.detail,
        )
        observer = self.context.observer
        if observer is not None:
            try:
                observer.record_cleanup_failure(
                    resource.value,
                    failure.error_type,
                    failure.detail,
                )
            except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - diagnostic sink boundary
                logger.warning("Cleanup failure telemetry could not be recorded")
        return failure
