#!/usr/bin/env python3
# pyright: reportArgumentType=false, reportCallIssue=false, reportIndexIssue=false, reportOperatorIssue=false, reportPrivateUsage=false, reportUnknownMemberType=false
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from collections.abc import MutableMapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError


from e2e_v3_bootstrap import PACKAGE_ROOT
from core.paths import execution_root
from core.owned_directory_lock import DirectoryLockIdentity, directory_lock_identity
from core.review_policy import ReviewCliOverrides
from core.execution_backend import ContainerBackend
from core.execution_env_context import (
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
)
from core.resource_manifest import ResourceManifestError, ResourceManifestErrorKind
from core.resource_retention import (
    ContainerRetention,
    resolve_v3_container_retention,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
)
from core.resource_retention_manifest import (
    RetentionManifestFinalizer,
    RetentionManifestRequest,
    create_retention_manifest,
)
from core.run_manifest import RunId
from core.run_outcome import RunOutcome, TerminalOutcome, WorkflowTerminal
from core.manifest_sealing_runner import run_direct_manifest_sealing
from core.v3_runtime_report import V3RuntimeReport
from core.v3_runtime_report_integration import (
    RuntimeReportingInputs,
    prepare_runtime_report_request,
)
from core.terminal_continuation_models import (
    PreparedTerminalContinuation,
    TerminalContinuationRunRequest,
    V3InvocationOptions,
    V3OpenCodeOptions,
    V3ReviewRunOptions,
    V3ServerRunOptions,
)
from core.types import WorkflowDefinition
from core.v3_outcome_mapping import (
    V3OutcomeUnavailableError,
    V3RunFacts,
    build_v3_run_outcome,
)
from harness.run import (
    CleanupContext,
    ContinuationRunSummary,
    EvidenceContext,
    EvidencePersister,
    FinalizationHooks,
    FinalizationStage,
    PhaseStatus,
    ReportAllocationError,
    RunArtifacts,
    RunArtifactUpdate,
    RunExecution,
    RunFinalizationRequest,
    RunIdentity,
    RunSummary,
    ResourceCleanup,
    V3TelemetrySources,
    V3RunLifecycle,
    allocate_report_directory,
    build_telemetry_sidecars,
    compose_trace_hooks,
    finalize_run,
    persist_python_snapshot,
)
from harness.run.finalizer import build_run_summary
from harness.run.workflow_result_projection import project_workflow_result
from harness.run.v3_retention import (
    _compose_v3_retention_hooks,
)
from harness.run.v3_runtime_reporting import (
    V3RuntimeReportRecorder,
    print_runtime_report,
)
from harness.run.v3_trace_integration import (
    V3TraceCorrelationInputs,
    V3TraceIntegrationRequest,
    create_v3_trace_lifecycle,
)
from tests.e2e.dashboard_wiring import DashboardWiring

DEFAULT_SERVER_URL = "http://127.0.0.1:4096"
DEFAULT_MAX_PHASE5_ITER = 5
EXCLUDED_SNAPSHOT_DIRS = {".git", ".sm-artifacts", ".venv", "__pycache__"}

REPO_ROOT = execution_root()
TEMPLATE_DIR = PACKAGE_ROOT / "test_project_template"
_default_workflow_path = PACKAGE_ROOT / "workflows" / "seam_auto_default.yaml"
OUTPUT_ROOT = REPO_ROOT / "e2e-reports" / "src"


class Ansi:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"


def log(msg: str, *, flush: bool = True) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=flush)


def supports_color() -> bool:
    return sys.stdout.isatty() and (
        not hasattr(sys.stdout, "isatty") or sys.stdout.isatty()
    )


def colorize(text: str, color: str) -> str:
    if not supports_color():
        return text
    return f"{color}{text}{Ansi.RESET}"


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def print_phase_running(phase_number: int, phase_total: int, label: str) -> None:
    log(f"[Phase {phase_number}/{phase_total}] {label} — RUNNING")


def print_phase_finished(
    phase_number: int,
    phase_total: int,
    label: str,
    passed: bool,
    duration_seconds: float,
    error: str | None = None,
) -> None:
    status = "PASSED" if passed else "FAILED"
    details = f" ({duration_seconds:.1f}s)"
    if error:
        details += f"\n  Error: {error}"
    color = Ansi.GREEN if passed else Ansi.RED
    print(
        colorize(
            f"[Phase {phase_number}/{phase_total}] {label} — {status}{details}", color
        ),
        flush=True,
    )


def check_server_running(
    base_url: str,
    *,
    readiness_mode: str = "message",
    message_timeout: int = 120,
    verbose: bool = False,
) -> None:
    if readiness_mode == "off":
        log("OpenCode readiness check skipped (--opencode-readiness off)")
        return

    diag_script = REPO_ROOT / "scripts" / "diagnose_seam_opencode.py"
    if not diag_script.is_file():
        raise RuntimeError(f"OpenCode diagnostic script not found: {diag_script}")

    cmd = [
        sys.executable,
        str(diag_script),
        "--server-url",
        base_url,
        "--mode",
        readiness_mode,
        "--message-timeout",
        str(message_timeout),
    ]
    completed = subprocess.run(
        cmd, capture_output=True, text=True, timeout=message_timeout + 30, check=False
    )
    if completed.returncode not in {0, 20}:
        if completed.stdout.strip():
            for line in completed.stdout.strip().splitlines():
                log(f"OpenCode diagnostic: {line}")
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"exit code {completed.returncode}"
        )
        raise RuntimeError(f"OpenCode readiness diagnostic failed: {detail}")
    if verbose:
        for line in completed.stdout.strip().splitlines():
            log(f"OpenCode diagnostic: {line}")


def log_server_diagnostics(
    base_url: str,
    server_proc: subprocess.Popen[bytes] | None,
    work_dir: str,
) -> None:
    try:
        from harness.server.lifecycle import collect_server_diagnostics

        diagnostics = collect_server_diagnostics(
            base_url,
            server_proc=server_proc,
            work_dir=work_dir,
        )
    except Exception as exc:
        log(f"OpenCode server diagnostics unavailable: {exc}")
        return

    def display(value: object) -> str:
        if value is None or value == "":
            return "unknown"
        return str(value)

    log(
        "OpenCode server diagnostics: "
        f"url={display(diagnostics.get('url'))} "
        f"source={display(diagnostics.get('source'))} "
        f"pid={display(diagnostics.get('pid'))} "
        f"cwd={display(diagnostics.get('cwd'))} "
        f"start_work_dir={display(diagnostics.get('start_work_dir'))}"
    )

    config_files = diagnostics.get("config_files")
    if isinstance(config_files, list):
        for item in config_files:
            if not isinstance(item, dict):
                continue
            log(
                "OpenCode config file: "
                f"name={display(item.get('name'))} "
                f"path={display(item.get('path'))} "
                f"exists={display(item.get('exists'))}"
            )

    models = diagnostics.get("models")
    model_text = (
        ", ".join(str(model) for model in models)
        if isinstance(models, list) and models
        else "none found"
    )
    log(
        "OpenCode model config: "
        f"path={display(diagnostics.get('model_config_path'))} "
        f"models={model_text}"
    )


