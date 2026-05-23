#!/usr/bin/env python3
# pyright: reportArgumentType=false, reportCallIssue=false, reportIndexIssue=false, reportOperatorIssue=false, reportPrivateUsage=false, reportUnknownMemberType=false
from __future__ import annotations

import argparse
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from core.accelerator_context import extract_accelerator_context
from core.agent_io_logger import AgentIOLogger
from core.artifact_store import ArtifactStore
from core.config_loader import load_framework_config
from core.paths import default_output_projects_root, execution_root
from core.phase_runner import PhaseRunner
from core.prompt_loader import PromptLoader
from core.repair_loop import RepairLoopEngine
from core.validator_engine import ValidatorEngine
from harness.session.manager import SessionManager
from migrator.rule_based import RuleBasedMigrator
from tests.e2e.e2e_observer import TelemetryObserver

DEFAULT_SERVER_URL = "http://127.0.0.1:4096"
DEFAULT_MAX_PHASE5_ITER = 5
EXCLUDED_SNAPSHOT_DIRS = {".git", ".sm-artifacts", ".venv", "__pycache__"}


def log(msg: str, *, flush: bool = True) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=flush)


TEMPLATE_DIR = PACKAGE_ROOT / "test_project_template"
WORKFLOW_PATH = PACKAGE_ROOT / "workflows" / "npu_migration_v1.yaml"
REPO_ROOT = execution_root()
OUTPUT_ROOT = REPO_ROOT / "e2e-reports" / "migration_utils"


@dataclass
class PhaseStatus:
    phase_number: int
    phase_id: str
    label: str
    status: str
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class RunSummary:
    run_id: str
    base_url: str
    workflow_path: str
    output_dir: str
    temp_dir: str
    keep_temp_dir: bool
    requested_max_phase5_iter: int
    effective_max_phase5_iter: int
    phases: list[PhaseStatus]
    session_count: int
    command_count: int
    overall_status: str
    total_duration_seconds: float
    artifact_dir: str | None
    telemetry_paths: dict[str, str]
    before_snapshot_path: str | None
    after_snapshot_path: str | None
    entry_script: str | None
    errors: list[str]


class Ansi:
    RESET: str = "\033[0m"
    RED: str = "\033[31m"
    GREEN: str = "\033[32m"
    CYAN: str = "\033[36m"


def supports_color() -> bool:
    return sys.stdout.isatty() and (not hasattr(sys.stdout, "isatty") or sys.stdout.isatty())


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


def print_phase_finished(phase_number: int, phase_total: int, label: str, passed: bool, duration_seconds: float, error: str | None = None) -> None:
    status = "PASSED" if passed else "FAILED"
    details = f" ({duration_seconds:.1f}s)"
    if error:
        details += f"\n  Error: {error}"
    color = Ansi.GREEN if passed else Ansi.RED
    print(colorize(f"[Phase {phase_number}/{phase_total}] {label} — {status}{details}", color), flush=True)


def check_server_running(base_url: str) -> None:
    endpoint = f"{base_url.rstrip('/')}/agent"
    try:
        completed = subprocess.run(
            ["curl", "-fsS", "-o", "/dev/null", "--max-time", "5", endpoint],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"OpenCode server is not reachable at {endpoint}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"curl exit code {completed.returncode}"
        raise RuntimeError(f"OpenCode server is not reachable at {endpoint}: {detail}")


def copy_template(project_dir: Path) -> None:
    _ = shutil.copytree(TEMPLATE_DIR, project_dir, dirs_exist_ok=True)


def copy_project_light(src: Path, dst: Path) -> int:
    if not dst.is_dir():
        dst.mkdir(parents=True, exist_ok=True)

    excluded_dirs = EXCLUDED_SNAPSHOT_DIRS | {"build", "dist"}
    excluded_ext = {".bin", ".pt", ".pth", ".onnx", ".safetensors", ".tar", ".gz", ".zip", ".egg"}
    max_file_size_mb = 50
    max_file_size = max_file_size_mb * 1024 * 1024

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
    """Create symlinks in project_dir for large files that exist in source_dir but were skipped during copy."""
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
        if item.suffix.lower() in {".bin", ".pt", ".pth", ".onnx", ".safetensors", ".tar", ".gz", ".zip", ".egg"}:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(item.resolve()), str(target))
            symlinked += 1
        elif item.stat().st_size > 50 * 1024 * 1024:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(item.resolve()), str(target))
            symlinked += 1
    return symlinked


