from __future__ import annotations

from dataclasses import dataclass as frozen_dataclass
from enum import Enum
from typing import Protocol, final

from harness.session.opencode_contract import (
    CapabilityState,
    ChildrenResult,
    JsonObject,
    JsonValue,
    TraceContract,
)


class TraceCapabilityState(str, Enum):
    COMPATIBLE = "compatible"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class TraceHttp(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        query: JsonObject | None = None,
    ) -> JsonObject: ...


@final
@frozen_dataclass(frozen=True)
class EndpointCapture:
    __slots__ = (
        "capability",
        "status",
        "body",
        "headers",
        "raw_body",
        "error",
        "raw",
    )
    capability: CapabilityState
    status: int | None
    body: JsonValue
    headers: JsonObject
    raw_body: str | None
    error: str | None
    raw: JsonObject


@final
@frozen_dataclass(frozen=True)
class HealthRetrieval:
    __slots__ = ("capability", "server_version", "healthy", "capture")
    capability: CapabilityState
    server_version: str
    healthy: bool
    capture: EndpointCapture


@final
@frozen_dataclass(frozen=True)
class DocumentRetrieval:
    __slots__ = ("capability", "capture")
    capability: CapabilityState
    capture: EndpointCapture


@final
@frozen_dataclass(frozen=True)
class SessionGraphRetrieval:
    __slots__ = (
        "state",
        "contract",
        "fallback_children",
        "fallback_capture",
        "errors",
    )
    state: TraceCapabilityState
    contract: TraceContract
    fallback_children: ChildrenResult | None
    fallback_capture: EndpointCapture | None
    errors: tuple[str, ...]