def copy_project_light(src: Path, dst: Path) -> int:
    if not dst.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
    excluded_dirs = EXCLUDED_SNAPSHOT_DIRS | {"build", "dist"}
    excluded_ext = {
        ".bin",
        ".pt",
        ".pth",
        ".onnx",
        ".safetensors",
        ".tar",
        ".gz",
        ".zip",
        ".egg",
    }
    max_file_size = 50 * 1024 * 1024
    copied = 0
    for item in src.iterdir():
        if item.is_dir():
            if item.name not in excluded_dirs:
                copied += copy_project_light(item, dst / item.name)
        else:
            if item.suffix.lower() not in excluded_ext:
                try:
                    if item.stat().st_size <= max_file_size:
                        _ = shutil.copy2(item, dst / item.name)
                        copied += 1
                except Exception:
                    pass
    return copied


def symlink_large_files(project_dir: Path, source_dir: Path) -> int:
    symlinked = 0
    for item in source_dir.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source_dir)
        if any(part in EXCLUDED_SNAPSHOT_DIRS for part in relative.parts):
            continue
        target = project_dir / relative
        if target.exists():
            continue
        if item.suffix.lower() in {
            ".bin",
            ".pt",
            ".pth",
            ".onnx",
            ".safetensors",
            ".tar",
            ".gz",
            ".zip",
            ".egg",
        }:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            symlinked += 1
        elif item.stat().st_size > 50 * 1024 * 1024:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            symlinked += 1
    return symlinked


def _drop_legacy_copy_artifacts_hooks(workflow: WorkflowDefinition) -> None:
    """Drop legacy ``copy_artifacts`` entries from the ``workflow_end`` hooks.

    V3-only repair: EvidencePersister is the sole artifact writer, so the
    legacy builtin would double-write ``.sm-artifacts`` and the no-replace V3
    copy then raises FileExistsError. Only the ``workflow_end`` list is
    touched, only when a legacy entry is present; survivors keep their
    relative order and object identity. Missing or empty hook point: no-op.
    """
    hooks = workflow.hooks.get("workflow_end")
    if not hooks:
        return
    filtered = [hook for hook in hooks if hook.operation != "copy_artifacts"]
    if len(filtered) != len(hooks):
        hooks[:] = filtered


def _format_selector_result_log(
    selector_path: str,
    selected_path: str,
    materialized_path: str,
) -> str:
    return (
        "Workflow selector result: "
        f"selector={selector_path} "
        f"selected={selected_path} "
        f"materialized={materialized_path}"
    )


def print_summary(
    summary: RunSummary,
    *,
    finalization_failed: bool = False,
    runtime_report: V3RuntimeReport | None = None,
) -> None:
    if finalization_failed:
        print()
        print(colorize("E2E FINALIZATION FAILED", Ansi.RED))
        print(f"- Output dir: {summary.output_dir}")
        return
    headline = colorize(
        f"E2E {summary.overall_status.value}",
        Ansi.GREEN if summary.overall_status == "PASS" else Ansi.RED,
    )
    print()
    print(headline)
    print(f"- Output dir: {summary.output_dir}")
    print(f"- Temp dir: {summary.temp_dir}{' (kept)' if summary.keep_temp_dir else ''}")
    print(f"- Workflow: {summary.workflow_path}")
    print(f"- Sessions: {summary.session_count}")
    print(f"- Commands: {summary.command_count}")
    print(f"- Total duration: {summary.total_duration_seconds:.2f}s")
    if summary.entry_script:
        print(f"- Entry script: {summary.entry_script}")
    if summary.artifact_dir:
        print(f"- Copied artifacts: {summary.artifact_dir}")
    print("- Agent trace:")
    print(f"  - Requested: {'yes' if summary.trace.requested else 'no'}")
    print(f"  - Enabled: {'yes' if summary.trace.enabled else 'no'}")
    print(f"  - Complete: {'yes' if summary.trace.complete else 'no'}")
    print(f"  - Path: {summary.trace.path or 'unavailable'}")
    print("  - Errors:")
    if summary.trace.errors:
        for trace_error in summary.trace.errors:
            print(f"    - {trace_error}")
    else:
        print("    - none")
    print("- Phase timings:")
    for phase in summary.phases:
        suffix = f" - {phase.error}" if phase.error else ""
        print(
            f"  - {phase.phase_id}: {phase.status.upper()} ({phase.duration_seconds:.2f}s){suffix}"
        )
    if summary.errors:
        print("- Errors:")
        for error in summary.errors:
            print(f"  - {error}")
    if runtime_report is not None:
        print("- Runtime resources:")
        print_runtime_report(runtime_report)


def write_usage_guide(
    project_dir: Path,
    *,
    entry_script: str | None,
    overall_status: str,
    output_dir: Path,
    ui_events_path: str | None = None,
) -> str:
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    usage_path = reports_dir / "USAGE.md"
    command = entry_script or "<entry command unavailable; inspect summary.json>"
    status_text = (
        "E2E TEST PASSED"
        if overall_status == "PASS"
        else "Migration did not fully pass validation"
    )
    reports_entries = [
        "- `migration_reports/USAGE.md`",
        "- `migration_reports/SUMMARY_REPORT.md`",
        "- `.sm-artifacts/`",
    ]
    if ui_events_path is not None:
        reports_entries.append(f"- `{ui_events_path}`")
    content = "\n".join(
        [
            "# Migrated Project Usage",
            "",
            f"Status: {status_text}",
            "",
            "## Project",
            "",
            f"- Migrated project: `{project_dir}`",
            f"- SEAM run reports: `{output_dir}`",
            "",
            "## Run",
            "",
            "```bash",
            f"cd {project_dir}",
            "source .venv/bin/activate  # if .venv exists",
            command,
            "```",
            "",
            "## Reports",
            "",
            *reports_entries,
            "",
            "## If Validation Failed",
            "",
            "Review `.sm-artifacts/`, `summary.json`, and `traceback.txt` if present, then rerun SEAM with a higher `--max-iter` or the missing external resource prepared.",
            "",
        ]
    )
    usage_path.write_text(content, encoding="utf-8")
    return str(usage_path)


