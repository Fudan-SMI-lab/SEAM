from __future__ import annotations

import shlex
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

from typing_extensions import assert_never

from .continuation_lock import project_owner_lock_is_active
from .resource_manifest_models import (
    FactProvenance,
    ProvenanceFact,
    ResourceManifestUpdate,
)
from .resource_retention import (
    ContainerCleanupStatus,
    ContainerDeleteAuthority,
    ContainerDeletionError,
    ContainerDeletionReceipt,
    ContainerOwnerKind,
    ContainerRetention,
    ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
    _authorized_container_cleanup,
)


_ACTIVE_RETENTION_FINALIZER: ContextVar[object | None] = ContextVar(
    "active_retention_finalizer", default=None
)


def _continuation_evidence_unavailable() -> bool:
    return False


class RetentionBackend(Protocol):
    @property
    def container_id(self) -> str | None: ...

    def retention_entry_command(self) -> tuple[str, ...]: ...

    def retention_state(self) -> str: ...

    def delete_container(
        self,
        authority: ContainerDeleteAuthority,
    ) -> ContainerDeletionReceipt: ...


@dataclass(frozen=True, slots=True)
class RetentionLifecycleRecord:
    requested: ContainerRetention
    effective: ContainerRetention
    owner_kind: ContainerOwnerKind
    entry_command: str
    pre_state: str
    post_state: str
    cleanup_status: ContainerCleanupStatus
    continuation_available: bool


class RetentionLifecycleRecorder:
    """Retains the one post-cleanup lifecycle observation for manifest sealing."""

    __slots__ = ("_record",)

    def __init__(self) -> None:
        self._record: RetentionLifecycleRecord | None = None

    def record(self, value: RetentionLifecycleRecord) -> None:
        self._record = value

    def require_record(self) -> RetentionLifecycleRecord:
        if self._record is None:
            raise ContainerDeletionError(
                "unknown", "unknown", "unknown", "cleanup did not run"
            )
        return self._record


@dataclass(frozen=True, slots=True)
class ContainerRetentionFinalizer:
    policy: V3ContainerRetentionPolicy
    backend: RetentionBackend | None
    output_project: Path | None
    recorder: RetentionLifecycleRecorder
    continuation_evidence_available: Callable[[], bool] = (
        _continuation_evidence_unavailable
    )

    def run(self) -> None:
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
            self.recorder.record(
                RetentionLifecycleRecord(
                    self.policy.requested,
                    self.policy.effective,
                    self.policy.owner_kind,
                    entry,
                    state,
                    state,
                    ContainerCleanupStatus.RETAINED,
                    available,
                )
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
        if _ACTIVE_RETENTION_FINALIZER.get() is not self:
            error = ContainerDeletionError(
                backend.container_id,
                "running",
                "running",
                "authorized cleanup finalization stage required",
            )
            self._record_failure(entry, error)
            raise error
        match authority:
            case ContinuationContainerDeleteAuthority(owner_lock=lock):
                if not project_owner_lock_is_active(lock):
                    error = ContainerDeletionError(
                        backend.container_id,
                        "running",
                        "running",
                        "active project owner lock required",
                    )
                    self._record_failure(entry, error)
                    raise error
            case CurrentRunContainerDeleteAuthority():
                pass
            case unreachable:
                assert_never(unreachable)
        try:
            with _authorized_container_cleanup(authority):
                receipt = backend.delete_container(authority)
        except ContainerDeletionError as error:
            self._record_failure(entry, error)
            raise
        self.recorder.record(
            RetentionLifecycleRecord(
                self.policy.requested,
                self.policy.effective,
                self.policy.owner_kind,
                entry,
                receipt.pre_state,
                receipt.post_state,
                ContainerCleanupStatus.DELETED,
                False,
            )
        )

    def _record_without_container(self) -> None:
        available = (
            self.continuation_evidence_available()
            and self.output_project is not None
            and self.output_project.is_dir()
        )
        self.recorder.record(
            RetentionLifecycleRecord(
                self.policy.requested,
                ContainerRetention.RETAIN,
                self.policy.owner_kind,
                "",
                "not_applicable",
                "not_applicable",
                ContainerCleanupStatus.NOT_APPLICABLE,
                available,
            )
        )

    def _record_failure(self, entry: str, error: ContainerDeletionError) -> None:
        available = (
            error.post_state == "running"
            and self.continuation_evidence_available()
            and self.output_project is not None
            and self.output_project.is_dir()
        )
        self.recorder.record(
            RetentionLifecycleRecord(
                self.policy.requested,
                self.policy.effective,
                self.policy.owner_kind,
                entry,
                error.pre_state,
                error.post_state,
                ContainerCleanupStatus.FAILED,
                available,
            )
        )


@contextmanager
def _authorized_retention_finalization(
    finalizer: ContainerRetentionFinalizer,
) -> Iterator[None]:
    token = _ACTIVE_RETENTION_FINALIZER.set(finalizer)
    try:
        yield
    finally:
        _ACTIVE_RETENTION_FINALIZER.reset(token)


def retention_manifest_update(
    record: RetentionLifecycleRecord,
    expected_revision: int,
) -> ResourceManifestUpdate:
    values = (
        ("retention.owner_kind", record.owner_kind),
        ("retention.entry_command", record.entry_command or "not_applicable"),
        ("retention.pre_state", record.pre_state),
        ("retention.post_state", record.post_state),
        ("retention.cleanup_result", record.cleanup_status.value),
        (
            "retention.continuation_available",
            "true" if record.continuation_available else "false",
        ),
    )
    return ResourceManifestUpdate(
        expected_revision=expected_revision,
        facts=tuple(
            ProvenanceFact(
                name=name,
                value=value,
                provenance=FactProvenance.DERIVED,
                namespace="host",
            )
            for name, value in values
        ),
    )
