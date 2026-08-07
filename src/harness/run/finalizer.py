from __future__ import annotations

import logging
import re
from pathlib import Path

from core.agent_io_logger import redact_sensitive_text

from core.run_manifest import RunId
from core.run_outcome import TerminalOutcome
from core.requested_cleanup_error import RequestedContainerCleanupError

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
    RunFinalizationRequest,
    RunSummary,
    SidecarWriteError,
)
from .sidecars import write_diagnostics, write_summary
from .summary_builder import build_summary, read_trace_status

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
    authoritative = request.frozen_outcome
    if authoritative is None:
        return TerminalOutcome.FAILED
    return authoritative.terminal_outcome


def build_run_summary(request: RunFinalizationRequest) -> RunSummary:
    validation = validate_initial_artifacts(
        Path(request.identity.output_dir), request.initial_artifacts
    )
    return build_summary(
        request,
        validation.receipts.to_artifacts(),
        _freeze_outcome(request),
        read_trace_status(request),
    )


def finalize_run(request: RunFinalizationRequest) -> FinalizationResult:
    outcome = _freeze_outcome(request)
    diagnostics: list[FinalizationDiagnostic] = []
    finalization_failed = False
    requested_cleanup_failed = False
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
        except RequestedContainerCleanupError as exc:
            detail = redact_sensitive_text(str(exc))
            logger.warning(
                "Finalization hook %s failed with %s: %s",
                stage.value,
                type(exc).__name__,
                detail,
            )
            diagnostics.append(
                FinalizationDiagnostic(stage, type(exc).__name__, detail)
            )
            if stage is FinalizationStage.AUTHORIZED_CLEANUP:
                requested_cleanup_failed = True
            if stage in request.required_stages:
                finalization_failed = True
            continue
        except Exception as exc:
            detail = redact_sensitive_text(str(exc))
            logger.warning(
                "Finalization hook %s failed with %s: %s",
                stage.value,
                type(exc).__name__,
                detail,
            )
            diagnostics.append(
                FinalizationDiagnostic(stage, type(exc).__name__, detail)
            )
            if stage in request.required_stages:
                finalization_failed = True
            continue
        receipts = receipts.overlay(validation.update)
        _append_artifact_diagnostics(diagnostics, stage, validation.errors)
        if validation.errors and stage in request.required_stages:
            finalization_failed = True

    runtime_report = None
    if request.runtime_report_source is not None:
        try:
            runtime_report = request.runtime_report_source()
        except Exception as exc:
            detail = redact_sensitive_text(str(exc))
            logger.warning(
                "Runtime reporting failed with %s: %s",
                type(exc).__name__,
                detail,
            )
            diagnostics.append(
                FinalizationDiagnostic(
                    FinalizationStage.RUNTIME_REPORT,
                    type(exc).__name__,
                    detail,
                )
            )
    trace_status = read_trace_status(request)
    diagnostics.extend(
        FinalizationDiagnostic(
            FinalizationStage.TRACE_EXPORT,
            "TraceExportDiagnostic",
            error,
        )
        for error in trace_status.errors
    )
    frozen = freeze_artifacts(report_dir, receipts)
    _append_artifact_diagnostics(
        diagnostics, FinalizationStage.ARTIFACT_FREEZE, frozen.errors
    )
    summary = build_summary(
        request,
        frozen.receipts.to_artifacts(),
        outcome,
        trace_status,
    )
    summary_path = report_dir / "summary.json"
    persisted_summary_path: str | None = None
    if not finalization_failed:
        try:
            persisted_summary_path = write_summary(
                summary_path,
                summary,
                request.continuation,
                runtime_report,
            )
        except SidecarWriteError as exc:
            diagnostics.append(
                FinalizationDiagnostic(
                    stage=FinalizationStage.SUMMARY_WRITE,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                )
            )
            if request.summary_required:
                finalization_failed = True

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
        runtime_report=runtime_report,
        finalization_failed=finalization_failed,
        requested_cleanup_failed=requested_cleanup_failed,
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
