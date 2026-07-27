from __future__ import annotations

import logging
import re
from pathlib import Path

from typing_extensions import assert_never

from core.run_manifest import RunId
from core.run_outcome import TerminalOutcome

from .artifact_receipts import (
    ArtifactReceiptError,
    freeze_artifacts,
    validate_artifact_update,
    validate_initial_artifacts,
)
from .artifact_paths import snapshot_report
from .models import (
    FinalizationDiagnostic,
    FinalizationResult,
    FinalizationStage,
    ReportAllocationError,
    ReportAllocationErrorKind,
    RunArtifacts,
    RunFinalizationRequest,
    RunSummary,
    SidecarWriteError,
)
from .sidecars import write_diagnostics, write_summary

logger = logging.getLogger("harness.run.finalizer")
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def allocate_report_directory(
    report_root: Path,
    run_id: RunId,
) -> Path:
    if _SAFE_RUN_ID.fullmatch(str(run_id)) is None:
        raise ReportAllocationError(
            kind=ReportAllocationErrorKind.UNSAFE_RUN_ID,
            detail=f"run_id is not a safe namespace: {run_id!s}",
        )
    try:
        report_root.mkdir(parents=True, exist_ok=True)
        report_dir = report_root / str(run_id)
        report_dir.mkdir()
    except FileExistsError as exc:
        raise ReportAllocationError(
            kind=ReportAllocationErrorKind.DUPLICATE_RUN,
            detail=f"report namespace already exists: {run_id!s}",
        ) from exc
    except OSError as exc:
        raise ReportAllocationError(
            kind=ReportAllocationErrorKind.CREATE_FAILED,
            detail=str(exc),
        ) from exc
    return report_dir


def _freeze_outcome(request: RunFinalizationRequest) -> TerminalOutcome:
    match request.frozen_outcome:
        case None:
            return TerminalOutcome.FAILED
        case authoritative:
            return authoritative.terminal_outcome


def _build_summary(
    request: RunFinalizationRequest,
    artifacts: RunArtifacts,
    outcome: TerminalOutcome,
) -> RunSummary:
    identity = request.identity
    execution = request.execution
    total_duration_seconds = (
        execution.total_duration_seconds
        if execution.duration_source is None
        else execution.duration_source()
    )
    match outcome:
        case TerminalOutcome.FAILED:
            overall_status = "FAIL"
        case TerminalOutcome.PASSED | TerminalOutcome.PASSED_WITH_REVIEWS:
            overall_status = "PASS"
        case unreachable:
            assert_never(unreachable)
    return RunSummary(
        run_id=str(identity.run_id),
        base_url=identity.base_url,
        workflow_path=identity.workflow_path,
        output_dir=identity.output_dir,
        temp_dir=identity.temp_dir,
        keep_temp_dir=execution.keep_temp_dir,
        requested_max_phase5_iter=execution.requested_max_phase5_iter,
        effective_max_phase5_iter=execution.effective_max_phase5_iter,
        phases=execution.phases,
        session_count=execution.session_count,
        command_count=execution.command_count,
        overall_status=overall_status,
        total_duration_seconds=round(total_duration_seconds, 3),
        artifact_dir=artifacts.artifact_dir,
        telemetry_paths=dict(artifacts.telemetry_paths),
        before_snapshot_path=artifacts.before_snapshot_path,
        after_snapshot_path=artifacts.after_snapshot_path,
        entry_script=artifacts.entry_script,
        errors=execution.errors,
        review_timeout_observability=request.observability,
    )


def build_run_summary(request: RunFinalizationRequest) -> RunSummary:
    validation = validate_initial_artifacts(
        Path(request.identity.output_dir), request.initial_artifacts
    )
    return _build_summary(
        request,
        validation.receipts.to_artifacts(),
        _freeze_outcome(request),
    )


def finalize_run(request: RunFinalizationRequest) -> FinalizationResult:
    outcome = _freeze_outcome(request)
    diagnostics: list[FinalizationDiagnostic] = []
    report_dir = Path(request.identity.output_dir)
    initial = validate_initial_artifacts(report_dir, request.initial_artifacts)
    receipts = initial.receipts
    _append_artifact_diagnostics(
        diagnostics, FinalizationStage.INITIAL_ARTIFACTS, initial.errors
    )
    for stage, hook in request.hooks.ordered():
        before = snapshot_report(report_dir)
        try:
            validation = validate_artifact_update(
                report_dir, hook(outcome), before, stage
            )
        except Exception as exc:
            logger.warning(
                "Finalization hook %s failed with %s: %s",
                stage.value,
                type(exc).__name__,
                exc,
            )
            diagnostics.append(
                FinalizationDiagnostic(stage, type(exc).__name__, str(exc))
            )
            continue
        receipts = receipts.overlay(validation.update)
        _append_artifact_diagnostics(diagnostics, stage, validation.errors)

    frozen = freeze_artifacts(report_dir, receipts)
    _append_artifact_diagnostics(
        diagnostics, FinalizationStage.ARTIFACT_FREEZE, frozen.errors
    )
    summary = _build_summary(request, frozen.receipts.to_artifacts(), outcome)
    summary_path = report_dir / "summary.json"
    persisted_summary_path: str | None = None
    try:
        persisted_summary_path = write_summary(summary_path, summary)
    except SidecarWriteError as exc:
        diagnostics.append(
            FinalizationDiagnostic(
                stage=FinalizationStage.SUMMARY_WRITE,
                error_type=type(exc).__name__,
                detail=str(exc),
            )
        )

    persisted_diagnostics_path: str | None = None
    if diagnostics:
        diagnostics_path = report_dir / "finalization_diagnostics.json"
        try:
            persisted_diagnostics_path = write_diagnostics(
                diagnostics_path, tuple(diagnostics)
            )
        except SidecarWriteError:
            persisted_diagnostics_path = None
    return FinalizationResult(
        outcome=outcome,
        summary=summary,
        diagnostics=tuple(diagnostics),
        summary_path=persisted_summary_path,
        diagnostics_path=persisted_diagnostics_path,
    )


def _append_artifact_diagnostics(
    diagnostics: list[FinalizationDiagnostic],
    stage: FinalizationStage,
    errors: tuple[ArtifactReceiptError, ...],
) -> None:
    for error in errors:
        logger.warning(
            "Finalization stage %s rejected artifact: %s", stage.value, error
        )
        diagnostics.append(
            FinalizationDiagnostic(stage, type(error).__name__, str(error))
        )
