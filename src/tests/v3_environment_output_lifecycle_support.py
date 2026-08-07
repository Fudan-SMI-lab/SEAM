from __future__ import annotations

import shlex
from dataclasses import dataclass

from typing_extensions import assert_never

from core.resource_manifest import ResourceManifestStore
from core.resource_retention import (
    ContainerCleanupStatus,
    ContainerDeleteAuthority,
    ContainerDeletionError,
    ContainerDeletionReceipt,
    ContainerRetention,
    V3ContainerRetentionPolicy,
    resolve_v3_container_retention,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
)
from core.resource_retention_manifest import RetentionManifestFinalizer
from tests.resource_retention_test_support import container_workflow


@dataclass(frozen=True, slots=True)
class _MeasuredLifecycleBackend:
    entry_command: tuple[str, ...]
    state: str
    cleanup_fails: bool

    @property
    def container_id(self) -> str:
        return "cid-123"

    def retention_entry_command(self) -> tuple[str, ...]:
        return self.entry_command

    def retention_state(self) -> str:
        return self.state

    def delete_container(
        self,
        authority: ContainerDeleteAuthority,
    ) -> ContainerDeletionReceipt:
        del authority
        if self.cleanup_fails:
            raise ContainerDeletionError(
                self.container_id,
                self.state,
                self.state,
                "measured cleanup failure",
            )
        return ContainerDeletionReceipt(self.container_id, self.state, "absent")


def seal_lifecycle(
    store: ResourceManifestStore,
    *,
    cleanup: ContainerCleanupStatus,
    entry_command: str = "",
    post_state: str = "not_applicable",
) -> None:
    recorder = RetentionLifecycleRecorder()
    if cleanup is ContainerCleanupStatus.NOT_APPLICABLE:
        policy = V3ContainerRetentionPolicy(
            requested=ContainerRetention.RETAIN,
            effective=ContainerRetention.RETAIN,
            owner_kind="unknown",
            delete_authority=None,
        )
        backend = None
    elif cleanup is ContainerCleanupStatus.RETAINED:
        policy = resolve_v3_container_retention(
            container_workflow(), ContainerRetention.RETAIN, "run-safe-1"
        )
        backend = _MeasuredLifecycleBackend(
            tuple(shlex.split(entry_command)),
            post_state,
            False,
        )
    elif (
        cleanup is ContainerCleanupStatus.DELETED
        or cleanup is ContainerCleanupStatus.FAILED
    ):
        policy = resolve_v3_container_retention(
            container_workflow(), ContainerRetention.DELETE, "run-safe-1"
        )
        backend = _MeasuredLifecycleBackend(
            tuple(shlex.split(entry_command)),
            post_state,
            cleanup is ContainerCleanupStatus.FAILED,
        )
    else:
        assert_never(cleanup)
    finalizer = ContainerRetentionFinalizer(
        policy,
        backend,
        store.path.parent,
        recorder,
        continuation_evidence_available=lambda: post_state == "running",
    )
    manifest_finalizer = RetentionManifestFinalizer(store, recorder, backend)
    with _authorized_retention_finalization(finalizer):
        try:
            finalizer.run()
        except ContainerDeletionError:
            if cleanup is not ContainerCleanupStatus.FAILED:
                raise
    _ = manifest_finalizer.persist_and_seal("passed")
