from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.review_gate import REVIEW_GATE_STATE_KEY, ReviewGate, ReviewGateClosedError
from core.run_outcome import ReviewOutcome, ReviewVerdict
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


@dataclass(frozen=True, slots=True)
class ReviewBoundaryCase:
    responses: tuple[str, ...]
    expected: ReviewOutcome
    command_count: int


def test_active_v3_review_reject_increments_current_loop_counter(
    tmp_path: Path,
) -> None:
    # Given
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "review-session"
    session_manager.send_command.return_value = (
        '{"verdict": "reject", "reasoning": "accelerator evidence is missing"}'
    )
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "review"
    executor = WorkflowExecutor(
        WorkflowDefinition(
            name="review-characterization",
            version="1.0",
            phases=[],
            terminals=["complete"],
        ),
        session_manager,
        artifact_store,
        prompt_loader,
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="review",
        name="Review",
        prompt_template="review_prompt",
        output_schema={},
        type="review",
        agent="main_engineer",
    )
    loop_state = {"iteration": 1}

    # When
    result = executor._execute_review_phase(
        phase,
        {},
        {},
        loop_vars={},
        loop_state=loop_state,
        loop_history=[],
        sub_workflow_def=None,
        verdicts_cfg={},
    )

    # Then
    assert result["status"] == "reject"
    assert loop_state["review_reject_count"] == 1


def test_logical_reject_then_accept_records_two_rounds() -> None:
    # Given
    gate = ReviewGate()

    # When
    rejected = gate.record_judgment(ReviewVerdict.REJECT)
    accepted = rejected.record_judgment(ReviewVerdict.ACCEPT)

    # Then
    assert [review_round.round_number for review_round in accepted.rounds] == [1, 2]
    assert accepted.outcome is ReviewOutcome.ACCEPTED
    assert accepted.remaining_rounds == 1


def test_three_rejects_exhaust_gate_and_forbid_fourth_judgment() -> None:
    # Given
    gate = ReviewGate()

    # When
    for _round_number in range(3):
        gate = gate.record_judgment(ReviewVerdict.REJECT)

    # Then
    assert gate.outcome is ReviewOutcome.REJECT_EXHAUSTED
    assert gate.remaining_rounds == 0
    with pytest.raises(ReviewGateClosedError):
        gate.record_judgment(ReviewVerdict.ACCEPT)


@pytest.mark.parametrize(
    ("transition", "expected"),
    [
        ("unknown", ReviewOutcome.UNKNOWN),
        ("session_error", ReviewOutcome.SESSION_ERROR),
    ],
)
def test_non_verdict_terminal_states_close_one_logical_round(
    transition: str,
    expected: ReviewOutcome,
) -> None:
    # Given
    gate = ReviewGate()

    # When
    terminal = (
        gate.record_judgment(ReviewVerdict.UNKNOWN)
        if transition == "unknown"
        else gate.record_session_error()
    )

    # Then
    assert len(terminal.rounds) == 1
    assert terminal.outcome is expected


def test_improvement_error_replaces_rejection_without_adding_judgment() -> None:
    # Given
    rejected = ReviewGate().record_judgment(ReviewVerdict.REJECT)

    # When
    failed = rejected.record_improvement_error()

    # Then
    assert len(failed.rounds) == 1
    assert failed.outcome is ReviewOutcome.IMPROVEMENT_ERROR


@pytest.mark.parametrize(
    "case",
    [
        ReviewBoundaryCase(
            responses=(
                "not json",
                '{"verdict": "reject", "reasoning": "missing evidence"}',
            ),
            expected=ReviewOutcome.REJECTED,
            command_count=2,
        ),
        ReviewBoundaryCase(
            responses=(
                "All checks passed; accept this run.",
                "All checks passed; accept this run.",
            ),
            expected=ReviewOutcome.UNKNOWN,
            command_count=2,
        ),
        ReviewBoundaryCase(
            # rationale: Task 6 contract — compaction raises ContextExhaustedError; lowercase envelope stays SESSION_ERROR (Edge 14).
            responses=('{"ok": false, "error": "transport failure"}',),
            expected=ReviewOutcome.SESSION_ERROR,
            command_count=1,
        ),
    ],
)
def test_review_boundary_records_one_round_after_transport_and_parse_handling(
    case: ReviewBoundaryCase,
    tmp_path: Path,
) -> None:
    # Given
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "review-session"
    session_manager.send_command.side_effect = list(case.responses)
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "review"
    executor = WorkflowExecutor(
        WorkflowDefinition(
            name="review-boundary",
            version="1.0",
            phases=[],
            terminals=["complete"],
        ),
        session_manager,
        artifact_store,
        prompt_loader,
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="review",
        name="Review",
        prompt_template="review_prompt",
        output_schema={},
        type="review",
        agent="main_engineer",
    )
    loop_state = {REVIEW_GATE_STATE_KEY: ReviewGate()}

    # When
    result = executor._execute_review_phase(
        phase,
        {},
        {},
        loop_vars={},
        loop_state=loop_state,
        loop_history=[],
        sub_workflow_def=None,
        verdicts_cfg={},
    )

    # Then
    updated_gate = loop_state[REVIEW_GATE_STATE_KEY]
    assert isinstance(updated_gate, ReviewGate)
    assert updated_gate.outcome is case.expected
    assert len(updated_gate.rounds) == 1
    assert session_manager.send_command.call_count == case.command_count
    assert result["status"] == case.expected.value
