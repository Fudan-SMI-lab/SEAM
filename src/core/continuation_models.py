from __future__ import annotations

from enum import Enum, unique
from pathlib import Path
from typing import ClassVar, Dict, Literal, NamedTuple, NewType, Optional, Tuple, final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from core.compat import Annotated, Self, override

from .resource_manifest import ResourceManifest
from .run_manifest import RunId, RunManifest, Sha256Digest
from .run_outcome import TerminalAnchor
from .v3_runtime_report import V3RuntimeReport

_SafeId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
_Status = Annotated[str, StringConstraints(min_length=1, max_length=32)]
OwnerToken = NewType("OwnerToken", str)


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


@unique
class TerminalParentStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@unique
class PhasePresentationStatus(str, Enum):
    """Stable status tokens persisted for phase presentation."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: JsonValue) -> PhasePresentationStatus:
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@unique
class SummaryStatus(str, Enum):
    """Stable terminal tokens persisted in public run summaries."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_raw(cls, raw: JsonValue) -> SummaryStatus:
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@unique
class ContinuationErrorKind(str, Enum):
    UNSAFE_SUMMARY_PATH = "unsafe_summary_path"
    MALFORMED_SUMMARY = "malformed_summary"
    STATUS_INELIGIBLE = "status_ineligible"
    RUN_ID_MISMATCH = "run_id_mismatch"
    INCOMPLETE_PARENT = "incomplete_parent"
    OUTPUT_PROJECT_MISMATCH = "output_project_mismatch"
    WORKFLOW_MISMATCH = "workflow_mismatch"
    AUTHORITY_INVALID = "authority_invalid"
    ANCHOR_INVALID = "anchor_invalid"
    CHILD_RUN_ID_REUSED = "child_run_id_reused"
    PROJECT_LOCKED = "project_locked"
    LOCK_IO = "lock_io"
    LOCK_RELEASE = "lock_release"


@final
class ContinuationError(Exception):
    __slots__ = ("kind", "detail")

    def __init__(self, kind: ContinuationErrorKind, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


class _ReviewDetails(_FrozenModel):
    record_id: _Text
    run_id: Optional[_Text] = None
    phase_execution_id: Optional[_Text] = None
    review_round_id: Optional[_Text] = None
    framework_invocation_id: Optional[_Text] = None
    phase_id: _Text
    phase5_iteration: Annotated[int, Field(ge=1)]
    logical_round: Annotated[int, Field(ge=1)]
    max_rounds: Annotated[int, Field(ge=1)]
    remaining_rounds: Annotated[int, Field(ge=0)]
    verdict: _Text
    outcome: _Text
    duration_seconds: Annotated[float, Field(ge=0)]
    improvement_status: _Text
    session_id: _Text
    command_id: _Text
    reviewer_agent: _Text
    sub_phase: _Text


class _TimeoutDetails(_FrozenModel):
    record_id: _Text
    run_id: Optional[_Text] = None
    phase_execution_id: Optional[_Text] = None
    framework_invocation_id: Optional[_Text] = None
    transport_invocation_id: Optional[_Text] = None
    transport_attempt_id: Optional[_Text] = None
    event_phase: _Text
    agent: _Text
    sub_phase: _Text
    session_id: _Text
    command_id: _Text
    attempt: Annotated[int, Field(ge=1)]
    max_attempts: Annotated[int, Field(ge=1)]
    configured_timeout_seconds: Annotated[float, Field(ge=0)]
    elapsed_seconds: Annotated[float, Field(ge=0)]
    retry_decision: _Text
    reason: _Text
    exhausted: bool


class _ObservabilitySummary(_FrozenModel):
    schema_version: _Text
    review_count: Annotated[int, Field(ge=0)]
    reviews: Tuple[_ReviewDetails, ...]
    timeout_count: Annotated[int, Field(ge=0)]
    timeouts: Tuple[_TimeoutDetails, ...]
    exhaustion_count: Annotated[int, Field(ge=0)]
    dropped_event_count: Annotated[int, Field(ge=0)]
    review_duration_seconds: Annotated[float, Field(ge=0)]
    timeout_elapsed_seconds: Annotated[float, Field(ge=0)]


class _PhaseSummary(_FrozenModel):
    phase_number: Annotated[int, Field(ge=1)]
    phase_id: _SafeId
    label: _Text
    status: PhasePresentationStatus
    duration_seconds: Annotated[float, Field(ge=0)] = 0.0
    error: Optional[_Text] = None

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, raw: JsonValue) -> PhasePresentationStatus:
        return PhasePresentationStatus.from_raw(raw)


