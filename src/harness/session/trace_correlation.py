from __future__ import annotations

from dataclasses import dataclass
from typing import final

from harness.session.opencode_contract import JsonObject, ToolPart
from harness.session.opencode_trace_models import SessionGraphRetrieval
from harness.session.trace_correlation_models import (
    CorrelationDiagnostic,
    SessionCorrelation,
    ToolCallCorrelation,
    TraceCorrelationContext,
    TraceCorrelationProjection,
)
from harness.session.trace_correlation_payloads import session_correlation_value
from harness.session.trace_correlation_validation import validate_context_relations
from harness.session.trace_seeds import TraceSeed
from core.trace_correlation_models import SessionId, ToolCallId

_GRAPH_DIAGNOSTICS = frozenset(
    {
        "malformed_seed_id",
        "trace_session_limit_exceeded",
        "malformed_child_id",
        "session_id_limit_exceeded",
        "child_parent_mismatch",
        "trace_edge_limit_exceeded",
        "duplicate_child_id",
        "cycle_detected",
        "trace_depth_limit_exceeded",
        "session_retrieval_error",
    }
)


@dataclass(frozen=True)
class SessionCorrelationInput:
    session_id: str
    path: tuple[str, ...]
    seeds: tuple[TraceSeed, ...]
    retrieval: SessionGraphRetrieval | None


@final
class TraceCorrelationProjector:
    def __init__(self, context: TraceCorrelationContext) -> None:
        self._context = context
        self._sessions: dict[SessionId, SessionCorrelation] = {}
        self._tools: dict[ToolCallId, ToolCallCorrelation] = {}
        self._root_metadata: dict[SessionId, tuple[str | None, str | None]] = {}
        self._diagnostics: list[CorrelationDiagnostic] = list(context.diagnostics)
        self._diagnostic_keys: set[tuple[str, str, str]] = {
            (item.code, item.record_kind, item.record_id)
            for item in context.diagnostics
        }

    def record_session(self, source: SessionCorrelationInput) -> JsonObject:
        session_id = SessionId(source.session_id)
        root_id = SessionId(source.path[0])
        parent_id = SessionId(source.path[-2]) if len(source.path) > 1 else None
        logical_role, scope = self._metadata(root_id, source.seeds)
        record = SessionCorrelation(
            run_id=self._context.scope.run_id,
            root_session_id=root_id,
            session_id=session_id,
            parent_session_id=parent_id,
            logical_role=logical_role,
            scope=scope,
        )
        if session_id in self._sessions:
            self._add("duplicate_record", "session", source.session_id)
        else:
            self._sessions[session_id] = record
        session_tools: list[ToolCallCorrelation] = []
        retrieval = source.retrieval
        if retrieval is not None:
            session_tools.extend(self._record_tools(record, retrieval))
        else:
            self._add("session_retrieval_error", "session", source.session_id)
        return session_correlation_value(record, tuple(session_tools))

    def finish(
        self, export_errors: tuple[JsonObject, ...]
    ) -> TraceCorrelationProjection:
        for error in export_errors:
            code = error.get("code")
            if not isinstance(code, str) or code not in _GRAPH_DIAGNOSTICS:
                continue
            session_id = error.get("session_id")
            self._add(
                code,
                "session_edge",
                session_id if isinstance(session_id, str) else "run",
            )
        self._validate_sessions()
        for diagnostic in validate_context_relations(self._context):
            self._add(
                diagnostic.code,
                diagnostic.record_kind,
                diagnostic.record_id,
            )
        self._validate_runtime_links()
        self._validate_tools()
        return TraceCorrelationProjection(
            context=self._context,
            sessions=tuple(self._sessions.values()),
            tool_calls=tuple(self._tools.values()),
            diagnostics=tuple(self._diagnostics),
        )

    def _metadata(
        self,
        root_id: SessionId,
        seeds: tuple[TraceSeed, ...],
    ) -> tuple[str | None, str | None]:
        if seeds:
            first = seeds[0]
            metadata = (first.logical_role, first.scope)
            if len(seeds) > 1:
                self._add("duplicate_record", "trace_seed", str(root_id))
                if any(
                    (item.logical_role, item.scope) != metadata for item in seeds[1:]
                ):
                    self._add("contradictory_seed", "trace_seed", str(root_id))
            self._root_metadata[root_id] = metadata
            return metadata
        return self._root_metadata.get(root_id, (None, None))

    def _record_tools(
        self,
        session: SessionCorrelation,
        retrieval: SessionGraphRetrieval,
    ) -> list[ToolCallCorrelation]:
        recorded: list[ToolCallCorrelation] = []
        for message in retrieval.contract.messages.messages:
            message_id = message.info.get("id")
            if not isinstance(message_id, str):
                continue
            for part in message.parts:
                if not isinstance(part, ToolPart):
                    continue
                raw_call_id = part.raw.get("callID")
                part_id = part.raw.get("id")
                if not isinstance(raw_call_id, str) or not isinstance(part_id, str):
                    self._add(
                        "malformed_tool_call",
                        "tool_call",
                        str(session.session_id),
                    )
                    continue
                call_id = ToolCallId(raw_call_id)
                child_id = (
                    SessionId(part.lineage.child_session_id)
                    if part.lineage is not None
                    else None
                )
                tool = ToolCallCorrelation(
                    run_id=session.run_id,
                    root_session_id=session.root_session_id,
                    session_id=session.session_id,
                    message_id=message_id,
                    part_id=part_id,
                    call_id=call_id,
                    tool=part.tool,
                    child_session_id=child_id,
                )
                if call_id in self._tools:
                    self._add("duplicate_record", "tool_call", raw_call_id)
                    continue
                self._tools[call_id] = tool
                recorded.append(tool)
        return recorded

    def _validate_sessions(self) -> None:
        for session in self._sessions.values():
            parent_id = session.parent_session_id
            if parent_id is not None and parent_id not in self._sessions:
                self._add("orphan_session", "session", str(session.session_id))
            if session.root_session_id not in self._sessions:
                self._add("orphan_session", "session", str(session.session_id))

    def _validate_runtime_links(self) -> None:
        session_ids = set(self._sessions)
        records = (
            *self._context.review_rounds,
            *self._context.framework_invocations,
            *self._context.transport_attempts,
        )
        for record in records:
            if record.session_id not in session_ids:
                self._add(
                    "orphan_session",
                    type(record).__name__,
                    str(record.session_id),
                )

    def _validate_tools(self) -> None:
        for tool in self._tools.values():
            child_id = tool.child_session_id
            if child_id is None:
                continue
            child = self._sessions.get(child_id)
            if child is None:
                self._add("orphan_session", "tool_call", str(tool.call_id))
            elif child.parent_session_id != tool.session_id:
                self._add("contradictory_parent", "tool_call", str(tool.call_id))

    def _add(self, code: str, kind: str, record_id: str) -> None:
        key = (code, kind, record_id)
        if key in self._diagnostic_keys:
            return
        self._diagnostic_keys.add(key)
        self._diagnostics.append(CorrelationDiagnostic(code, kind, record_id, ""))


__all__ = ("SessionCorrelationInput", "TraceCorrelationProjector")
