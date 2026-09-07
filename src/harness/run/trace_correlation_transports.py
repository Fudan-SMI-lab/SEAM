from __future__ import annotations

from dataclasses import dataclass

from core.run_manifest import RunId
from core.run_outcome import PhaseId
from core.runtime_observability_models import ObservabilitySummary
from core.trace_correlation_models import (
    FrameworkInvocationId,
    PhaseExecutionId,
    RunCorrelationScope,
    SessionId,
    TransportAttemptId,
    make_phase_execution_id,
    make_transport_attempt_id,
)
from harness.session.events import TransportInvocationId
from harness.session.trace_correlation_models import (
    CorrelationDiagnostic,
    FrameworkInvocationCorrelation,
    TransportAttemptCorrelation,
)


@dataclass(frozen=True)
class TransportCorrelationRequest:
    scope: RunCorrelationScope
    phase_ids: frozenset[str]
    observability: ObservabilitySummary
    invocations: tuple[FrameworkInvocationCorrelation, ...]


@dataclass(frozen=True)
class TransportCorrelationResult:
    transports: tuple[TransportAttemptCorrelation, ...]
    invocations: tuple[FrameworkInvocationCorrelation, ...]
    diagnostics: tuple[CorrelationDiagnostic, ...]


def build_transport_correlations(
    request: TransportCorrelationRequest,
) -> TransportCorrelationResult:
    transports: list[TransportAttemptCorrelation] = []
    invocations = {item.invocation_id: item for item in request.invocations}
    diagnostics: list[CorrelationDiagnostic] = []
    records: set[str] = set()
    for details in request.observability.timeouts:
        record_id = details["record_id"]
        if record_id in records:
            diagnostics.append(_diagnostic("duplicate_record", record_id))
            continue
        records.add(record_id)
        run_id = RunId(details["run_id"])
        if run_id != request.scope.run_id:
            diagnostics.append(_diagnostic("cross_run_record", record_id))
        phase_id = PhaseId(details["sub_phase"])
        execution_id = make_phase_execution_id(run_id, phase_id)
        if details["phase_execution_id"] != execution_id:
            diagnostics.append(_diagnostic("contradictory_phase", record_id))
        if str(phase_id) not in request.phase_ids:
            diagnostics.append(_diagnostic("orphan_phase", record_id))
        transport_id = details["transport_invocation_id"]
        attempt_id = make_transport_attempt_id(transport_id, details["attempt"])
        if details["transport_attempt_id"] != attempt_id:
            diagnostics.append(_diagnostic("contradictory_attempt", record_id))
        framework_value = details["framework_invocation_id"]
        framework_id = (
            FrameworkInvocationId(framework_value)
            if framework_value is not None
            else None
        )
        session_id = SessionId(details["session_id"])
        if framework_id is None:
            diagnostics.append(_diagnostic("orphan_invocation", record_id))
        else:
            invocation = FrameworkInvocationCorrelation(
                run_id, execution_id, framework_id, session_id
            )
            existing = invocations.get(framework_id)
            if existing is None:
                invocations[framework_id] = invocation
            elif existing != invocation:
                diagnostics.append(
                    CorrelationDiagnostic(
                        "contradictory_invocation",
                        "framework_invocation",
                        str(framework_id),
                        "",
                    )
                )
        transports.append(
            TransportAttemptCorrelation(
                run_id=run_id,
                phase_execution_id=PhaseExecutionId(details["phase_execution_id"]),
                framework_invocation_id=framework_id,
                transport_invocation_id=TransportInvocationId(transport_id),
                transport_attempt_id=TransportAttemptId(
                    details["transport_attempt_id"]
                ),
                session_id=session_id,
                attempt=details["attempt"],
                event_phase=details["event_phase"],
                record_id=record_id,
            )
        )
    return TransportCorrelationResult(
        tuple(transports), tuple(invocations.values()), tuple(diagnostics)
    )


def _diagnostic(code: str, record_id: str) -> CorrelationDiagnostic:
    return CorrelationDiagnostic(code, "transport_attempt", record_id, "")


__all__ = (
    "TransportCorrelationRequest",
    "TransportCorrelationResult",
    "build_transport_correlations",
)
