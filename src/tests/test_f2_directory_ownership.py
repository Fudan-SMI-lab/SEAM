from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from core import (
    owned_directory_lock,
    run_manifest_allocation,
    run_manifest_paths,
    run_manifest_tree_writer,
)
from core.run_manifest import RunManifestError, RunManifestStore
from harness.session.trace_export_models import TraceExportError, TraceExportRequest
from harness.session.trace_export_transaction import TraceExportTransaction
from tests.run_manifest_test_support import root_manifest, storage_context


def test_run_directory_replaced_before_identity_capture_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = storage_context(tmp_path)
    manifest = root_manifest(context)
    run_dir = context.authoritative_root / str(manifest.run_id)
    sentinel = run_dir / "replacement.txt"
    original_capture = owned_directory_lock.empty_directory_identity

    def replace_before_capture(
        path: Path,
    ) -> owned_directory_lock.DirectoryLockIdentity:
        shutil.rmtree(path)
        path.mkdir()
        _ = sentinel.write_text("preserve", encoding="utf-8")
        return original_capture(path)

    monkeypatch.setattr(
        run_manifest_allocation,
        "empty_directory_identity",
        replace_before_capture,
    )

    with pytest.raises(RunManifestError):
        _ = RunManifestStore.create(context, manifest)
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_trace_lock_replaced_before_identity_capture_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = TraceExportRequest(
        destination=tmp_path / "trace",
        seeds=(),
        overflow_roots=(),
        captured_at="2026-07-30T00:00:00+00:00",
    )
    lock = tmp_path / ".trace.publish.lock"
    sentinel = lock / "replacement.txt"
    original_capture = owned_directory_lock.empty_directory_identity

    def replace_before_capture(
        path: Path,
    ) -> owned_directory_lock.DirectoryLockIdentity:
        if path == lock:
            shutil.rmtree(path)
            path.mkdir()
            _ = sentinel.write_text("preserve", encoding="utf-8")
        return original_capture(path)

    monkeypatch.setattr(
        "harness.session.trace_export_transaction.empty_directory_identity",
        replace_before_capture,
    )

    with pytest.raises(TraceExportError):
        with TraceExportTransaction(request):
            pass
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_tree_copy_stays_on_opened_destination_after_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    source.mkdir()
    destination.mkdir()
    outside.mkdir()
    _ = (source / "artifact.txt").write_bytes(b"owned")
    original = tmp_path / "original-destination"
    real_open = os.open
    swapped = False

    def swap_before_file_open(
        target: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if target == "artifact.txt" and not swapped:
            swapped = True
            _ = destination.rename(original)
            destination.symlink_to(outside, target_is_directory=True)
        return real_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        run_manifest_tree_writer, "destination_open", swap_before_file_open
    )

    with pytest.raises(RunManifestError):
        run_manifest_paths.copy_real_tree_into(source, tmp_path, destination)

    assert swapped
    assert not (outside / "artifact.txt").exists()
    assert (original / "artifact.txt").read_bytes() == b"owned"
