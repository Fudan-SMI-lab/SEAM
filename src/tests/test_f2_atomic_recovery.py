from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import pytest

from core import atomic_file
from core.resource_manifest_lock import ResourceManifestLock
from core.run_manifest_lock import RunFileLock
from harness.session.trace_export_io import copy_overflow
from harness.session.trace_export_models import OverflowCopyRequest, OverflowStatus


def test_atomic_replace_parent_sync_failure_restores_prior_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    _ = path.write_bytes(b"prior")
    sync_calls = 0

    def fail_published_sync(_path: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            raise OSError("forced publication sync failure")

    monkeypatch.setattr(atomic_file, "_fsync_parent", fail_published_sync)

    with pytest.raises(OSError, match="forced publication sync failure"):
        atomic_file.atomic_write_bytes(path, b"replacement")
    assert path.read_bytes() == b"prior"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_atomic_initial_write_parent_sync_failure_removes_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "new.json"

    def fail_sync(_path: Path) -> NoReturn:
        raise OSError("forced initial sync failure")

    monkeypatch.setattr(atomic_file, "_fsync_parent", fail_sync)

    with pytest.raises(OSError, match="forced initial sync failure"):
        atomic_file.atomic_write_bytes(path, b"new")
    assert not path.exists()


def test_atomic_rollback_preserves_concurrent_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    successor = tmp_path / "successor.json"
    _ = path.write_bytes(b"prior")
    _ = successor.write_bytes(b"successor")
    sync_calls = 0

    def replace_then_fail(_path: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            _ = successor.replace(path)
            raise OSError("forced publication sync failure")

    monkeypatch.setattr(atomic_file, "_fsync_parent", replace_then_fail)

    with pytest.raises(OSError, match="forced publication sync failure"):
        atomic_file.atomic_write_bytes(path, b"replacement")
    assert path.read_bytes() == b"successor"


def test_atomic_publication_rejects_replaced_temporary_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    _ = path.write_bytes(b"prior")

    def replace_swapped_source(source: Path, destination: Path) -> None:
        source.unlink()
        _ = source.write_bytes(b"forged-publication")
        os.replace(source, destination)

    with pytest.raises(OSError):
        atomic_file.atomic_write_bytes_with(
            path,
            b"replacement",
            replace_swapped_source,
            atomic_file._fsync_parent,
        )

    assert path.read_bytes() == b"prior"


def test_atomic_backup_cleanup_sync_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    _ = path.write_bytes(b"prior")
    sync_calls = 0

    def fail_cleanup_sync(_path: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 3:
            raise OSError("forced backup cleanup sync failure")

    monkeypatch.setattr(atomic_file, "_fsync_parent", fail_cleanup_sync)

    with pytest.raises(OSError, match="forced backup cleanup sync failure"):
        atomic_file.atomic_write_bytes(path, b"replacement")


def test_atomic_restore_failure_retains_prior_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    _ = path.write_bytes(b"prior")
    original_link = os.link
    sync_calls = 0

    def fail_restore_link(
        source: Path,
        destination: Path,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        if source.suffix == ".bak":
            raise OSError("forced restoration failure")
        original_link(
            source,
            destination,
            follow_symlinks=follow_symlinks,
        )

    def fail_published_sync(_path: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            raise OSError("forced publication sync failure")

    monkeypatch.setattr(os, "link", fail_restore_link)
    monkeypatch.setattr(atomic_file, "_fsync_parent", fail_published_sync)

    with pytest.raises(OSError, match="forced publication sync failure"):
        atomic_file.atomic_write_bytes(path, b"replacement")
    backups = list(tmp_path.glob(".*.bak"))
    assert not path.exists()
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"prior"


def test_atomic_create_requests_private_file_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "private.json"
    original_open = os.open
    modes: list[int] = []

    def record_open(
        target: Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & os.O_CREAT:
            modes.append(mode)
        return original_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", record_open)

    atomic_file.atomic_create_bytes(path, b"private")
    assert modes == [0o600]


def test_manifest_lock_owner_files_request_private_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = os.open
    modes: list[int] = []

    def record_open(
        target: Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & os.O_CREAT:
            modes.append(mode)
        return original_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", record_open)
    with RunFileLock(tmp_path):
        pass
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    with ResourceManifestLock(report_dir):
        pass

    assert modes
    assert set(modes) == {0o600}


def test_trace_overflow_temporary_requests_private_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    _ = source.write_bytes(b"captured")
    destination = tmp_path / "trace" / "captured.txt"
    destination.parent.mkdir()
    original_open = os.open
    modes: list[int] = []

    def record_open(
        target: Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & os.O_CREAT:
            modes.append(mode)
        return original_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", record_open)
    capture = copy_overflow(
        OverflowCopyRequest(
            reference=str(source),
            destination=destination,
            allowed_roots=(tmp_path,),
            max_bytes=32,
        )
    )

    assert capture.status is OverflowStatus.COPIED
    assert modes == [0o600]
