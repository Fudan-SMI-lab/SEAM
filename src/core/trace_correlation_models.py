from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from core.compat import override

from core.run_manifest import RunId, Sha256Digest
from core.run_outcome import PhaseId

PhaseExecutionId = NewType("PhaseExecutionId", str)
ReviewRoundId = NewType("ReviewRoundId", str)
FrameworkInvocationId = NewType("FrameworkInvocationId", str)
TransportAttemptId = NewType("TransportAttemptId", str)
SessionId = NewType("SessionId", str)
ToolCallId = NewType("ToolCallId", str)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class TraceCorrelationContractError(Exception):
    field: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.field}: {self.detail}"


def _require_text(field: str, value: str) -> None:
    if not value.strip():
        raise TraceCorrelationContractError(field, "identifier must be non-empty")


def make_phase_execution_id(run_id: RunId, phase_id: PhaseId) -> PhaseExecutionId:
    _require_text("run_id", str(run_id))
    _require_text("phase_id", str(phase_id))
    return PhaseExecutionId(f"{run_id}:phase:{phase_id}")


def make_review_round_id(
    run_id: RunId,
    phase_id: PhaseId,
    round_number: int,
) -> ReviewRoundId:
    if round_number < 1:
        raise TraceCorrelationContractError("round_number", "must be positive")
    _require_text("run_id", str(run_id))
    _require_text("phase_id", str(phase_id))
    return ReviewRoundId(f"{run_id}:{phase_id}:review:{round_number}")


def make_transport_attempt_id(
    invocation_id: str,
    attempt: int,
) -> TransportAttemptId:
    _require_text("transport_invocation_id", invocation_id)
    if attempt < 1:
        raise TraceCorrelationContractError("transport_attempt", "must be positive")
    return TransportAttemptId(f"{invocation_id}:attempt-{attempt}")


@dataclass(frozen=True)
class ParentTraceReference:
    run_id: RunId
    manifest_path: Path
    sha256: Sha256Digest
    size_bytes: int

    def __post_init__(self) -> None:
        _require_text("parent_trace.run_id", str(self.run_id))
        if not self.manifest_path.is_absolute():
            raise TraceCorrelationContractError(
                "parent_trace.manifest_path", "must be absolute"
            )
        if _SHA256.fullmatch(str(self.sha256)) is None:
            raise TraceCorrelationContractError(
                "parent_trace.sha256", "must be lowercase SHA-256"
            )
        if self.size_bytes < 0:
            raise TraceCorrelationContractError(
                "parent_trace.size_bytes", "must be non-negative"
            )


@dataclass(frozen=True)
class RunCorrelationScope:
    run_id: RunId
    parent_run_id: RunId | None
    lineage_root_run_id: RunId
    parent_trace: ParentTraceReference | None

    def __post_init__(self) -> None:
        _require_text("run_id", str(self.run_id))
        _require_text("lineage_root_run_id", str(self.lineage_root_run_id))
        if self.parent_run_id == self.run_id:
            raise TraceCorrelationContractError(
                "parent_run_id", "a run cannot be its own parent"
            )
        if self.parent_run_id is None and self.lineage_root_run_id != self.run_id:
            raise TraceCorrelationContractError(
                "lineage_root_run_id", "a root run must be its lineage root"
            )
        if self.parent_trace is not None and (
            self.parent_run_id is None or self.parent_trace.run_id != self.parent_run_id
        ):
            raise TraceCorrelationContractError(
                "parent_trace", "reference must identify the immediate parent"
            )
