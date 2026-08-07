import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from typing_extensions import assert_never

from core.continuation_models import PhasePresentationStatus
from core.run_manifest import RunId
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    RunOutcome,
    TerminalAnchor,
    TerminalOutcome,
    WorkflowTerminal,
)
from harness.run import (
    FinalizationHooks,
    PhaseStatus,
    RunArtifacts,
    RunExecution,
    RunFinalizationRequest,
    RunIdentity,
    build_run_summary,
    write_json_text,
)


@pytest.mark.parametrize(
    ("phase_status", "errors", "expected_status", "expected_exit"),
    [
        (PhasePresentationStatus.PASSED, [], "PASS", 0),
        (
            PhasePresentationStatus.FAILED,
            ["RuntimeError: migration failed"],
            "FAIL",
            1,
        ),
    ],
)
def test_v3_summary_bytes_and_exit_mapping_are_stable(
    tmp_path: Path,
    phase_status: PhasePresentationStatus,
    errors: list[str],
    expected_status: str,
    expected_exit: int,
) -> None:
    # Given
    outcome = RunOutcome(
        validation_succeeded=(
            phase_status is PhasePresentationStatus.PASSED and not errors
        ),
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_0_env_detect")),
        executed_phases=(PhaseId("phase_0_env_detect"),),
        accepted_attempt_id=(
            AcceptedAttemptId("phase-5-attempt-summary")
            if phase_status is PhasePresentationStatus.PASSED and not errors
            else None
        ),
        review_rounds=(),
    )
    summary = build_run_summary(
        RunFinalizationRequest(
            identity=RunIdentity(
                run_id=RunId("baseline-run"),
                base_url="http://127.0.0.1:4096",
                workflow_path="workflow.yaml",
                output_dir="output",
                temp_dir="temp",
            ),
            execution=RunExecution(
                keep_temp_dir=False,
                requested_max_phase5_iter=5,
                effective_max_phase5_iter=5,
                phases=(
                    PhaseStatus(
                        phase_number=1,
                        phase_id="phase_0_env_detect",
                        label="phase_0_env_detect",
                        status=phase_status,
                        duration_seconds=1.25,
                        error=errors[0] if errors else None,
                    ),
                ),
                session_count=2,
                command_count=3,
                total_duration_seconds=4.5,
                errors=tuple(errors),
            ),
            initial_artifacts=RunArtifacts(
                telemetry_paths=(("telemetry_json", "telemetry.json"),),
                before_snapshot_path="before_snapshot.json",
                after_snapshot_path="after_snapshot.json",
                entry_script="python app.py",
            ),
            hooks=FinalizationHooks.empty(),
            authoritative_outcome=outcome,
        )
    )
    summary_path = tmp_path / "summary.json"

    # When
    serialized_summary = json.dumps(
        asdict(summary),
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    _ = write_json_text(summary_path, serialized_summary)
    actual_bytes = summary_path.read_bytes()
    if outcome.terminal_outcome is TerminalOutcome.FAILED:
        exit_code = 1
    elif (
        outcome.terminal_outcome is TerminalOutcome.PASSED
        or outcome.terminal_outcome is TerminalOutcome.PASSED_WITH_REVIEWS
    ):
        exit_code = 0
    else:
        assert_never(outcome.terminal_outcome)

    # Then
    expected_payload = asdict(summary)
    expected_payload["overall_status"] = expected_status
    expected_text = json.dumps(
        expected_payload,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    expected_bytes = expected_text.replace("\n", os.linesep).encode()
    assert actual_bytes == expected_bytes
    assert list(expected_payload) == [
        "run_id",
        "base_url",
        "workflow_path",
        "output_dir",
        "temp_dir",
        "keep_temp_dir",
        "requested_max_phase5_iter",
        "effective_max_phase5_iter",
        "phases",
        "session_count",
        "command_count",
        "overall_status",
        "total_duration_seconds",
        "artifact_dir",
        "telemetry_paths",
        "before_snapshot_path",
        "after_snapshot_path",
        "entry_script",
        "errors",
        "trace",
        "review_timeout_observability",
    ]
    assert summary.overall_status == expected_status
    assert exit_code == expected_exit
