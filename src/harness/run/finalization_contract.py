from __future__ import annotations

import sys
import typing
from enum import Enum, unique

if sys.version_info >= (3, 10):
    from core.run_outcome import RunOutcome as RunOutcome
    from core.run_outcome import TerminalOutcome as TerminalOutcome

    from .models import FinalizationDiagnostic as FinalizationDiagnostic
    from .models import FinalizationHooks as FinalizationHooks
    from .models import FinalizationResult as FinalizationResult
    from .models import FinalizationStage as FinalizationStage
    from .models import PhaseStatus as PhaseStatus
    from .models import RunArtifacts as RunArtifacts
    from .models import RunArtifactUpdate as RunArtifactUpdate
    from .models import RunExecution as RunExecution
    from .models import RunFinalizationRequest as RunFinalizationRequest
    from .models import RunIdentity as RunIdentity
    from .models import RunSummary as RunSummary
else:

    @unique
    class TerminalOutcome(str, Enum):
        PASSED = "passed"
        PASSED_WITH_REVIEWS = "passed_with_reviews"
        FAILED = "failed"

    class RunOutcome(typing.NamedTuple):
        terminal_outcome: TerminalOutcome

    class PhaseStatus(typing.NamedTuple):
        phase_number: int
        phase_id: str
        label: str
        status: str
        duration_seconds: float = 0.0
        error: typing.Optional[str] = None

    class RunIdentity(typing.NamedTuple):
        run_id: str
        base_url: str
        workflow_path: str
        output_dir: str
        temp_dir: str

    class RunExecution(typing.NamedTuple):
        keep_temp_dir: bool
        requested_max_phase5_iter: int
        effective_max_phase5_iter: int
        phases: typing.Tuple[PhaseStatus, ...]
        session_count: int
        command_count: int
        total_duration_seconds: float
        errors: typing.Tuple[str, ...]

    class RunArtifactUpdate(typing.NamedTuple):
        artifact_dir: typing.Optional[str] = None
        telemetry_paths: typing.Tuple[typing.Tuple[str, str], ...] = ()
        directory_paths: typing.Tuple[typing.Tuple[str, str], ...] = ()
        before_snapshot_path: typing.Optional[str] = None
        after_snapshot_path: typing.Optional[str] = None
        entry_script: typing.Optional[str] = None

    class RunArtifacts(typing.NamedTuple):
        artifact_dir: typing.Optional[str] = None
        telemetry_paths: typing.Tuple[typing.Tuple[str, str], ...] = ()
        before_snapshot_path: typing.Optional[str] = None
        after_snapshot_path: typing.Optional[str] = None
        entry_script: typing.Optional[str] = None

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
                after_snapshot_path=(
                    update.after_snapshot_path or self.after_snapshot_path
                ),
                entry_script=update.entry_script or self.entry_script,
            )

    @unique
    class FinalizationStage(str, Enum):
        EVIDENCE_REPLAY = "evidence_replay"
        TRACE_EXPORT = "trace_export"
        AUTHORIZED_CLEANUP = "authorized_cleanup"
        POST_CLEANUP_MANIFEST = "post_cleanup_manifest"

    FinalizationHook = typing.Callable[[TerminalOutcome], RunArtifactUpdate]

    def _empty_hook(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        return RunArtifactUpdate()

    @typing.final
    class FinalizationHooks:
        __slots__ = (
            "authorized_cleanup",
            "evidence_replay",
            "post_cleanup_manifest",
            "trace_export",
        )

        def __init__(
            self,
            evidence_replay: FinalizationHook = _empty_hook,
            trace_export: FinalizationHook = _empty_hook,
            authorized_cleanup: FinalizationHook = _empty_hook,
            post_cleanup_manifest: FinalizationHook = _empty_hook,
        ) -> None:
            self.evidence_replay = evidence_replay
            self.trace_export = trace_export
            self.authorized_cleanup = authorized_cleanup
            self.post_cleanup_manifest = post_cleanup_manifest

        def ordered(
            self,
        ) -> typing.Tuple[typing.Tuple[FinalizationStage, FinalizationHook], ...]:
            return (
                (FinalizationStage.EVIDENCE_REPLAY, self.evidence_replay),
                (FinalizationStage.TRACE_EXPORT, self.trace_export),
                (FinalizationStage.AUTHORIZED_CLEANUP, self.authorized_cleanup),
                (FinalizationStage.POST_CLEANUP_MANIFEST, self.post_cleanup_manifest),
            )

    class RunFinalizationRequest(typing.NamedTuple):
        identity: RunIdentity
        execution: RunExecution
        initial_artifacts: RunArtifacts
        hooks: FinalizationHooks
        authoritative_outcome: RunOutcome
        observability: typing.Tuple[str, ...] = ()

    class FinalizationDiagnostic(typing.NamedTuple):
        stage: FinalizationStage
        error_type: str
        detail: str

    class RunSummary(typing.NamedTuple):
        run_id: str
        output_dir: str
        overall_status: str
        telemetry_paths: typing.Dict[str, str]
        errors: typing.Tuple[str, ...]

    class FinalizationResult(typing.NamedTuple):
        outcome: TerminalOutcome
        summary: RunSummary
        diagnostics: typing.Tuple[FinalizationDiagnostic, ...]
        summary_path: typing.Optional[str]
        diagnostics_path: typing.Optional[str]

        @property
        def exit_code(self) -> int:
            return 1 if self.outcome is TerminalOutcome.FAILED else 0


__all__ = (
    "FinalizationDiagnostic",
    "FinalizationHooks",
    "FinalizationResult",
    "FinalizationStage",
    "PhaseStatus",
    "RunArtifacts",
    "RunArtifactUpdate",
    "RunExecution",
    "RunFinalizationRequest",
    "RunIdentity",
    "RunOutcome",
    "RunSummary",
    "TerminalOutcome",
)
