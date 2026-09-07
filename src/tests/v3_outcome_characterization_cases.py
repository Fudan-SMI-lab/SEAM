from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from core.run_outcome import RunOutcome
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor
from harness.run import finalize_run
from tests.run_finalizer_test_support import (
    FinalizerScenario,
    failed_finalizer_outcome,
    finalization_request,
    passing_finalizer_outcome,
)


def _executor_for_phase(tmp_path: Path, phase: PhaseDefinition) -> WorkflowExecutor:
    workflow = WorkflowDefinition(
        name="task-8-characterization",
        version="1.0",
        phases=[phase],
        terminals=["complete", "failed"],
    )
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "session"
    session_manager.send_command.return_value = '{"result": "ok"}'
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "prompt"
    executor = WorkflowExecutor(
        workflow,
        session_manager,
        MagicMock(),
        prompt_loader,
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.hook_manager = MagicMock()
    return executor


@pytest.mark.parametrize(
    ("phase_status", "errors", "authority", "expected_exit"),
    [
        ("passed", (), passing_finalizer_outcome(), 0),
        ("failed", ("migration failed",), failed_finalizer_outcome(), 1),
    ],
)
def test_characterizes_existing_v3_finalizer_pass_and_fail_exits(
    tmp_path: Path,
    phase_status: str,
    errors: tuple[str, ...],
    authority: RunOutcome,
    expected_exit: int,
) -> None:
    # Given
    request = finalization_request(
        tmp_path,
        FinalizerScenario(
            status=phase_status,
            errors=errors,
            authoritative_outcome=authority,
        ),
    )

    # When
    result = finalize_run(request)

    # Then
    assert result.outcome is authority.terminal_outcome
    assert result.exit_code == expected_exit


def test_characterizes_failure_routed_to_complete_as_failed_phase(
    tmp_path: Path,
) -> None:
    # Given
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="unsupported",
        transitions={"on_failure": "complete"},
    )
    executor = _executor_for_phase(tmp_path, phase)

    # When
    result = executor.execute({})

    # Then
    assert result["status"] == "complete"
    assert result["phase_results"]["phase_5_validation"]["status"] == "failure"


def test_characterizes_legacy_executor_terminal_envelope(
    tmp_path: Path,
) -> None:
    # Given
    phase = PhaseDefinition(
        id="phase_a",
        name="A",
        prompt_template="prompt",
        output_schema={},
        type="llm",
        transitions={"on_success": "failed"},
    )
    executor = _executor_for_phase(tmp_path, phase)

    # When
    result = executor.execute({})

    # Then
    assert result == {
        "state": executor.state,
        "phase_results": executor.phase_results,
        "status": "complete",
    }


def test_characterizes_empty_legacy_executor_as_complete(tmp_path: Path) -> None:
    # Given
    executor = _executor_for_phase(
        tmp_path,
        PhaseDefinition(
            id="unused",
            name="Unused",
            prompt_template="",
            output_schema={},
        ),
    )
    executor.workflow.phases = []

    # When
    result = executor.execute({})

    # Then
    assert result["status"] == "complete"
    assert result["phase_results"] == {}


def test_characterizes_v2_summary_mapping_without_v3_domain_outcome() -> None:
    # Given
    script = """
from tests.e2e.e2e_test_v2 import PhaseStatus, build_v2_summary
phase = PhaseStatus(1, "phase_0", "phase_0", "passed")
summary = build_v2_summary(
    run_id="v2-run", base_url="http://127.0.0.1:4096", output_dir="output",
    temp_dir="temp", keep_temp_dir=False, max_phase5_iter=5,
    phase_results=[phase], session_count=0, command_count=0,
    total_duration_seconds=0.0, artifact_dir=None, telemetry_paths={},
    before_snapshot_path=None, after_snapshot_path=None, entry_script=None, errors=[],
)
print(summary.overall_status)
"""

    # When
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "PASS"
