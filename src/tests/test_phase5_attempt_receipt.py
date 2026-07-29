from __future__ import annotations

from pathlib import Path

import pytest

from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    accept_attempt_receipt,
    artifact_file_receipt,
    finalize_attempt_receipt,
    load_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_backend_failure_cases import (
    test_container_setup_failure_never_allocates_fabricated_local_receipt as _backend_failure_case,
)
from tests.phase5_receipt_security_cases import (
    test_artifact_tampering_before_acceptance_fails_closed as _tamper_case,
    test_receipt_from_another_run_cannot_be_accepted as _run_binding_case,
    test_symlink_preseed_cannot_overwrite_external_file as _symlink_case,
    test_mutated_invocation_cannot_be_accepted as _invocation_case,
    test_accepted_receipt_cannot_transition_back_to_finalized as _monotonic_case,
    test_finalized_gate_evidence_substitution_invalidates_authority as _gate_evidence_case,
)
from tests.phase5_persistence_failure_cases import (
    test_compatibility_exhaustion_requires_finalized_receipt_authority as _compatibility_persistence_case,
    test_internal_receipt_failure_rolls_back_attempt_files as _rollback_case,
    test_metadata_hash_failure_rolls_back_shell_artifacts as _hash_rollback_case,
    test_successful_rerun_with_receipt_failure_cannot_reuse_prior_attempt as _persistence_case,
)
from tests.phase5_receipt_test_support import authority, review, save_attempt
from tests.phase5_bonus_receipt_cases import (
    test_final_budget_validation_only_rerun_is_the_accepted_receipt as _bonus_case,
)
from tests.phase5_attempt_workflow_cases import (
    test_fake_retained_container_receipt_captures_same_call_identity as _container_case,
    test_real_validation_rerun_reserves_before_launch_and_accepts_actual_attempt as _local_case,
)

test_fake_retained_container_receipt_captures_same_call_identity = _container_case
test_real_validation_rerun_reserves_before_launch_and_accepts_actual_attempt = (
    _local_case
)
test_final_budget_validation_only_rerun_is_the_accepted_receipt = _bonus_case
test_container_setup_failure_never_allocates_fabricated_local_receipt = (
    _backend_failure_case
)


def custom_op_evidence(
    tmp_path: Path, status: CustomOpGateStatus
) -> CustomOpGateEvidence:
    if status is not CustomOpGateStatus.PASSED:
        return CustomOpGateEvidence(status=status)
    report_path = tmp_path / "custom_op_final_gate.json"
    _ = report_path.write_text('{"passed": true}', encoding="utf-8")
    return CustomOpGateEvidence(
        status=status,
        report=artifact_file_receipt(report_path),
    )


def test_attempt_ids_are_reserved_at_execution_not_derived_from_artifact_order(
    tmp_path: Path,
) -> None:
    # Given misleading shell metadata names exist before any actual reservation.
    store = ArtifactStore(str(tmp_path), "run-1")
    shell_dir = Path(store.artifact_dir) / "shell_attempts"
    shell_dir.mkdir(parents=True)
    _ = (shell_dir / "run_entry_script_attempt9999.meta.json").write_text(
        "{}", encoding="utf-8"
    )

    # When two actual execution slots are reserved.
    first = store.reserve_phase5_attempt()
    second = store.reserve_phase5_attempt()

    # Then IDs follow atomic reservations rather than filenames or list order.
    assert first.attempt_id == "phase_5_validation-attempt-1"
    assert second.attempt_id == "phase_5_validation-attempt-2"


def test_fail_then_validation_only_success_accepts_only_second_attempt(
    tmp_path: Path,
) -> None:
    # Given one failed shell and one final validation-only success.
    store = ArtifactStore(str(tmp_path), "run-1")
    failed_path = save_attempt(store, tmp_path, exit_code=7)
    successful_path = save_attempt(store, tmp_path, exit_code=0)
    inactive = CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE)
    failed = finalize_attempt_receipt(
        failed_path, custom_op_gate=inactive, review=review(ReviewOutcome.DISABLED)
    )
    successful = finalize_attempt_receipt(
        successful_path,
        custom_op_gate=inactive,
        review=review(ReviewOutcome.DISABLED),
    )

    # When Task 8 marks the real accepting execution.
    accepted = accept_attempt_receipt(
        successful_path, authority(store, successful_path)
    )

    # Then the failed attempt remains rejected and only the rerun is accepted.
    assert failed.accepted is False
    assert successful.accepted is False
    assert accepted.accepted is True
    assert load_attempt_receipt(failed_path).accepted is False


