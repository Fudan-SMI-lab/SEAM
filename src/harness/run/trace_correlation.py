from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.run_manifest import EvidenceDigest, RunId
from core.run_outcome import AcceptedAttemptId, PhaseId, ReviewRound, RunOutcome
from core.runtime_observability_models import ObservabilitySummary
from core.terminal_continuation_models import PreparedTerminalContinuation
from core.trace_correlation_models import ParentTraceReference, RunCorrelationScope
from core.trace_correlation_models import make_phase_execution_id
from harness.session.trace_correlation_models import (
    CorrelationDiagnostic,
    Phase5AttemptCorrelation,
    PhaseExecutionCorrelation,
    TraceCorrelationContext,
)
from harness.run.trace_correlation_reviews import (
    ReviewCorrelationRequest,
    build_review_correlations,
)
from harness.run.trace_correlation_transports import (
    TransportCorrelationRequest,
    build_transport_correlations,
)

ObservabilitySource = Callable[[], ObservabilitySummary]
_PHASE5_ATTEMPT = re.compile(r"phase_5_validation-attempt-([1-9][0-9]*)\Z")


@dataclass(frozen=True)
class RuntimeTraceCorrelationRequest:
    scope: RunCorrelationScope
    executed_phases: tuple[PhaseId, ...]
    accepted_attempt_id: AcceptedAttemptId | None
    review_rounds: tuple[ReviewRound, ...]
    observability: ObservabilitySummary


@dataclass(frozen=True)
class V3TraceCorrelationInputs:
    run_id: RunId
    outcome: RunOutcome
    observability_source: ObservabilitySource
    continuation: PreparedTerminalContinuation | None


def _parent_trace_reference(
    continuation: PreparedTerminalContinuation,
) -> ParentTraceReference | None:
    parent_run_id = continuation.parent.run_id
    expected = "trace/manifest.json"
    matches = tuple(
        item
        for item in continuation.evidence.parent_report_inventory
        if item.relative_path == expected
    )
    if len(matches) != 1:
        return None
    evidence: EvidenceDigest = matches[0]
    return ParentTraceReference(
        run_id=parent_run_id,
        manifest_path=continuation.evidence.context.authoritative_root
        / str(parent_run_id)
        / Path(evidence.relative_path),
        sha256=evidence.digest,
        size_bytes=evidence.size_bytes,
    )


def build_v3_trace_correlation(
    inputs: V3TraceCorrelationInputs,
) -> TraceCorrelationContext:
    continuation = inputs.continuation
    scope = (
        RunCorrelationScope(
            run_id=inputs.run_id,
            parent_run_id=None,
            lineage_root_run_id=inputs.run_id,
            parent_trace=None,
        )
        if continuation is None
        else RunCorrelationScope(
            run_id=inputs.run_id,
            parent_run_id=continuation.parent.run_id,
            lineage_root_run_id=continuation.parent.run_manifest.lineage_root_run_id,
            parent_trace=_parent_trace_reference(continuation),
        )
    )
    outcome = inputs.outcome
    return build_runtime_trace_correlation(
        RuntimeTraceCorrelationRequest(
            scope=scope,
            executed_phases=outcome.executed_phases,
            accepted_attempt_id=outcome.accepted_attempt_id,
            review_rounds=outcome.review_rounds,
            observability=inputs.observability_source(),
        )
    )


def build_runtime_trace_correlation(
    request: RuntimeTraceCorrelationRequest,
) -> TraceCorrelationContext:
    diagnostics: list[CorrelationDiagnostic] = []
    phases: list[PhaseExecutionCorrelation] = []
    phase_ids: set[str] = set()
    for phase_id in request.executed_phases:
        phase_text = str(phase_id)
        if phase_text in phase_ids:
            diagnostics.append(
                CorrelationDiagnostic(
                    "duplicate_record", "phase_execution", phase_text, ""
                )
            )
            continue
        phase_ids.add(phase_text)
        phases.append(
            PhaseExecutionCorrelation(
                request.scope.run_id,
                phase_id,
                make_phase_execution_id(request.scope.run_id, phase_id),
            )
        )
    observed_phases = tuple(
        PhaseId(details["phase_id"])
        for details in request.observability.reviews
        if details["run_id"] == request.scope.run_id
    ) + tuple(
        PhaseId(details["sub_phase"])
        for details in request.observability.timeouts
        if details["run_id"] == request.scope.run_id
    )
    for phase_id in observed_phases:
        phase_text = str(phase_id)
        if phase_text in phase_ids:
            continue
        phase_ids.add(phase_text)
        phases.append(
            PhaseExecutionCorrelation(
                request.scope.run_id,
                phase_id,
                make_phase_execution_id(request.scope.run_id, phase_id),
            )
        )
    attempts = _attempts(request, phase_ids, diagnostics)
    review_result = build_review_correlations(
        ReviewCorrelationRequest(
            request.scope,
            frozenset(phase_ids),
            request.review_rounds,
            request.observability,
        )
    )
    diagnostics.extend(review_result.diagnostics)
    transport_result = build_transport_correlations(
        TransportCorrelationRequest(
            request.scope,
            frozenset(phase_ids),
            request.observability,
            review_result.invocations,
        )
    )
    diagnostics.extend(transport_result.diagnostics)
    if request.scope.parent_run_id is not None and request.scope.parent_trace is None:
        diagnostics.append(
            CorrelationDiagnostic(
                "parent_trace_missing",
                "run_scope",
                str(request.scope.run_id),
                "continuation has no immutable parent trace reference",
            )
        )
    return TraceCorrelationContext(
        scope=request.scope,
        phase_executions=tuple(phases),
        phase5_attempts=tuple(attempts),
        review_rounds=review_result.reviews,
        framework_invocations=transport_result.invocations,
        transport_attempts=transport_result.transports,
        diagnostics=tuple(diagnostics),
    )


def _attempts(
    request: RuntimeTraceCorrelationRequest,
    phase_ids: set[str],
    diagnostics: list[CorrelationDiagnostic],
) -> list[Phase5AttemptCorrelation]:
    accepted = request.accepted_attempt_id
    if accepted is None:
        return []
    match = _PHASE5_ATTEMPT.fullmatch(str(accepted))
    if match is None:
        diagnostics.append(
            CorrelationDiagnostic(
                "malformed_attempt", "phase5_attempt", str(accepted), ""
            )
        )
        return []
    phase_id = PhaseId("phase_5_validation")
    if str(phase_id) not in phase_ids:
        diagnostics.append(
            CorrelationDiagnostic("orphan_phase", "phase5_attempt", str(accepted), "")
        )
    return [
        Phase5AttemptCorrelation(
            run_id=request.scope.run_id,
            phase_execution_id=make_phase_execution_id(request.scope.run_id, phase_id),
            attempt_id=str(accepted),
            attempt_number=int(match.group(1)),
            accepted=True,
        )
    ]


__all__ = (
    "RuntimeTraceCorrelationRequest",
    "V3TraceCorrelationInputs",
    "build_runtime_trace_correlation",
    "build_v3_trace_correlation",
)
