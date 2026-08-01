"""Hardware-free fixture: real direct-run seal-to-continue lifecycle."""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

import harness.server.lifecycle as server_lifecycle
import harness.session.manager as manager_module
from harness.session.events import TransportObserver
from core.manifest_sealing_models import MANIFEST_SEALING_FILENAME
from core.run_manifest import RUN_MANIFEST_FILENAME
from . import e2e_test_v3 as target
from .e2e_v3_direct_seal_trace_support import (
    build_child_trace_client,
    build_parent_trace_client,
    child_trace_seeds,
)
from .e2e_v3_direct_seal_workflow import venv_phase2_response, write_workflow
from . import e2e_v3_runtime_fakes as _fakes
from .e2e_v3_runtime_fakes import ScriptedSessionManager, SessionScript


def _noop(*_args: object, **_kwargs: object) -> None:
    ...


def _fake_resolve(*_args: object, **_kwargs: object) -> tuple[str, None]:
    return ("http://opencode.test", None)


@dataclass(frozen=True, slots=True)
class DirectSealScenario:
    run_hex: str
    fail_point: str
    child_hex: str


@dataclass(frozen=True, slots=True)
class DirectSealParent:
    exit_code: int
    stdout: str
    report_dir: Path
    summary_path: Path
    run_manifest_path: Path
    sidecar_path: Path
    evidence_dir: Path
    workflow_path: Path
    run_id: str
    venv_python: str
    venv_dir: Path
    manager: ScriptedSessionManager


@dataclass(frozen=True, slots=True)
class ContinuationOutcome:
    exit_code: int
    stdout: str
    child_report_dir: Path
    child_run_id: str
    manager: ScriptedSessionManager


def read_session_id(manager: ScriptedSessionManager) -> str:
    for call in manager.calls:
        if call.path.startswith("/session/"):
            parts = call.path.split("/")
            if len(parts) >= 3:
                return parts[2]
    return ""


def _exclusive_scenario_root(tmp_path: Path, run_hex: str) -> Path:
    """Short, collision-resistant, exclusive scenario root under system temp.

    Uses the system temp dir (not pytest's deep ``tmp_path.parent`` tree)
    so trace session file paths stay well under Windows MAX_PATH (260).
    Derives a short hash from the resolved ``tmp_path`` identity AND the
    scenario run hex. Exclusive ``mkdir`` (no ``exist_ok``). Cleanup is
    registered via ``atexit``.
    """
    identity_source = f"{tmp_path.resolve()}:{run_hex}"
    short = hashlib.sha256(identity_source.encode("utf-8")).hexdigest()[:10]
    base = Path(tempfile.gettempdir()).resolve()
    root = base / f"dsc-{short}"
    root.mkdir()
    _ = atexit.register(shutil.rmtree, root, True)
    return root