def snapshot_python_files(project_dir: Path) -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for path in sorted(project_dir.rglob("*.py")):
        relative_path = path.relative_to(project_dir)
        if any(part in EXCLUDED_SNAPSHOT_DIRS for part in relative_path.parts):
            continue
        content = path.read_text(encoding="utf-8")
        snapshot[str(relative_path)] = {
            "sha256": sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }
    return snapshot


def write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return str(path)


def sync_phase_alias(artifact_store: ArtifactStore, phase_id: str, payload: dict[str, object]) -> None:
    _ = artifact_store.save_phase_output(phase_id, payload, attempt=1)
    _ = artifact_store.mark_validated(phase_id, payload)


def run_phase_4_compat(
    runner: PhaseRunner,
    project_dir: str,
    artifact_store: ArtifactStore,
    migrator: RuleBasedMigrator,
) -> dict[str, object]:
    phase_4_runner = cast(Callable[..., object], runner.run_phase_4)
    signature = inspect.signature(phase_4_runner)
    if len(signature.parameters) == 3:
        return cast(dict[str, object], phase_4_runner(project_dir, artifact_store, migrator))
    return cast(dict[str, object], phase_4_runner(artifact_store, migrator))


def resolve_entry_script(phase_3_output: dict[str, object]) -> str:
    run_command = phase_3_output.get("run_command")
    if not isinstance(run_command, str) or not run_command.strip():
        raise ValueError("Phase 3 did not return a non-empty run_command")
    return run_command


def _build_env_context(
    phase_0_output: dict[str, object],
    phase_2_output: dict[str, object],
) -> dict[str, object]:
    env = dict(phase_0_output)
    installed = phase_2_output.get("installed_packages", [])
    accel_ctx = extract_accelerator_context(installed)
    env["torch_npu_version"] = accel_ctx["torch_npu_version"]
    env["accelerator_packages"] = accel_ctx["accelerator_packages"]
    env["accelerator_package_versions"] = accel_ctx["accelerator_package_versions"]
    return env


def execute_phase(
    *,
    phase_number: int,
    phase_total: int,
    phase_id: str,
    label: str,
    observer: TelemetryObserver,
    runner: Callable[[], dict[str, object]],
    phase_results: list[PhaseStatus],
) -> dict[str, object]:
    log(f"[Phase {phase_number}/{phase_total}] {label} — STARTING")
    observer.set_active_phase(phase_id)
    try:
        with observer.timing_phase(phase_id):
            result = runner()
    except Exception as exc:
        metric = observer.phase_metrics.get(phase_id)
        duration_seconds = metric.duration_seconds if metric is not None else 0.0
        status = PhaseStatus(
            phase_number=phase_number,
            phase_id=phase_id,
            label=label,
            status="failed",
            duration_seconds=duration_seconds,
            error=f"{exc.__class__.__name__}: {exc}",
        )
        phase_results.append(status)
        print_phase_finished(phase_number, phase_total, label, False, duration_seconds, status.error)
        raise
    finally:
        observer.set_active_phase(None)
    metric = observer.phase_metrics.get(phase_id)
    duration_seconds = metric.duration_seconds if metric is not None else 0.0
    phase_results.append(
        PhaseStatus(
            phase_number=phase_number,
            phase_id=phase_id,
            label=label,
            status="passed",
            duration_seconds=duration_seconds,
        )
    )
    print_phase_finished(phase_number, phase_total, label, True, duration_seconds)
    return result


