from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from secrets import token_urlsafe
import typing
from contextvars import ContextVar, Token
from types import TracebackType
from typing import TYPE_CHECKING, Literal, TypeAlias, final

from typing_extensions import assert_never, override

from .continuation_environment_models import (
    ContainerDeleteForbidden,
    ContinuationEnvironmentEligibility,
    ExistingContainerAttachment,
    FrameworkContainerDeleteEligible,
    _framework_container_delete_eligibility_is_verified,
)
from .continuation_lock import (
    ActiveProjectOwnerLock,
    current_project_owner_lock,
    project_owner_lock_is_active,
)
from .types import WorkflowDefinition
from .requested_cleanup_error import RequestedContainerCleanupError

ContainerOwnerKind: TypeAlias = Literal["framework", "user", "external", "unknown"]


@unique
class ContainerRetention(str, Enum):
    RETAIN = "retain"
    DELETE = "delete"


@unique
class ContainerCleanupStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    RETAINED = "retained"
    DELETED = "deleted"
    FAILED = "failed"


@unique
class _ContainerSource(str, Enum):
    IMAGE = "image"
    EXISTING = "existing_container"


@dataclass(frozen=True, slots=True)
class CurrentRunContainerDeleteAuthority:
    original_owner_run_id: str
    lineage_root_run_id: str
    ownership_token: str
    ownership_label: str


@dataclass(frozen=True, slots=True)
class ContinuationContainerDeleteAuthority:
    attachment: ExistingContainerAttachment
    eligibility: FrameworkContainerDeleteEligible
    owner_lock: ActiveProjectOwnerLock


if TYPE_CHECKING:
    ContainerDeleteAuthority: TypeAlias = (
        CurrentRunContainerDeleteAuthority | ContinuationContainerDeleteAuthority
    )
else:
    ContainerDeleteAuthority: TypeAlias = typing.Union[
        CurrentRunContainerDeleteAuthority, ContinuationContainerDeleteAuthority
    ]


@dataclass(frozen=True, slots=True)
class V3ContainerRetentionPolicy:
    requested: ContainerRetention
    effective: ContainerRetention
    owner_kind: ContainerOwnerKind
    delete_authority: ContainerDeleteAuthority | None
    attachment: ExistingContainerAttachment | None = None


@dataclass(frozen=True, slots=True)
class ContainerDeletionReceipt:
    container_id: str
    pre_state: str
    post_state: str


@dataclass(frozen=True, slots=True)
class ContainerDeletionError(RequestedContainerCleanupError):
    container_id: str
    pre_state: str
    post_state: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"container {self.container_id}: {self.detail}"


_ACTIVE_CONTAINER_CLEANUP: ContextVar[ContainerDeleteAuthority | None] = ContextVar(
    "seam_active_container_cleanup",
    default=None,
)


@final
class _AuthorizedContainerCleanup:
    __slots__ = ("_authority", "_token")

    def __init__(self, authority: ContainerDeleteAuthority) -> None:
        self._authority = authority
        self._token: Token[ContainerDeleteAuthority | None] | None = None

    def __enter__(self) -> None:
        self._token = _ACTIVE_CONTAINER_CLEANUP.set(self._authority)

    def __exit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        token = self._token
        if token is not None:
            _ACTIVE_CONTAINER_CLEANUP.reset(token)
            self._token = None
        return False


def _authorized_container_cleanup(
    authority: ContainerDeleteAuthority,
) -> _AuthorizedContainerCleanup:
    return _AuthorizedContainerCleanup(authority)


def _container_cleanup_is_authorized(authority: ContainerDeleteAuthority) -> bool:
    return _ACTIVE_CONTAINER_CLEANUP.get() is authority


def resolve_v3_container_retention(
    workflow: WorkflowDefinition,
    requested: ContainerRetention,
    run_id: str,
    continuation: ContinuationEnvironmentEligibility | None = None,
) -> V3ContainerRetentionPolicy:
    config = workflow.execution_backend
    if config is None or config.mode == "local":
        return V3ContainerRetentionPolicy(
            requested=requested,
            effective=ContainerRetention.RETAIN,
            owner_kind="unknown",
            delete_authority=None,
        )

    config.cleanup = False
    config.runtime_flags = [
        flag for flag in config.runtime_flags if flag.partition("=")[0] != "--rm"
    ]
    match _ContainerSource(config.source):
        case _ContainerSource.IMAGE:
            authority = CurrentRunContainerDeleteAuthority(
                original_owner_run_id=run_id,
                lineage_root_run_id=run_id,
                ownership_token=token_urlsafe(24),
                ownership_label=f"seam.owner={run_id}",
            )
            return V3ContainerRetentionPolicy(
                requested=requested,
                effective=requested,
                owner_kind="framework",
                delete_authority=authority,
            )
        case _ContainerSource.EXISTING:
            return _existing_container_policy(requested, continuation, run_id)
        case unreachable:
            assert_never(unreachable)


def _existing_container_policy(
    requested: ContainerRetention,
    continuation: ContinuationEnvironmentEligibility | None,
    child_run_id: str,
) -> V3ContainerRetentionPolicy:
    if continuation is None or continuation.attachment is None:
        return V3ContainerRetentionPolicy(
            requested=requested,
            effective=ContainerRetention.RETAIN,
            owner_kind="external",
            delete_authority=None,
        )
    attachment = continuation.attachment
    match continuation.deletion:
        case FrameworkContainerDeleteEligible() as eligibility:
            lock = current_project_owner_lock()
            lock_matches = (
                _framework_container_delete_eligibility_is_verified(eligibility)
                and lock is not None
                and project_owner_lock_is_active(lock)
                and lock.child_run_id == child_run_id
                and lock.lineage_root_run_id == eligibility.lineage_root_run_id
                and attachment.lineage_root_run_id == eligibility.lineage_root_run_id
                and attachment.original_owner_run_id
                == eligibility.original_owner_run_id
                and attachment.ownership_token == eligibility.ownership_token
                and attachment.ownership_label == eligibility.ownership_label
            )
            if lock_matches and lock is not None:
                return V3ContainerRetentionPolicy(
                    requested=requested,
                    effective=requested,
                    owner_kind=attachment.owner_kind,
                    delete_authority=ContinuationContainerDeleteAuthority(
                        attachment=attachment,
                        eligibility=eligibility,
                        owner_lock=lock,
                    ),
                    attachment=attachment,
                )
        case ContainerDeleteForbidden():
            pass
        case unreachable:
            assert_never(unreachable)
    return V3ContainerRetentionPolicy(
        requested=requested,
        effective=ContainerRetention.RETAIN,
        owner_kind=attachment.owner_kind,
        delete_authority=None,
        attachment=attachment,
    )
