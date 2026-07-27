from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    import json
    import typing
    from pathlib import Path

    from .finalization_contract import (
        FinalizationDiagnostic,
        FinalizationResult,
        RunArtifactUpdate,
        RunFinalizationRequest,
        RunSummary,
        TerminalOutcome,
    )

    def _contained_update(
        report_dir: Path, update: RunArtifactUpdate
    ) -> RunArtifactUpdate:
        telemetry: typing.List[typing.Tuple[str, str]] = []
        canonical_report = report_dir.resolve(strict=True)
        for key, raw_path in update.telemetry_paths:
            candidate = Path(raw_path).resolve(strict=True)
            try:
                _ = candidate.relative_to(canonical_report)
            except ValueError:
                continue
            if candidate.is_file():
                telemetry.append((key, raw_path))
        return RunArtifactUpdate(
            artifact_dir=update.artifact_dir,
            telemetry_paths=tuple(telemetry),
            directory_paths=update.directory_paths,
            before_snapshot_path=update.before_snapshot_path,
            after_snapshot_path=update.after_snapshot_path,
            entry_script=update.entry_script,
        )

    def finalize_run(request: RunFinalizationRequest) -> FinalizationResult:
        outcome = request.authoritative_outcome.terminal_outcome
        report_dir = Path(request.identity.output_dir)
        artifacts = request.initial_artifacts
        diagnostics: typing.List[FinalizationDiagnostic] = []
        for stage, hook in request.hooks.ordered():
            try:
                artifacts = artifacts.overlay(
                    _contained_update(report_dir, hook(outcome))
                )
            except (Exception,) as exc:
                diagnostics.append(
                    FinalizationDiagnostic(stage, type(exc).__name__, str(exc))
                )
        summary = RunSummary(
            run_id=request.identity.run_id,
            output_dir=request.identity.output_dir,
            overall_status="FAIL" if outcome is TerminalOutcome.FAILED else "PASS",
            telemetry_paths=dict(artifacts.telemetry_paths),
            errors=request.execution.errors,
        )
        summary_path = report_dir / "summary.json"
        _ = summary_path.write_text(
            json.dumps(summary._asdict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        diagnostics_path: typing.Optional[Path] = None
        if diagnostics:
            diagnostics_path = report_dir / "finalization_diagnostics.json"
            _ = diagnostics_path.write_text(
                json.dumps([item._asdict() for item in diagnostics], indent=2) + "\n",
                encoding="utf-8",
            )
        return FinalizationResult(
            outcome=outcome,
            summary=summary,
            diagnostics=tuple(diagnostics),
            summary_path=str(summary_path),
            diagnostics_path=str(diagnostics_path) if diagnostics_path else None,
        )


__all__ = ("finalize_run",) if sys.version_info < (3, 10) else ()
