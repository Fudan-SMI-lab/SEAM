from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol
from unittest.mock import Mock

import pytest

import core.continuation_lock as continuation_lock
from core.continuation_lock_identity import LockPathSnapshot
from core.continuation import (
    ContinuationError,
    ContinuationErrorKind,
    ContinuationRequest,
    claim_terminal_parent,
    current_project_owner_lock,
)
from tests.terminal_run_continuation_test_support import (
    CHILD_RUN_ID,
    create_parent_run,
    read_json_payload,
    tree_bytes,
)


class _Closable(Protocol):
    @property
    def closed(self) -> bool: ...


def _lock_path(parent_project: Path, reports_root: Path) -> Path:
    import hashlib
    import os

    canonical = parent_project.resolve(strict=True)
    digest = hashlib.sha256(os.path.normcase(str(canonical)).encode()).hexdigest()
    return reports_root / "locks" / f"{digest}.lock"


def test_lock_is_external_exclusive_and_context_managed(tmp_path: Path) -> None:
    # Given an eligible parent and its deterministic external lock name.
    parent = create_parent_run(tmp_path)
    expected_lock = _lock_path(parent.project_dir, parent.reports_root)

    # When the parent is exclusively claimed.
    with claim_terminal_parent(
        ContinuationRequest(summary_path=parent.summary_path, child_run_id=CHILD_RUN_ID)
    ) as resolved:
        lock_bytes = expected_lock.read_bytes()
        metadata = read_json_payload(expected_lock)
        assert resolved.output_project == parent.project_dir.resolve(strict=True)
        assert expected_lock.parent == parent.reports_root / "locks"

    # Then owner diagnostics omit paths and release occurs after body finalization.
    assert metadata["parent_run_id"] == "parent-run-001"
    assert metadata["child_run_id"] == "child-run-001"
    assert isinstance(metadata["pid"], int)
    assert isinstance(metadata["hostname"], str)
    assert str(parent.project_dir) not in lock_bytes.decode("utf-8")
    assert str(parent.summary_path) not in lock_bytes.decode("utf-8")
    assert not expected_lock.exists()


def test_lock_duplicate_or_stale_owner_is_never_broken(tmp_path: Path) -> None:
    # Given an existing owner record that may be stale or ambiguous.
    parent = create_parent_run(tmp_path)
    lock_path = _lock_path(parent.project_dir, parent.reports_root)
    lock_path.parent.mkdir()
    stale_bytes = b'{"pid": 1, "hostname": "stale-owner"}\n'
    _ = lock_path.write_bytes(stale_bytes)
    parent_before = tree_bytes(parent.report_dir)
    project_before = tree_bytes(parent.project_dir)
    session_factory = Mock()
    backend_factory = Mock()
    child_artifact_factory = Mock()

    # When another child tries to claim the same output project.
    with pytest.raises(ContinuationError) as raised:
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=parent.summary_path, child_run_id=CHILD_RUN_ID
            )
        ):
            session_factory()
            backend_factory()
            child_artifact_factory()

    # Then the existing owner is preserved and downstream work never starts.
    assert raised.value.kind is ContinuationErrorKind.PROJECT_LOCKED
    assert lock_path.read_bytes() == stale_bytes
    session_factory.assert_not_called()
    backend_factory.assert_not_called()
    child_artifact_factory.assert_not_called()
    assert tree_bytes(parent.report_dir) == parent_before
    assert tree_bytes(parent.project_dir) == project_before


def test_lock_atomic_concurrent_acquisition_has_one_owner(tmp_path: Path) -> None:
    # Given two independent claimers synchronized on one output project.
    parent = create_parent_run(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def hold_owner() -> str:
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=parent.summary_path, child_run_id=CHILD_RUN_ID
            )
        ):
            entered.set()
            assert release.wait(timeout=5)
            return "owner"

    def contend() -> str:
        assert entered.wait(timeout=5)
        try:
            with claim_terminal_parent(
                ContinuationRequest(
                    summary_path=parent.summary_path, child_run_id=CHILD_RUN_ID
                )
            ):
                return "unexpected-owner"
        except ContinuationError as error:
            return error.kind.value

    # When both real filesystem acquisitions overlap.
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(hold_owner)
        contender = pool.submit(contend)
        contender_result = contender.result(timeout=5)
        release.set()
        owner_result = owner.result(timeout=5)

    # Then O_EXCL admits exactly one owner and cleanup leaves no lock.
    assert sorted((owner_result, contender_result)) == ["owner", "project_locked"]
    assert not _lock_path(parent.project_dir, parent.reports_root).exists()


