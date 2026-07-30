from __future__ import annotations

from contextvars import ContextVar, Token
from types import TracebackType
from typing import Literal, final

from core.resource_retention_authority import (
    ContainerCleanupStatus as ContainerCleanupStatus,
    ContainerDeleteAuthority as ContainerDeleteAuthority,
    ContainerDeletionError as ContainerDeletionError,
    ContainerDeletionReceipt as ContainerDeletionReceipt,
    ContainerOwnerKind as ContainerOwnerKind,
    ContainerRetention as ContainerRetention,
    ContinuationContainerDeleteAuthority as ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority as CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy as V3ContainerRetentionPolicy,
)
from core.resource_retention_resolution import (
    delete_authority_is_registered,
    resolve_v3_container_retention as resolve_v3_container_retention,
)


_ACTIVE_CONTAINER_CLEANUP: ContextVar[ContainerDeleteAuthority | None] = ContextVar(
    "seam_active_container_cleanup", default=None
)


@final
class _AuthorizedContainerCleanup:
    __slots__ = ("_authority", "_token")

    def __init__(self, authority: ContainerDeleteAuthority) -> None:
        self._authority = authority
        self._token: Token[ContainerDeleteAuthority | None] | None = None

    def __enter__(self) -> None:
        if not delete_authority_is_registered(self._authority):
            raise ContainerDeletionError(
                "unknown",
                "unknown",
                "unknown",
                "container deletion authority is not registered",
            )
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
    return _ACTIVE_CONTAINER_CLEANUP.get() is authority and (
        delete_authority_is_registered(authority)
    )
