#!/usr/bin/env python3
# pyright: reportArgumentType=false, reportCallIssue=false,
# reportIndexIssue=false, reportOperatorIssue=false,
# reportPrivateUsage=false, reportUnknownMemberType=false
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from core.paths import execution_root  # pylint: disable=wrong-import-position; silent

DEFAULT_SERVER_URL = "http://127.0.0.1:4096"
DEFAULT_MAX_PHASE5_ITER = 5
EXCLUDED_SNAPSHOT_DIRS = {".git", ".sm-artifacts", ".venv", "__pycache__"}

REPO_ROOT = execution_root()
TEMPLATE_DIR = PACKAGE_ROOT / "test_project_template"
_default_workflow_path = PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"
OUTPUT_ROOT = REPO_ROOT / "e2e-reports" / "src"


@dataclass
class PhaseStatus:
    phase_number: int
    phase_id: str
    label: str
    status: str
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class RunSummary:  # pylint: disable=too-many-instance-attributes; silent
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


class Ansi:  # pylint: disable=too-few-public-methods; silent
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"


def log(msg: str, *, flush: bool = True) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=flush)


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


# pylint: disable-next=too-many-arguments,too-many-positional-arguments; silent
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
        colorize(f"[Phase {phase_number}/{phase_total}] {label} — {status}{details}", color),
        flush=True,
    )


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
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"curl exit code {completed.returncode}"
        )
        raise RuntimeError(f"OpenCode server is not reachable at {endpoint}: {detail}")


def copy_project_light(src: Path, dst: Path) -> int:
    if not dst.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
    excluded_dirs = EXCLUDED_SNAPSHOT_DIRS | {"build", "dist"}
    excluded_ext = {".bin", ".pt", ".pth", ".onnx", ".safetensors", ".tar", ".gz", ".zip", ".egg"}
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
                except Exception:  # pylint: disable=broad-exception-caught; silent
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
    _ = path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return str(path)


def copy_artifacts(temp_dir: Path, output_dir: Path) -> str | None:
    source = temp_dir / ".sm-artifacts"
    if not source.exists():
        return None
    destination = output_dir / ".sm-artifacts"
    if destination.exists():
        shutil.rmtree(destination)
    _ = shutil.copytree(source, destination)
    return str(destination)