def build_v3_summary(
    *,
    authoritative_outcome: RunOutcome,
    run_id: str,
    base_url: str,
    workflow_path: str,
    output_dir: str,
    temp_dir: str,
    keep_temp_dir: bool,
    max_phase5_iter: int,
    phase_results: list[PhaseStatus],
    session_count: int,
    command_count: int,
    total_duration_seconds: float,
    artifact_dir: str | None,
    telemetry_paths: dict[str, str],
    before_snapshot_path: str | None,
    after_snapshot_path: str | None,
    entry_script: str | None,
    errors: list[str],
) -> RunSummary:
    request = RunFinalizationRequest(
        identity=RunIdentity(
            run_id=RunId(run_id),
            base_url=base_url,
            workflow_path=workflow_path,
            output_dir=output_dir,
            temp_dir=temp_dir,
        ),
        execution=RunExecution(
            keep_temp_dir=keep_temp_dir,
            requested_max_phase5_iter=max_phase5_iter,
            effective_max_phase5_iter=max_phase5_iter,
            phases=tuple(phase_results),
            session_count=session_count,
            command_count=command_count,
            total_duration_seconds=total_duration_seconds,
            errors=tuple(errors),
        ),
        initial_artifacts=RunArtifacts(
            artifact_dir=artifact_dir,
            telemetry_paths=tuple(telemetry_paths.items()),
            before_snapshot_path=before_snapshot_path,
            after_snapshot_path=after_snapshot_path,
            entry_script=entry_script,
        ),
        hooks=FinalizationHooks.empty(),
        authoritative_outcome=authoritative_outcome,
    )
    return build_run_summary(request)


def _build_project_context(project_dir: Path) -> dict[str, object]:
    project_dir = project_dir.resolve()
    setup_py = project_dir / "setup.py"
    setup_cfg = project_dir / "setup.cfg"
    pyproject_toml = project_dir / "pyproject.toml"
    build_system = ""
    if setup_py.exists() or setup_cfg.exists():
        build_system = "setuptools"
    elif pyproject_toml.exists():
        build_system = "pyproject"
    return {
        "project_path": str(project_dir),
        "project_name": project_dir.name,
        "language": "Python",
        "file_count": sum(1 for path in project_dir.rglob("*") if path.is_file()),
        "build_system": build_system,
    }


def run_terminal_continuation(
    request: TerminalContinuationRunRequest,
    *,
    dashboard_mode: str = "auto",
    dashboard_backend: str = "auto",
) -> int:
    from core.continuation import ContinuationError, ContinuationHydrationError
    from core.continuation_environment import ContinuationEnvironmentError
    from core.continuation_evidence import ContinuationEvidenceError
    from core.terminal_continuation import prepare_terminal_continuation
    from core.terminal_continuation_models import TerminalContinuationError

    child_run_id = f"e2e-v3-{uuid4().hex[:12]}"
    try:
        with prepare_terminal_continuation(
            request.summary_path,
            child_run_id,
        ) as continuation:
            return run_e2e_v3(
                base_url=request.server.base_url,
                max_phase5_iter=request.review.max_phase5_iter,
                keep_temp_dir=request.invocation.keep_temp_dir,
                agent_name=request.invocation.agent_name,
                project_dir=None,
                user_constraints=request.invocation.user_constraints,
                server_auto_start=request.server.auto_start,
                server_port=request.server.port,
                review_gate=request.review.enabled,
                review_policy_overrides=request.review.overrides,
                framework_config_path=request.invocation.framework_config_path,
                workflow_path=None,
                opencode_readiness=request.opencode.readiness,
                opencode_message_timeout=request.opencode.message_timeout,
                continuation=continuation,
                container_retention=request.invocation.container_retention,
                save_agent_trace=request.invocation.save_agent_trace,
                dashboard_mode=dashboard_mode,
                dashboard_backend=dashboard_backend,
            )
    except (
        ContinuationError,
        ContinuationHydrationError,
        ContinuationEnvironmentError,
        ContinuationEvidenceError,
        TerminalContinuationError,
        OSError,
        ValueError,
    ) as exc:
        print(colorize(f"E2E CONTINUATION FAILED: {exc}", Ansi.RED), file=sys.stderr)
        return 1


def _prepare_dashboard_wiring(
    dashboard_mode: str,
    output_dir: Path,
    run_id: str,
    *,
    is_tty: bool,
    environ: MutableMapping[str, str],
    dashboard_backend: str = "auto",
) -> DashboardWiring:
    """Create the live-dashboard event pipeline, or nothing when it is off.

    Off mode (explicit ``off``, or ``auto`` without an interactive TTY) is
    fully inert: no sink, no ``SEAM_UI_EVENTS_PATH``/``SEAM_RUN_ID``
    environment variables, no events, no dashboard thread. After mode/TTY/CI
    enablement the renderer backend is probed once: textual is preferred,
    rich is the fallback, and explicit ``on`` with neither raises a typed
    actionable error before any side effect. ``auto`` with neither renderer
    returns the same fully inert wiring as ``off``.

    For an active wiring the returned ``DashboardWiring`` captures the exact
    prior values of the two dashboard env keys so its ``close()`` can
    restore or delete them idempotently from any exit path.
    """
    from core.dashboard import (
        DashboardBackend,
        DashboardBackendUnavailableError,
        SeamDashboardApp,
        _activate_dashboard_backend,
        resolve_dashboard_backend,
    )
    from core.ui_events import UIEventSink, dashboard_enabled

    if not dashboard_enabled(dashboard_mode, is_tty=is_tty, environ=environ):
        return DashboardWiring(None, None, None, False)
    if dashboard_backend == "textual":
        backend = DashboardBackend.TEXTUAL
    elif dashboard_backend == "rich":
        backend = DashboardBackend.RICH
    else:
        backend = resolve_dashboard_backend()
    if backend is DashboardBackend.NONE:
        normalized_mode = str(dashboard_mode or "auto").strip().lower()
        if normalized_mode == "on":
            raise DashboardBackendUnavailableError()
        return DashboardWiring(None, None, None, False)
    _activate_dashboard_backend(backend)
    ui_event_sink = UIEventSink(output_dir, run_id)
    prior_env = {
        key: environ[key]
        for key in ("SEAM_UI_EVENTS_PATH", "SEAM_RUN_ID")
        if key in environ
    }
    environ.update(ui_event_sink.as_env())
    ui_event_sink.emit(
        "runner_started",
        status="running",
        message="SEAM migration run started",
        details={"dashboard_mode": dashboard_mode},
    )
    dashboard_stop = threading.Event()
    dashboard_app = SeamDashboardApp(
        ui_event_sink.path, dashboard_stop, backend=backend
    )
    dashboard_thread = threading.Thread(
        target=dashboard_app.run,
        daemon=True,
    )
    dashboard_thread.start()
    return DashboardWiring(
        ui_event_sink,
        dashboard_stop,
        dashboard_thread,
        True,
        environ=environ,
        prior_env=prior_env,
    )


