from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO, NoReturn, Protocol
from unittest.mock import MagicMock

import pytest

from core import (
    atomic_file,
    phase5_attempt_receipt,
    phase5_attempt_receipt_persistence,
)
from core.artifact_store import ArtifactStore
from core.continuation_lock_identity import LockIdentity, release_owned_file
from core.phase5_attempt_models import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
)
from core.phase5_attempt_receipt import (
    CustomOpGateEvidence,
    CustomOpGateStatus,
    accept_attempt_receipt,
    finalize_attempt_receipt,
    load_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor
from core import workflow_shell_capture
from harness.session.opencode_contract import JsonObject
from tests.phase5_receipt_test_support import authority, execution, review, save_attempt


class _ShellPhaseExecution(Protocol):
    def __call__(
        self,
        phase: PhaseDefinition,
        state: JsonObject,
        context: JsonObject,
        loop_vars: JsonObject | None = None,
        loop_state: JsonObject | None = None,
    ) -> tuple[str, JsonObject]: ...


def test_initial_receipt_reader_never_observes_partial_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "atomic-initial")
    attempt = execution(store, tmp_path)
    receipt_path = Path(attempt.reservation.receipt_path)
    original_link = os.link
    observed: list[AttemptReceiptErrorKind] = []

    def observe_before_publication(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if Path(destination).name == receipt_path.name:
            try:
                _ = load_attempt_receipt(receipt_path)
            except AttemptReceiptError as exc:
                observed.append(exc.kind)
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", observe_before_publication)

    _ = store.save_shell_attempt_artifacts(
        "run_entry_script",
        command="python validate.py",
        cwd=str(tmp_path),
        backend_workdir=str(tmp_path),
        exit_code=0,
        duration=0.01,
        stdout="ok",
        stderr="",
        execution=attempt,
    )

    assert observed == [AttemptReceiptErrorKind.MISSING]
    assert load_attempt_receipt(receipt_path).complete is False


def test_receipt_lock_cleanup_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "receipt-lock-race")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    lock_path = receipt_path.with_name(f".{receipt_path.name}.lock")
    replacement = b"successor-lock"

    def replace_before_release(
        path: Path,
        _identity: LockIdentity,
        _content: bytes | None = None,
    ) -> None:
        if path.name == lock_path.name:
            path.unlink()
            _ = path.write_bytes(replacement)
        raise OSError("owned lock changed before release")

    monkeypatch.setattr(
        phase5_attempt_receipt_persistence,
        "release_owned_file",
        replace_before_release,
        raising=False,
    )

    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    assert finalized.complete is True
    assert lock_path.read_bytes() == replacement


def test_reservation_marker_cleanup_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "marker-race")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    receipt = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    marker_path = receipt_path.parent / f".{receipt.attempt_id}.reserved"
    replacement = b"successor-marker"

    def replace_before_release(
        path: Path,
        identity: LockIdentity,
        content: bytes | None = None,
    ) -> None:
        if path == marker_path:
            path.unlink()
            _ = path.write_bytes(replacement)
            raise OSError("owned marker changed before release")
        release_owned_file(path, identity, content)

    monkeypatch.setattr(
        phase5_attempt_receipt,
        "release_owned_file",
        replace_before_release,
        raising=False,
    )

    accepted = accept_attempt_receipt(receipt_path, authority(store, receipt_path))
    assert accepted.accepted is True
    assert load_attempt_receipt(receipt_path).accepted is True
    assert marker_path.read_bytes() == replacement


def test_initial_receipt_parent_sync_failure_rolls_back_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "receipt-sync-failure")
    attempt = execution(store, tmp_path)
    receipt_path = Path(attempt.reservation.receipt_path)

    def fail_directory_sync(_path: Path) -> NoReturn:
        raise OSError("forced receipt directory sync failure")

    monkeypatch.setattr(atomic_file, "_fsync_parent", fail_directory_sync)

    with pytest.raises(OSError, match="forced receipt directory sync failure"):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="ok",
            stderr="",
            execution=attempt,
        )
    assert not receipt_path.exists()


def test_workflow_output_capture_does_not_reopen_replaced_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "emit.py"
    _ = script.write_text("print('ORIGINAL-EVIDENCE')\n", encoding="utf-8")
    workflow = WorkflowDefinition(
        name="stable-output", version="1.0", phases=[], terminals=[]
    )
    store = ArtifactStore(str(tmp_path), "stable-output")
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="",
        output_schema={},
        type="shell",
        on_failure="continue",
    )
    setattr(phase, "command", "${loop_vars.entry_script}")

    def replace_path_before_read(
        source: BinaryIO,
        max_bytes: int = 500_000,
    ) -> str:
        _ = source.seek(0, os.SEEK_END)
        size = source.tell()
        _ = source.seek(max(0, size - max_bytes))
        content = source.read(max_bytes)
        return content.decode("utf-8", errors="replace")

    monkeypatch.setattr(workflow_shell_capture, "_read_tail", replace_path_before_read)

    execute_shell: _ShellPhaseExecution = getattr(executor, "_execute_shell_phase")
    _status, output = execute_shell(
        phase,
        state={},
        context={},
        loop_vars={"entry_script": f'"{sys.executable}" "{script}"'},
        loop_state={},
    )

    artifacts = output.get("artifacts")
    assert isinstance(artifacts, dict)
    stdout = artifacts.get("stdout_path")
    assert isinstance(stdout, str)
    stdout_path = Path(stdout)
    assert stdout_path.read_text(encoding="utf-8") == "ORIGINAL-EVIDENCE\n"
