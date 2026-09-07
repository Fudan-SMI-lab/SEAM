from __future__ import annotations

from pathlib import Path

from core.continuation_lock_identity import fsync_parent
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    empty_directory_identity,
    release_owned_directory,
)
from core.run_manifest_models import ManifestErrorKind, RunId
from core.run_manifest_validation import manifest_error


def create_run_directory(
    authoritative_root: Path,
    run_id: RunId,
) -> tuple[Path, DirectoryLockIdentity]:
    run_dir = authoritative_root / str(run_id)
    try:
        run_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise manifest_error(
            ManifestErrorKind.DUPLICATE_RUN,
            f"run namespace already exists: {run_id}",
        ) from exc
    identity: DirectoryLockIdentity | None = None
    try:
        identity = empty_directory_identity(run_dir)
        fsync_parent(run_dir)
    except OSError as exc:
        cleanup_detail = ""
        if identity is not None:
            try:
                release_owned_directory(run_dir, identity)
            except OSError as cleanup_error:
                cleanup_detail = f"; allocation cleanup failed: {cleanup_error}"
        raise manifest_error(
            ManifestErrorKind.WRITE_INTERRUPTED,
            f"{exc}{cleanup_detail}",
        ) from exc
    return run_dir, identity
