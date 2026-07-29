from __future__ import annotations

import os as os
from collections.abc import Iterator
from contextlib import contextmanager

from .continuation_models import (
    ContinuationErrorKind,
    ContinuationRequest,
    ResolvedTerminalParent,
)
from .continuation_lock_context import (
    _ACTIVE_PROJECT_OWNER,
    ActiveProjectOwnerLock,
    current_project_owner_lock as current_project_owner_lock,
    project_owner_lock_is_active as project_owner_lock_is_active,
)
from .continuation_project_lock import (
    _ExclusiveProjectLock,
    continuation_lock_error,
)
from .continuation_resolver import resolve_authority


@contextmanager
def claim_terminal_parent(
    request: ContinuationRequest,
) -> Iterator[ResolvedTerminalParent]:
    authority = resolve_authority(request.summary_path)
    if request.child_run_id == authority.parent.run_id:
        raise continuation_lock_error(
            ContinuationErrorKind.CHILD_RUN_ID_REUSED,
            "continuation child run ID must differ from its parent",
        )
    owner = _ExclusiveProjectLock(authority, request.child_run_id)
    proof = ActiveProjectOwnerLock(
        parent_run_id=str(authority.parent.run_id),
        child_run_id=request.child_run_id,
        lineage_root_run_id=str(authority.parent.run_manifest.lineage_root_run_id),
        output_project=authority.parent.output_project,
        owner=owner,
    )
    active_token = _ACTIVE_PROJECT_OWNER.set(proof)
    try:
        yield authority.parent
    finally:
        try:
            owner.release()
        finally:
            _ACTIVE_PROJECT_OWNER.reset(active_token)
