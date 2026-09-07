from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from core.run_manifest import RunId, Sha256Digest
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    ReviewRound,
    ReviewVerdict,
)
from core.runtime_observability_models import (
    ObservabilitySummary,
    ReviewDetails,
    TimeoutDetails,
)
from core.trace_correlation_models import (
    FrameworkInvocationId,
    ParentTraceReference,
    RunCorrelationScope,
    make_phase_execution_id,
    make_review_round_id,
    make_transport_attempt_id,
)
from harness.run.trace_correlation import (
    RuntimeTraceCorrelationRequest,
    build_runtime_trace_correlation,
)
from harness.session.opencode_contract import ChildSession, ChildrenResult
from harness.session.trace_correlation_models import TraceCorrelationContext
from tests.trace_export_test_support import GraphFixture, child_info


def correlation_context(
    *,
    run_id: str = "child-run-001",
    review_run_id: str | None = None,
    review_session_id: str = "ses_root",
    duplicate_review: bool = False,
    parent_trace: ParentTraceReference | None = None,
) -> TraceCorrelationContext:
    record_run_id = review_run_id or run_id
    phase_id = PhaseId("phase_5_validation")
    phase_execution_id = make_phase_execution_id(RunId(record_run_id), phase_id)
    first_round_id = make_review_round_id(
        RunId(record_run_id), phase_id, round_number=1
    )
    first_review = ReviewDetails(
        record_id=str(first_round_id),
        run_id=record_run_id,
        phase_execution_id=str(phase_execution_id),
        review_round_id=str(first_round_id),
        framework_invocation_id="framework-review-000001",
        phase_id=str(phase_id),
        phase5_iteration=2,
        logical_round=1,
        max_rounds=3,
        remaining_rounds=2,
        verdict="reject",
        outcome="rejected",
        duration_seconds=3.25,
        improvement_status="applied",
        session_id=review_session_id,
        command_id="framework-review-000001",
        reviewer_agent="reviewer",
        sub_phase="review_result",
    )
    second_round_id = make_review_round_id(
        RunId(record_run_id), phase_id, round_number=2
    )
    second_review = ReviewDetails(
        record_id=str(second_round_id),
        run_id=record_run_id,
        phase_execution_id=str(phase_execution_id),
        review_round_id=str(second_round_id),
        framework_invocation_id="framework-review-000002",
        phase_id=str(phase_id),
        phase5_iteration=3,
        logical_round=2,
        max_rounds=3,
        remaining_rounds=1,
        verdict="accept",
        outcome="accepted",
        duration_seconds=4.25,
        improvement_status="not_required",
        session_id=review_session_id,
        command_id="framework-review-000002",
        reviewer_agent="reviewer",
        sub_phase="review_result",
    )
    transport_attempt_id = make_transport_attempt_id("transport-000009", 2)
    timeout = TimeoutDetails(
        record_id=f"{record_run_id}:transport:transport-000009:2:timeout",
        run_id=record_run_id,
        phase_execution_id=str(phase_execution_id),
        framework_invocation_id="framework-review-000002",
        transport_invocation_id="transport-000009",
        transport_attempt_id=str(transport_attempt_id),
        event_phase="timeout",
        agent="reviewer",
        sub_phase="phase_5_validation",
        session_id=review_session_id,
        command_id="transport-000009",
        attempt=2,
        max_attempts=3,
        configured_timeout_seconds=30.0,
        elapsed_seconds=30.0,
        retry_decision="retry_same_session",
        reason="request_timeout",
        exhausted=False,
    )
    reviews = (
        (first_review, second_review, second_review)
        if duplicate_review
        else (first_review, second_review)
    )
    return build_runtime_trace_correlation(
        RuntimeTraceCorrelationRequest(
            scope=RunCorrelationScope(
                run_id=RunId(run_id),
                parent_run_id=RunId("parent-run-001"),
                lineage_root_run_id=RunId("parent-run-001"),
                parent_trace=parent_trace,
            ),
            executed_phases=(phase_id,),
            accepted_attempt_id=AcceptedAttemptId("phase_5_validation-attempt-2"),
            review_rounds=(
                ReviewRound(
                    round_number=1,
                    max_rounds=3,
                    verdict=ReviewVerdict.REJECT,
                    outcome=ReviewOutcome.REJECTED,
                ),
                ReviewRound(
                    round_number=2,
                    max_rounds=3,
                    verdict=ReviewVerdict.ACCEPT,
                    outcome=ReviewOutcome.ACCEPTED,
                ),
            ),
            observability=ObservabilitySummary(reviews=reviews, timeouts=(timeout,)),
        )
    )


def parent_trace_reference(
    path: Path, run_id: str = "parent-run-001"
) -> ParentTraceReference:
    content = path.read_bytes()
    return ParentTraceReference(
        run_id=RunId(run_id),
        manifest_path=path,
        sha256=Sha256Digest(hashlib.sha256(content).hexdigest()),
        size_bytes=len(content),
    )


def with_contradictory_child(fixture: GraphFixture) -> GraphFixture:
    raw = child_info("ses_wrong", "foreign-parent")
    child = ChildSession("ses_wrong", "foreign-parent", raw)
    direct = fixture.retrieval.contract.children
    assert isinstance(direct.raw, list)
    children = ChildrenResult(
        (*direct.sessions, child),
        direct.capability,
        direct.completeness,
        [*direct.raw, raw],
    )
    contract = replace(fixture.retrieval.contract, children=children)
    return replace(fixture, retrieval=replace(fixture.retrieval, contract=contract))


def framework_invocation(value: str) -> FrameworkInvocationId:
    return FrameworkInvocationId(value)
