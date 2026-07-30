from __future__ import annotations

from pathlib import Path

from core.continuation_lock_identity import (
    BoundedReadError,
    BoundedReadErrorKind,
    read_verified_bytes,
)
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    close_directory_identity,
    release_owned_directory,
)


def rollback_owned_allocation(
    run_dir: Path,
    manifest_path: Path,
    owner_path: Path,
    owner_content: bytes,
    identity: DirectoryLockIdentity,
) -> None:
    try:
        try:
            owns_allocation = read_verified_bytes(owner_path, 128) == owner_content
        except BoundedReadError as exc:
            if exc.kind is BoundedReadErrorKind.MISSING and not manifest_path.exists():
                try:
                    release_owned_directory(run_dir, identity)
                except OSError as cleanup_error:
                    _ = cleanup_error
            return
        except FileNotFoundError:
            if not manifest_path.exists():
                try:
                    release_owned_directory(run_dir, identity)
                except OSError as cleanup_error:
                    _ = cleanup_error
            return
        if owns_allocation and not manifest_path.exists():
            try:
                release_owned_directory(
                    run_dir, identity, owner_path.name, owner_content
                )
            except OSError as cleanup_error:
                _ = cleanup_error
    finally:
        close_directory_identity(identity)
