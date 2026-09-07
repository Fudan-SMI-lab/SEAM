from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple, Protocol


class ActiveLockOwner(Protocol):
    @property
    def active(self) -> bool: ...


class ActiveProjectOwnerLock(NamedTuple):
    parent_run_id: str
    child_run_id: str
    lineage_root_run_id: str
    output_project: Path
    owner: ActiveLockOwner

    @property
    def active(self) -> bool:
        return _ACTIVE_PROJECT_OWNER.get() is self and self.owner.active


_ACTIVE_PROJECT_OWNER: ContextVar[ActiveProjectOwnerLock | None] = ContextVar(
    "seam_active_project_owner",
    default=None,
)


def current_project_owner_lock() -> ActiveProjectOwnerLock | None:
    return _ACTIVE_PROJECT_OWNER.get()


def project_owner_lock_is_active(lock: ActiveProjectOwnerLock) -> bool:
    return lock.active
