from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    import json
    import hashlib
    import os
    import stat
    import typing
    from pathlib import Path

    from core.requested_cleanup_error import RequestedContainerCleanupError

    from .finalization_contract import (
        FinalizationDiagnostic,
        FinalizationResult,
        RunArtifactUpdate,
        RunFinalizationRequest,
        RunSummary,
        TerminalOutcome,
    )

    _REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def _is_link_or_reparse(path: Path) -> bool:
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT)

    def _fingerprint(path: Path, directory: bool) -> str:
        digest = hashlib.sha256()
        if not directory:
            digest.update(path.read_bytes())
            return digest.hexdigest()
        for parent, directories, files in os.walk(str(path), followlinks=False):
            directories.sort()
            files.sort()
            root = Path(parent)
            for name in directories:
                child = root / name
                if _is_link_or_reparse(child):
                    raise OSError("linked artifact directory")
                digest.update(child.relative_to(path).as_posix().encode())
                digest.update(b"D")
            for name in files:
                child = root / name
                if _is_link_or_reparse(child):
                    raise OSError("linked artifact file")
                digest.update(child.relative_to(path).as_posix().encode())
                digest.update(b"F")
                digest.update(hashlib.sha256(child.read_bytes()).digest())
        return digest.hexdigest()

    def _snapshot_report(report_dir: Path) -> typing.Dict[str, str]:
        snapshot: typing.Dict[str, str] = {}
        for parent, directories, files in os.walk(str(report_dir), followlinks=False):
            root = Path(parent)
            for name in directories:
                path = root / name
                try:
                    snapshot[str(path.resolve(strict=True))] = _fingerprint(path, True)
                except OSError:
                    continue
            for name in files:
                path = root / name
                try:
                    snapshot[str(path.resolve(strict=True))] = _fingerprint(path, False)
                except OSError:
                    continue
        return snapshot

    def _contained_update(
        report_dir: Path,
        update: RunArtifactUpdate,
        before: typing.Dict[str, str],
    ) -> typing.Tuple[RunArtifactUpdate, bool]:
        telemetry: typing.List[typing.Tuple[str, str]] = []
        directories: typing.List[typing.Tuple[str, str]] = []
        rejected = False
        canonical_report = report_dir.resolve(strict=True)

        def accepted(
            raw_path: typing.Optional[str], directory: bool
        ) -> typing.Optional[str]:
            nonlocal rejected
            if raw_path is None:
                return None
            try:
                candidate = Path(raw_path).resolve(strict=True)
                _ = candidate.relative_to(canonical_report)
                if candidate == canonical_report:
                    raise ValueError("report root is not an artifact")
            except (OSError, ValueError):
                rejected = True
                return None
            valid = candidate.is_dir() if directory else candidate.is_file()
            if not valid:
                rejected = True
                return None
            try:
                fingerprint = _fingerprint(candidate, directory)
            except OSError:
                rejected = True
                return None
            if before.get(str(candidate)) == fingerprint:
                rejected = True
                return None
            return raw_path

        for key, raw_path in update.telemetry_paths:
            selected = accepted(raw_path, False)
            if selected is not None:
                telemetry.append((key, selected))
        for key, raw_path in update.directory_paths:
            selected = accepted(raw_path, True)
            if selected is not None:
                directories.append((key, selected))
        return RunArtifactUpdate(
            artifact_dir=accepted(update.artifact_dir, True),
            telemetry_paths=tuple(telemetry),
            directory_paths=tuple(directories),
            before_snapshot_path=accepted(update.before_snapshot_path, False),
            after_snapshot_path=accepted(update.after_snapshot_path, False),
            entry_script=update.entry_script,
        ), rejected

    def finalize_run(request: RunFinalizationRequest) -> FinalizationResult:
        outcome = request.authoritative_outcome.terminal_outcome
        report_dir = Path(request.identity.output_dir)
        artifacts = request.initial_artifacts
        diagnostics: typing.List[FinalizationDiagnostic] = []
        finalization_failed = False
        requested_cleanup_failed = False
        for stage, hook in request.hooks.ordered():
            before = _snapshot_report(report_dir)
            try:
                update, rejected = _contained_update(report_dir, hook(outcome), before)
                artifacts = artifacts.overlay(update)
                if rejected:
                    diagnostics.append(
                        FinalizationDiagnostic(
                            stage,
                            "SidecarValidationError",
                            "hook artifact escaped the report directory",
                        )
                    )
                    if stage in request.required_stages:
                        finalization_failed = True
            except RequestedContainerCleanupError as exc:
                diagnostics.append(
                    FinalizationDiagnostic(stage, type(exc).__name__, str(exc))
                )
                if stage.value == "authorized_cleanup":
                    requested_cleanup_failed = True
                if stage in request.required_stages:
                    finalization_failed = True
            except (Exception,) as exc:
                diagnostics.append(
                    FinalizationDiagnostic(stage, type(exc).__name__, str(exc))
                )
                if stage in request.required_stages:
                    finalization_failed = True
        summary = RunSummary(
            run_id=request.identity.run_id,
            output_dir=request.identity.output_dir,
            overall_status="FAIL" if outcome is TerminalOutcome.FAILED else "PASS",
            telemetry_paths=dict(artifacts.telemetry_paths),
            errors=request.execution.errors,
        )
        summary_path = report_dir / "summary.json"
        payload = summary._asdict()
        if request.continuation is not None:
            payload["continuation"] = request.continuation._asdict()
        persisted_summary_path: typing.Optional[str] = None
        if not finalization_failed:
            try:
                _ = summary_path.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                persisted_summary_path = str(summary_path)
            except OSError:
                if request.summary_required:
                    finalization_failed = True
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
            summary_path=persisted_summary_path,
            diagnostics_path=str(diagnostics_path) if diagnostics_path else None,
            finalization_failed=finalization_failed,
            requested_cleanup_failed=requested_cleanup_failed,
        )


__all__ = ("finalize_run",) if sys.version_info < (3, 10) else ()
