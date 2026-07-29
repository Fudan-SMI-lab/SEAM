from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from typing_extensions import assert_never

from .continuation_lock import project_owner_lock_is_active
from .resource_retention import (
    ContainerCleanupStatus,
    ContainerDeletionError,
    ContainerRetention,
    ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
    _authorized_container_cleanup,
)
from .resource_retention_lifecycle import (
    RetentionBackend as RetentionBackend,
    RetentionLifecycleRecord as RetentionLifecycleRecord,
    retention_manifest_update as retention_manifest_update,
)
from .resource_retention_recorder import (
    RetentionLifecycleRecorder as RetentionLifecycleRecorder,
    _authorized_retention_finalization as _authorized_retention_finalization,
    _retention_finalizer_is_active,
)


def _continuation_evidence_unavailable() -> bool:
    return False


@dataclass(frozen=True)
class ContainerRetentionFinalizer:
    policy: V3ContainerRetentionPolicy
    backend: RetentionBackend | None
    output_project: Path | None
    recorder: RetentionLifecycleRecorder
    continuation_evidence_available: Callable[[], bool] = (
        _continuation_evidence_unavailable
    )

    def run(self) -> None:
        if not _retention_finalizer_is_active(self):
            raise ContainerDeletionError(
                self.backend.container_id
                if self.backend is not None and self.backend.container_id is not None
                else "unknown",
                "unknown",
                "unknown",
                "authorized cleanup finalization stage required",
            )
        backend = self.backend
        if backend is None or backend.container_id is None:
            self._record_without_container()
            return
        entry = shlex.join(backend.retention_entry_command())
        if self.policy.effective is ContainerRetention.RETAIN:
            state = backend.retention_state()
            available = (
                state == "running"
                and self.continuation_evidence_available()
                and self.output_project is not None
                and self.output_project.is_dir()
            )
            self.recorder._record_measured(
                self,
                RetentionLifecycleRecord(
                    self.policy.requested,
                    self.policy.effective,
                    self.policy.owner_kind,
                    entry,
                    state,
                    state,
                    ContainerCleanupStatus.RETAINED,
                    available,
                ),
            )
            return
        authority = self.policy.delete_authority
        if authority is None:
            raise ContainerDeletionError(
                backend.container_id,
                "running",
                "running",
                "delete policy has no ownership authority",
            )
        if isinstance(authority, ContinuationContainerDeleteAuthority):
            if not project_owner_lock_is_active(authority.owner_lock):
                error = ContainerDeletionError(
                    backend.container_id,
                    "running",
                    "running",
                    "active project owner lock required",
                )
                self._record_failure(entry, error)
                raise error
        elif not isinstance(authority, CurrentRunContainerDeleteAuthority):
            assert_never(authority)
        container_id = backend.container_id
        try:
            with _authorized_container_cleanup(authority):
                receipt = backend.delete_container(authority)
        except ContainerDeletionError as error:
            self._record_failure(entry, error)
            raise
        if receipt.container_id != container_id:
            raise ContainerDeletionError(
                container_id,
                receipt.pre_state,
                receipt.post_state,
                "deletion receipt identity differs from requested backend",
            )
        self.recorder._record_measured(
            self,
            RetentionLifecycleRecord(
                self.policy.requested,
                self.policy.effective,
                self.policy.owner_kind,
                entry,
                receipt.pre_state,
                receipt.post_state,
                ContainerCleanupStatus.DELETED,
                False,
            ),
        )

    def _record_without_container(self) -> None:
        available = (
            self.continuation_evidence_available()
            and self.output_project is not None
            and self.output_project.is_dir()
        )
        self.recorder._record_measured(
            self,
            RetentionLifecycleRecord(
                self.policy.requested,
                ContainerRetention.RETAIN,
                self.policy.owner_kind,
                "",
                "not_applicable",
                "not_applicable",
                ContainerCleanupStatus.NOT_APPLICABLE,
                available,
            ),
        )

    def _record_failure(self, entry: str, error: ContainerDeletionError) -> None:
        available = (
            error.post_state == "running"
            and self.continuation_evidence_available()
            and self.output_project is not None
            and self.output_project.is_dir()
        )
        self.recorder._record_measured(
            self,
            RetentionLifecycleRecord(
                self.policy.requested,
                self.policy.effective,
                self.policy.owner_kind,
                entry,
                error.pre_state,
                error.post_state,
                ContainerCleanupStatus.FAILED,
                available,
            ),
        )
