from __future__ import annotations

from harness.session.opencode_contract_children import parse_children, parse_document
from harness.session.opencode_contract_json import RawCapture, decode_capture
from harness.session.opencode_contract_messages import parse_messages
from harness.session.opencode_contract_models import (
    CapabilityState,
    ChildrenResult,
    Completeness,
    Compatibility,
    EndpointFeatures,
    MessagesResult,
    ToolPart,
    TraceContract,
)
from harness.session.opencode_contract_values import (
    object_value,
    response_status,
    string_value,
)


def _lineage_consistency(
    messages: MessagesResult,
    children: ChildrenResult,
) -> tuple[bool, bool]:
    if (
        children.capability is not CapabilityState.SUPPORTED
        or children.completeness is not Completeness.COMPLETE
    ):
        return False, False
    child_by_id = {child.session_id: child for child in children.sessions}
    if messages.messages and children.sessions:
        root_session_id = string_value(messages.messages[0].info.get("sessionID"))
        if any(child.parent_id != root_session_id for child in children.sessions):
            return False, True
    partial = False
    for message in messages.messages:
        for part in message.parts:
            if not isinstance(part, ToolPart) or part.lineage is None:
                continue
            child = child_by_id.get(part.lineage.child_session_id)
            if child is None:
                partial = True
                continue
            if child.parent_id != part.lineage.parent_session_id:
                return partial, True
    return partial, False


def assemble_trace_contract(payload: str | bytes) -> TraceContract:
    raw: RawCapture = decode_capture(payload)
    root = None if isinstance(raw, bytes) else object_value(raw)
    health = object_value(root.get("health")) if root is not None else None
    health_body = object_value(health.get("body")) if health is not None else None
    version = (
        string_value(health_body.get("version")) if health_body is not None else ""
    )
    health_ok = (
        health is not None
        and response_status(health) == 200
        and health_body is not None
        and health_body.get("healthy") is True
        and version != ""
    )
    messages_response = object_value(root.get("messages")) if root is not None else None
    children_response = object_value(root.get("children")) if root is not None else None
    messages, messages_bad = parse_messages(messages_response)
    children, children_bad = parse_children(children_response)
    document, paths, document_bad = parse_document(
        object_value(root.get("doc")) if root is not None else None
    )
    v2 = object_value(root.get("v2History")) if root is not None else None
    v2_body = (
        object_value(v2.get("body"))
        if v2 is not None and response_status(v2) == 200
        else None
    )
    features = EndpointFeatures(
        document,
        CapabilityState.SUPPORTED if health_ok else CapabilityState.UNKNOWN,
        CapabilityState.SUPPORTED
        if response_status(messages_response) == 200
        else CapabilityState.UNKNOWN,
        children.capability,
        CapabilityState.SUPPORTED
        if v2_body is not None
        or (paths is not None and "/api/session/{sessionID}/history" in paths)
        else CapabilityState.UNKNOWN,
    )
    lineage_partial, lineage_bad = _lineage_consistency(messages, children)
    incompatible = (
        root is None
        or not health_ok
        or messages_bad
        or children_bad
        or document_bad
        or lineage_bad
    )
    if incompatible:
        compatibility = Compatibility.INCOMPATIBLE
        completeness = Completeness.INCOMPATIBLE
    elif (
        document is not CapabilityState.SUPPORTED
        or messages.completeness is Completeness.PARTIAL
        or children.completeness is not Completeness.COMPLETE
        or lineage_partial
    ):
        compatibility = Compatibility.COMPATIBLE
        completeness = Completeness.PARTIAL
    else:
        compatibility = Compatibility.COMPATIBLE
        completeness = Completeness.COMPLETE
    overflow = tuple(
        path
        for message in messages.messages
        for part in message.parts
        if isinstance(part, ToolPart)
        for path in part.output_paths
    )
    return TraceContract(
        server_version=version,
        compatibility=compatibility,
        completeness=completeness,
        features=features,
        messages=messages,
        children=children,
        overflow_paths=tuple(dict.fromkeys(overflow)),
        v2_history=v2_body,
        raw=raw,
        history_authority="v1",
    )
