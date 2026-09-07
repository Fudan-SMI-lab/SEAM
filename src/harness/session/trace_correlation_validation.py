from __future__ import annotations

from typing import final

from core.run_outcome import PhaseId
from core.trace_correlation_models import (
    FrameworkInvocationId,
    PhaseExecutionId,
    make_phase_execution_id,
    make_review_round_id,
    make_transport_attempt_id,
)
from harness.session.trace_correlation_models import (
    CorrelationDiagnostic,
    FrameworkInvocationCorrelation,
    PhaseExecutionCorrelation,
    TraceCorrelationContext,
)


@final
class _RelationValidator:
    def __init__(self, context: TraceCorrelationContext) -> None:
        self._context = context
        self._diagnostics: list[CorrelationDiagnostic] = []
        self._keys: set[tuple[str, str, str]] = set()
        self._phases: dict[PhaseExecutionId, PhaseExecutionCorrelation] = {}
        self._framework: dict[
            FrameworkInvocationId, FrameworkInvocationCorrelation
        ] = {}

    def validate(self) -> tuple[CorrelationDiagnostic, ...]:
        self._validate_phases()
        self._validate_attempts()
        self._validate_framework()
        self._validate_reviews()
        self._validate_transports()
        return tuple(self._diagnostics)

    def _validate_phases(self) -> None:
        run_id = self._context.scope.run_id
        for phase in self._context.phase_executions:
            record_id = str(phase.phase_execution_id)
            if phase.run_id != run_id:
                self._add("cross_run_record", "phase_execution", record_id)
            if phase.phase_execution_id != make_phase_execution_id(
                phase.run_id, phase.phase_id
            ):
                self._add("contradictory_phase", "phase_execution", record_id)
            if phase.phase_execution_id in self._phases:
                self._add("duplicate_record", "phase_execution", record_id)
            else:
                self._phases[phase.phase_execution_id] = phase

    def _validate_attempts(self) -> None:
        run_id = self._context.scope.run_id
        seen: set[str] = set()
        phase5_id = make_phase_execution_id(run_id, PhaseId("phase_5_validation"))
        for attempt in self._context.phase5_attempts:
            if attempt.run_id != run_id:
                self._add("cross_run_record", "phase5_attempt", attempt.attempt_id)
            if attempt.phase_execution_id != phase5_id:
                self._add("contradictory_phase", "phase5_attempt", attempt.attempt_id)
            if attempt.phase_execution_id not in self._phases:
                self._add("orphan_phase", "phase5_attempt", attempt.attempt_id)
            expected = f"phase_5_validation-attempt-{attempt.attempt_number}"
            if attempt.attempt_number < 1 or attempt.attempt_id != expected:
                self._add("contradictory_attempt", "phase5_attempt", attempt.attempt_id)
            if attempt.attempt_id in seen:
                self._add("duplicate_record", "phase5_attempt", attempt.attempt_id)
            seen.add(attempt.attempt_id)

    def _validate_framework(self) -> None:
        run_id = self._context.scope.run_id
        for invocation in self._context.framework_invocations:
            record_id = str(invocation.invocation_id)
            if invocation.run_id != run_id:
                self._add("cross_run_record", "framework_invocation", record_id)
            if invocation.phase_execution_id not in self._phases:
                self._add("orphan_phase", "framework_invocation", record_id)
            if invocation.invocation_id in self._framework:
                self._add("duplicate_record", "framework_invocation", record_id)
            else:
                self._framework[invocation.invocation_id] = invocation

    def _validate_reviews(self) -> None:
        run_id = self._context.scope.run_id
        seen: set[str] = set()
        for review in self._context.review_rounds:
            if review.run_id != run_id:
                self._add("cross_run_record", "review_round", review.record_id)
            phase = self._phases.get(review.phase_execution_id)
            if phase is None:
                self._add("orphan_phase", "review_round", review.record_id)
            else:
                expected = make_review_round_id(
                    review.run_id,
                    phase.phase_id,
                    review.review_round.round_number,
                )
                if review.review_round_id != expected:
                    self._add("contradictory_phase", "review_round", review.record_id)
            invocation = self._framework.get(review.framework_invocation_id)
            if invocation is None:
                self._add("orphan_invocation", "review_round", review.record_id)
            elif (
                invocation.run_id != review.run_id
                or invocation.phase_execution_id != review.phase_execution_id
                or invocation.session_id != review.session_id
            ):
                self._add("contradictory_invocation", "review_round", review.record_id)
            if review.record_id in seen:
                self._add("duplicate_record", "review_round", review.record_id)
            seen.add(review.record_id)

    def _validate_transports(self) -> None:
        run_id = self._context.scope.run_id
        seen: set[str] = set()
        for transport in self._context.transport_attempts:
            if transport.run_id != run_id:
                self._add("cross_run_record", "transport_attempt", transport.record_id)
            if transport.phase_execution_id not in self._phases:
                self._add("orphan_phase", "transport_attempt", transport.record_id)
            expected = make_transport_attempt_id(
                str(transport.transport_invocation_id), transport.attempt
            )
            if transport.transport_attempt_id != expected:
                self._add(
                    "contradictory_attempt", "transport_attempt", transport.record_id
                )
            framework_id = transport.framework_invocation_id
            if framework_id is not None:
                invocation = self._framework.get(framework_id)
                if invocation is None:
                    self._add(
                        "orphan_invocation", "transport_attempt", transport.record_id
                    )
                elif (
                    invocation.run_id != transport.run_id
                    or invocation.phase_execution_id != transport.phase_execution_id
                    or invocation.session_id != transport.session_id
                ):
                    self._add(
                        "contradictory_invocation",
                        "transport_attempt",
                        transport.record_id,
                    )
            if transport.record_id in seen:
                self._add("duplicate_record", "transport_attempt", transport.record_id)
            seen.add(transport.record_id)

    def _add(self, code: str, kind: str, record_id: str) -> None:
        key = (code, kind, record_id)
        if key in self._keys:
            return
        self._keys.add(key)
        self._diagnostics.append(CorrelationDiagnostic(code, kind, record_id, ""))


def validate_context_relations(
    context: TraceCorrelationContext,
) -> tuple[CorrelationDiagnostic, ...]:
    return _RelationValidator(context).validate()


__all__ = ("validate_context_relations",)