def copy_artifacts(temp_dir: Path, output_dir: Path) -> str | None:
    source = temp_dir / ".sm-artifacts"
    if not source.exists():
        return None
    destination = output_dir / ".sm-artifacts"
    if destination.exists():
        shutil.rmtree(destination)
    _ = shutil.copytree(source, destination)
    return str(destination)


def build_summary(
    *,
    run_id: str,
    base_url: str,
    output_dir: Path,
    temp_dir: Path,
    keep_temp_dir: bool,
    requested_max_phase5_iter: int,
    effective_max_phase5_iter: int,
    phase_results: list[PhaseStatus],
    observer: TelemetryObserver,
    total_duration_seconds: float,
    artifact_dir: str | None,
    telemetry_paths: dict[str, str],
    before_snapshot_path: str | None,
    after_snapshot_path: str | None,
    entry_script: str | None,
    errors: list[str],
    expected_phase_total: int,
) -> RunSummary:
    passed = all(phase.status == "passed" for phase in phase_results) and len(phase_results) == expected_phase_total
    return RunSummary(
        run_id=run_id,
        base_url=base_url,
        workflow_path=str(WORKFLOW_PATH),
        output_dir=str(output_dir),
        temp_dir=str(temp_dir),
        keep_temp_dir=keep_temp_dir,
        requested_max_phase5_iter=requested_max_phase5_iter,
        effective_max_phase5_iter=effective_max_phase5_iter,
        phases=phase_results,
        session_count=observer.session_count,
        command_count=observer.command_count,
        overall_status="PASS" if passed else "FAIL",
        total_duration_seconds=round(total_duration_seconds, 3),
        artifact_dir=artifact_dir,
        telemetry_paths=telemetry_paths,
        before_snapshot_path=before_snapshot_path,
        after_snapshot_path=after_snapshot_path,
        entry_script=entry_script,
        errors=errors,
    )


def print_summary(summary: RunSummary) -> None:
    headline = colorize(f"E2E {summary.overall_status}", Ansi.GREEN if summary.overall_status == "PASS" else Ansi.RED)
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
    print("- Phase timings:")
    for phase in summary.phases:
        suffix = f" - {phase.error}" if phase.error else ""
        print(f"  - {phase.phase_id}: {phase.status.upper()} ({phase.duration_seconds:.2f}s){suffix}")
    if summary.errors:
        print("- Errors:")
        for error in summary.errors:
            print(f"  - {error}")


