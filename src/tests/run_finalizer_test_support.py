from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.run_manifest import RunId
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    RunOutcome,
    TerminalAnchor,
    WorkflowTerminal,
)
from harness.run import (
    FinalizationHooks,
    PhaseStatus,
    RunArtifacts,
    RunExecution,
    RunFinalizationRequest,
    RunIdentity,
)


def _finalizer_outcome(validation_succeeded: bool) -> RunOutcome:
    return RunOutcome(
        validation_succeeded=validation_succeeded,
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_0_env_detect")),
        executed_phases=(PhaseId("phase_0_env_detect"),),
        accepted_attempt_id=(
            AcceptedAttemptId("phase-5-attempt-final") if validation_succeeded else None
        ),
        review_rounds=(),
    )


def passing_finalizer_outcome() -> RunOutcome:
    return _finalizer_outcome(True)


def failed_finalizer_outcome() -> RunOutcome:
    return _finalizer_outcome(False)


@dataclass(frozen=True, slots=True)
class FinalizerScenario:
    status: str = "passed"
    errors: tuple[str, ...] = ()
    hooks: FinalizationHooks | None = None
    authoritative_outcome: RunOutcome = field(default_factory=passing_finalizer_outcome)


def finalization_request(
    output_dir: Path,
    scenario: FinalizerScenario,
) -> RunFinalizationRequest:
    before_snapshot = output_dir / "before_snapshot.json"
    if not before_snapshot.exists():
        _ = before_snapshot.write_text("{}", encoding="utf-8")
    return RunFinalizationRequest(
        identity=RunIdentity(
            run_id=RunId("run-safe-1"),
            base_url="http://127.0.0.1:4096",
            workflow_path="workflow.yaml",
            output_dir=str(output_dir),
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
                    status=scenario.status,
                    duration_seconds=1.25,
                    error=scenario.errors[0] if scenario.errors else None,
                ),
            ),
            session_count=2,
            command_count=3,
            total_duration_seconds=4.5,
            errors=scenario.errors,
        ),
        initial_artifacts=RunArtifacts(
            before_snapshot_path=str(before_snapshot),
            entry_script="python app.py",
        ),
        hooks=scenario.hooks or FinalizationHooks.empty(),
        authoritative_outcome=scenario.authoritative_outcome,
    )
