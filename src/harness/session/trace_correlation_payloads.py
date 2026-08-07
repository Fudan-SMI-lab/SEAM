from __future__ import annotations

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.trace_correlation_models import (
    CorrelationDiagnostic,
    SessionCorrelation,
    ToolCallCorrelation,
    TraceCorrelationProjection,
)


def session_correlation_value(
    session: SessionCorrelation,
    tools: tuple[ToolCallCorrelation, ...],
) -> JsonObject:
    tool_ids: list[JsonValue] = [str(item.call_id) for item in tools]
    return {
        "run_id": str(session.run_id),
        "root_session_id": str(session.root_session_id),
        "session_id": str(session.session_id),
        "parent_session_id": (
            str(session.parent_session_id)
            if session.parent_session_id is not None
            else None
        ),
        "logical_role": session.logical_role,
        "scope": session.scope,
        "tool_call_ids": tool_ids,
    }


def projection_value(projection: TraceCorrelationProjection) -> JsonObject:
    context = projection.context
    parent_trace = context.scope.parent_trace
    parent_value: JsonValue = None
    if parent_trace is not None:
        parent_value = {
            "run_id": str(parent_trace.run_id),
            "manifest_path": str(parent_trace.manifest_path),
            "sha256": str(parent_trace.sha256),
            "size_bytes": parent_trace.size_bytes,
        }
    run_scope: JsonObject = {
        "run_id": str(context.scope.run_id),
        "parent_run_id": (
            str(context.scope.parent_run_id)
            if context.scope.parent_run_id is not None
            else None
        ),
        "lineage_root_run_id": str(context.scope.lineage_root_run_id),
        "parent_trace": parent_value,
    }
    phase_executions: list[JsonValue] = [
        {
            "run_id": str(item.run_id),
            "phase_id": str(item.phase_id),
            "phase_execution_id": str(item.phase_execution_id),
        }
        for item in context.phase_executions
    ]
    attempts: list[JsonValue] = [
        {
            "run_id": str(item.run_id),
            "phase_execution_id": str(item.phase_execution_id),
            "attempt_id": item.attempt_id,
            "attempt_number": item.attempt_number,
            "accepted": item.accepted,
        }
        for item in context.phase5_attempts
    ]
    reviews: list[JsonValue] = [
        {
            "record_id": item.record_id,
            "run_id": str(item.run_id),
            "phase_execution_id": str(item.phase_execution_id),
            "phase5_iteration": item.phase5_iteration,
            "review_round_id": str(item.review_round_id),
            "logical_round": item.review_round.round_number,
            "max_rounds": item.review_round.max_rounds,
            "verdict": item.review_round.verdict.value,
            "outcome": item.review_round.outcome.value,
            "framework_invocation_id": str(item.framework_invocation_id),
            "session_id": str(item.session_id),
        }
        for item in context.review_rounds
    ]
    invocations: list[JsonValue] = [
        {
            "run_id": str(item.run_id),
            "phase_execution_id": str(item.phase_execution_id),
            "invocation_id": str(item.invocation_id),
            "session_id": str(item.session_id),
        }
        for item in context.framework_invocations
    ]
    transports: list[JsonValue] = [
        {
            "record_id": item.record_id,
            "run_id": str(item.run_id),
            "phase_execution_id": str(item.phase_execution_id),
            "framework_invocation_id": (
                str(item.framework_invocation_id)
                if item.framework_invocation_id is not None
                else None
            ),
            "transport_invocation_id": str(item.transport_invocation_id),
            "transport_attempt_id": str(item.transport_attempt_id),
            "session_id": str(item.session_id),
            "attempt": item.attempt,
            "event_phase": item.event_phase,
        }
        for item in context.transport_attempts
    ]
    sessions: list[JsonValue] = [
        session_correlation_value(
            item,
            tuple(
                tool
                for tool in projection.tool_calls
                if tool.session_id == item.session_id
            ),
        )
        for item in projection.sessions
    ]
    tools: list[JsonValue] = [
        {
            "run_id": str(item.run_id),
            "root_session_id": str(item.root_session_id),
            "session_id": str(item.session_id),
            "message_id": item.message_id,
            "part_id": item.part_id,
            "call_id": str(item.call_id),
            "tool": item.tool,
            "child_session_id": (
                str(item.child_session_id)
                if item.child_session_id is not None
                else None
            ),
        }
        for item in projection.tool_calls
    ]
    diagnostics: list[JsonValue] = [
        diagnostic_value(item) for item in projection.diagnostics
    ]
    return {
        "schema": "seam.trace-correlation",
        "schema_version": 1,
        "complete": projection.complete,
        "run_scope": run_scope,
        "phase_executions": phase_executions,
        "phase5_attempts": attempts,
        "review_rounds": reviews,
        "framework_invocations": invocations,
        "transport_attempts": transports,
        "sessions": sessions,
        "tool_calls": tools,
        "diagnostics": diagnostics,
    }


def diagnostic_value(diagnostic: CorrelationDiagnostic) -> JsonObject:
    return {
        "code": diagnostic.code,
        "record_kind": diagnostic.record_kind,
        "record_id": diagnostic.record_id,
        "detail": diagnostic.detail,
    }


__all__ = ("projection_value", "session_correlation_value")
