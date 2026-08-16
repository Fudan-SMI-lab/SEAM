from __future__ import annotations

import copy
import errno
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, final

import pytest

from core import (
    atomic_file,
    continuation_evidence_io,
    continuation_lock_identity,
    owned_directory_lock,
)
from core.run_manifest_models import ManifestErrorKind, RunManifestError
from core.run_outcome import TerminalOutcome
from harness.run.cleanup import CleanupContext, ResourceCleanup
from harness.run.models import FinalizationHookError, SidecarWriteError
from harness.run import sidecars
from harness.run.sidecars import copy_run_artifacts
from harness.session.trace_export_models import TraceExportRequest
from harness.session.trace_export_transaction import TraceExportTransaction


@final
class _EvidenceRecord:
    __slots__: tuple[str, ...] = ()

    def model_dump_json(self, *, by_alias: bool, indent: int) -> str:
        _ = by_alias, indent
        return "{}"


def _trace_request(tmp_path: Path) -> TraceExportRequest:
    return TraceExportRequest(
        destination=tmp_path / "trace",
        seeds=(),
        overflow_roots=(),
        captured_at="2026-07-29T00:00:00+00:00",
    )


def test_trace_abort_preserves_replacement_staging_directory(tmp_path: Path) -> None:
    transaction = TraceExportTransaction(_trace_request(tmp_path))
    _ = transaction.__enter__()
    staging = transaction.staging
    shutil.rmtree(staging)
    staging.mkdir()
    sentinel = staging / "replacement.txt"
    _ = sentinel.write_text("preserve", encoding="utf-8")

    assert transaction.__exit__(RuntimeError, RuntimeError("abort"), None) is False

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_run_cleanup_preserves_directory_replaced_after_identity_capture(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    cleanup = ResourceCleanup(CleanupContext(owned, False, True, None, None))
    shutil.rmtree(owned)
    owned.mkdir()
    sentinel = owned / "replacement.txt"
    _ = sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(FinalizationHookError):
        _ = cleanup(TerminalOutcome.PASSED)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_sidecar_cleanup_preserves_replacement_staging_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "work" / ".sm-artifacts"
    output = tmp_path / "report"
    source.mkdir(parents=True)
    output.mkdir()
    replacements: list[Path] = []

    def interrupt_with_replacement(
        _source: Path,
        _container: Path,
        staging: Path,
    ) -> None:
        if not staging.exists():
            staging.mkdir()
        _ = staging.rename(staging.with_suffix(".original"))
        staging.mkdir()
        replacement = staging / "replacement.txt"
        _ = replacement.write_text("preserve", encoding="utf-8")
        replacements.append(replacement)
        raise RunManifestError(
            kind=ManifestErrorKind.CONTAINMENT,
            detail="forced replacement",
        )

    monkeypatch.setattr(sidecars, "artifact_tree_copy", interrupt_with_replacement)

    with pytest.raises((SidecarWriteError, RunManifestError)):
        _ = copy_run_artifacts(source.parent, output)

    assert len(replacements) == 1
    assert replacements[0].read_text(encoding="utf-8") == "preserve"


def test_evidence_write_cleanup_preserves_post_validation_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "evidence.json"
    successor = b"successor-evidence"

    def replace_and_fail(_path: Path) -> NoReturn:
        path.unlink()
        _ = path.write_bytes(successor)
        raise OSError("forced evidence sync failure")

    monkeypatch.setattr(atomic_file, "_fsync_parent", replace_and_fail)

    with pytest.raises(OSError, match="forced evidence sync failure"):
        _ = continuation_evidence_io.write_exclusive_record(path, _EvidenceRecord())

    assert path.read_bytes() == successor


def test_owned_file_release_restores_quarantined_directory_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "owned"
    _ = path.write_bytes(b"owned")
    identity = continuation_lock_identity.lock_identity(path.lstat())
    original_replace = os.replace

    def replace_with_directory(source: Path, destination: Path) -> None:
        path.unlink()
        path.mkdir()
        original_replace(source, destination)

    monkeypatch.setattr(
        os,
        "replace",
        replace_with_directory,
    )

    with pytest.raises(OSError):
        continuation_lock_identity.release_owned_file(path, identity)
    assert path.is_dir()


def test_directory_release_treats_successful_final_rmdir_as_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return
    path = tmp_path / "owned"
    path.mkdir()
    identity = owned_directory_lock.directory_lock_identity(path)
    original_rmdir = os.rmdir
    swapped = False

    def swap_before_rmdir(
        target: str | bytes | Path,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if dir_fd is not None and str(target).endswith(".release") and not swapped:
            swapped = True
            os.rename(
                target, f"{target}.original", src_dir_fd=dir_fd, dst_dir_fd=dir_fd
            )
            os.mkdir(target, dir_fd=dir_fd)
        original_rmdir(target, dir_fd=dir_fd)

    monkeypatch.setattr(os, "rmdir", swap_before_rmdir)

    owned_directory_lock.release_owned_directory(path, identity)

    assert swapped
    assert not any(tmp_path.glob(".owned.*.release"))


def test_directory_release_rejects_directory_replaced_before_release(
    tmp_path: Path,
) -> None:
    path = tmp_path / "owned"
    path.mkdir()
    identity = owned_directory_lock.directory_lock_identity(path)
    shutil.rmtree(path)
    path.mkdir()
    sentinel = path / "successor.txt"
    _ = sentinel.write_text("successor", encoding="utf-8")

    with pytest.raises(owned_directory_lock.OwnedDirectoryChangedError):
        owned_directory_lock.release_owned_directory(path, identity)

    assert sentinel.read_text(encoding="utf-8") == "successor"


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy])
def test_copied_directory_identity_releases_single_retained_lease(
    tmp_path: Path,
    copier: Callable[
        [owned_directory_lock.DirectoryLockIdentity],
        owned_directory_lock.DirectoryLockIdentity,
    ],
) -> None:
    path = tmp_path / "owned-copy"
    path.mkdir()
    identity = owned_directory_lock.directory_lock_identity(path)
    copied = copier(identity)

    assert copied is identity
    owned_directory_lock.release_owned_directory(path, copied)
    assert not path.exists()


def test_directory_release_preserves_child_replaced_before_non_directory_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return
    path = tmp_path / "owned-child"
    path.mkdir()
    _ = (path / "child.txt").write_text("owned", encoding="utf-8")
    identity = owned_directory_lock.directory_lock_identity(path)
    real_open = os.open
    swapped = False

    def swap_before_open(
        target: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if target == "child.txt" and dir_fd is not None and not swapped:
            swapped = True
            os.unlink(target, dir_fd=dir_fd)
            os.symlink("successor", target, dir_fd=dir_fd)
            raise OSError(errno.ELOOP, "forced child replacement")
        return real_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_open)

    with pytest.raises(owned_directory_lock.OwnedDirectoryChangedError):
        owned_directory_lock.release_owned_directory(path, identity)

    assert swapped
    quarantines = tuple(tmp_path.glob(".owned-child.*.release"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "child.txt").is_symlink()
