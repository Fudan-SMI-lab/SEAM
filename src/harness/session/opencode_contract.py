from __future__ import annotations

from harness.session.opencode_contract_assembly import assemble_trace_contract
from harness.session.opencode_contract_json import (
    MAX_CAPTURE_CHARS,
    JsonObject,
    JsonValue,
)
from harness.session.opencode_contract_models import (
    PINNED_VERSION,
    CapabilityState,
    ChildSession,
    ChildrenResult,
    CompletedToolState,
    Completeness,
    Compatibility,
    EndpointFeatures,
    ErrorToolState,
    MessageWithParts,
    MessagesResult,
    Part,
    PendingToolState,
    ReasoningPart,
    RunningToolState,
    TaskLineage,
    TextPart,
    ToolPart,
    ToolState,
    TraceContract,
    UnknownPart,
    UnknownToolState,
)

__all__ = [
    "PINNED_VERSION",
    "MAX_CAPTURE_CHARS",
    "JsonValue",
    "JsonObject",
    "Compatibility",
    "CapabilityState",
    "Completeness",
    "EndpointFeatures",
    "TaskLineage",
    "PendingToolState",
    "RunningToolState",
    "CompletedToolState",
    "ErrorToolState",
    "UnknownToolState",
    "ToolState",
    "TextPart",
    "ReasoningPart",
    "ToolPart",
    "UnknownPart",
    "Part",
    "MessageWithParts",
    "MessagesResult",
    "ChildSession",
    "ChildrenResult",
    "TraceContract",
    "parse_trace_contract",
]


def parse_trace_contract(payload: str | bytes) -> TraceContract:
    """Parse a captured endpoint bundle without interpreting payload text."""
    return assemble_trace_contract(payload)