def run_e2e_v3(
    *,
    base_url: str | None,
    max_phase5_iter: int,
    keep_temp_dir: bool,
    agent_name: str | None,
    project_dir: Path | None,
    output_project_dir: Path | None = None,
    user_constraints: str = "",
    server_auto_start: bool = True,
    server_port: int = 0,
    review_gate: bool = False,
    review_policy_overrides: ReviewCliOverrides | None = None,
    framework_config_path: str | None = None,
    workflow_path: Path | None = None,
    opencode_readiness: str = "message",
    opencode_message_timeout: int = 120,
    continuation: PreparedTerminalContinuation | None = None,
    container_retention: ContainerRetention = ContainerRetention.RETAIN,
    save_agent_trace: bool | None = None,
    dashboard_mode: str = "auto",
    dashboard_backend: str = "auto",
    seal_manifest: bool = False,
    verbose: bool = False,
) -> int:
    from core.agent_io_logger import AgentIOLogger
    from core.artifact_store import ArtifactStore
    from core.config import load_workflow
    from core.config_loader import load_framework_config
    from core.paths import default_output_projects_root
    from core.prompt_loader import PromptLoader
    from core.review_policy import (
        ReviewPolicyInputs,
        apply_review_policy,
        framework_review_defaults,
        resolve_review_policy,
        workflow_review_defaults,
    )
    from core.runtime_observability_models import (
        EMPTY_OBSERVABILITY_SUMMARY,
        ObservabilitySummary,
    )
    from core.telemetry_bridge import TelemetryBridge
    from core.validator_engine import ValidatorEngine
    from core.workflow_executor import WorkflowExecutor
    from core.workflow_selector import (
        is_selector_file,
        read_selector_resolution_metadata,
        resolve_workflow_from_selector,
    )
    from harness.session.manager import SessionManager
    from tests.e2e.e2e_observer import TelemetryObserver, TelemetryObserverConfig
    from validators.validate_env_detect import validate as validate_env_detect
    from validators.validate_project_analysis import (
        validate as validate_project_analysis,
    )
    from validators.validate_venv import validate as validate_venv
    from validators.validate_entry_script import validate as validate_entry_script
    from validators.validate_entry_static import validate as validate_entry_static
    from validators.validate_rule_migration import validate as validate_rule_migration
    from validators.validate_validation_final import (
        validate as validate_validation_final,
    )
    from validators.validate_reports import validate as validate_reports
    from validators.validate_constraint_summary import (
        validate as validate_constraint_summary,
    )

    started_at = datetime.now(timezone.utc)
    if continuation is None:
        run_id = RunId(f"e2e-v3-{uuid4().hex[:12]}")
        try:
            output_dir = allocate_report_directory(OUTPUT_ROOT, run_id)
        except ReportAllocationError as exc:
            print(colorize(f"E2E FAILED: {exc}", Ansi.RED), file=sys.stderr)
            return 1
    else:
        run_id = RunId(continuation.evidence.request.continuation.child_run_id)
        output_dir = continuation.evidence.namespace.report_dir

    effective_workflow_path = (
        continuation.parent.workflow_path
        if continuation is not None
        else workflow_path
        if workflow_path
        else _default_workflow_path
    )

    server_proc: subprocess.Popen[bytes] | None = None
    temp_dir: Path | None = None
    temp_dir_identity: DirectoryLockIdentity | None = None
    before_snapshot_path: str | None = None
    entry_script: str | None = None
    errors: list[str] = []
    phase_results: list[PhaseStatus] = []
    observer: TelemetryObserver | None = None
    session_mgr = None
    telemetry_bridge: TelemetryBridge | None = None
    agent_io_logger: AgentIOLogger | None = None
    traceback_text: str | None = None
    retention_cleanup: ContainerRetentionFinalizer | None = None
    retention_manifest: RetentionManifestFinalizer | None = None
    retention_backend = None
    resource_store = None
    artifact_store = None
    executor = None
    retention_manifest_error: OSError | ResourceManifestError | None = None
    continuation_evidence_sealed = False
    authoritative_outcome = build_v3_run_outcome(
        V3RunFacts(
            executed_phases=(),
            workflow_terminal=WorkflowTerminal("interrupted"),
            phase5_decision=None,
        )
    )
    dashboard_wiring = _prepare_dashboard_wiring(
        dashboard_mode,
        output_dir,
        str(run_id),
        is_tty=sys.stdout.isatty(),
        environ=os.environ,
        dashboard_backend=dashboard_backend,
    )
    ui_event_sink = dashboard_wiring.ui_event_sink

    try:
        try:
            from harness.server.lifecycle import resolve_server_url

            base_url, server_proc = resolve_server_url(
                base_url,
                auto_start=server_auto_start,
                default_url=DEFAULT_SERVER_URL,
                work_dir=str(REPO_ROOT),
                server_port=server_port,
            )
            if server_proc is not None:
                log(f"Auto-started OpenCode server at {base_url}")
            if ui_event_sink is not None:
                ui_event_sink.emit(
                    "phase_started",
                    phase_id="init_server_check",
                    message="Checking OpenCode server readiness...",
                )
            check_server_running(
                base_url,
                readiness_mode=opencode_readiness,
                message_timeout=opencode_message_timeout,
                verbose=verbose,
            )
            log(f"OpenCode server ready at {base_url}")
            if ui_event_sink is not None:
                ui_event_sink.emit(
                    "phase_finished",
                    phase_id="init_server_check",
                    status="passed",
                    message=f"OpenCode server ready at {base_url}",
                )
            if verbose:
                log_server_diagnostics(base_url, server_proc, str(REPO_ROOT))
            if continuation is not None:
                temp_dir = continuation.parent.output_project
                keep_temp_dir = True
                log(f"Continuing in lineage-shared project: {temp_dir}")
            elif project_dir is not None:
                project_name = project_dir.resolve().name
                timestamp = started_at.strftime("%Y%m%d_%H%M%S")
                output_project_base = (
                    output_project_dir
                    if output_project_dir
                    else default_output_projects_root()
                )
                output_project_base.mkdir(parents=True, exist_ok=True)
                dest = output_project_base / f"{project_name}_{timestamp}"
                if ui_event_sink is not None:
                    ui_event_sink.emit(
                        "phase_started",
                        phase_id="init_project_copy",
                        message=f"Copying project {project_dir} to {dest}...",
                    )
                log(f"Copying project {project_dir} to {dest}...")
                copied_count = copy_project_light(project_dir, dest)
                symlinked_count = symlink_large_files(dest, project_dir)
                temp_dir = dest.resolve()
                log(
                    f"Copied {copied_count} files, symlinked {symlinked_count} large files to {temp_dir}"
                )
                if ui_event_sink is not None:
                    ui_event_sink.emit(
                        "phase_finished",
                        phase_id="init_project_copy",
                        status="passed",
                        message=f"Copied {copied_count} files, symlinked {symlinked_count} large files",
                    )
                keep_temp_dir = True
            else:
                temp_dir = Path(tempfile.mkdtemp(prefix="migration-utils-e2e-v3-"))
                temp_dir_identity = directory_lock_identity(temp_dir, retain=True)
                if TEMPLATE_DIR.exists():
                    _ = shutil.copytree(TEMPLATE_DIR, temp_dir, dirs_exist_ok=True)
                log(f"Created temp dir: {temp_dir}")

            if continuation is not None:
                before_snapshot_path = str(continuation.evidence.namespace.baseline_path)
            else:
                before_snapshot = persist_python_snapshot(
                    temp_dir, output_dir / "before_snapshot.json"
                )
                before_snapshot_path = before_snapshot.path
                log(f"Snapshot: {before_snapshot.file_count} .py files")

            agent_io_logger = AgentIOLogger.from_env(output_dir, str(run_id))
            observed_session = TelemetryObserver.create_observed_session(
                lambda transport_observer: SessionManager(
                    work_dir=str(temp_dir),
                    base_url=base_url,
                    transport_observer=transport_observer,
                ),
                TelemetryObserverConfig(
                    output_dir=output_dir,
                    run_id=str(run_id),
                    agent_io_logger=agent_io_logger,
                    ui_event_sink=ui_event_sink,
                ),
            )
            session_mgr = observed_session.session_manager
            observer = observed_session.observer
            if agent_name:
                try:
                    canonical = session_mgr.override_agent(agent_name)
                except ValueError as exc:
                    raise RuntimeError(
                        f"Cannot use --agent '{agent_name}': {exc}. "
                        f"Use one of the canonical names from /agent or ensure the server is running."
                    ) from exc
            else:
                canonical = session_mgr.active_agent
            log(
                f"SessionManager created: active_agent={canonical}, overridden={agent_name is not None}"
            )

            observer.set_metadata("base_url", base_url)
            observer.set_metadata("review_gate", review_gate)

            artifact_store = (
                continuation.evidence.artifact_store
                if continuation is not None
                else ArtifactStore(str(temp_dir), str(run_id))
            )
            prompt_loader = PromptLoader()

            # ── Workflow Selector resolution (before load_workflow) ──────────
            original_workflow_path = effective_workflow_path
            selector_resolved_path: str | None = None
            try:
                if continuation is None and is_selector_file(str(effective_workflow_path)):
                    log(f"Detected selector YAML: {effective_workflow_path}")
                    project_ctx = _build_project_context(temp_dir)
                    materialized = resolve_workflow_from_selector(
                        str(effective_workflow_path),
                        observer,  # observer.send_command logs command + response automatically
                        prompt_loader,
                        project_context=project_ctx,
                        user_constraints=user_constraints,
                        output_dir=output_dir / "artifacts",
                        telemetry=observer,  # selector-specific events via record_event
                    )
                    effective_workflow_path = materialized
                    selector_resolved_path = str(materialized)
                    selector_metadata = (
                        read_selector_resolution_metadata(materialized) or {}
                    )
                    selected_workflow_path = selector_metadata.get(
                        "selected_path", "(unknown)"
                    )
                    selector_path_for_log = selector_metadata.get(
                        "selector_path", str(original_workflow_path)
                    )
                    materialized_path_for_log = selector_metadata.get(
                        "materialized_path", str(materialized)
                    )
                    log(
                        _format_selector_result_log(
                            selector_path_for_log,
                            selected_workflow_path,
                            materialized_path_for_log,
                        )
                    )
                    observer.set_metadata("selector_path", str(original_workflow_path))
                    observer.set_metadata("selected_workflow_path", selected_workflow_path)
                    observer.set_metadata("resolved_workflow_path", selector_resolved_path)
            except Exception:
                log("Selector resolution failed; re-raising to surface the error")
                raise

            validator = ValidatorEngine()
            validator.register_validator("env_detect", validate_env_detect)
            validator.register_validator("project_analysis", validate_project_analysis)
            validator.register_validator("venv", validate_venv)
            validator.register_validator("entry_script", validate_entry_script)
            validator.register_validator("entry_static", validate_entry_static)
            validator.register_validator("rule_migration", validate_rule_migration)
            validator.register_validator("validation_final", validate_validation_final)
            validator.register_validator("reports", validate_reports)
            validator.register_validator("constraint_summary", validate_constraint_summary)
            validator.register_validator(
                "repair_classification",
                lambda d: {"passed": True, "errors": [], "warnings": []},
            )

            workflow = (
                continuation.workflow
                if continuation is not None
                else load_workflow(str(effective_workflow_path))
            )
            _drop_legacy_copy_artifacts_hooks(workflow)
            log(
                f"Workflow loaded: {workflow.name} v{workflow.version} from {effective_workflow_path}"
            )

            raw_backend_mode = (
                workflow.execution_backend.mode
                if workflow.execution_backend is not None
                else "local"
            )
            if raw_backend_mode == "auto":
                requested_backend = "auto"
            elif raw_backend_mode == "container":
                requested_backend = "container"
            else:
                requested_backend = "local"
            retention_policy = resolve_v3_container_retention(
                workflow,
                container_retention,
                str(run_id),
                continuation.eligibility if continuation is not None else None,
            )

            framework_config = load_framework_config(framework_config_path)
            effective_review_policy = resolve_review_policy(
                ReviewPolicyInputs(
                    cli=(
                        review_policy_overrides
                        if review_policy_overrides is not None
                        else ReviewCliOverrides(max_iterations=None, fail_closed=None)
                    ),
                    workflow=workflow_review_defaults(workflow),
                    framework=framework_review_defaults(framework_config),
                )
            )
            apply_review_policy(workflow, effective_review_policy)
            log(
                "Review policy resolved: "
                f"max={effective_review_policy.max_iterations} "
                f"fail_closed={effective_review_policy.fail_closed}"
            )
            observer.set_metadata(
                "max_review_iterations", int(effective_review_policy.max_iterations)
            )
            observer.set_metadata("review_fail_closed", effective_review_policy.fail_closed)

            if isinstance(workflow.globals, dict):
                workflow.globals["max_repair_iterations"] = max_phase5_iter
                workflow.globals["review_gate_enabled"] = review_gate

            telemetry_bridge = TelemetryBridge(str(output_dir))

            experience_store = None
            if workflow.experience.enabled:
                from core.experience_store import ExperienceStore

                experience_store = ExperienceStore(str(REPO_ROOT))

            execution_user_constraints = user_constraints
            if continuation is not None:
                continuation_context = continuation.prompt_facts.render()
                execution_user_constraints = (
                    f"{user_constraints}\n\n{continuation_context}"
                    if user_constraints
                    else continuation_context
                )

            executor = WorkflowExecutor(
                workflow=workflow,
                session_mgr=observer,
                artifact_store=artifact_store,
                prompt_loader=prompt_loader,
                validator_engine=validator,
                telemetry_observer=observer,
                framework_config=framework_config,
                project_dir=str(temp_dir),
                output_dir=str(output_dir),
                user_constraints=execution_user_constraints,
                telemetry_bridge=telemetry_bridge,
                experience_store=experience_store,
                continuation=(continuation.hydration if continuation is not None else None),
                container_delete_authority=retention_policy.delete_authority,
                defer_execution_backend_cleanup=True,
                defer_execution_backend_preflight=True,
                ui_event_sink=ui_event_sink,
            )

            retention_backend = (
                executor.exec_backend
                if isinstance(executor.exec_backend, ContainerBackend)
                else None
            )
            retention_recorder = RetentionLifecycleRecorder()
            retention_cleanup = ContainerRetentionFinalizer(
                retention_policy,
                retention_backend,
                temp_dir,
                retention_recorder,
                lambda: continuation_evidence_sealed,
            )
            try:
                executor._preflight_execution_backend()
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
                retention_manifest_error = ResourceManifestError(
                    ResourceManifestErrorKind.WRITE_INTERRUPTED,
                    f"backend preflight failed before manifest setup: {exc}",
                )
                raise
            try:
                resource_store = create_retention_manifest(
                    RetentionManifestRequest(
                        report_dir=output_dir,
                        run_id=str(run_id),
                        requested_workflow=Path(original_workflow_path),
                        effective_workflow=Path(effective_workflow_path),
                        workspace=temp_dir,
                        requested_backend=requested_backend,
                        endpoint=base_url,
                        server_process_id=(
                            server_proc.pid if server_proc is not None else None
                        ),
                        policy=retention_policy,
                        backend=retention_backend,
                    )
                )
                retention_manifest = RetentionManifestFinalizer(
                    resource_store,
                    retention_recorder,
                    retention_backend,
                )
            except (OSError, ResourceManifestError) as exc:
                retention_manifest_error = exc
            retention_cleanup = ContainerRetentionFinalizer(
                retention_policy,
                retention_backend,
                temp_dir,
                retention_recorder,
                lambda: continuation_evidence_sealed,
            )

            execution_result = executor.execute(
                {
                    "PROJECT_DIR": str(temp_dir),
                    "USER_CONSTRAINTS": execution_user_constraints,
                }
            )
            outcome_value = execution_result.get("run_outcome")
            if not isinstance(outcome_value, RunOutcome):
                raise V3OutcomeUnavailableError(
                    detail="active V3 executor did not return a frozen RunOutcome"
                )
            authoritative_outcome = outcome_value

            telemetry_bridge.set_metadata("agent_name", agent_name)
            telemetry_bridge.set_metadata("workflow_name", workflow.name)
            telemetry_bridge.set_metadata("workflow_version", workflow.version)

            projection = project_workflow_result(
                tuple(phase.id for phase in executor.workflow.phases or ()),
                executor.phase_results,
                executor.state,
            )
            phase_results.extend(projection.phases)
            entry_script = projection.entry_script

        except Exception as exc:
            errors.append(f"{exc.__class__.__name__}: {exc}")
            traceback_text = traceback.format_exc()
            if observer is not None:
                observer.record_event(
                    "runner_error", error=str(exc), traceback=traceback_text
                )
            if ui_event_sink is not None:
                ui_event_sink.emit(
                    "runner_failed",
                    status="failed",
                    message=f"{exc.__class__.__name__}: {exc}",
                )

        telemetry = build_telemetry_sidecars(
            V3TelemetrySources(
                observer=observer,
                bridge=telemetry_bridge,
                agent=agent_io_logger,
                keep_temp_dir=keep_temp_dir,
                bridge_command_count=(
                    (lambda: len(telemetry_bridge._commands))
                    if telemetry_bridge is not None
                    else None
                ),
            )
        )
        lifecycle = V3RunLifecycle(
            evidence=EvidencePersister(
                EvidenceContext(
                    output_dir=output_dir,
                    temp_dir=temp_dir if continuation is None else None,
                    traceback_text=traceback_text,
                    phase_results=tuple(phase_results),
                ),
                telemetry,
                log,
            ),
            cleanup=ResourceCleanup(
                CleanupContext(
                    temp_dir=temp_dir,
                    keep_temp_dir=(
                        keep_temp_dir
                        or (
                            retention_cleanup is not None
                            and retention_cleanup.policy.effective
                            is ContainerRetention.RETAIN
                        )
                    ),
                    owns_temp_dir=continuation is None and project_dir is None,
                    observer=telemetry.observer,
                    server_process=server_proc,
                    owned_temp_identity=temp_dir_identity,
                )
            ),
            telemetry=telemetry,
            started_at=started_at,
        )
        counts = lifecycle.counts()
        finalization_hooks = lifecycle.hooks()

        if ui_event_sink is not None:
            ui_events_jsonl_path = str(ui_event_sink.path)

            def record_ui_events_sidecar(_outcome: TerminalOutcome) -> RunArtifactUpdate:
                return RunArtifactUpdate(
                    telemetry_paths=(("ui_events_jsonl", ui_events_jsonl_path),)
                )

            finalization_hooks = replace(
                finalization_hooks,
                evidence_replay=compose_trace_hooks(
                    finalization_hooks.evidence_replay,
                    record_ui_events_sidecar,
                ),
            )
        trace_destination = (
            continuation.evidence.namespace.trace_dir
            if continuation is not None
            else output_dir / "trace"
        )

        def trace_observability_source() -> ObservabilitySummary:
            return (
                observer.observability_summary
                if observer is not None
                else EMPTY_OBSERVABILITY_SUMMARY
            )

        trace_lifecycle = create_v3_trace_lifecycle(
            V3TraceIntegrationRequest(
                cli_value=save_agent_trace,
                destination=trace_destination,
                session=session_mgr,
                overflow_roots=(temp_dir,) if temp_dir is not None else (),
                telemetry=observer,
                correlation_inputs=V3TraceCorrelationInputs(
                    run_id=run_id,
                    outcome=authoritative_outcome,
                    observability_source=trace_observability_source,
                    continuation=continuation,
                ),
            )
        )
        finalization_hooks = replace(
            finalization_hooks,
            trace_export=compose_trace_hooks(
                trace_lifecycle,
                finalization_hooks.trace_export,
            ),
        )
        phase2_environment = None
        if executor is not None:
            phase2_value = executor.state.get("phase_2_venv_create")
            if isinstance(phase2_value, dict):
                try:
                    phase2_report = Phase2EnvironmentReport.model_validate(phase2_value)
                except ValidationError:
                    phase2_report = None
                if phase2_report is not None:
                    phase2_environment = Phase2EnvironmentRequest(
                        environment_id="phase2-project-venv",
                        namespace=(
                            f"container:{retention_backend.container_id}"
                            if retention_backend is not None
                            and retention_backend.container_id is not None
                            else "host"
                        ),
                        container_id=(
                            retention_backend.container_id
                            if retention_backend is not None
                            else None
                        ),
                        report=phase2_report,
                    )
        runtime_report_request = prepare_runtime_report_request(
            RuntimeReportingInputs(
                manifest_store=resource_store,
                artifact_store=artifact_store,
                outcome=authoritative_outcome,
                expected_run_id=str(run_id),
                phase2_environment=phase2_environment,
            )
        )
        runtime_report_recorder = None
        if observer is not None:
            runtime_report_recorder = V3RuntimeReportRecorder(
                runtime_report_request,
                observer,
                finalization_hooks.post_cleanup_manifest,
            )
            finalization_hooks = FinalizationHooks(
                evidence_replay=finalization_hooks.evidence_replay,
                trace_export=finalization_hooks.trace_export,
                authorized_cleanup=finalization_hooks.authorized_cleanup,
                post_cleanup_manifest=runtime_report_recorder,
            )
        if retention_cleanup is not None:
            finalization_hooks = _compose_v3_retention_hooks(
                finalization_hooks,
                lifecycle.cleanup,
                retention_cleanup,
                retention_manifest,
                retention_manifest_error,
            )
        continuation_summary = None
        if continuation is not None:
            from core.continuation_evidence import (
                seal_child_evidence,
                verify_final_child_evidence,
            )

            def seal_and_verify_child(_outcome: TerminalOutcome) -> RunArtifactUpdate:
                nonlocal continuation_evidence_sealed
                _ = seal_child_evidence(
                    continuation.evidence,
                    terminal_anchor=authoritative_outcome.terminal_anchor,
                )
                _ = verify_final_child_evidence(continuation.evidence)
                continuation_evidence_sealed = True
                return RunArtifactUpdate()

            finalization_hooks = FinalizationHooks(
                evidence_replay=finalization_hooks.evidence_replay,
                trace_export=compose_trace_hooks(
                    finalization_hooks.trace_export,
                    seal_and_verify_child,
                ),
                authorized_cleanup=finalization_hooks.authorized_cleanup,
                post_cleanup_manifest=finalization_hooks.post_cleanup_manifest,
            )
            continuation_summary = ContinuationRunSummary(
                parent_run_id=continuation.prompt_facts.parent_run_id,
                anchor_phase_id=continuation.prompt_facts.anchor_phase_id,
                inherited_phase_ids=continuation.prompt_facts.inherited_phase_ids,
                resource_eligibility=continuation.prompt_facts.resource_eligibility,
                attachment_mode=continuation.prompt_facts.attachment_mode,
            )

        finalization = finalize_run(
            RunFinalizationRequest(
                identity=RunIdentity(
                    run_id=run_id,
                    base_url=base_url or DEFAULT_SERVER_URL,
                    workflow_path=str(effective_workflow_path),
                    output_dir=str(output_dir),
                    temp_dir=str(temp_dir or ""),
                ),
                execution=RunExecution(
                    keep_temp_dir=keep_temp_dir,
                    requested_max_phase5_iter=max_phase5_iter,
                    effective_max_phase5_iter=max_phase5_iter,
                    phases=tuple(phase_results),
                    session_count=counts.session_count,
                    command_count=counts.command_count,
                    total_duration_seconds=0.0,
                    errors=tuple(errors),
                    duration_source=lifecycle.elapsed_seconds,
                ),
                initial_artifacts=RunArtifacts(
                    before_snapshot_path=before_snapshot_path,
                    entry_script=entry_script,
                ),
                hooks=finalization_hooks,
                authoritative_outcome=authoritative_outcome,
                observability=(
                    observer.observability_summary
                    if observer is not None
                    else EMPTY_OBSERVABILITY_SUMMARY
                ),
                continuation=continuation_summary,
                required_stages=frozenset(
                    (
                        {FinalizationStage.TRACE_EXPORT}
                        if continuation is not None
                        else set()
                    )
                    | (
                        {FinalizationStage.POST_CLEANUP_MANIFEST}
                        if retention_manifest is not None
                        or retention_manifest_error is not None
                        else set()
                    )
                ),
                summary_required=continuation is not None,
                runtime_report_source=(
                    runtime_report_recorder.read
                    if runtime_report_recorder is not None
                    else None
                ),
                trace_status_source=trace_lifecycle.read,
            )
        )
        if temp_dir is not None and ui_event_sink is not None:
            usage_path = write_usage_guide(
                temp_dir,
                entry_script=entry_script,
                overall_status=finalization.summary.overall_status.value,
                output_dir=output_dir,
                ui_events_path=str(ui_event_sink.path),
            )
            ui_event_sink.emit(
                "usage_guide_written",
                status="passed",
                message="Usage guide written",
                artifact_path=usage_path,
            )
        print_summary(
            finalization.summary,
            finalization_failed=finalization.finalization_failed,
            runtime_report=finalization.runtime_report,
        )
        if ui_event_sink is not None:
            ui_event_sink.emit(
                "runner_finished",
                status=finalization.summary.overall_status.value.lower(),
                message=f"E2E {finalization.summary.overall_status.value}",
                details={"summary_path": str(output_dir / "summary.json")},
            )
        # Sealing runs after the headline/exit code freeze so any sealing
        # fault stays outcome-neutral (never mutates RunOutcome/exit code).
        frozen_exit_code = finalization.exit_code
        run_direct_manifest_sealing(
            seal_requested=seal_manifest,
            is_continuation=continuation is not None,
            report_dir=output_dir,
            run_id=str(run_id),
            project_dir=temp_dir,
            workflow_path=effective_workflow_path,
            artifact_store=artifact_store,
            terminal_anchor=authoritative_outcome.terminal_anchor,
            summary_path=(
                Path(finalization.summary_path)
                if finalization.summary_path is not None
                else None
            ),
        )
        return frozen_exit_code
    finally:
        dashboard_wiring.close()


