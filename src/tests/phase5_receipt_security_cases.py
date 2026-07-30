from __future__ import annotations

from pathlib import Path

import pytest

from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    ShellInvocation,
    accept_attempt_receipt,
    finalize_attempt_receipt,
    load_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import authority, execution, review, save_attempt


def test_artifact_tampering_before_acceptance_fails_closed(tmp_path: Path) -> None:
    # Given a finalized receipt whose stdout changes before acceptance.
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    receipt = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    original_authority = authority(store, receipt_path)
    _ = Path(receipt.artifacts.stdout.path).write_text("tampered", encoding="utf-8")

    # When acceptance checks the durable evidence.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = accept_attempt_receipt(receipt_path, original_authority)

    # Then stale hashes never become acceptance authority.
    assert raised.value.kind is AttemptReceiptErrorKind.INTEGRITY_MISMATCH


def test_receipt_from_another_run_cannot_be_accepted(tmp_path: Path) -> None:
    # Given a valid receipt from a different run with the same attempt number.
    store = ArtifactStore(str(tmp_path), "run-a")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    other_root = tmp_path / "other-run"
    other_root.mkdir()
    other_store = ArtifactStore(str(other_root), "run-b")
    other_path = save_attempt(other_store, other_root, exit_code=0)
    _ = finalize_attempt_receipt(
        other_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )

    # When another run tries to accept it.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = accept_attempt_receipt(
            receipt_path,
            authority(other_store, other_path),
        )

    # Then run identity is part of acceptance authority.
    assert raised.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH


def test_symlink_preseed_cannot_overwrite_external_file(tmp_path: Path) -> None:
    # Given an attacker pre-seeds the reserved stdout path as a symlink.
    store = ArtifactStore(str(tmp_path), "run-1")
    attempt = execution(store, tmp_path)
    victim = tmp_path / "victim.txt"
    _ = victim.write_text("keep", encoding="utf-8")
    stdout_path = Path(f"{attempt.reservation.prefix}.stdout.log")
    try:
        stdout_path.symlink_to(victim)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    # When shell artifacts are persisted.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="unsafe overwrite",
            stderr="",
            execution=attempt,
        )
    assert raised.value.kind is AttemptReceiptErrorKind.UNSAFE_PATH

    # Then the symlink target remains untouched.
    assert victim.read_text(encoding="utf-8") == "keep"


def test_mutated_invocation_cannot_be_accepted(tmp_path: Path) -> None:
    # Given a draft receipt whose recorded invocation is replaced in place.
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    original_authority = authority(store, receipt_path)
    draft = load_attempt_receipt(receipt_path)
    forged = draft.model_copy(
        update={"invocation": ShellInvocation(argv=("python", "forged.py"))}
    )
    _ = receipt_path.write_text(forged.model_dump_json(indent=2), encoding="utf-8")
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )

    # When the live store promotes the mutated receipt.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = accept_attempt_receipt(receipt_path, original_authority)

    # Then same-run marker possession cannot forge actual execution facts.
    assert raised.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH


def test_accepted_receipt_cannot_transition_back_to_finalized(tmp_path: Path) -> None:
    # Given a fully accepted receipt.
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    inactive = CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE)
    disabled = review(ReviewOutcome.DISABLED)
    _ = finalize_attempt_receipt(receipt_path, custom_op_gate=inactive, review=disabled)
    _ = accept_attempt_receipt(receipt_path, authority(store, receipt_path))

    # When finalization is attempted again.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = finalize_attempt_receipt(
            receipt_path, custom_op_gate=inactive, review=disabled
        )

    # Then accepted state is monotonic.
    assert raised.value.kind is AttemptReceiptErrorKind.STALE_TRANSITION


def test_finalized_gate_evidence_substitution_invalidates_authority(
    tmp_path: Path,
) -> None:
    # Given trusted finalized evidence is recorded before acceptance.
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    finalized_authority = authority(store, receipt_path)
    receipt = load_attempt_receipt(receipt_path)
    substituted = receipt.model_copy(
        update={
            "custom_op_gate": CustomOpGateEvidence(
                status=CustomOpGateStatus.FAILED,
                errors=("substituted",),
            )
        }
    )
    _ = receipt_path.write_text(substituted.model_dump_json(indent=2), encoding="utf-8")

    # When acceptance receives the substituted evidence.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = accept_attempt_receipt(receipt_path, finalized_authority)

    # Then the finalized evidence digest fails closed.
    assert raised.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH
