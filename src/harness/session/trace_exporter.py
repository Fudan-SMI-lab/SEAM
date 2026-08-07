from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.trace_export_index import TraceExportIndex
from harness.session.trace_export_models import (
    SessionPayloadInput,
    TraceExportError,
    TraceExportRequest,
    TraceExportResult,
    TraceGraphClient,
)
from harness.session.trace_export_payloads import (
    child_evidence_source,
    selected_children,
    session_payload,
    session_reasons,
)
from harness.session.trace_export_session_info import resolve_session_info
from harness.session.trace_export_traversal import (
    MAX_SESSION_ID_CHARS,
    MAX_TRACE_SESSIONS,
    GraphTraversal,
    QueuedSession,
)
from harness.session.trace_export_transaction import TraceExportTransaction
from harness.session.trace_seeds import TraceSeed
from harness.session.trace_correlation import (
    SessionCorrelationInput,
    TraceCorrelationProjector,
)

MAX_TRACE_SEEDS: Final = 10_000


@dataclass(frozen=True)
class _ExportCaptureState:
    seeds: dict[str, tuple[TraceSeed, ...]]
    traversal: GraphTraversal
    correlation: TraceCorrelationProjector | None


__all__ = [
    "TraceExportError",
    "TraceExportRequest",
    "TraceExportResult",
    "TraceExporter",
]


@final
class TraceExporter:
    """Capture accessible OpenCode V1 session graphs without interpreting outcomes."""

    def __init__(self, client: TraceGraphClient) -> None:
        self._client = client

    def export(self, request: TraceExportRequest) -> TraceExportResult:
        if request.max_overflow_bytes < 0:
            raise TraceExportError(
                request.destination, "max_overflow_bytes must be nonnegative"
            )
        with TraceExportTransaction(request) as transaction:
            index = TraceExportIndex(transaction.request)
            seeds = self._seed_map(request.seeds, index)
            traversal = GraphTraversal(seeds, index)
            projector = (
                TraceCorrelationProjector(request.correlation)
                if request.correlation is not None
                else None
            )
            state = _ExportCaptureState(seeds, traversal, projector)
            while traversal.queue:
                queued = traversal.queue.popleft()
                if queued.session_id in traversal.visited:
                    continue
                traversal.visited.add(queued.session_id)
                self._capture_session(queued, state)
            projection = (
                projector.finish(tuple(index.errors)) if projector is not None else None
            )
            _ = index.write_manifest(seeds, projection)
            transaction.commit()
            error_codes = tuple(str(item["code"]) for item in index.errors)
            return TraceExportResult(
                manifest_path=request.destination / "manifest.json",
                complete=not index.errors
                and all(record.get("complete") is True for record in index.sessions)
                and (projection is None or projection.complete),
                session_count=len(index.sessions),
                errors=error_codes,
                correlation_complete=(
                    projection.complete if projection is not None else None
                ),
                correlation_errors=(
                    tuple(item.code for item in projection.diagnostics)
                    if projection is not None
                    else ()
                ),
            )

    def _capture_session(
        self,
        queued: QueuedSession,
        state: _ExportCaptureState,
    ) -> None:
        traversal = state.traversal
        seeds = state.seeds.get(queued.session_id, ())
        index = traversal.index
        try:
            retrieval = self._client.retrieve_session_graph(queued.session_id)
        except (OSError, RuntimeError, UnicodeError) as exc:
            if state.correlation is not None:
                _ = state.correlation.record_session(
                    SessionCorrelationInput(
                        queued.session_id,
                        queued.path,
                        seeds,
                        None,
                    )
                )
            index.add_error("session_retrieval_error", queued.session_id, str(exc))
            failure_reasons: list[JsonValue] = ["session_retrieval_error"]
            failure_errors: list[JsonValue] = [str(exc)]
            failure_record: JsonObject = {
                "session_id": queued.session_id,
                "artifact": None,
                "complete": False,
                "reasons": failure_reasons,
                "errors": failure_errors,
            }
            index.sessions.append(failure_record)
            return
        contract = retrieval.contract
        index.versions.add(contract.server_version)
        index.message_count += len(contract.messages.messages)
        index.part_count += sum(
            len(message.parts) for message in contract.messages.messages
        )
        index.capabilities[queued.session_id] = {
            "state": retrieval.state.value,
            "document": contract.features.document.value,
            "health": contract.features.health.value,
            "messages": contract.features.messages.value,
            "children": contract.features.children.value,
            "direct_vs_fallback": child_evidence_source(retrieval),
        }
        resolved_info = resolve_session_info(
            self._client,
            queued.session_id,
            queued.session_info,
        )
        for reason in resolved_info.reasons:
            index.add_error(reason, queued.session_id, reason)
        children = selected_children(retrieval)
        for child in children:
            traversal.enqueue_child(queued, child.raw)
        overflow_failures = index.capture_overflows(
            queued.session_id, contract.overflow_paths
        )
        capture_failures = (
            *overflow_failures,
            *index.error_codes_for(queued.session_id),
        )
        reasons = session_reasons(retrieval, seeds, capture_failures)
        session_errors = tuple(
            dict.fromkeys(
                (*retrieval.errors, *index.error_codes_for(queued.session_id))
            )
        )
        correlation = (
            state.correlation.record_session(
                SessionCorrelationInput(
                    queued.session_id,
                    queued.path,
                    seeds,
                    retrieval,
                )
            )
            if state.correlation is not None
            else None
        )
        payload = session_payload(
            SessionPayloadInput(
                session_id=queued.session_id,
                session_info=resolved_info.value,
                session_info_capture=resolved_info.capture,
                seeds=seeds,
                retrieval=retrieval,
                reasons=tuple(reasons),
                errors=session_errors,
                correlation=correlation,
            )
        )
        artifact = index.write_session_payload(queued.session_id, payload, reasons)
        reason_values: list[JsonValue] = list(reasons)
        current_errors: list[JsonValue] = list(
            dict.fromkeys((*session_errors, *index.error_codes_for(queued.session_id)))
        )
        record: JsonObject = {
            "session_id": queued.session_id,
            "artifact": artifact,
            "complete": not reasons and artifact is not None,
            "reasons": reason_values,
            "errors": current_errors,
        }
        index.sessions.append(record)

    @staticmethod
    def _seed_map(
        seeds: tuple[TraceSeed, ...],
        index: TraceExportIndex,
    ) -> dict[str, tuple[TraceSeed, ...]]:
        grouped: dict[str, list[TraceSeed]] = {}
        for position, seed in enumerate(seeds):
            if position >= MAX_TRACE_SEEDS:
                index.add_error("trace_seed_limit_exceeded", None, str(position))
                break
            if not seed.session_id or len(seed.session_id) > MAX_SESSION_ID_CHARS:
                index.add_error("malformed_seed_id", None, "empty session ID")
                continue
            if seed.session_id not in grouped and len(grouped) >= MAX_TRACE_SESSIONS:
                index.add_error(
                    "trace_session_limit_exceeded", None, seed.session_id[:128]
                )
                continue
            values = grouped.setdefault(seed.session_id, [])
            if values:
                index.add_error("duplicate_seed", seed.session_id, str(len(values) + 1))
            values.append(seed)
        return {session_id: tuple(values) for session_id, values in grouped.items()}
