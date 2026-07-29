from __future__ import annotations

from core.resource_retention import (
    ContainerDeleteAuthority,
    ContainerDeletionError,
    ContainerDeletionReceipt,
    ContainerRetention,
    CurrentRunContainerDeleteAuthority,
    resolve_v3_container_retention,
)
from core.types import ExecutionBackendConfig, WorkflowDefinition


class RecordingBackend:
    def __init__(self, deletion_error: ContainerDeletionError | None = None) -> None:
        self._container_id: str | None = "immutable-id"
        self.deletion_error: ContainerDeletionError | None = deletion_error
        self.delete_calls: list[ContainerDeleteAuthority] = []

    @property
    def container_id(self) -> str | None:
        return self._container_id

    def retention_entry_command(self) -> tuple[str, ...]:
        return ("docker", "exec", "-it", "immutable-id", "bash")

    def retention_state(self) -> str:
        return "running"

    def delete_container(
        self,
        authority: ContainerDeleteAuthority,
    ) -> ContainerDeletionReceipt:
        self.delete_calls.append(authority)
        if self.deletion_error is not None:
            raise self.deletion_error
        self._container_id = None
        return ContainerDeletionReceipt("immutable-id", "running", "absent")


def container_workflow(source: str = "image") -> WorkflowDefinition:
    config = ExecutionBackendConfig.from_dict(
        {
            "mode": "container",
            "source": source,
            "image": "cpu:test",
            "container_name": "external" if source == "existing_container" else None,
            "cleanup": True,
        }
    )
    return WorkflowDefinition(
        name="retention-test",
        version="1.0",
        phases=[],
        terminals=["complete"],
        execution_backend=config,
    )


def current_run_delete_authority(run_id: str) -> CurrentRunContainerDeleteAuthority:
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.DELETE, run_id
    )
    authority = policy.delete_authority
    assert isinstance(authority, CurrentRunContainerDeleteAuthority)
    return authority
