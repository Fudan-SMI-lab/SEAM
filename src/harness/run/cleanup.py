from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Protocol
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from typing_extensions import override

from core.run_outcome import TerminalOutcome
from harness.server.lifecycle import stop_server

from .models import EMPTY_ARTIFACT_UPDATE, FinalizationHookError, RunArtifactUpdate

logger = logging.getLogger("harness.run.cleanup")


class CleanupObserver(Protocol):
    def record_cleanup_requested(self) -> None: ...

    def cleanup_sessions(self) -> int: ...

    def record_cleaned_sessions(self, count: int, /) -> None: ...


@unique
class CleanupResource(str, Enum):
    CLEANUP_REQUESTED = "cleanup requested bookkeeping"
    SESSIONS = "session cleanup"
    CLEANED_SESSIONS = "cleaned sessions bookkeeping"
    SERVER = "server cleanup"
    TEMP_DIRECTORY = "temporary directory cleanup"


@dataclass(frozen=True, slots=True)
class ResourceCleanupFailure:
    resource: CleanupResource
    error_type: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.resource.value} ({self.error_type}): {self.detail}"


@dataclass(frozen=True, slots=True)
class CleanupContext:
    temp_dir: Path | None
    keep_temp_dir: bool
    owns_temp_dir: bool
    observer: CleanupObserver | None
    server_process: subprocess.Popen[bytes] | None


@dataclass(frozen=True, slots=True)
class ResourceCleanup:
    context: CleanupContext

    def __call__(self, _outcome: TerminalOutcome) -> RunArtifactUpdate:
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
        ):
            try:
                shutil.rmtree(self.context.temp_dir)
            except Exception as exc:
                failures.append(self._failure(CleanupResource.TEMP_DIRECTORY, exc))
        if failures:
            raise FinalizationHookError(detail="; ".join(map(str, failures)))
        return EMPTY_ARTIFACT_UPDATE

    @staticmethod
    def _failure(
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
        return failure
