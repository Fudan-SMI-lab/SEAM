from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from harness.session.trace_export_models import TraceExportError, TraceExportRequest
from harness.session.trace_export_transaction import TraceExportTransaction


def _request(tmp_path: Path) -> TraceExportRequest:
    return TraceExportRequest(
        destination=tmp_path / "trace",
        seeds=(),
        overflow_roots=(),
        captured_at="2026-07-29T00:00:00+00:00",
    )


def _identity(path: Path) -> tuple[int, int, int]:
    metadata = os.lstat(path)
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def test_prepare_preserves_preexisting_publish_lock(tmp_path: Path) -> None:
    transaction = TraceExportTransaction(_request(tmp_path))
    lock = tmp_path / ".trace.publish.lock"
    lock.mkdir()
    expected = _identity(lock)

    with pytest.raises(TraceExportError):
        _ = transaction.__enter__()

    assert _identity(lock) == expected


def test_abort_preserves_successor_publish_lock(tmp_path: Path) -> None:
    transaction = TraceExportTransaction(_request(tmp_path))
    _ = transaction.__enter__()
    lock = tmp_path / ".trace.publish.lock"
    shutil.rmtree(lock)
    lock.mkdir()
    expected = _identity(lock)

    assert transaction.__exit__(RuntimeError, RuntimeError("abort"), None) is False

    assert _identity(lock) == expected


def test_staging_cleanup_failure_still_attempts_publish_lock_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = TraceExportTransaction(_request(tmp_path))
    _ = transaction.__enter__()
    lock_cleanup_called = False

    def fail_staging() -> str:
        return "staging changed"

    def clean_lock() -> None:
        nonlocal lock_cleanup_called
        lock_cleanup_called = True
        return None

    monkeypatch.setattr(transaction, "_remove_staging", fail_staging)
    monkeypatch.setattr(transaction, "_remove_publish_lock", clean_lock)

    with pytest.raises(TraceExportError, match="staging changed"):
        _ = transaction.__exit__(None, None, None)

    assert lock_cleanup_called