class _ContinuationRunSummary(_FrozenModel):
    parent_run_id: _SafeId
    anchor_phase_id: _SafeId
    inherited_phase_ids: Tuple[_SafeId, ...]
    resource_eligibility: _Status
    attachment_mode: _Status


class _TraceCorrelationSummary(_FrozenModel):
    schema_version: Literal[1] = 1
    complete: bool
    run_id: _SafeId
    parent_run_id: Optional[_SafeId]
    lineage_root_run_id: _SafeId
    diagnostics: Tuple[str, ...]


class _TraceLifecycleSummary(_FrozenModel):
    requested: bool = False
    enabled: bool = False
    complete: bool = False
    path: Optional[_Text] = None
    errors: Tuple[str, ...] = ()
    correlation: Optional[_TraceCorrelationSummary] = None


class RunSummaryDocument(_FrozenModel):
    run_id: _SafeId
    base_url: _Text
    workflow_path: _Text
    output_dir: _Text
    temp_dir: _Text
    keep_temp_dir: bool
    requested_max_phase5_iter: Annotated[int, Field(ge=1)]
    effective_max_phase5_iter: Annotated[int, Field(ge=1)]
    phases: Tuple[_PhaseSummary, ...]
    session_count: Annotated[int, Field(ge=0)]
    command_count: Annotated[int, Field(ge=0)]
    overall_status: SummaryStatus
    total_duration_seconds: Annotated[float, Field(ge=0)]
    artifact_dir: Optional[_Text]
    telemetry_paths: Dict[str, str]
    before_snapshot_path: Optional[_Text]
    after_snapshot_path: Optional[_Text]
    entry_script: Optional[_Text]
    errors: Tuple[str, ...]
    review_timeout_observability: _ObservabilitySummary
    trace: _TraceLifecycleSummary = _TraceLifecycleSummary()
    continuation: Optional[_ContinuationRunSummary] = None
    runtime: Optional[V3RuntimeReport] = None

    @field_validator("overall_status", mode="before")
    @classmethod
    def parse_overall_status(cls, raw: JsonValue) -> SummaryStatus:
        return SummaryStatus.from_raw(raw)

    @model_validator(mode="after")
    def require_unique_phases(self) -> Self:
        phase_ids = tuple(phase.phase_id for phase in self.phases)
        if len(phase_ids) != len(set(phase_ids)):
            raise PydanticCustomError(
                "duplicate_phase", "summary phase identifiers must be unique"
            )
        return self


class ContinuationRequest(_FrozenModel):
    summary_path: Path
    child_run_id: _SafeId


class ResolvedTerminalParent(_FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", arbitrary_types_allowed=True
    )

    run_id: RunId
    status: TerminalParentStatus
    output_project: Path
    workflow_path: Path
    workflow_digest: Sha256Digest
    summary_digest: Sha256Digest
    terminal_anchor: TerminalAnchor
    run_manifest: RunManifest
    resource_manifest: ResourceManifest


class ResolvedAuthority(NamedTuple):
    parent: ResolvedTerminalParent
    authoritative_root: Path
    workspace_digest: Sha256Digest


class OwnerMetadata(_FrozenModel):
    schema_id: Literal["seam.continuation-owner"] = Field(
        default="seam.continuation-owner",
        alias="schema",
        serialization_alias="schema",
    )
    schema_version: Literal[1] = 1
    parent_run_id: _SafeId
    child_run_id: _SafeId
    pid: Annotated[int, Field(ge=0)]
    hostname: _Text
    acquired_at_utc: _Text
    owner_token: _Text