@pytest.mark.parametrize(
    ("exit_code", "gate", "review_outcome"),
    [
        (1, CustomOpGateStatus.INACTIVE, ReviewOutcome.DISABLED),
        (0, CustomOpGateStatus.FAILED, ReviewOutcome.DISABLED),
        (0, CustomOpGateStatus.INACTIVE, ReviewOutcome.REJECTED),
        (0, CustomOpGateStatus.INACTIVE, ReviewOutcome.UNKNOWN),
    ],
)
def test_failed_gate_or_unreviewed_attempt_cannot_be_accepted(
    tmp_path: Path,
    exit_code: int,
    gate: CustomOpGateStatus,
    review_outcome: ReviewOutcome,
) -> None:
    # Given a receipt missing at least one required acceptance fact.
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=exit_code)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=custom_op_evidence(tmp_path, gate),
        review=review(review_outcome),
    )

    # When acceptance is requested.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = accept_attempt_receipt(receipt_path, authority(store, receipt_path))

    # Then the typed authority rejects the attempt.
    assert raised.value.kind is AttemptReceiptErrorKind.NOT_ACCEPTABLE


@pytest.mark.parametrize(
    ("gate", "review_outcome"),
    [
        (CustomOpGateStatus.INACTIVE, ReviewOutcome.DISABLED),
        (CustomOpGateStatus.PASSED, ReviewOutcome.DISABLED),
        (CustomOpGateStatus.INACTIVE, ReviewOutcome.ACCEPTED),
        (CustomOpGateStatus.PASSED, ReviewOutcome.ACCEPTED),
    ],
)
def test_review_disabled_or_explicit_accept_and_valid_gate_are_acceptable(
    tmp_path: Path,
    gate: CustomOpGateStatus,
    review_outcome: ReviewOutcome,
) -> None:
    # Given a zero-exit complete receipt with every active gate satisfied.
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=custom_op_evidence(tmp_path, gate),
        review=review(review_outcome),
    )

    # When the matching execution identity is accepted.
    receipt = accept_attempt_receipt(receipt_path, authority(store, receipt_path))

    # Then acceptance is durable and evidence-backed.
    assert receipt.accepted is True
    assert load_attempt_receipt(receipt_path).accepted is True


def test_missing_receipt_and_mismatched_attempt_identity_fail_closed(
    tmp_path: Path,
) -> None:
    # Given a missing path and a complete real receipt.
    missing = tmp_path / "missing.receipt.json"
    store = ArtifactStore(str(tmp_path), "run-1")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    other_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        other_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )

    # When either authority is used.
    with pytest.raises(AttemptReceiptError) as missing_error:
        _ = load_attempt_receipt(missing)
    with pytest.raises(AttemptReceiptError) as identity_error:
        _ = accept_attempt_receipt(
            receipt_path,
            authority(store, other_path),
        )

    # Then both fail with precise reasons.
    assert missing_error.value.kind is AttemptReceiptErrorKind.MISSING
    assert identity_error.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH


test_artifact_tampering_before_acceptance_fails_closed = _tamper_case
test_receipt_from_another_run_cannot_be_accepted = _run_binding_case
test_symlink_preseed_cannot_overwrite_external_file = _symlink_case
test_mutated_invocation_cannot_be_accepted = _invocation_case
test_accepted_receipt_cannot_transition_back_to_finalized = _monotonic_case
test_finalized_gate_evidence_substitution_invalidates_authority = _gate_evidence_case
test_internal_receipt_failure_rolls_back_attempt_files = _rollback_case
test_compatibility_exhaustion_requires_finalized_receipt_authority = (
    _compatibility_persistence_case
)
test_metadata_hash_failure_rolls_back_shell_artifacts = _hash_rollback_case
test_successful_rerun_with_receipt_failure_cannot_reuse_prior_attempt = (
    _persistence_case
)