class _SingleContainerRetentionAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[str] | None,
        option_string: str | None = None,
    ) -> None:
        del option_string
        if getattr(namespace, "_container_retention_seen", False):
            parser.error("--container-retention may be supplied only once")
        if not isinstance(values, str):
            parser.error("--container-retention requires one value")
        setattr(namespace, self.dest, values)
        setattr(namespace, "_container_retention_seen", True)


class _ContinueFromAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del option_string
        if getattr(namespace, "_seal_manifest_seen", False):
            parser.error(
                "--continue-from is not valid with --seal-manifest; a "
                "continuation child must consume the parent's already-sealed "
                "evidence rather than re-seal its own root manifest."
            )
        setattr(namespace, self.dest, values)
        setattr(namespace, "_continue_from_seen", True)


class _SealManifestAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del values, option_string
        if getattr(namespace, "_continue_from_seen", False):
            parser.error(
                "--seal-manifest is not valid with --continue-from; a "
                "continuation child must consume the parent's already-sealed "
                "evidence rather than re-seal its own root manifest."
            )
        setattr(namespace, self.dest, True)
        setattr(namespace, "_seal_manifest_seen", True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run YAML-driven migration_utils E2E migration workflow (V3 — supports custom workflow path)."
    )
    _ = parser.add_argument("--server-url", default=None)
    _ = parser.add_argument(
        "--max-phase5-iter", type=positive_int, default=DEFAULT_MAX_PHASE5_ITER
    )
    from core.review_policy import add_review_policy_arguments

    add_review_policy_arguments(parser)
    _ = parser.add_argument("--keep-temp-dir", action="store_true")
    _ = parser.add_argument(
        "--container-retention",
        choices=("retain", "delete"),
        default="retain",
        action=_SingleContainerRetentionAction,
    )
    trace_policy = parser.add_mutually_exclusive_group()
    _ = trace_policy.add_argument(
        "--save-agent-trace", dest="save_agent_trace", action="store_true"
    )
    _ = trace_policy.add_argument(
        "--no-save-agent-trace", dest="save_agent_trace", action="store_false"
    )
    parser.set_defaults(save_agent_trace=None)
    run_mode = parser.add_mutually_exclusive_group(required=True)
    _ = run_mode.add_argument("--project-dir", type=Path, default=None)
    _ = run_mode.add_argument(
        "--continue-from",
        type=Path,
        default=None,
        action=_ContinueFromAction,
    )
    _ = parser.add_argument("--agent", type=str, default=None)
    _ = parser.add_argument("--output-dir", type=Path, default=None)
    _ = parser.add_argument("--user-constraints", type=Path, default=None)
    _ = parser.add_argument("--review-gate", action="store_true")
    _ = parser.add_argument("--framework-config", type=str, default=None)
    _ = parser.add_argument("--server-auto-start", action="store_true", default=True)
    _ = parser.add_argument("--server-no-auto-start", action="store_true")
    _ = parser.add_argument("--server-port", type=int, default=0)
    _ = parser.add_argument(
        "--opencode-readiness", choices=("off", "basic", "message"), default="message"
    )
    _ = parser.add_argument(
        "--opencode-message-timeout", type=positive_int, default=120
    )
    _ = parser.add_argument(
        "--dashboard-mode", choices=("auto", "on", "off"), default="auto"
    )
    _ = parser.add_argument(
        "--dashboard", action="store_true", help="Enable the live dashboard."
    )
    _ = parser.add_argument(
        "--no-dashboard", action="store_true", help="Disable the live dashboard."
    )
    _ = parser.add_argument(
        "--dashboard-backend",
        choices=("auto", "textual", "rich"),
        default="auto",
        help="Force dashboard renderer backend (default: auto-probe textual then rich).",
    )
    _ = parser.add_argument(
        "--seal-manifest",
        nargs=0,
        action=_SealManifestAction,
        default=False,
        help="Create and seal a root run-manifest.v1.json so the run is eligible for --continue-from.",
    )
    _ = parser.add_argument("--verbose", action="store_true")
    _ = parser.add_argument(
        "--workflow-path",
        type=Path,
        default=None,
        help="Absolute or relative path to a workflow YAML file (overrides default).",
    )
    return parser


