import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.state_machine import StateMachine
from core.types import PhaseDefinition, WorkflowDefinition


def test_success_transition_chain():
    phases = [
        PhaseDefinition(
            id="phase_0",
            name="P0",
            prompt_template="p0.md",
            output_schema="s0.json",
            validator="none",
            transitions={"on_success": "phase_1", "on_failure": "failed"},
        ),
        PhaseDefinition(
            id="phase_1",
            name="P1",
            prompt_template="p1.md",
            output_schema="s1.json",
            validator="none",
            transitions={"on_success": "complete", "on_failure": "failed"},
        ),
    ]
    sm = StateMachine(
        WorkflowDefinition(
            name="test",
            version=1,
            globals={},
            phases=phases,
            terminals=["complete", "failed"],
        )
    )

    success, next_phase = sm.record_success("phase_0")
    assert success is True
    assert next_phase == "phase_1", f"Expected phase_1, got {next_phase}"
    assert sm.current_phase == "phase_1"

    success, next_phase = sm.record_success("phase_1")
    assert success is True
    assert next_phase == "complete", f"Expected complete, got {next_phase}"
    assert sm.is_terminal()
    assert sm.current_terminal() == "complete"


def test_max_retry_enforcement():
    phases = [
        PhaseDefinition(
            id="phase_0",
            name="P0",
            prompt_template="p0.md",
            output_schema="s0.json",
            validator="none",
            transitions={"on_success": "complete", "on_failure": "failed"},
        )
    ]
    sm = StateMachine(
        WorkflowDefinition(
            name="test",
            version=1,
            globals={"max_retry_per_phase": 2},
            phases=phases,
            terminals=["complete", "failed"],
        )
    )

    retries_left, next_phase = sm.record_failure("phase_0", "error1")
    assert retries_left is True
    assert next_phase is None
    assert sm.get_failure_count("phase_0") == 1
    assert not sm.is_terminal()

    retries_left, next_phase = sm.record_failure("phase_0", "error2")
    assert retries_left is False
    assert next_phase == "failed"
    assert sm.is_terminal(), "Should be terminal after max retries"
    assert sm.current_terminal() == "failed", f"Expected failed, got {sm.current_terminal()}"
