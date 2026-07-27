from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, unique
from typing import TypeAlias

from typing_extensions import assert_never, override

from core.run_manifest import RunId
from core.run_outcome import RunOutcome, TerminalOutcome
from core.runtime_observability_models import (
    EMPTY_OBSERVABILITY_SUMMARY,
    ObservabilitySummary,
)

DurationSource: TypeAlias = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PhaseStatus:
    phase_number: int
    phase_id: str
    label: str
    status: str
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: RunId
    base_url: str
    workflow_path: str
    output_dir: str
    temp_dir: str


@dataclass(frozen=True, slots=True)
class RunExecution:
    keep_temp_dir: bool
    requested_max_phase5_iter: int
    effective_max_phase5_iter: int
    phases: tuple[PhaseStatus, ...]
    session_count: int
    command_count: int
    total_duration_seconds: float
    errors: tuple[str, ...]
    duration_source: DurationSource | None = None


@dataclass(frozen=True, slots=True)
class RunArtifactUpdate:
    artifact_dir: str | None = None
    telemetry_paths: tuple[tuple[str, str], ...] = ()
    directory_paths: tuple[tuple[str, str], ...] = ()
    before_snapshot_path: str | None = None
    after_snapshot_path: str | None = None
    entry_script: str | None = None


EMPTY_ARTIFACT_UPDATE = RunArtifactUpdate()


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    artifact_dir: str | None = None
    telemetry_paths: tuple[tuple[str, str], ...] = ()
    before_snapshot_path: str | None = None
    after_snapshot_path: str | None = None
    entry_script: str | None = None

    def overlay(self, update: RunArtifactUpdate) -> RunArtifacts:
        telemetry = dict(self.telemetry_paths)
        telemetry.update(update.telemetry_paths)
        telemetry.update(update.directory_paths)
        return RunArtifacts(
            artifact_dir=update.artifact_dir or self.artifact_dir,
            telemetry_paths=tuple(telemetry.items()),
            before_snapshot_path=(
                update.before_snapshot_path or self.before_snapshot_path
            ),
            after_snapshot_path=update.after_snapshot_path or self.after_snapshot_path,
            entry_script=update.entry_script or self.entry_script,
        )


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    base_url: str
    workflow_path: str
    output_dir: str
    temp_dir: str
    keep_temp_dir: bool
    requested_max_phase5_iter: int
    effective_max_phase5_iter: int
    phases: tuple[PhaseStatus, ...]
    session_count: int
    command_count: int
    overall_status: str
    total_duration_seconds: float
    artifact_dir: str | None
    telemetry_paths: dict[str, str]
    before_snapshot_path: str | None
    after_snapshot_path: str | None
    entry_script: str | None
    errors: tuple[str, ...]
    review_timeout_observability: ObservabilitySummary = EMPTY_OBSERVABILITY_SUMMARY


@unique
class FinalizationStage(str, Enum):
    INITIAL_ARTIFACTS = "initial_artifacts"
    EVIDENCE_REPLAY = "evidence_replay"
    TRACE_EXPORT = "trace_export"
    AUTHORIZED_CLEANUP = "authorized_cleanup"
    POST_CLEANUP_MANIFEST = "post_cleanup_manifest"
    ARTIFACT_FREEZE = "artifact_freeze"
    SUMMARY_WRITE = "summary_write"

    @classmethod
    def callback_stages(cls) -> tuple[FinalizationStage, ...]:
        return (
            cls.EVIDENCE_REPLAY,
            cls.TRACE_EXPORT,
            cls.AUTHORIZED_CLEANUP,
            cls.POST_CLEANUP_MANIFEST,
        )


@dataclass(frozen=True, slots=True)
class FinalizationHookError(RuntimeError):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


FinalizationHook: TypeAlias = Callable[[TerminalOutcome], RunArtifactUpdate]


def _empty_hook(_outcome: TerminalOutcome) -> RunArtifactUpdate:
    return EMPTY_ARTIFACT_UPDATE


@dataclass(frozen=True, slots=True)
class FinalizationHooks:
    evidence_replay: FinalizationHook = _empty_hook
    trace_export: FinalizationHook = _empty_hook
    authorized_cleanup: FinalizationHook = _empty_hook
    post_cleanup_manifest: FinalizationHook = _empty_hook

    @classmethod
    def empty(cls) -> FinalizationHooks:
        return cls()

    @classmethod
    def from_mapping(
        cls,
        callbacks: Mapping[FinalizationStage, FinalizationHook],
    ) -> FinalizationHooks:
        return cls(
            evidence_replay=callbacks[FinalizationStage.EVIDENCE_REPLAY],
            trace_export=callbacks[FinalizationStage.TRACE_EXPORT],
            authorized_cleanup=callbacks[FinalizationStage.AUTHORIZED_CLEANUP],
            post_cleanup_manifest=callbacks[FinalizationStage.POST_CLEANUP_MANIFEST],
        )

    def ordered(self) -> tuple[tuple[FinalizationStage, FinalizationHook], ...]:
        return (
            (FinalizationStage.EVIDENCE_REPLAY, self.evidence_replay),
            (FinalizationStage.TRACE_EXPORT, self.trace_export),
            (FinalizationStage.AUTHORIZED_CLEANUP, self.authorized_cleanup),
            (FinalizationStage.POST_CLEANUP_MANIFEST, self.post_cleanup_manifest),
        )


@dataclass(frozen=True, slots=True)
class RunFinalizationRequest:
    identity: RunIdentity
    execution: RunExecution
    initial_artifacts: RunArtifacts
    hooks: FinalizationHooks
    authoritative_outcome: RunOutcome | None
    observability: ObservabilitySummary = EMPTY_OBSERVABILITY_SUMMARY

    @property
    def frozen_outcome(self) -> RunOutcome | None:
        return self.authoritative_outcome


@dataclass(frozen=True, slots=True)
class FinalizationDiagnostic:
    stage: FinalizationStage
    error_type: str
    detail: str


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    outcome: TerminalOutcome
    summary: RunSummary
    diagnostics: tuple[FinalizationDiagnostic, ...]
    summary_path: str | None
    diagnostics_path: str | None

    @property
    def exit_code(self) -> int:
        match self.outcome:
            case TerminalOutcome.FAILED:
                return 1
            case TerminalOutcome.PASSED | TerminalOutcome.PASSED_WITH_REVIEWS:
                return 0
            case unreachable:
                assert_never(unreachable)


@unique
class ReportAllocationErrorKind(str, Enum):
    UNSAFE_RUN_ID = "unsafe_run_id"
    DUPLICATE_RUN = "duplicate_run"
    CREATE_FAILED = "create_failed"


@dataclass(frozen=True, slots=True)
class ReportAllocationError(Exception):
    kind: ReportAllocationErrorKind
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


@dataclass(frozen=True, slots=True)
class SidecarWriteError(OSError):
    path: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"
