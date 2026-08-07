from __future__ import annotations

import typing
from dataclasses import dataclass as frozen_dataclass
from enum import Enum
from typing import Final, Literal, final

from harness.session.opencode_contract_json import (
    JsonObject,
    JsonValue,
    RawCapture,
    clone_json,
)

if typing.TYPE_CHECKING:
    from core.compat import TypeAlias


PINNED_VERSION: Final = "1.18.5"


class Compatibility(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class Completeness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    INCOMPATIBLE = "incompatible"


@final
@frozen_dataclass(frozen=True)
class EndpointFeatures:
    __slots__ = ("document", "health", "messages", "children", "v2_history")
    document: CapabilityState
    health: CapabilityState
    messages: CapabilityState
    children: CapabilityState
    v2_history: CapabilityState


@final
@frozen_dataclass(frozen=True)
class TaskLineage:
    __slots__ = ("parent_session_id", "child_session_id", "raw")
    parent_session_id: str
    child_session_id: str
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class PendingToolState:
    __slots__ = ("raw",)
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class RunningToolState:
    __slots__ = ("raw",)
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class CompletedToolState:
    __slots__ = ("raw",)
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class ErrorToolState:
    __slots__ = ("raw",)
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class UnknownToolState:
    __slots__ = ("status", "raw")
    status: str
    raw: JsonObject


KnownToolState: "TypeAlias" = (
    "PendingToolState | RunningToolState | CompletedToolState | ErrorToolState"
)
ToolState: "TypeAlias" = "KnownToolState | UnknownToolState"


@final
@frozen_dataclass(frozen=True)
class TextPart:
    __slots__ = ("text", "raw")
    text: str
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class ReasoningPart:
    __slots__ = ("text", "raw")
    text: str
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class ToolPart:
    __slots__ = ("tool", "state", "lineage", "output_paths", "raw")
    tool: str
    state: ToolState
    lineage: TaskLineage | None
    output_paths: tuple[str, ...]
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class UnknownPart:
    __slots__ = ("type_name", "raw")
    type_name: str
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class KnownPart:
    __slots__ = ("type_name", "raw")
    type_name: str
    raw: JsonObject


Part: "TypeAlias" = "TextPart | ReasoningPart | ToolPart | KnownPart | UnknownPart"


@final
@frozen_dataclass(frozen=True)
class MessageWithParts:
    __slots__ = ("info", "parts", "raw")
    info: JsonObject
    parts: tuple[Part, ...]
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class MessagesResult:
    __slots__ = ("messages", "completeness", "is_full_history")
    messages: tuple[MessageWithParts, ...]
    completeness: Completeness
    is_full_history: bool


@final
@frozen_dataclass(frozen=True)
class ChildSession:
    __slots__ = ("session_id", "parent_id", "raw")
    session_id: str
    parent_id: str
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class ChildrenResult:
    __slots__ = ("sessions", "capability", "completeness", "raw")
    sessions: tuple[ChildSession, ...]
    capability: CapabilityState
    completeness: Completeness
    raw: JsonValue


@final
@frozen_dataclass(frozen=True)
class TraceContract:
    __slots__ = (
        "server_version",
        "compatibility",
        "completeness",
        "features",
        "messages",
        "children",
        "overflow_paths",
        "v2_history",
        "raw",
        "history_authority",
    )
    server_version: str
    compatibility: Compatibility
    completeness: Completeness
    features: EndpointFeatures
    messages: MessagesResult
    children: ChildrenResult
    overflow_paths: tuple[str, ...]
    v2_history: JsonObject | None
    raw: RawCapture
    history_authority: Literal["v1"]

    def to_json_value(self) -> RawCapture:
        return clone_json(self.raw)