def run_e2e(
    *,
    base_url: str,
    max_phase5_iter: int,
    keep_temp_dir: bool,
    agent_name: str | None,
    project_dir: Path | None,
    output_project_dir: Path | None = None,
    user_constraints: str = "",
    server_auto_start: bool = True,
    server_port: int = 0,
    review_gate: bool = False,
    framework_config_path: str | None = None,
) -> int:
    started_at = datetime.now(timezone.utc)
    run_id = f"e2e-real-{uuid4().hex[:12]}"
    output_dir = OUTPUT_ROOT / started_at.strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    server_proc: subprocess.Popen[bytes] | None = None
    temp_dir: Path | None = None
    artifact_dir: str | None = None
    before_snapshot_path: str | None = None
    after_snapshot_path: str | None = None
    telemetry_paths: dict[str, str] = {}
    entry_script: str | None = None
    errors: list[str] = []
    phase_results: list[PhaseStatus] = []
    observer: TelemetryObserver | None = None
    phase_total = 7 if user_constraints else 6

    try:
        if server_auto_start and (not base_url or base_url == DEFAULT_SERVER_URL):
            from harness.server.lifecycle import find_available_port, start_server, stop_server, wait_for_server

            port = server_port if server_port > 0 else find_available_port()
            base_url = f"http://127.0.0.1:{port}"
            server_proc = start_server(work_dir=str(REPO_ROOT), port=port)
            if not wait_for_server(base_url, timeout=30):
                _ = stop_server(server_proc)
                server_proc = None
                raise RuntimeError(f"Server failed to start on {base_url}")
        check_server_running(base_url)
        log(f"OpenCode server reachable at {base_url}")
    except Exception as exc:
        print(colorize(f"E2E FAILED: {exc}", Ansi.RED), file=sys.stderr)
        return 1

    try:
        if project_dir is not None:
            project_name = project_dir.resolve().name
            timestamp = started_at.strftime("%Y%m%d_%H%M%S")
            output_project_base = output_project_dir if output_project_dir else default_output_projects_root()
            output_project_base.mkdir(parents=True, exist_ok=True)
            dest = output_project_base / f"{project_name}_{timestamp}"
            log(f"Copying project {project_dir} to {dest}...")
            copied_count = copy_project_light(project_dir, dest)
            symlinked_count = symlink_large_files(dest, project_dir)
            temp_dir = dest.resolve()
            log(f"Copied {copied_count} files, symlinked {symlinked_count} large files to {temp_dir}")
            keep_temp_dir = True
        else:
            temp_dir = Path(tempfile.mkdtemp(prefix="migration-utils-e2e-real-"))
            copy_template(temp_dir)
            log(f"Created temp dir: {temp_dir}")

        before_snapshot = snapshot_python_files(temp_dir)
        before_snapshot_path = write_json(output_dir / "before_snapshot.json", before_snapshot)
        log(f"Snapshot: {len(before_snapshot)} .py files")

        session_mgr = SessionManager(work_dir=str(temp_dir), base_url=base_url)
        if agent_name and agent_name != session_mgr.active_agent:
            session_mgr._detected_agent = agent_name
        log(f"SessionManager created: detected_agent={session_mgr.active_agent}, overridden={agent_name is not None}")

        agent_io_logger = AgentIOLogger.from_env(output_dir, run_id)
        observer = TelemetryObserver(session_mgr, output_dir, agent_io_logger=agent_io_logger)
        observer.set_metadata("agent_name", agent_name)
        observer.set_metadata("run_id", run_id)
        observer.set_metadata("base_url", base_url)
        observer.set_metadata("workflow_path", str(WORKFLOW_PATH))
        observer.set_metadata("project_dir", str(temp_dir))
        observer.set_metadata("review_gate", review_gate)
        observer.set_metadata("framework_config_path", framework_config_path)

        artifact_store = ArtifactStore(str(temp_dir), run_id)
        prompt_loader = PromptLoader()
        validator = ValidatorEngine()
        framework_config = load_framework_config(framework_config_path)
        runner = PhaseRunner(observer, artifact_store, prompt_loader, validator)
        repair = RepairLoopEngine(observer, artifact_store, prompt_loader, validator, config=framework_config)
        migrator = RuleBasedMigrator()

        main_session_id = observer.get_or_create(role="main_engineer", lifecycle="persistent")
        observer.set_metadata("main_session_id", main_session_id)
        log(f"Main session created: {main_session_id}")

        phase_outputs: dict[str, dict[str, object]] = {}

        # Phase 0-1: Environment Detection + Project Analysis (with user_constraints)
        phase_0_1_outputs = execute_phase(
            phase_number=0,
            phase_total=phase_total,
            phase_id="phase_0_1_combined",
            label="Environment Detection + Project Analysis",
            observer=observer,
            phase_results=phase_results,
            runner=lambda: runner.run_phase_0_to_1(
                project_dir=str(temp_dir),
                session_mgr=observer,
                artifact_store=artifact_store,
                user_constraints=user_constraints,
            ),
        )
        phase_outputs.update(phase_0_1_outputs)
        for phase_alias in ("phase_0_env_detect", "phase_1_project_analysis"):
            sync_phase_alias(artifact_store, phase_alias, phase_outputs.get(phase_alias, {}))

        # Phase 1.5: Constraint Summary (conditional on user_constraints)
        constraint_summary = ""
        if user_constraints:
            phase_1_output = phase_outputs.get("phase_1_project_analysis")
            constraint_summary = execute_phase(
                phase_number=1,
                phase_total=phase_total,
                phase_id="phase_1_5_constraint_summary",
                label="Constraint Summary Generation",
                observer=observer,
                phase_results=phase_results,
                runner=lambda: runner.run_phase_1_5(
                    main_session_id=main_session_id,
                    session_mgr=observer,
                    artifact_store=artifact_store,
                    project_dir=str(temp_dir),
                    user_constraints=user_constraints,
                    phase_1_output=phase_1_output,
                ),
            )
            log(f"Phase 1.5: constraint_summary generated ({len(constraint_summary)} chars)")

        # Phase 2-3: venv + Entry Script (with constraint_summary)
        phase_2_3_outputs = execute_phase(
            phase_number=2,
            phase_total=phase_total,
            phase_id="phase_2_3_combined",
            label="Virtual Environment + Entry Script",
            observer=observer,
            phase_results=phase_results,
            runner=lambda: runner.run_phase_2_to_3(
                project_dir=str(temp_dir),
                session_mgr=observer,
                artifact_store=artifact_store,
                prior_outputs=phase_outputs,
                constraint_summary=constraint_summary,
            ),
        )
        phase_outputs.update(phase_2_3_outputs)
        for phase_alias in ("phase_2_venv_create", "phase_3_entry_script"):
            sync_phase_alias(artifact_store, phase_alias, phase_outputs.get(phase_alias, {}))

        if isinstance(phase_2_3_outputs.get("phase_3_entry_script"), dict) and "project_dir" not in phase_2_3_outputs.get("phase_3_entry_script", {}):
            phase_2_3_outputs.setdefault("phase_3_entry_script", {})["project_dir"] = str(temp_dir)
            sync_phase_alias(artifact_store, "phase_3_entry_script", phase_2_3_outputs["phase_3_entry_script"])

        phase_outputs["phase_3_entry_script"] = phase_outputs.get("phase_3_entry_script", {})
        phase_outputs["phase_3_entry_script"]["project_dir"] = str(temp_dir)

        # Phase 4: Rule-Based Migration (unchanged)
        phase_4_output = execute_phase(
            phase_number=4,
            phase_total=phase_total,
            phase_id="phase_4_rule_migration",
            label="Rule-Based Migration",
            observer=observer,
            phase_results=phase_results,
            runner=lambda: run_phase_4_compat(runner, str(temp_dir), artifact_store, migrator),
        )
        phase_outputs["phase_4_rule_migration"] = phase_4_output

        entry_script = resolve_entry_script(phase_outputs["phase_3_entry_script"])
        log(f"Entry script resolved: {entry_script}")

        env_context = _build_env_context(
            phase_outputs.get("phase_0_env_detect") if isinstance(phase_outputs.get("phase_0_env_detect"), dict) else {},
            phase_outputs.get("phase_2_venv_create") if isinstance(phase_outputs.get("phase_2_venv_create"), dict) else {},
        )
        phase3_output = phase_outputs.get("phase_3_entry_script")
        phase3_contract = dict(phase3_output) if isinstance(phase3_output, dict) else None

        effective_max_phase5_iter = max_phase5_iter
        framework_section_obj = framework_config.get("framework", {})
        framework_section = (
            cast(dict[str, object], framework_section_obj)
            if isinstance(framework_section_obj, dict)
            else {}
        )
        review_cfg_obj = framework_section.get("review", {})
        review_cfg = cast(dict[str, object], review_cfg_obj) if isinstance(review_cfg_obj, dict) else {}
        observer.set_metadata("effective_max_phase5_iter", effective_max_phase5_iter)
        log(f"Phase 5 config: max_iter={effective_max_phase5_iter}")

        def run_phase_5() -> dict[str, object]:
            log("Phase 5: Running repair loop...")

            def _review_fn(repair_ctx: dict[str, object]) -> dict[str, object]:
                history = repair_ctx.get("history", [])
                repair_history = RepairLoopEngine._format_history_summary(history)
                return runner.run_review_check(
                    review_session_id=main_session_id,
                    session_mgr=observer,
                    project_dir=str(temp_dir),
                    repair_history=repair_history,
                    last_artifact_path=str(
                        repair_ctx.get("last_artifact_path", "(no artifact available)")
                    ),
                    attempt_log_content=str(
                        repair_ctx.get("attempt_log_content", "(attempt log unavailable)")
                    ),
                    execution_duration=str(
                        repair_ctx.get("execution_duration", "(not available)")
                    ),
                )

            result = repair.run(
                entry_script,
                str(temp_dir),
                max_iterations=effective_max_phase5_iter,
                logger=lambda msg: log(msg),
                review_callable=_review_fn,
                constraint_summary=constraint_summary,
                env_context=env_context,
                enable_review_gate=review_gate,
                max_review_iterations=cast(int, review_cfg.get("max_review_iterations", 3)),
                phase3_contract=phase3_contract,
            )
            _ = artifact_store.mark_validated("phase_5_validation", result)
            log(f"Phase 5: Repair loop result: status={result.get('status')}, iterations={result.get('iteration_count')}")
            return result

        phase_5_output = execute_phase(
            phase_number=5,
            phase_total=phase_total,
            phase_id="phase_5_validation",
            label="Validation Repair Loop",
            observer=observer,
            phase_results=phase_results,
            runner=run_phase_5,
        )
        phase_outputs["phase_5_validation"] = phase_5_output

        phase_6_output = execute_phase(
            phase_number=6,
            phase_total=phase_total,
            phase_id="phase_6_report",
            label="Final Report Generation",
            observer=observer,
            phase_results=phase_results,
            runner=lambda: runner.run_phase_6(str(temp_dir), artifact_store, observer),
        )
        phase_outputs["phase_6_report"] = phase_6_output

        _ = execute_phase(
            phase_number=7,
            phase_total=phase_total,
            phase_id="phase_7_artifacts_finalization",
            label="Artifact Finalization",
            observer=observer,
            phase_results=phase_results,
            runner=lambda: {
                "after_snapshot": str(write_json(output_dir / "after_snapshot.json", snapshot_python_files(temp_dir))),
                "artifact_dir": str(copy_artifacts(temp_dir, output_dir)),
            },
        )

        observer.record_event("artifacts_copied", artifact_dir=artifact_dir)
    except Exception as exc:
        errors.append(f"{exc.__class__.__name__}: {exc}")
        traceback_path = output_dir / "traceback.txt"
        _ = traceback_path.write_text(traceback.format_exc(), encoding="utf-8")
        if temp_dir is not None:
            after_snapshot = snapshot_python_files(temp_dir)
            after_snapshot_path = write_json(output_dir / "after_snapshot.json", after_snapshot)
            artifact_dir = copy_artifacts(temp_dir, output_dir)
        if observer is not None:
            observer.record_event("runner_error", error=str(exc), traceback=traceback.format_exc())
    finally:
        if temp_dir is not None and observer is not None:
            observer.record_event("cleanup_requested", keep_temp_dir=keep_temp_dir)
            cleaned_sessions = observer.cleanup_all()
            observer.set_metadata("cleaned_sessions", cleaned_sessions)
        if observer is not None:
            telemetry_paths = observer.save_metrics()
            _ = write_json(output_dir / "phase_results.json", [asdict(phase) for phase in phase_results])
        if server_proc is not None:
            from harness.server.lifecycle import stop_server

            _ = stop_server(server_proc)
        if temp_dir is not None and not keep_temp_dir and project_dir is None:
            shutil.rmtree(temp_dir, ignore_errors=True)

    total_duration_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
    effective_max = max_phase5_iter
    if observer is None or temp_dir is None:
        summary = RunSummary(
            run_id=run_id,
            base_url=base_url,
            workflow_path=str(WORKFLOW_PATH),
            output_dir=str(output_dir),
            temp_dir=str(temp_dir or ""),
            keep_temp_dir=keep_temp_dir,
            requested_max_phase5_iter=max_phase5_iter,
            effective_max_phase5_iter=effective_max,
            phases=phase_results,
            session_count=0,
            command_count=0,
            overall_status="FAIL",
            total_duration_seconds=round(total_duration_seconds, 3),
            artifact_dir=artifact_dir,
            telemetry_paths=telemetry_paths,
            before_snapshot_path=before_snapshot_path,
            after_snapshot_path=after_snapshot_path,
            entry_script=entry_script,
            errors=errors or ["Initialization failed before telemetry observer was created."],
        )
    else:
        summary = build_summary(
            run_id=run_id,
            base_url=base_url,
            output_dir=output_dir,
            temp_dir=temp_dir,
            keep_temp_dir=keep_temp_dir,
            requested_max_phase5_iter=max_phase5_iter,
            effective_max_phase5_iter=effective_max,
            phase_results=phase_results,
            observer=observer,
            total_duration_seconds=total_duration_seconds,
            artifact_dir=artifact_dir,
            telemetry_paths=telemetry_paths,
            before_snapshot_path=before_snapshot_path,
            after_snapshot_path=after_snapshot_path,
            entry_script=entry_script,
            errors=errors,
            expected_phase_total=phase_total,
        )

    _ = write_json(output_dir / "summary.json", asdict(summary))
    print_summary(summary)
    return 0 if summary.overall_status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the real migration_utils E2E workflow against the test project template.")
    _ = parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help=f"OpenCode server URL (default: {DEFAULT_SERVER_URL})")
    _ = parser.add_argument(
        "--max-phase5-iter",
        type=positive_int,
        default=DEFAULT_MAX_PHASE5_ITER,
        help=f"Maximum Phase 5 repair-loop iterations (default: {DEFAULT_MAX_PHASE5_ITER})",
    )
    _ = parser.add_argument(
        "--keep-temp-dir",
        action="store_true",
        help="Keep the temporary migrated project directory for inspection.",
    )
    _ = parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Use this directory instead of creating a temp dir (must contain train.py or similar).",
    )
    _ = parser.add_argument(
        "--agent",
        type=str,
        default=None,
        help="Override the auto-detected agent name (e.g., 'Atlas (Plan Executor)').",
    )
    _ = parser.add_argument(
        "--output-project-dir",
        type=Path,
        default=None,
        help="Base directory for output projects (default: <execution_root>/output_projects).",
    )
    _ = parser.add_argument(
        "--user-constraints",
        type=str,
        default="",
        help="Path to a Markdown file containing user constraints, or raw constraint text.",
    )
    _ = parser.add_argument("--review-gate", action="store_true", help="Enable review gate improvement mode")
    _ = parser.add_argument("--framework-config", type=str, default=None, help="Path to framework config YAML")
    _ = parser.add_argument("--server-no-auto-start", action="store_true", help="Disable auto-start of OpenCode server")
    _ = parser.add_argument("--server-port", type=int, default=0, help="Specific port for auto-started server (0=auto)")
    return parser


