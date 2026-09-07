from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    iteration: int
    stagnation_count: int
    last_error_signature: str
    review_reject_count: int
    entry_script_revision_count: int
    environment_reset_count: int


def test_hydrate_phase5_execution_resets_all_loop_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        max_iterations=1,
        stagnation_threshold=3,
        review_gate_enabled=True,
        max_review_iterations=3,
    )
    workflow = WorkflowDefinition(
        name="counter-reset",
        version="1",
        sub_workflows={"repair_loop": sub_workflow},
        terminals=["complete"],
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Phase 5",
        prompt_template="",
        output_schema={},
        type="loop",
        sub_workflow="repair_loop",
    )
    starts: list[CounterSnapshot] = []

    def run_sub_workflow(*args):
        loop_state = args[-1]
        starts.append(
            CounterSnapshot(
                iteration=int(loop_state["iteration"]),
                stagnation_count=int(loop_state["stagnation_count"]),
                last_error_signature=str(loop_state["last_error_signature"]),
                review_reject_count=int(loop_state["review_reject_count"]),
                entry_script_revision_count=int(
                    loop_state["entry_script_revision_count"]
                ),
                environment_reset_count=int(loop_state["environment_reset_count"]),
            )
        )
        loop_state.update(
            {
                "iteration": 99,
                "stagnation_count": 8,
                "last_error_signature": "parent-error",
                "review_reject_count": 7,
                "entry_script_revision_count": 6,
                "environment_reset_count": 5,
            }
        )
        return {"status": "success", "step_outputs": {"script_exit_code": 0}}

    monkeypatch.setattr(executor, "_run_sub_workflow", run_sub_workflow)
    _ = executor._execute_loop_phase(phase, {}, {})
    _ = executor._execute_loop_phase(phase, {}, {})

    expected = CounterSnapshot(
        iteration=0,
        stagnation_count=0,
        last_error_signature="",
        review_reject_count=0,
        entry_script_revision_count=0,
        environment_reset_count=0,
    )
    assert starts == [expected, expected]