def run_direct_seal_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: DirectSealScenario,
    *,
    seal_manifest: bool = True,
    save_trace: bool = False,
) -> DirectSealParent:
    root = _exclusive_scenario_root(tmp_path, scenario.run_hex)
    project_dir = root / "project"
    project_dir.mkdir()
    _ = (project_dir / "placeholder.txt").write_text("", encoding="utf-8")
    output_base = root / "output"
    output_base.mkdir()
    venv_dir = root / "venv"
    venv.create(venv_dir, with_pip=False, clear=True)
    venv_python = str(venv_dir / "Scripts" / "python.exe")
    if not Path(venv_python).exists():
        venv_python = str(venv_dir / "bin" / "python")
    phase2_response = venv_phase2_response(venv_python)
    workflow_path = write_workflow(root, scenario.fail_point, venv_python)
    session_id = f"ses-{scenario.run_hex[:8]}"
    monkeypatch.setattr(_fakes, "_SESSION_ID", session_id)
    managers: list[ScriptedSessionManager] = []

    def factory(
        *,
        work_dir: str,
        base_url: str,
        transport_observer: TransportObserver | None,
    ) -> ScriptedSessionManager:
        if save_trace:
            script = SessionScript(
                responses=(phase2_response, "{}", "{}", "{}", "{}", "{}", "{}", "{}"),
                trace_client=build_parent_trace_client(session_id),
            )
        else:
            script = SessionScript(
                responses=(phase2_response, "{}", "{}", "{}", "{}", "{}", "{}", "{}"),
            )
        m = ScriptedSessionManager(
            work_dir=work_dir, base_url=base_url,
            transport_observer=transport_observer,
            script=script,
        )
        managers.append(m)
        return m

    def _uuid() -> UUID:
        return UUID(hex=scenario.run_hex)

    monkeypatch.setattr(target, "OUTPUT_ROOT", root / "r")
    monkeypatch.setattr(target, "TEMPLATE_DIR", tmp_path / "no-template")
    monkeypatch.setattr(target, "uuid4", _uuid)
    monkeypatch.setattr(server_lifecycle, "resolve_server_url", _fake_resolve)
    monkeypatch.setattr(target, "check_server_running", _noop)
    monkeypatch.setattr(target, "log_server_diagnostics", _noop)
    monkeypatch.setattr(manager_module, "SessionManager", factory)
    argv = [
        "e2e_test_v3", "--project-dir", str(project_dir),
        "--workflow-path", str(workflow_path),
        "--output-dir", str(output_base),
        "--server-no-auto-start", "--opencode-readiness", "off",
    ]
    if seal_manifest:
        argv.append("--seal-manifest")
    if save_trace:
        argv.append("--save-agent-trace")
    monkeypatch.setattr(sys, "argv", argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = target.main()
    rid = f"e2e-v3-{scenario.run_hex[:12]}"
    rdir = root / "r" / rid
    return DirectSealParent(
        exit_code, buf.getvalue(), rdir,
        rdir / "summary.json", rdir / RUN_MANIFEST_FILENAME,
        rdir / MANIFEST_SEALING_FILENAME, rdir / "sealed-artifacts",
        workflow_path, rid, venv_python, venv_dir, managers[0],
    )


def run_direct_seal_continuation(
    monkeypatch: pytest.MonkeyPatch,
    scenario: DirectSealScenario,
    summary_path: Path,
    parent_report_dir: Path,
    *,
    save_trace: bool = False,
) -> ContinuationOutcome:
    session_id = f"ses-{scenario.child_hex[:8]}"
    monkeypatch.setattr(_fakes, "_SESSION_ID", session_id)
    managers: list[ScriptedSessionManager] = []

    def _child_uuid() -> UUID:
        return UUID(hex=scenario.child_hex)

    def factory(
        *,
        work_dir: str,
        base_url: str,
        transport_observer: TransportObserver | None,
    ) -> ScriptedSessionManager:
        if save_trace:
            script = SessionScript(
                responses=("{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}"),
                trace_client=build_child_trace_client(
                    session_id, f"{session_id}-c"
                ),
                trace_seeds=child_trace_seeds(session_id),
            )
        else:
            script = SessionScript(
                responses=("{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}"),
            )
        m = ScriptedSessionManager(
            work_dir=work_dir, base_url=base_url,
            transport_observer=transport_observer,
            script=script,
        )
        managers.append(m)
        return m

    monkeypatch.setattr(target, "uuid4", _child_uuid)
    monkeypatch.setattr(server_lifecycle, "resolve_server_url", _fake_resolve)
    monkeypatch.setattr(target, "check_server_running", _noop)
    monkeypatch.setattr(target, "log_server_diagnostics", _noop)
    monkeypatch.setattr(manager_module, "SessionManager", factory)
    argv = [
        "e2e_test_v3", "--continue-from", str(summary_path),
        "--server-no-auto-start", "--opencode-readiness", "off",
    ]
    if save_trace:
        argv.append("--save-agent-trace")
    monkeypatch.setattr(sys, "argv", argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = target.main()
    child_rid = f"e2e-v3-{scenario.child_hex[:12]}"
    return ContinuationOutcome(
        exit_code, buf.getvalue(),
        parent_report_dir.parent / child_rid, child_rid, managers[0],
    )


def resolve_site_packages(venv_python: str) -> Path:
    """Fail-loud, Python-minor-portable site-packages resolution.

    ``check=True`` surfaces any subprocess failure. Empty stdout is
    rejected before ``Path("")`` can be constructed. The result is
    resolved and verified as a real directory.
    """
    result = subprocess.run(
        [venv_python, "-c",
         "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, timeout=15,
        check=True,
    )
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"site-packages resolution returned empty output from {venv_python}"
        )
    sp = Path(stdout).resolve()
    if not sp.is_dir():
        raise RuntimeError(f"resolved site-packages is not a directory: {sp}")
    return sp
