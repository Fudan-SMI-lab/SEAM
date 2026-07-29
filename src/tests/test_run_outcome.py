from __future__ import annotations

import pytest

from harness.run import PhaseStatus
from core.run_outcome import (
    ReviewOutcome,
    RunOutcome,
    TerminalOutcome,
)
from .e2e.e2e_test_v3 import build_v3_summary
from tests.run_outcome_test_support import (
    run_outcome as _run_outcome,
)
from tests.run_outcome_contract_cases import (
    test_domain_records_are_immutable as test_domain_records_are_immutable,
    test_empty_executed_phase_set_cannot_pass as test_empty_executed_phase_set_cannot_pass,
    test_malformed_or_misleading_review_status_cannot_silently_pass as test_malformed_or_misleading_review_status_cannot_silently_pass,
    test_review_parsers_fail_closed_for_non_string_input as test_review_parsers_fail_closed_for_non_string_input,
    test_review_verdict_boundary_parses_only_explicit_tokens as test_review_verdict_boundary_parses_only_explicit_tokens,
    test_run_outcome_accepts_consistent_review_history as test_run_outcome_accepts_consistent_review_history,
    test_run_outcome_preserves_distinct_terminal_and_review_facts as test_run_outcome_preserves_distinct_terminal_and_review_facts,
    test_run_outcome_rejects_inconsistent_review_history as test_run_outcome_rejects_inconsistent_review_history,
)


def test_existing_v3_summary_passes_when_an_executed_phase_passes() -> None:
    # Given
    phase_results = [
        PhaseStatus(
            phase_number=1,
            phase_id="phase_0_env_detect",
            label="phase_0_env_detect",
            status="passed",
        )
    ]

    # When
    summary = build_v3_summary(
        authoritative_outcome=_run_outcome(
            True,
            ReviewOutcome.DISABLED,
            True,
        ),
        run_id="baseline-run",
        base_url="http://127.0.0.1:4096",
        workflow_path="workflow.yaml",
        output_dir="output",
        temp_dir="temp",
        keep_temp_dir=True,
        max_phase5_iter=5,
        phase_results=phase_results,
        session_count=0,
        command_count=0,
        total_duration_seconds=0.0,
        artifact_dir=None,
        telemetry_paths={},
        before_snapshot_path=None,
        after_snapshot_path=None,
        entry_script=None,
        errors=[],
    )

    # Then
    assert summary.overall_status == "PASS"


@pytest.mark.parametrize(
    ("authority", "phase_status", "errors", "expected_status"),
    [
        (
            _run_outcome(True, ReviewOutcome.DISABLED, True),
            "failed",
            ["contradictory display failure"],
            "PASS",
        ),
        (
            _run_outcome(False, ReviewOutcome.DISABLED, True),
            "passed",
            [],
            "FAIL",
        ),
    ],
)
def test_v3_summary_uses_only_supplied_authority(
    authority: RunOutcome,
    phase_status: str,
    errors: list[str],
    expected_status: str,
) -> None:
    # Given
    phase_results = [
        PhaseStatus(
            phase_number=1,
            phase_id="phase_0_env_detect",
            label="phase_0_env_detect",
            status=phase_status,
        )
    ]

    # When
    summary = build_v3_summary(
        authoritative_outcome=authority,
        run_id="authority-run",
        base_url="http://127.0.0.1:4096",
        workflow_path="workflow.yaml",
        output_dir="output",
        temp_dir="temp",
        keep_temp_dir=True,
        max_phase5_iter=5,
        phase_results=phase_results,
        session_count=0,
        command_count=0,
        total_duration_seconds=0.0,
        artifact_dir=None,
        telemetry_paths={},
        before_snapshot_path=None,
        after_snapshot_path=None,
        entry_script=None,
        errors=errors,
    )

    # Then
    assert summary.overall_status == expected_status


@pytest.mark.parametrize(
    "matrix_case",
    [
        (ReviewOutcome.DISABLED, TerminalOutcome.PASSED, TerminalOutcome.PASSED),
        (ReviewOutcome.ACCEPTED, TerminalOutcome.PASSED, TerminalOutcome.PASSED),
        (ReviewOutcome.REJECTED, TerminalOutcome.FAILED, TerminalOutcome.FAILED),
        (
            ReviewOutcome.REJECT_EXHAUSTED,
            TerminalOutcome.FAILED,
            TerminalOutcome.PASSED_WITH_REVIEWS,
        ),
        (ReviewOutcome.UNKNOWN, TerminalOutcome.FAILED, TerminalOutcome.FAILED),
        (ReviewOutcome.SESSION_ERROR, TerminalOutcome.FAILED, TerminalOutcome.FAILED),
        (
            ReviewOutcome.IMPROVEMENT_ERROR,
            TerminalOutcome.FAILED,
            TerminalOutcome.FAILED,
        ),
    ],
)
@pytest.mark.parametrize("review_fail_closed", [True, False])
@pytest.mark.parametrize("validation_succeeded", [True, False])
def test_terminal_outcome_follows_validation_review_and_policy_matrix(
    matrix_case: tuple[ReviewOutcome, TerminalOutcome, TerminalOutcome],
    review_fail_closed: bool,
    validation_succeeded: bool,
) -> None:
    # Given
    review_outcome, strict_expected, compatibility_expected = matrix_case
    expected = (
        TerminalOutcome.FAILED
        if not validation_succeeded
        else strict_expected
        if review_fail_closed
        else compatibility_expected
    )

    # When
    outcome = _run_outcome(
        validation_succeeded,
        review_outcome,
        review_fail_closed,
    )

    # Then
    assert outcome.terminal_outcome is expected