def _resolve_user_constraints(raw: str) -> str:
    """Resolve user constraints: if it's a file path, read the file; otherwise use raw text."""
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return raw


def main() -> int:
    args = build_parser().parse_args()
    server_url = cast(str, args.server_url)
    max_phase5_iter = cast(int, args.max_phase5_iter)
    keep_temp_dir = cast(bool, args.keep_temp_dir)
    agent_name = cast(str | None, args.agent)
    project_dir = cast(Path | None, args.project_dir)
    output_project_dir = cast(Path | None, args.output_project_dir)
    user_constraints = _resolve_user_constraints(cast(str, args.user_constraints))
    review_gate = cast(bool, args.review_gate)
    framework_config_path = cast(str | None, args.framework_config)
    server_no_auto_start = cast(bool, args.server_no_auto_start)
    server_port = cast(int, args.server_port)
    return run_e2e(
        base_url=server_url,
        max_phase5_iter=max_phase5_iter,
        keep_temp_dir=keep_temp_dir,
        agent_name=agent_name,
        project_dir=project_dir,
        output_project_dir=output_project_dir,
        user_constraints=user_constraints,
        server_auto_start=not server_no_auto_start,
        server_port=server_port,
        review_gate=review_gate,
        framework_config_path=framework_config_path,
    )


if __name__ == "__main__":
    sys.exit(main())
