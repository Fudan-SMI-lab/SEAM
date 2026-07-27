from __future__ import annotations

from harness.session.opencode_contract import (
    CapabilityState,
    ChildSession,
    CompletedToolState,
    Completeness,
    Compatibility,
    JsonObject,
    JsonValue,
    ToolPart,
    UnknownPart,
    UnknownToolState,
)
from harness.session.opencode_trace_models import (
    EndpointCapture,
    SessionGraphRetrieval,
)
from harness.session.trace_export_models import SessionPayloadInput
from harness.session.trace_seeds import TraceSeed, TraceSeedMetadataState


def seed_value(seed: TraceSeed) -> JsonObject:
    return {
        "session_id": seed.session_id,
        "logical_role": seed.logical_role,
        "scope": seed.scope,
        "lifecycle": seed.lifecycle,
        "agent": seed.agent,
        "working_directory": seed.working_directory,
        "metadata_state": seed.metadata_state.value,
    }


def session_reasons(
    retrieval: SessionGraphRetrieval,
    seeds: tuple[TraceSeed, ...],
    overflow_failures: tuple[str, ...],
) -> list[str]:
    reasons = list(overflow_failures)
    if any(seed.metadata_state is TraceSeedMetadataState.PARTIAL for seed in seeds):
        reasons.append("seed_metadata_partial")
    contract = retrieval.contract
    if contract.raw is None:
        reasons.append("raw_contract_unavailable")
    if contract.server_version != "1.18.5":
        reasons.append("unsupported_server_version")
    if contract.compatibility is Compatibility.INCOMPATIBLE:
        reasons.append("contract_incompatible")
    if not contract.messages.is_full_history:
        reasons.append("message_history_partial")
    if contract.children.capability is not CapabilityState.SUPPORTED:
        reasons.append("direct_children_unsupported")
    if contract.children.completeness is not Completeness.COMPLETE:
        reasons.append("direct_children_incomplete")
    if contract.features.document is not CapabilityState.SUPPORTED:
        reasons.append("document_capability_partial")
    child_ids = {child.session_id for child in selected_children(retrieval)}
    for message in contract.messages.messages:
        for part in message.parts:
            if isinstance(part, UnknownPart):
                reasons.append("unknown_or_malformed_part")
            if not isinstance(part, ToolPart):
                continue
            if isinstance(part.state, UnknownToolState):
                reasons.append("unknown_tool_state")
            if (
                part.tool == "task"
                and isinstance(part.state, CompletedToolState)
                and part.lineage is None
            ):
                reasons.append("task_lineage_partial")
            if (
                part.lineage is not None
                and part.lineage.child_session_id not in child_ids
            ):
                reasons.append("task_child_unresolved")
    reasons.extend(f"retrieval:{error}" for error in retrieval.errors)
    return list(dict.fromkeys(reasons))


def selected_children(retrieval: SessionGraphRetrieval) -> tuple[ChildSession, ...]:
    direct = retrieval.contract.children
    if direct.capability is CapabilityState.SUPPORTED:
        return direct.sessions
    if retrieval.fallback_children is not None:
        return retrieval.fallback_children.sessions
    return ()


def child_evidence_source(retrieval: SessionGraphRetrieval) -> str:
    if retrieval.contract.children.capability is CapabilityState.SUPPORTED:
        return "direct"
    if retrieval.fallback_children is not None:
        return "fallback"
    if retrieval.fallback_capture is not None:
        return "fallback_evidence_only"
    return "none"


def session_payload(source: SessionPayloadInput) -> JsonObject:
    contract = source.retrieval.contract
    raw_contract = contract.to_json_value()
    if isinstance(raw_contract, bytes):
        raw_value: JsonValue = {
            "encoding": "bytes",
            "hex": raw_contract.hex(),
            "size": len(raw_contract),
        }
    else:
        raw_value = raw_contract
    return {
        "schema": "seam.opencode.raw-session",
        "schema_version": 1,
        "session_id": source.session_id,
        "session_info": source.session_info,
        "session_info_capture": _endpoint_value(source.session_info_capture),
        "seed_correlations": [seed_value(seed) for seed in source.seeds],
        "source_state": source.retrieval.state.value,
        "server_version": contract.server_version,
        "capabilities": {
            "document": contract.features.document.value,
            "health": contract.features.health.value,
            "messages": contract.features.messages.value,
            "children": contract.features.children.value,
            "v2_history": contract.features.v2_history.value,
            "history_authority": contract.history_authority,
        },
        "raw_contract": raw_value,
        "messages": [message.raw for message in contract.messages.messages],
        "children": children_value(source.retrieval),
        "overflow_references": list(contract.overflow_paths),
        "errors": list(source.errors),
        "reasons": list(source.reasons),
    }


def children_value(retrieval: SessionGraphRetrieval) -> JsonObject:
    direct = retrieval.contract.children
    fallback = retrieval.fallback_children
    if direct.capability is CapabilityState.SUPPORTED:
        source = "direct"
    elif fallback is not None:
        source = "fallback"
    else:
        source = "none"
    fallback_value: JsonValue = None
    if fallback is not None or retrieval.fallback_capture is not None:
        typed_children: JsonValue = None
        if fallback is not None:
            typed_children = {
                "capability": fallback.capability.value,
                "completeness": fallback.completeness.value,
                "raw": fallback.raw,
            }
        fallback_value = {
            "typed_children": typed_children,
            "raw_capture": _endpoint_value(retrieval.fallback_capture),
        }
    return {
        "traversal_source": source,
        "direct": {
            "capability": direct.capability.value,
            "completeness": direct.completeness.value,
            "raw": direct.raw,
        },
        "fallback": fallback_value,
    }


def _endpoint_value(capture: EndpointCapture | None) -> JsonValue:
    if capture is None:
        return None
    return {
        "capability": capture.capability.value,
        "status": capture.status,
        "body": capture.body,
        "headers": capture.headers,
        "raw_body": capture.raw_body,
        "error": capture.error,
        "raw": capture.raw,
    }