def print_summary(summary: RunSummary) -> None:
    headline = colorize(
        f"E2E {summary.overall_status}",
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


def build_v3_summary(  # pylint: disable=too-many-arguments,too-many-locals; silent
    *,
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
    passed = all(p.status == "passed" for p in phase_results)
    return RunSummary(
        run_id=run_id,
        base_url=base_url,
        workflow_path=workflow_path,
        output_dir=output_dir,
        temp_dir=temp_dir,
        keep_temp_dir=keep_temp_dir,
        requested_max_phase5_iter=max_phase5_iter,
        effective_max_phase5_iter=max_phase5_iter,
        phases=phase_results,
        session_count=session_count,
        command_count=command_count,
        overall_status="PASS" if passed and not errors else "FAIL",
        total_duration_seconds=round(total_duration_seconds, 3),
        artifact_dir=artifact_dir,
        telemetry_paths=telemetry_paths,
        before_snapshot_path=before_snapshot_path,
        after_snapshot_path=after_snapshot_path,
        entry_script=entry_script,
        errors=errors,
    )


def _build_project_context(project_dir: Path) -> dict[str, object]:
    py_files = list(project_dir.rglob("*.py"))
    file_hints = [str(p.relative_to(project_dir)) for p in py_files[:10]]
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
        "file_count": len(py_files),
        "build_system": build_system,
        "file_hints": file_hints,
    }


def _install_sqlite_fallback_if_needed() -> None:
    """Install a minimal sqlite3 stub for Python builds missing the _sqlite3 C extension.

    The stub raises sqlite3.Error on connect so optional sqlite fallback paths in
    harness.session.manager are safely skipped (via except sqlite3.Error) while the
    HTTP session flow continues normally. This is only needed for python3.10 builds
    compiled without the _sqlite3 extension. The standard library sqlite3 module is
    used when available.
    """
    if "sqlite3" in sys.modules:
        return
    try:
        __import__("sqlite3")
        return
    except ModuleNotFoundError:
        pass

    class _SqliteError(Exception):
        """Placeholder for sqlite3.Error so except clauses skip the sqlite path."""

        pass  # pylint: disable=unnecessary-pass; silent

    class _StubDBModule:  # pylint: disable=too-few-public-methods; silent
        """Minimal DB-API 2.0 stub so 'import sqlite3' does not crash."""

        apilevel = "2.0"
        paramstyle = "qmark"
        threadsafety = 1

        # Expose sqlite3.Error so harness.session.manager's except clause works.
        Error = _SqliteError
        Warning = Exception
        InterfaceError = _SqliteError
        DatabaseError = _SqliteError
        DataError = _SqliteError
        OperationalError = _SqliteError
        IntegrityError = _SqliteError
        InternalError = _SqliteError
        ProgrammingError = _SqliteError
        NotSupportedError = _SqliteError

        @staticmethod
        def connect(*_args, **_kwargs):
            raise _SqliteError("sqlite3 unavailable: _sqlite3 C extension is not installed")

    class _StubDbapi2(_StubDBModule):  # pylint: disable=too-few-public-methods; silent
        pass

    stub = _StubDBModule()
    stub.dbapi2 = _StubDbapi2  # pylint: disable=attribute-defined-outside-init; silent
    sys.modules["sqlite3"] = stub
    sys.modules["sqlite3.dbapi2"] = _StubDbapi2


# pylint: disable-next=too-many-arguments,too-many-branches,too-many-locals,too-many-statements; silent
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
    framework_config_path: str | None = None,
    workflow_path: Path | None = None,
) -> int:
    _install_sqlite_fallback_if_needed()

    # pylint: disable-next=import-outside-toplevel; silent
    from core.agent_io_logger import AgentIOLogger
    # pylint: disable-next=import-outside-toplevel; silent
    from core.artifact_store import ArtifactStore
    from core.config import load_workflow  # pylint: disable=import-outside-toplevel; silent
    # pylint: disable-next=import-outside-toplevel; silent
    from core.config_loader import load_framework_config
    # pylint: disable-next=import-outside-toplevel; silent
    from core.paths import default_output_projects_root
    from core.prompt_loader import PromptLoader  # pylint: disable=import-outside-toplevel; silent
    # pylint: disable-next=import-outside-toplevel; silent
    from core.telemetry_bridge import TelemetryBridge
    # pylint: disable-next=import-outside-toplevel; silent
    from core.validator_engine import ValidatorEngine
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_executor import WorkflowExecutor
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_selector import is_selector_file, resolve_workflow_from_selector
    # pylint: disable-next=import-outside-toplevel; silent
    from harness.session.manager import SessionManager
    # pylint: disable-next=import-outside-toplevel; silent
    from tests.e2e.e2e_observer import TelemetryObserver
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_constraint_summary import validate as validate_constraint_summary
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_entry_script import validate as validate_entry_script
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_entry_static import validate as validate_entry_static
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_env_detect import validate as validate_env_detect
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_project_analysis import validate as validate_project_analysis
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_reports import validate as validate_reports
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_rule_migration import validate as validate_rule_migration
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_validation_final import validate as validate_validation_final
    # pylint: disable-next=import-outside-toplevel; silent
    from validators.validate_venv import validate as validate_venv

    started_at = datetime.now(timezone.utc)
    run_id = f"e2e-v3-{uuid4().hex[:12]}"
    output_dir = OUTPUT_ROOT / started_at.strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    effective_workflow_path = workflow_path if workflow_path else _default_workflow_path

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
    telemetry_bridge: TelemetryBridge | None = None

    try:
        # pylint: disable-next=import-outside-toplevel; silent
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
        check_server_running(base_url)
        log(f"OpenCode server reachable at {base_url}")
    except Exception as exc:  # pylint: disable=broad-exception-caught; silent
        print(colorize(f"E2E FAILED: {exc}", Ansi.RED), file=sys.stderr)
        return 1

    try:
        if project_dir is not None:
            project_name = project_dir.resolve().name
            timestamp = started_at.strftime("%Y%m%d_%H%M%S")
            output_project_base = (
                output_project_dir if output_project_dir else default_output_projects_root()
            )
            output_project_base.mkdir(parents=True, exist_ok=True)
            dest = output_project_base / f"{project_name}_{timestamp}"
            log(f"Copying project {project_dir} to {dest}...")
            copied_count = copy_project_light(project_dir, dest)
            symlinked_count = symlink_large_files(dest, project_dir)
            temp_dir = dest.resolve()
            log(
                # pylint: disable-next=line-too-long; silent
                f"Copied {copied_count} files, symlinked {symlinked_count} large files to {temp_dir}"
            )
            keep_temp_dir = True
        else:
            temp_dir = Path(tempfile.mkdtemp(prefix="migration-utils-e2e-v3-"))
            if TEMPLATE_DIR.exists():
                _ = shutil.copytree(TEMPLATE_DIR, temp_dir, dirs_exist_ok=True)
            log(f"Created temp dir: {temp_dir}")

        before_snapshot = snapshot_python_files(temp_dir)
        before_snapshot_path = write_json(output_dir / "before_snapshot.json", before_snapshot)
        log(f"Snapshot: {len(before_snapshot)} .py files")

        session_mgr = SessionManager(work_dir=str(temp_dir), base_url=base_url)
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

        agent_io_logger = AgentIOLogger.from_env(output_dir, run_id)
        observer = TelemetryObserver(session_mgr, output_dir, agent_io_logger=agent_io_logger)
        observer.set_metadata("run_id", run_id)
        observer.set_metadata("base_url", base_url)
        observer.set_metadata("review_gate", review_gate)

        artifact_store = ArtifactStore(str(temp_dir), run_id)
        prompt_loader = PromptLoader()

        # ── Workflow Selector resolution (before load_workflow) ──────────
        original_workflow_path = effective_workflow_path
        selector_resolved_path: str | None = None
        try:
            if is_selector_file(str(effective_workflow_path)):
                log(f"Detected selector YAML: {effective_workflow_path}")
                project_ctx = _build_project_context(temp_dir)
                materialized = resolve_workflow_from_selector(
                    str(effective_workflow_path),
                    session_mgr,
                    prompt_loader,
                    project_context=project_ctx,
                    output_dir=output_dir / "artifacts",
                )
                effective_workflow_path = materialized
                selector_resolved_path = str(materialized)
                log(f"Selector resolved to: {materialized}")
                observer.set_metadata("selector_path", str(original_workflow_path))
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
            "repair_classification", lambda d: {"passed": True, "errors": [], "warnings": []}
        )

        workflow = load_workflow(str(effective_workflow_path))
        log(f"Workflow loaded: {workflow.name} v{workflow.version} from {effective_workflow_path}")

        if isinstance(workflow.globals, dict):
            workflow.globals["max_repair_iterations"] = max_phase5_iter
            workflow.globals["review_gate_enabled"] = review_gate

        telemetry_bridge = TelemetryBridge(str(output_dir))
        framework_config = load_framework_config(framework_config_path)

        experience_store = None
        if workflow.experience.enabled:
            # pylint: disable-next=import-outside-toplevel; silent
            from core.experience_store import ExperienceStore

            experience_store = ExperienceStore(str(REPO_ROOT))

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
            user_constraints=user_constraints,
            telemetry_bridge=telemetry_bridge,
            experience_store=experience_store,
        )

        executor.execute(
            {
                "PROJECT_DIR": str(temp_dir),
                "USER_CONSTRAINTS": user_constraints if user_constraints else "",
            }
        )

        telemetry_bridge.set_metadata("agent_name", agent_name)
        telemetry_bridge.set_metadata("workflow_name", workflow.name)
        telemetry_bridge.set_metadata("workflow_version", workflow.version)

        phase_order_map = {p.id: i for i, p in enumerate(executor.workflow.phases or [])}
        for pid, pr in executor.phase_results.items():
            idx = phase_order_map.get(pid, 999)
            status_str = str(pr.get("status", "unknown"))
            mapped_status = {"success": "passed", "failure": "failed", "skipped": "skipped"}.get(
                status_str, status_str
            )
            error_msg = pr.get("output_summary", "")[:500] if status_str == "failure" else None
            phase_results.append(
                PhaseStatus(
                    phase_number=idx + 1,
                    phase_id=pid,
                    label=pid,
                    status=mapped_status,
                    duration_seconds=round(pr.get("duration", 0.0), 3),
                    error=error_msg,
                )
            )
        phase_results.sort(key=lambda p: p.phase_number)

        after_snapshot = snapshot_python_files(temp_dir)
        after_snapshot_path = write_json(output_dir / "after_snapshot.json", after_snapshot)
        log(f"After snapshot: {len(after_snapshot)} .py files")

        artifact_dir = copy_artifacts(temp_dir, output_dir)
        if artifact_dir:
            log(f"Artifacts copied to {artifact_dir}")

        observer_paths = observer.save_metrics()
        bridge_paths = telemetry_bridge.save_metrics(
            filename="telemetry_bridge.json",
            return_key="telemetry_bridge_json",
        )
        telemetry_paths = {**observer_paths, **bridge_paths}
        if agent_io_logger is not None:
            agent_io_paths = agent_io_logger.paths()
            telemetry_paths["agent_io_jsonl"] = agent_io_paths["jsonl"]
            telemetry_paths["agent_io_payload_dir"] = agent_io_paths["payload_dir"]
            telemetry_json_path = telemetry_paths.get("telemetry_json")
            if telemetry_json_path:
                try:
                    telemetry_payload = json.loads(
                        Path(telemetry_json_path).read_text(encoding="utf-8")
                    )
                    metadata = telemetry_payload.setdefault("metadata", {})
                    if isinstance(metadata, dict):
                        metadata["agent_io_paths"] = agent_io_paths
                    _ = Path(telemetry_json_path).write_text(
                        json.dumps(telemetry_payload, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught; silent
                    observer.record_event("agent_io_metadata_error", error=str(exc))

        _ = write_json(output_dir / "phase_results.json", [asdict(p) for p in phase_results])

        phase_3_output = executor.state.get("phase_3_entry_script")
        if isinstance(phase_3_output, dict):
            run_command = phase_3_output.get("run_command")
            if isinstance(run_command, str) and run_command.strip():
                entry_script = run_command

    except Exception as exc:  # pylint: disable=broad-exception-caught; silent
        errors.append(f"{exc.__class__.__name__}: {exc}")
        traceback_path = output_dir / "traceback.txt"
        _ = traceback_path.write_text(traceback.format_exc(), encoding="utf-8")
        if temp_dir is not None:
            try:
                after_snapshot = snapshot_python_files(temp_dir)
                after_snapshot_path = write_json(output_dir / "after_snapshot.json", after_snapshot)
            except Exception:  # pylint: disable=broad-exception-caught; silent
                pass
            artifact_dir = copy_artifacts(temp_dir, output_dir)
        if observer is not None:
            observer.record_event("runner_error", error=str(exc), traceback=traceback.format_exc())
    finally:
        if temp_dir is not None and observer is not None:
            observer.record_event("cleanup_requested", keep_temp_dir=keep_temp_dir)
            try:
                cleaned_sessions = observer.cleanup_all()
                observer.set_metadata("cleaned_sessions", cleaned_sessions)
            except Exception:  # pylint: disable=broad-exception-caught; silent
                pass
        if server_proc is not None:
            # pylint: disable-next=import-outside-toplevel; silent
            from harness.server.lifecycle import stop_server

            _ = stop_server(server_proc)
        if temp_dir is not None and not keep_temp_dir and project_dir is None:
            shutil.rmtree(temp_dir, ignore_errors=True)

    total_duration_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
    session_count = observer.session_count if observer else 0
    command_count = observer.command_count if observer else 0
    if not observer and telemetry_bridge is not None:
        command_count = len(telemetry_bridge._commands)  # pylint: disable=protected-access; silent

    summary = build_v3_summary(
        run_id=run_id,
        base_url=base_url,
        workflow_path=str(effective_workflow_path),
        output_dir=str(output_dir),
        temp_dir=str(temp_dir or ""),
        keep_temp_dir=keep_temp_dir,
        max_phase5_iter=max_phase5_iter,
        phase_results=phase_results,
        session_count=session_count,
        command_count=command_count,
        total_duration_seconds=total_duration_seconds,
        artifact_dir=artifact_dir,
        telemetry_paths=telemetry_paths,
        before_snapshot_path=before_snapshot_path,
        after_snapshot_path=after_snapshot_path,
        entry_script=entry_script,
        errors=errors,
    )

    _ = write_json(output_dir / "summary.json", asdict(summary))
    print_summary(summary)
    return 0 if summary.overall_status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
     # pylint: disable-next=line-too-long; silent
     description="Run YAML-driven migration_utils E2E migration workflow (V3 — supports custom workflow path)." )
    _ = parser.add_argument("--server-url", default=None)
    _ = parser.add_argument("--max-phase5-iter", type=positive_int, default=DEFAULT_MAX_PHASE5_ITER)
    _ = parser.add_argument("--keep-temp-dir", action="store_true")
    _ = parser.add_argument("--project-dir", type=Path, default=None)
    _ = parser.add_argument("--agent", type=str, default=None)
    _ = parser.add_argument("--output-dir", type=Path, default=None)
    _ = parser.add_argument("--user-constraints", type=Path, default=None)
    _ = parser.add_argument("--review-gate", action="store_true")
    _ = parser.add_argument("--framework-config", type=str, default=None)
    _ = parser.add_argument("--server-auto-start", action="store_true", default=True)
    _ = parser.add_argument("--server-no-auto-start", action="store_true")
    _ = parser.add_argument("--server-port", type=int, default=0)
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
    parser = build_parser()
    args = parser.parse_args()

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
        framework_config_path=args.framework_config,
        workflow_path=args.workflow_path,
    )


if __name__ == "__main__":
    sys.exit(main())
