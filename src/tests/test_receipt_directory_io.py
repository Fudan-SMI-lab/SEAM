from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import phase5_attempt_receipt_persistence as persistence
from core import receipt_directory_io as relative_io
from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    accept_attempt_receipt,
    finalize_attempt_receipt,
    load_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import authority, execution, review, save_attempt

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative IO")


@pytest.fixture(autouse=True)
def use_descriptor_io(monkeypatch):
    # Exercise the no-procfs branch on Linux CI as well as on macOS.
    monkeypatch.setattr(persistence, "sys", SimpleNamespace(platform="darwin"))


def test_complete_receipt_lifecycle_without_procfs(tmp_path):
    store = ArtifactStore(str(tmp_path), "portable")
    path = save_attempt(store, tmp_path, exit_code=0)
    finalize_attempt_receipt(
        path, custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    accepted = accept_attempt_receipt(path, authority(store, path))
    assert accepted.accepted
    assert load_attempt_receipt(path) == accepted
    assert not list(path.parent.glob(".*.tmp"))
    assert not list(path.parent.glob(".*.bak"))
    assert not list(path.parent.glob(".*.lock"))


def test_parent_replacement_does_not_redirect_receipt(tmp_path, monkeypatch):
    store = ArtifactStore(str(tmp_path), "parent-race")
    attempt = execution(store, tmp_path)
    path = Path(attempt.reservation.receipt_path)
    old_parent = path.parent.with_name("original-parent")
    original_create = relative_io._create_at

    def move_after_create(directory, name, content):
        identity = original_create(directory, name, content)
        if name.endswith(".tmp"):
            path.parent.rename(old_parent)
            path.parent.mkdir()
            (path.parent / path.name).write_bytes(b"replacement")
        return identity

    monkeypatch.setattr(relative_io, "_create_at", move_after_create)
    with pytest.raises(OSError, match="identity_mismatch") as raised:
        store.save_shell_attempt_artifacts(
            "run_entry_script", command="python validate.py", cwd=str(tmp_path),
            backend_workdir=str(tmp_path), exit_code=0, duration=0.01,
            stdout="ok", stderr="", execution=attempt,
        )
    assert isinstance(raised.value.__cause__, AttemptReceiptError)
    assert path.read_bytes() == b"replacement"
    assert not (old_parent / path.name).exists()
    assert not list(old_parent.glob(".*.tmp"))


def test_transition_sync_failure_restores_previous_receipt(tmp_path, monkeypatch):
    store = ArtifactStore(str(tmp_path), "sync-race")
    path = save_attempt(store, tmp_path, exit_code=0)
    previous = path.read_bytes()
    original_sync = os.fsync
    directory_syncs = 0

    def fail_after_replace(fd):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_syncs += 1
            if directory_syncs == 2:
                raise OSError("publication sync failed")
        original_sync(fd)

    monkeypatch.setattr(relative_io.os, "fsync", fail_after_replace)
    with pytest.raises(OSError, match="publication sync failed"):
        finalize_attempt_receipt(
            path, custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
            review=review(ReviewOutcome.DISABLED),
        )
    assert path.read_bytes() == previous
    assert not list(path.parent.glob(".*.lock"))


def test_symlink_receipt_cannot_change_external_target(tmp_path):
    store = ArtifactStore(str(tmp_path), "symlink")
    path = save_attempt(store, tmp_path, exit_code=0)
    receipt = load_attempt_receipt(path)
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    path.unlink()
    path.symlink_to(victim)
    with pytest.raises(AttemptReceiptError):
        persistence.write_attempt_receipt(path, receipt)
    assert victim.read_bytes() == b"keep"
