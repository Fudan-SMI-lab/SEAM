from __future__ import annotations

from enum import Enum, unique
from secrets import token_urlsafe
from typing import NamedTuple, Protocol

from typing_extensions import assert_never

from core.continuation_environment_models import (
    ContainerDeleteForbidden,
    ContinuationEnvironmentEligibility,
    FrameworkContainerDeleteEligible,
    framework_container_delete_eligibility_is_verified,
)
from core.continuation_lock_context import (
    ActiveProjectOwnerLock,
    current_project_owner_lock,
    project_owner_lock_is_active,
)
from core.resource_retention_authority import (
    ContainerDeleteAuthority,
    ContainerRetention,
    ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
)
from core.types import WorkflowDefinition


@unique
class _ContainerSource(str, Enum):
    IMAGE = "image"
    EXISTING = "existing_container"


class _DeleteAuthorityBinding(NamedTuple):
    authority: ContainerDeleteAuthority
    ownership: (
        tuple[str, str, str, str]
        | tuple[
            str,
            str,
            str | None,
            str | None,
            str | None,
            str | None,
            str,
            str,
            str,
            str,
            ActiveProjectOwnerLock,
        ]
    )
    policy: V3ContainerRetentionPolicy


class _RetentionResolver(Protocol):
    def __call__(
        self,
        workflow: WorkflowDefinition,
        requested: ContainerRetention,
        run_id: str,
        continuation: ContinuationEnvironmentEligibility | None = None,
    ) -> V3ContainerRetentionPolicy: ...


class _AuthorityRegistrationCheck(Protocol):
    def __call__(self, authority: ContainerDeleteAuthority) -> bool: ...


class _PolicyRegistrationCheck(Protocol):
    def __call__(self, policy: V3ContainerRetentionPolicy) -> bool: ...


def _creator_bound_retention() -> tuple[
    _RetentionResolver,
    _AuthorityRegistrationCheck,
    _PolicyRegistrationCheck,
]:
    bindings: dict[int, _DeleteAuthorityBinding] = {}

    def authority_is_registered(authority: ContainerDeleteAuthority) -> bool:
        binding = bindings.get(id(authority))
        match authority:
            case CurrentRunContainerDeleteAuthority():
                ownership = (
                    authority.original_owner_run_id,
                    authority.lineage_root_run_id,
                    authority.ownership_token,
                    authority.ownership_label,
                )
            case ContinuationContainerDeleteAuthority():
                attachment = authority.attachment
                eligibility = authority.eligibility
                ownership = (
                    attachment.container_id,
                    attachment.container_name,
                    attachment.original_owner_run_id,
                    attachment.lineage_root_run_id,
                    attachment.ownership_token,
                    attachment.ownership_label,
                    eligibility.original_owner_run_id,
                    eligibility.lineage_root_run_id,
                    eligibility.ownership_token,
                    eligibility.ownership_label,
                    authority.owner_lock,
                )
            case unreachable:
                assert_never(unreachable)
        return (
            binding is not None
            and binding.authority is authority
            and binding.ownership == ownership
        )

    def policy_is_registered(policy: V3ContainerRetentionPolicy) -> bool:
        authority = policy.delete_authority
        if authority is None:
            return policy.effective is ContainerRetention.RETAIN
        binding = bindings.get(id(authority))
        return binding is not None and binding.policy is policy

    def existing_policy(
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
        match deletion:
            case FrameworkContainerDeleteEligible():
                lock = current_project_owner_lock()
                lock_matches = (
                    framework_container_delete_eligibility_is_verified(deletion)
                    and lock is not None
                    and project_owner_lock_is_active(lock)
                    and lock.child_run_id == child_run_id
                    and lock.lineage_root_run_id == deletion.lineage_root_run_id
                    and attachment.lineage_root_run_id == deletion.lineage_root_run_id
                    and attachment.original_owner_run_id
                    == deletion.original_owner_run_id
                    and attachment.ownership_token == deletion.ownership_token
                    and attachment.ownership_label == deletion.ownership_label
                )
                if lock_matches and lock is not None:
                    authority = object.__new__(ContinuationContainerDeleteAuthority)
                    object.__setattr__(authority, "_attachment", attachment)
                    object.__setattr__(authority, "_eligibility", deletion)
                    object.__setattr__(authority, "_owner_lock", lock)
                    policy = V3ContainerRetentionPolicy(
                        requested,
                        requested,
                        attachment.owner_kind,
                        authority,
                        attachment,
                    )
                    bindings[id(authority)] = _DeleteAuthorityBinding(
                        authority,
                        (
                            attachment.container_id,
                            attachment.container_name,
                            attachment.original_owner_run_id,
                            attachment.lineage_root_run_id,
                            attachment.ownership_token,
                            attachment.ownership_label,
                            deletion.original_owner_run_id,
                            deletion.lineage_root_run_id,
                            deletion.ownership_token,
                            deletion.ownership_label,
                            lock,
                        ),
                        policy,
                    )
                    return policy
            case ContainerDeleteForbidden():
                pass
            case unreachable:
                assert_never(unreachable)
        return V3ContainerRetentionPolicy(
            requested,
            ContainerRetention.RETAIN,
            attachment.owner_kind,
            None,
            attachment,
        )

    def resolve(
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
            policy = V3ContainerRetentionPolicy(
                requested, requested, "framework", authority
            )
            bindings[id(authority)] = _DeleteAuthorityBinding(
                authority,
                (
                    authority.original_owner_run_id,
                    authority.lineage_root_run_id,
                    authority.ownership_token,
                    authority.ownership_label,
                ),
                policy,
            )
            return policy
        if source is _ContainerSource.EXISTING:
            return existing_policy(requested, continuation, run_id)
        assert_never(source)

    return resolve, authority_is_registered, policy_is_registered


(
    resolve_v3_container_retention,
    delete_authority_is_registered,
    retention_policy_is_registered,
) = _creator_bound_retention()
del _creator_bound_retention