def _resolve_user_constraints(raw: str | None) -> str:
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return raw


def main() -> int:
    from core.review_policy import review_cli_overrides_from_namespace

    parser = build_parser()
    args = parser.parse_args()
    if args.continue_from is not None and args.workflow_path is not None:
        parser.error("--continue-from and --workflow-path are mutually exclusive")

    user_constraints_text = ""
    if args.user_constraints:
        user_constraints_text = _resolve_user_constraints(str(args.user_constraints))

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )

    server_auto_start = not args.server_no_auto_start
    dashboard_mode = args.dashboard_mode
    if args.dashboard:
        dashboard_mode = "on"
    if args.no_dashboard:
        dashboard_mode = "off"

    review_overrides = review_cli_overrides_from_namespace(args, parser)
    if args.continue_from is not None:
        return run_terminal_continuation(
            TerminalContinuationRunRequest(
                summary_path=args.continue_from,
                server=V3ServerRunOptions(
                    base_url=args.server_url,
                    auto_start=server_auto_start,
                    port=args.server_port,
                ),
                review=V3ReviewRunOptions(
                    max_phase5_iter=args.max_phase5_iter,
                    enabled=args.review_gate,
                    overrides=review_overrides,
                ),
                invocation=V3InvocationOptions(
                    keep_temp_dir=args.keep_temp_dir,
                    agent_name=args.agent,
                    user_constraints=user_constraints_text,
                    framework_config_path=args.framework_config,
                    container_retention=ContainerRetention(args.container_retention),
                    save_agent_trace=args.save_agent_trace,
                ),
                opencode=V3OpenCodeOptions(
                    readiness=args.opencode_readiness,
                    message_timeout=args.opencode_message_timeout,
                ),
            ),
            dashboard_mode=dashboard_mode,
            dashboard_backend=args.dashboard_backend,
        )

    return run_e2e_v3(
        base_url=args.server_url,
        max_phase5_iter=args.max_phase5_iter,
        keep_temp_dir=args.keep_temp_dir,
        agent_name=args.agent,
        project_dir=args.project_dir,
        output_project_dir=args.output_dir,
        user_constraints=user_constraints_text,
        server_auto_start=server_auto_start,
        server_port=args.server_port,
        review_gate=args.review_gate,
        review_policy_overrides=review_overrides,
        framework_config_path=args.framework_config,
        workflow_path=args.workflow_path,
        opencode_readiness=args.opencode_readiness,
        opencode_message_timeout=args.opencode_message_timeout,
        container_retention=ContainerRetention(args.container_retention),
        save_agent_trace=args.save_agent_trace,
        dashboard_mode=dashboard_mode,
        dashboard_backend=args.dashboard_backend,
        seal_manifest=args.seal_manifest,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
