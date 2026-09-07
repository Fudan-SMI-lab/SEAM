from __future__ import annotations

from pathlib import Path
import os
import stat
from threading import Event, Thread
from typing import NoReturn

import pytest

from core import (
    phase5_artifact_store,
    phase5_attempt_receipt_persistence,
    run_manifest_evidence_seal,
)
from core.artifact_store import ArtifactStore
from core.phase5_attempt_models import AttemptReceiptError, Phase5AttemptReceipt
from core.phase5_attempt_receipt import (
    CustomOpGateEvidence,
    CustomOpGateStatus,
    finalize_attempt_receipt,
    load_attempt_receipt,
)
from core.run_manifest import RunManifestError, RunManifestStore
from core.run_manifest_models import EvidenceDigest
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import authority, execution, review, save_attempt
from tests.run_manifest_test_support import root_manifest, storage_context


def test_path_source_symlink_is_rejected_before_artifact_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "source-link")
    outside = tmp_path / "outside.log"
    source = tmp_path / "captured.log"
    _ = outside.write_text("outside evidence", encoding="utf-8")
    _ = source.write_text("outside evidence", encoding="utf-8")
    original_lstat = Path.lstat

    def linked_lstat(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        if path != source:
            return metadata
        values = list(metadata)
        values[0] = stat.S_IFLNK | 0o777
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", linked_lstat)
    with pytest.raises((AttemptReceiptError, OSError, RunManifestError)):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout_source_path=str(source),
            stderr="",
        )


def test_compatibility_artifacts_require_shell_attempt_directory_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "compatibility-sync")

    def fail_shell_attempt_sync(path: Path) -> None:
        if path.parent.name == "shell_attempts":
            raise OSError("forced shell-attempt directory sync failure")

    monkeypatch.setattr(phase5_artifact_store, "fsync_parent", fail_shell_attempt_sync)
    with pytest.raises(OSError, match="forced shell-attempt directory sync failure"):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="ok",
            stderr="",
        )
    assert not tuple((Path(store.artifact_dir) / "shell_attempts").glob("*"))


def test_attempt_rollback_preserves_in_place_artifact_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "rollback-mutation")
    attempt = execution(store, tmp_path)
    stdout_path = Path(f"{attempt.reservation.prefix}.stdout.log")

    def mutate_then_fail(_path: Path, _receipt: Phase5AttemptReceipt) -> NoReturn:
        _ = stdout_path.write_text("concurrent replacement", encoding="utf-8")
        raise OSError("forced receipt failure")

    monkeypatch.setattr(
        phase5_artifact_store, "write_attempt_receipt", mutate_then_fail
    )
    with pytest.raises(OSError, match="forced receipt failure"):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="owned",
            stderr="",
            execution=attempt,
        )
    assert stdout_path.read_text(encoding="utf-8") == "concurrent replacement"


def test_reservation_rejects_replaced_shell_attempt_parent(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), "reservation-parent")
    attempt = execution(store, tmp_path)
    shell_attempts = Path(attempt.reservation.prefix).parent
    original = shell_attempts.with_name("shell_attempts-original")
    _ = shell_attempts.rename(original)
    shell_attempts.mkdir()

    with pytest.raises(AttemptReceiptError):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="owned",
            stderr="",
            execution=attempt,
        )

    assert not tuple(shell_attempts.iterdir())


def test_receipt_cas_preserves_successor_after_parent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(str(tmp_path), "receipt-parent")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    shell_attempts = receipt_path.parent
    original_parent = shell_attempts.with_name("shell_attempts-original")
    original_load = load_attempt_receipt
    successor = b"successor-receipt"
    swapped = False

    def swap_after_load(path: Path) -> Phase5AttemptReceipt:
        nonlocal swapped
        receipt = original_load(path)
        if not swapped:
            swapped = True
            _ = shell_attempts.rename(original_parent)
            shell_attempts.mkdir()
            _ = receipt_path.write_bytes(successor)
        return receipt

    monkeypatch.setattr(
        phase5_attempt_receipt_persistence,
        "load_attempt_receipt",
        swap_after_load,
    )

    with pytest.raises((AttemptReceiptError, OSError)):
        _ = finalize_attempt_receipt(
            receipt_path,
            custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
            review=review(ReviewOutcome.DISABLED),
        )

    assert swapped
    assert receipt_path.read_bytes() == successor


def test_acceptance_cannot_complete_during_evidence_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    receipt_path = save_attempt(working, context.workspace_root, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    working.record_finalized_phase5_authority(str(receipt_path), finalized)
    writer = RunManifestStore.create(context, root_manifest(context))
    original_digest = run_manifest_evidence_seal.digest_inventory
    source = Path(working.artifact_dir)
    seal_paused = Event()
    resume_seal = Event()
    acceptance_finished = Event()
    source_calls = 0
    failures: list[BaseException] = []

    def pause_after_final_source_inventory(
        root: Path,
        container: Path,
    ) -> tuple[EvidenceDigest, ...]:
        nonlocal source_calls
        result = original_digest(root, container)
        if root == source:
            source_calls += 1
            if source_calls == 2:
                seal_paused.set()
                if not resume_seal.wait(5):
                    raise TimeoutError("seal resume timed out")
        return result

    def seal() -> None:
        try:
            _ = writer.seal_working_evidence(working)
        except (OSError, RunManifestError) as exc:
            failures.append(exc)

    def accept() -> None:
        try:
            _ = working.accept_phase5_attempt_receipt(
                receipt_path,
                authority(working, receipt_path),
            )
        except (OSError, AttemptReceiptError) as exc:
            failures.append(exc)
        finally:
            acceptance_finished.set()

    monkeypatch.setattr(
        run_manifest_evidence_seal,
        "digest_inventory",
        pause_after_final_source_inventory,
    )
    seal_thread = Thread(target=seal)
    accept_thread = Thread(target=accept)
    seal_thread.start()
    assert seal_paused.wait(5)
    accept_thread.start()
    try:
        assert not acceptance_finished.wait(0.2)
    finally:
        resume_seal.set()
        seal_thread.join(5)
        accept_thread.join(5)
    assert not seal_thread.is_alive()
    assert not accept_thread.is_alive()
    assert failures == []
