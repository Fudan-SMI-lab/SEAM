from __future__ import annotations

from dataclasses import dataclass as frozen_dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, final

from core.compat import override

from harness.session.opencode_contract import JsonObject
from harness.session.opencode_trace_models import EndpointCapture, SessionGraphRetrieval
from harness.session.trace_correlation_models import TraceCorrelationContext
from harness.session.trace_seeds import TraceSeed


DEFAULT_MAX_OVERFLOW_BYTES = 64 * 1024 * 1024


class TraceGraphClient(Protocol):
    def get_session_info(self, session_id: str) -> EndpointCapture: ...

    def retrieve_session_graph(self, session_id: str) -> SessionGraphRetrieval: ...


class OverflowStatus(str, Enum):
    COPIED = "copied"
    REMOTE = "remote"
    RELATIVE = "relative"
    PATH_TRAVERSAL = "path_traversal"
    MISSING = "missing"
    OUTSIDE_ALLOWED_ROOTS = "outside_allowed_roots"
    OVERSIZED = "oversized"
    MALFORMED = "malformed"
    UNSAFE = "unsafe"
    NOT_REGULAR = "not_regular"
    READ_ERROR = "read_error"
    WRITE_INTERRUPTED = "write_interrupted"
    LIMIT_EXCEEDED = "limit_exceeded"


@final
@frozen_dataclass(frozen=True, init=False)
class TraceExportRequest:
    """Immutable inputs for one raw V1 trace capture."""

    __slots__ = (
        "destination",
        "seeds",
        "overflow_roots",
        "captured_at",
        "max_overflow_bytes",
        "correlation",
    )
    destination: Path
    seeds: tuple[TraceSeed, ...]
    overflow_roots: tuple[Path, ...]
    captured_at: str | None
    max_overflow_bytes: int
    correlation: TraceCorrelationContext | None

    def __init__(
        self,
        destination: Path,
        seeds: tuple[TraceSeed, ...],
        overflow_roots: tuple[Path, ...] = (),
        captured_at: str | None = None,
        max_overflow_bytes: int = DEFAULT_MAX_OVERFLOW_BYTES,
        correlation: TraceCorrelationContext | None = None,
    ) -> None:
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "seeds", seeds)
        object.__setattr__(self, "overflow_roots", overflow_roots)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "max_overflow_bytes", max_overflow_bytes)
        object.__setattr__(self, "correlation", correlation)


@final
@frozen_dataclass(frozen=True)
class TraceExportResult:
    __slots__ = (
        "manifest_path",
        "complete",
        "session_count",
        "errors",
        "correlation_complete",
        "correlation_errors",
    )
    manifest_path: Path
    complete: bool
    session_count: int
    errors: tuple[str, ...]
    correlation_complete: bool | None
    correlation_errors: tuple[str, ...]


@final
@frozen_dataclass(frozen=True)
class OverflowCopyRequest:
    __slots__ = ("reference", "destination", "allowed_roots", "max_bytes")
    reference: str
    destination: Path
    allowed_roots: tuple[Path, ...]
    max_bytes: int


@final
@frozen_dataclass(frozen=True)
class SessionPayloadInput:
    __slots__ = (
        "session_id",
        "session_info",
        "session_info_capture",
        "seeds",
        "retrieval",
        "reasons",
        "errors",
        "correlation",
    )
    session_id: str
    session_info: JsonObject | None
    session_info_capture: EndpointCapture | None
    seeds: tuple[TraceSeed, ...]
    retrieval: SessionGraphRetrieval
    reasons: tuple[str, ...]
    errors: tuple[str, ...]
    correlation: JsonObject | None


@final
@frozen_dataclass(frozen=True)
class ResolvedSessionInfo:
    __slots__ = ("value", "capture", "reasons")
    value: JsonObject | None
    capture: EndpointCapture | None
    reasons: tuple[str, ...]


@final
@frozen_dataclass(frozen=True)
class StoredArtifact:
    __slots__ = ("path", "size", "sha256")
    path: Path
    size: int
    sha256: str


@final
@frozen_dataclass(frozen=True)
class OverflowCapture:
    __slots__ = ("status", "artifact", "detail")
    status: OverflowStatus
    artifact: StoredArtifact | None
    detail: str | None


@final
@frozen_dataclass(frozen=True)
class TraceExportError(Exception):
    __slots__ = ("path", "detail")
    path: Path
    detail: str

    @override
    def __str__(self) -> str:
        return f"trace export failed at {self.path}: {self.detail}"


@final
@frozen_dataclass(frozen=True)
class TraceWriteError(Exception):
    __slots__ = ("path", "detail")
    path: Path
    detail: str

    @override
    def __str__(self) -> str:
        return f"trace artifact write failed at {self.path}: {self.detail}"
