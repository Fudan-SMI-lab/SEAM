from __future__ import annotations

from enum import Enum, unique
from pathlib import Path
from typing import Final, Literal, NamedTuple, NoReturn, final

from core.compat import TypeAlias, override

from core.continuation_environment_models import (
    ExistingContainerAttachment,
    FrameworkContainerDeleteEligible,
)
from core.continuation_lock_context import ActiveProjectOwnerLock
from core.requested_cleanup_error import RequestedContainerCleanupError

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


class DeleteAuthorityError(TypeError): ...


class RetentionOwnershipTransitionError(DeleteAuthorityError): ...


class _OpaqueDeleteAuthority:
    __slots__: tuple[str, ...] = ()

    @override
    def __setattr__(
        self,
        name: str,
        value: (
            str
            | ExistingContainerAttachment
            | FrameworkContainerDeleteEligible
            | ActiveProjectOwnerLock
        ),
    ) -> None:
        if hasattr(self, name):
            raise AttributeError("container deletion authority is immutable")
        object.__setattr__(self, name, value)

    def __copy__(self) -> NoReturn:
        raise DeleteAuthorityError("container deletion authority cannot be copied")

    def __deepcopy__(self, _memo: dict[int, _OpaqueDeleteAuthority]) -> NoReturn:
        raise DeleteAuthorityError("container deletion authority cannot be copied")

    @override
    def __reduce__(self) -> NoReturn:
        raise DeleteAuthorityError("container deletion authority cannot be serialized")


class _InactiveLockOwner:
    @property
    def active(self) -> bool:
        return False


_INACTIVE_LOCK_OWNER = _InactiveLockOwner()


@final
class CurrentRunContainerDeleteAuthority(_OpaqueDeleteAuthority):
    __slots__ = (
        "_lineage_root_run_id",
        "_original_owner_run_id",
        "_ownership_label",
        "_ownership_token",
    )

    def __init__(self) -> None:
        self._original_owner_run_id = ""
        self._lineage_root_run_id = ""
        self._ownership_token = ""
        self._ownership_label = ""
        raise DeleteAuthorityError(
            "container deletion authority is issued by retention policy"
        )

    @property
    def original_owner_run_id(self) -> str:
        return self._original_owner_run_id

    @property
    def lineage_root_run_id(self) -> str:
        return self._lineage_root_run_id

    @property
    def ownership_token(self) -> str:
        return self._ownership_token

    @property
    def ownership_label(self) -> str:
        return self._ownership_label


@final
class ContinuationContainerDeleteAuthority(_OpaqueDeleteAuthority):
    __slots__ = ("_attachment", "_eligibility", "_owner_lock")

    def __init__(self) -> None:
        self._attachment = ExistingContainerAttachment(
            "existing_container", "docker", "", "", "framework", "", "", "", ""
        )
        self._eligibility = FrameworkContainerDeleteEligible("", "", "", "")
        self._owner_lock = ActiveProjectOwnerLock(
            "", "", "", Path("."), _INACTIVE_LOCK_OWNER
        )
        raise DeleteAuthorityError(
            "container deletion authority is issued by retention policy"
        )

    @property
    def attachment(self) -> ExistingContainerAttachment:
        return self._attachment

    @property
    def eligibility(self) -> FrameworkContainerDeleteEligible:
        return self._eligibility

    @property
    def owner_lock(self) -> ActiveProjectOwnerLock:
        return self._owner_lock


ContainerDeleteAuthority: TypeAlias = (
    "CurrentRunContainerDeleteAuthority | ContinuationContainerDeleteAuthority"
)


@final
class _RejectedRetentionOwnershipTransitions:
    __slots__: tuple[str, ...] = ()

    def __getitem__(self, _key: int) -> NoReturn:
        raise RetentionOwnershipTransitionError(
            "retention ownership is bound by its resolver"
        )

    def __setitem__(
        self,
        _key: int,
        _authority: ContainerDeleteAuthority,
    ) -> NoReturn:
        raise RetentionOwnershipTransitionError(
            "retention ownership cannot be reassigned"
        )


_DELETE_AUTHORITY_BINDINGS: Final = _RejectedRetentionOwnershipTransitions()


class V3ContainerRetentionPolicy(NamedTuple):
    requested: ContainerRetention
    effective: ContainerRetention
    owner_kind: ContainerOwnerKind
    delete_authority: ContainerDeleteAuthority | None
    attachment: ExistingContainerAttachment | None = None


class ContainerDeletionReceipt(NamedTuple):
    container_id: str
    pre_state: str
    post_state: str


@final
class ContainerDeletionError(RequestedContainerCleanupError):
    container_id: str
    pre_state: str
    post_state: str
    detail: str

    def __init__(
        self,
        container_id: str,
        pre_state: str,
        post_state: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.container_id = container_id
        self.pre_state = pre_state
        self.post_state = post_state
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"container {self.container_id}: {self.detail}"
