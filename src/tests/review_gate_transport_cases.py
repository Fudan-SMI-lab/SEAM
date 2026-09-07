from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.review_gate import REVIEW_GATE_STATE_KEY, ReviewGate
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


@dataclass(frozen=True, slots=True)
class ReviewHarness:
    executor: WorkflowExecutor
    phase: PhaseDefinition
    loop_state: dict[str, ReviewGate]
    session_manager: MagicMock


def _review_harness(
    tmp_path: Path,
    failures: list[TimeoutError | KeyboardInterrupt],
) -> ReviewHarness:
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "review-session"
    session_manager.send_command.side_effect = failures
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "review"
    executor = WorkflowExecutor(
        WorkflowDefinition(
            name="review-transport",
            version="1.0",
            phases=[],
            terminals=["complete"],
        ),
        session_manager,
        MagicMock(),
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
    return ReviewHarness(
        executor=executor,
        phase=phase,
        loop_state={REVIEW_GATE_STATE_KEY: ReviewGate()},
        session_manager=session_manager,
    )


def test_reviewer_timeout_propagates_without_added_retry(tmp_path: Path) -> None:
    # Given
    harness = _review_harness(tmp_path, [TimeoutError("review timed out")])

    # When / Then
    with pytest.raises(TimeoutError, match="review timed out"):
        harness.executor._execute_review_phase(
            harness.phase,
            {},
            {},
            loop_vars={},
            loop_state=harness.loop_state,
            loop_history=[],
            sub_workflow_def=None,
            verdicts_cfg={},
        )
    assert harness.session_manager.send_command.call_count == 1
    assert harness.loop_state[REVIEW_GATE_STATE_KEY].rounds == ()


def test_repeated_reviewer_interruptions_do_not_advance_state(tmp_path: Path) -> None:
    # Given
    harness = _review_harness(
        tmp_path,
        [KeyboardInterrupt(), KeyboardInterrupt()],
    )

    # When / Then
    for _attempt in range(2):
        with pytest.raises(KeyboardInterrupt):
            harness.executor._execute_review_phase(
                harness.phase,
                {},
                {},
                loop_vars={},
                loop_state=harness.loop_state,
                loop_history=[],
                sub_workflow_def=None,
                verdicts_cfg={},
            )
    assert harness.session_manager.send_command.call_count == 2
    assert harness.loop_state[REVIEW_GATE_STATE_KEY].rounds == ()
