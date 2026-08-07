from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from core.compat import TypeAlias

from core.review_gate import ReviewGate
from core.continuation_hydration_models import ParentAcceptedAttemptReference
from core.run_outcome import PhaseId, RunOutcome, WorkflowTerminal
from core.v3_outcome_mapping import (
    ExecutedPhase,
    Phase5Decision,
    Phase5ExecutionDisposition,
    Phase5Inputs,
    V3RunFacts,
    build_v3_run_outcome,
    evaluate_phase5,
    parse_phase5_execution_disposition,
    parse_phase_disposition,
    PhaseDisposition,
)


RuntimeValue: TypeAlias = (
    "str | int | float | bool | None | ReviewGate | Mapping[str, RuntimeValue]"
)


@dataclass(frozen=True)
class Phase5RuntimeConfig:
    review_enabled: bool
    review_fail_closed: bool
    max_review_rounds: int


def phase5_decision_from_runtime(
    result: Mapping[str, RuntimeValue],
    config: Phase5RuntimeConfig,
) -> Phase5Decision:
    loop_state_value = result.get("loop_state")
    loop_state = loop_state_value if isinstance(loop_state_value, dict) else {}
    attempt_value = loop_state.get("latest_shell_attempt_artifacts")
    attempt = attempt_value if isinstance(attempt_value, dict) else {}
    attempt_number_value = attempt.get("attempt")
    attempt_number = (
        attempt_number_value
        if type(attempt_number_value) is int and attempt_number_value > 0
        else None
    )

    script_exit_code = loop_state.get("script_exit_code")
    validation_succeeded = type(script_exit_code) is int and script_exit_code == 0

    gate_value = result.get("review_gate")
    review_gate = gate_value if isinstance(gate_value, ReviewGate) else None
    review_enabled = (
        review_gate is not None
        or loop_state.get("review_gate_enabled") is True
        or config.review_enabled
    )
    max_rounds = (
        review_gate.max_rounds if review_gate is not None else config.max_review_rounds
    )

    status_value = result.get("status")
    execution_disposition = parse_phase5_execution_disposition(
        status_value if isinstance(status_value, str) else None
    )
    return evaluate_phase5(
        Phase5Inputs(
            validation_succeeded=(
                validation_succeeded
                and execution_disposition is Phase5ExecutionDisposition.COMPLETED
            ),
            review_enabled=review_enabled,
            review_gate=review_gate,
            review_fail_closed=config.review_fail_closed,
            max_review_rounds=max_rounds,
            accepted_attempt_number=attempt_number,
        )
    )


def build_executor_run_outcome(
    phase_results: Mapping[str, Mapping[str, RuntimeValue]],
    workflow_terminal: str | None,
    phase5_decision: Phase5Decision | None,
    terminal_failure_anchor: PhaseId | None,
) -> RunOutcome:
    executed_phases = tuple(
        ExecutedPhase(
            phase_id=PhaseId(phase_id),
            disposition=parse_phase_disposition(
                status_value if isinstance(status_value, str) else "failure"
            ),
        )
        for phase_id, phase_result in phase_results.items()
        if phase_result.get("inherited") is not True
        for status_value in (phase_result.get("status"),)
    )
    return build_v3_run_outcome(
        V3RunFacts(
            executed_phases=executed_phases,
            workflow_terminal=WorkflowTerminal(
                workflow_terminal if workflow_terminal is not None else "terminated"
            ),
            phase5_decision=phase5_decision,
            terminal_failure_anchor=terminal_failure_anchor,
        )
    )


def phase5_decision_with_inherited_attempt(
    decision: Phase5Decision | None,
    reference: ParentAcceptedAttemptReference | None,
) -> Phase5Decision | None:
    if decision is not None or reference is None:
        return decision
    return Phase5Decision(
        validation_succeeded=True,
        review_outcome=reference.review_outcome,
        review_rounds=reference.review_rounds,
        review_fail_closed=reference.review_fail_closed,
        accepted_attempt_id=reference.attempt_id,
        parent_disposition=PhaseDisposition.SUCCEEDED,
    )
