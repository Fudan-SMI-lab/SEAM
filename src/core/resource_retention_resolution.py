from __future__ import annotations

from enum import Enum, unique
from secrets import token_urlsafe

from typing_extensions import assert_never

from core.continuation_environment_models import (
    ContinuationEnvironmentEligibility,
    FrameworkContainerDeleteEligible,
    framework_container_delete_eligibility_is_verified,
)
from core.continuation_lock_context import (
    current_project_owner_lock,
    project_owner_lock_is_active,
)
from core.resource_retention_authority import (
    ContainerRetention,
    ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
    _DeleteAuthorityBinding,
    _DELETE_AUTHORITY_BINDINGS,
)
from core.types import WorkflowDefinition


@unique
class _ContainerSource(str, Enum):
    IMAGE = "image"
    EXISTING = "existing_container"


def resolve_v3_container_retention(
    workflow: WorkflowDefinition,
    requested: ContainerRetention,
    run_id: str,
    continuation: ContinuationEnvironmentEligibility | None = None,
) -> V3ContainerRetentionPolicy:
    config = workflow.execution_backend
    if config is None or config.mode == "local":
        return V3ContainerRetentionPolicy(
            requested, ContainerRetention.RETAIN, "unknown", None
        )
    config.cleanup = False
    config.runtime_flags = [
        flag for flag in config.runtime_flags if flag.partition("=")[0] != "--rm"
    ]
    source = _ContainerSource(config.source)
    if source is _ContainerSource.IMAGE:
        authority = object.__new__(CurrentRunContainerDeleteAuthority)
        object.__setattr__(authority, "_original_owner_run_id", run_id)
        object.__setattr__(authority, "_lineage_root_run_id", run_id)
        object.__setattr__(authority, "_ownership_token", token_urlsafe(24))
        object.__setattr__(authority, "_ownership_label", f"seam.owner={run_id}")
        _DELETE_AUTHORITY_BINDINGS[id(authority)] = _DeleteAuthorityBinding(
            authority,
            (
                authority.original_owner_run_id,
                authority.lineage_root_run_id,
                authority.ownership_token,
                authority.ownership_label,
            ),
        )
        return V3ContainerRetentionPolicy(requested, requested, "framework", authority)
    if source is _ContainerSource.EXISTING:
        return _existing_container_policy(requested, continuation, run_id)
    assert_never(source)


def _existing_container_policy(
    requested: ContainerRetention,
    continuation: ContinuationEnvironmentEligibility | None,
    child_run_id: str,
) -> V3ContainerRetentionPolicy:
    if continuation is None or continuation.attachment is None:
        return V3ContainerRetentionPolicy(
            requested, ContainerRetention.RETAIN, "external", None
        )
    attachment = continuation.attachment
    deletion = continuation.deletion
    if isinstance(deletion, FrameworkContainerDeleteEligible):
        lock = current_project_owner_lock()
        lock_matches = (
            framework_container_delete_eligibility_is_verified(deletion)
            and lock is not None
            and project_owner_lock_is_active(lock)
            and lock.child_run_id == child_run_id
            and lock.lineage_root_run_id == deletion.lineage_root_run_id
            and attachment.lineage_root_run_id == deletion.lineage_root_run_id
            and attachment.original_owner_run_id == deletion.original_owner_run_id
            and attachment.ownership_token == deletion.ownership_token
            and attachment.ownership_label == deletion.ownership_label
        )
        if lock_matches and lock is not None:
            authority = object.__new__(ContinuationContainerDeleteAuthority)
            object.__setattr__(authority, "_attachment", attachment)
            object.__setattr__(authority, "_eligibility", deletion)
            object.__setattr__(authority, "_owner_lock", lock)
            _DELETE_AUTHORITY_BINDINGS[id(authority)] = _DeleteAuthorityBinding(
                authority, (attachment, deletion, lock)
            )
            return V3ContainerRetentionPolicy(
                requested,
                requested,
                attachment.owner_kind,
                authority,
                attachment,
            )
        return V3ContainerRetentionPolicy(
            requested,
            ContainerRetention.RETAIN,
            attachment.owner_kind,
            None,
            attachment,
        )
    return V3ContainerRetentionPolicy(
        requested,
        ContainerRetention.RETAIN,
        attachment.owner_kind,
        None,
        attachment,
    )