def test_lock_keeps_owner_handle_open_for_claim_lifetime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an eligible parent and observation of the owner descriptor wrapper.
    parent = create_parent_run(tmp_path)
    opened: list[_Closable] = []
    original_fdopen = os.fdopen

    def capture_fdopen(descriptor: int, mode: str) -> _Closable:
        handle = original_fdopen(descriptor, mode)
        opened.append(handle)
        return handle

    monkeypatch.setattr(continuation_lock.os, "fdopen", capture_fdopen)

    # When the claim body executes.
    with claim_terminal_parent(
        ContinuationRequest(summary_path=parent.summary_path, child_run_id=CHILD_RUN_ID)
    ):
        assert len(opened) == 1
        assert not opened[0].closed

    # Then release closes the retained owner handle.
    assert opened[0].closed


def test_active_lock_revalidates_path_after_handle_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a pathname snapshot whose bytes differ from the retained owner handle.
    parent = create_parent_run(tmp_path)
    lock_path = _lock_path(parent.project_dir, parent.reports_root)
    replacement = b'{"owner":"replacement"}\n'
    original_snapshot = continuation_lock.read_lock_path_snapshot
    snapshot_calls = 0

    def mismatched_snapshot(path: Path, maximum_bytes: int) -> LockPathSnapshot:
        nonlocal snapshot_calls
        snapshot = original_snapshot(path, maximum_bytes)
        snapshot_calls += 1
        if snapshot_calls == 1:
            return LockPathSnapshot(snapshot.identity, replacement)
        return snapshot

    # When active validation races with pathname replacement.
    with claim_terminal_parent(
        ContinuationRequest(
            summary_path=parent.summary_path,
            child_run_id=CHILD_RUN_ID,
        )
    ):
        owner = current_project_owner_lock()
        assert owner is not None
        monkeypatch.setattr(
            continuation_lock,
            "read_lock_path_snapshot",
            mismatched_snapshot,
        )
        observed_active = owner.active

    # Then stale handle validity is rejected and normal release remains safe.
    assert observed_active is False
    assert not lock_path.exists()


def test_release_never_unlinks_post_validation_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given replacement immediately after release's final path validation.
    parent = create_parent_run(tmp_path)
    lock_path = _lock_path(parent.project_dir, parent.reports_root)
    replacement = b'{"owner":"replacement"}\n'
    original_lstat = Path.lstat
    target_calls = 0

    def replace_after_final_validation(path: Path):
        nonlocal target_calls
        metadata = original_lstat(path)
        if path == lock_path:
            target_calls += 1
            if target_calls == 2:
                path.unlink()
                _ = path.write_bytes(replacement)
        return metadata

    # When deterministic release reaches the unlink boundary.
    with pytest.raises(ContinuationError) as raised:
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=parent.summary_path,
                child_run_id=CHILD_RUN_ID,
            )
        ):
            monkeypatch.setattr(Path, "lstat", replace_after_final_validation)

    # Then another owner's replacement is not removed by pathname.
    assert raised.value.kind is ContinuationErrorKind.LOCK_RELEASE
    assert lock_path.read_bytes() == replacement


def test_partial_cleanup_never_unlinks_post_validation_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given failed lock publication and a replacement after cleanup validation.
    parent = create_parent_run(tmp_path)
    lock_path = _lock_path(parent.project_dir, parent.reports_root)
    replacement = b'{"owner":"replacement"}\n'
    original_lstat = Path.lstat
    replaced = False

    def replace_after_validation(path: Path):
        nonlocal replaced
        metadata = original_lstat(path)
        if path == lock_path and not replaced:
            replaced = True
            path.unlink()
            _ = path.write_bytes(replacement)
        return metadata

    def fail_publication(_descriptor: int) -> None:
        raise OSError("forced publication failure")

    monkeypatch.setattr(continuation_lock.os, "fsync", fail_publication)
    monkeypatch.setattr(Path, "lstat", replace_after_validation)

    # When partial publication cleanup reaches its removal boundary.
    with pytest.raises(ContinuationError):
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=parent.summary_path,
                child_run_id=CHILD_RUN_ID,
            )
        ):
            pass

    # Then another owner's replacement remains at the deterministic pathname.
    assert replaced is True
    assert lock_path.read_bytes() == replacement
