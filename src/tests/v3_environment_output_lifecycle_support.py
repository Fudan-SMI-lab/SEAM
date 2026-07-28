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
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
)
from core.resource_retention_manifest import RetentionManifestFinalizer


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
    authority = CurrentRunContainerDeleteAuthority(
        original_owner_run_id="run-safe-1",
        lineage_root_run_id="run-safe-1",
        ownership_token="measured-owner-token",
        ownership_label="seam.owner=run-safe-1",
    )
    match cleanup:
        case ContainerCleanupStatus.NOT_APPLICABLE:
            policy = V3ContainerRetentionPolicy(
                requested=ContainerRetention.RETAIN,
                effective=ContainerRetention.RETAIN,
                owner_kind="unknown",
                delete_authority=None,
            )
            backend = None
        case ContainerCleanupStatus.RETAINED:
            policy = V3ContainerRetentionPolicy(
                requested=ContainerRetention.RETAIN,
                effective=ContainerRetention.RETAIN,
                owner_kind="framework",
                delete_authority=None,
            )
            backend = _MeasuredLifecycleBackend(
                tuple(shlex.split(entry_command)),
                post_state,
                False,
            )
        case ContainerCleanupStatus.DELETED | ContainerCleanupStatus.FAILED:
            policy = V3ContainerRetentionPolicy(
                requested=ContainerRetention.DELETE,
                effective=ContainerRetention.DELETE,
                owner_kind="framework",
                delete_authority=authority,
            )
            backend = _MeasuredLifecycleBackend(
                tuple(shlex.split(entry_command)),
                post_state,
                cleanup is ContainerCleanupStatus.FAILED,
            )
        case unreachable:
            assert_never(unreachable)
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
