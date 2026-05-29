# pyright: reportImplicitOverride=false

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, cast

import pytest

from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner
from core.prompt_loader import PromptLoader
from core.validator_engine import ValidatorEngine
from harness.session.manager import SessionManager

from . import BASE_URL, cleanup_remote_sessions, server_available

pytestmark = pytest.mark.skipif(not server_available(), reason="No OpenCode server")


class PhaseRunnerSessionManager(Protocol):
    def get_or_create(self, role: str, lifecycle: str) -> str: ...

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str: ...


class SessionManagerAdapter:
    backend: SessionManager

    def __init__(self, backend: SessionManager) -> None:
        self.backend = backend

    def get_or_create(self, role: str, lifecycle: str) -> str:
        active_lifecycle = cast(Literal["persistent", "reusable", "ephemeral"], lifecycle)
        return self.backend.get_or_create(role=role, lifecycle=active_lifecycle)

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
        return self.backend.send_command(session_id, command, timeout=timeout)


class DeterministicPromptLoader(PromptLoader):
    def load_prompt(self, phase_id: str, context: dict[str, str] | None = None) -> str:
        active_context = context or {}
        project_dir = Path(str(active_context.get("project_dir", ".")))
        venv_path = project_dir / ".venv"
        python_path = venv_path / "bin" / "python"
        entry_script_path = project_dir / "train.py"
        prompts = {
            "phase_0_env_detect": (
                'Return ONLY valid JSON: {"platform": "cuda", "npu_detected": false, '
                '"python_version": "3.10.12", "cann_version": "not_found", '
                '"ascendc_available": false, "driver_version": "not_found"}'
            ),
            "phase_1_project_analysis": (
                "Return ONLY valid JSON: "
                f'{{"project_dir": "{project_dir}", "dependencies": ["torch", "numpy"], '
                '"cuda_detected": true, "entry_script": "train.py"}}'
            ),
            "phase_2_venv_create": (
                "Return ONLY valid JSON: "
                f'{{"venv_path": "{venv_path}", '
                f'"python_path": "{python_path}", '
                '"installed_packages": ["torch", "torch_npu"]}'
            ),
            "phase_3_entry_script": (
                "Return ONLY valid JSON: "
                f'{{"entry_script_path": "{entry_script_path}", '
                '"run_command": "python train.py"}}'
            ),
            "phase_35_static_validate": (
                "Return ONLY valid JSON: "
                '{"validation_passed": true, "issues": [], '
                '"fix_plan": "Entry command is non-interactive and safe to run."}'
            ),
        }
        return prompts[phase_id]


def _without_meta(output: dict[str, object]) -> dict[str, object]:
    payload = dict(output)
    _ = payload.pop("_meta", None)
    return payload


def test_run_phase_0_to_3_with_real_session_manager(tmp_path: Path) -> None:
    project_dir = tmp_path / "mock_project"
    project_dir.mkdir()
    _ = (project_dir / "train.py").write_text("print('hello')\n", encoding="utf-8")

    artifact_store = ArtifactStore(str(tmp_path), "integration-run")
    session_mgr = SessionManager(work_dir=str(project_dir), base_url=BASE_URL, timeout=15.0)
    runner_session_mgr: PhaseRunnerSessionManager = SessionManagerAdapter(session_mgr)
    runner = PhaseRunner(
        session_mgr=runner_session_mgr,
        artifact_store=artifact_store,
        prompt_loader=DeterministicPromptLoader(),
        validator=ValidatorEngine(),
    )

    try:
        outputs = runner.run_phase_0_to_3(str(project_dir), runner_session_mgr, artifact_store)
    finally:
        cleanup_remote_sessions(session_mgr)

    assert list(outputs) == [
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_2_venv_create",
        "phase_3_entry_script",
        "phase_35_static_validate",
    ]
    assert outputs["phase_0_env_detect"]["python_version"] == "3.10.12"
    assert outputs["phase_1_project_analysis"]["project_dir"] == str(project_dir)
    assert outputs["phase_2_venv_create"]["venv_path"] == str(project_dir / ".venv")
    assert outputs["phase_3_entry_script"]["entry_script_path"] == str(project_dir / "train.py")
    assert outputs["phase_35_static_validate"]["validation_passed"] is True

    assert artifact_store.load_phase_output("0_env_detect") == _without_meta(
        outputs["phase_0_env_detect"]
    )
    assert artifact_store.load_phase_output("1_project_analysis") == _without_meta(
        outputs["phase_1_project_analysis"]
    )
    assert artifact_store.load_phase_output("2_venv_create") == _without_meta(
        outputs["phase_2_venv_create"]
    )
    assert artifact_store.load_phase_output("3_entry_script") == _without_meta(
        outputs["phase_3_entry_script"]
    )
    assert artifact_store.load_phase_output("35_static_validate") == _without_meta(
        outputs["phase_35_static_validate"]
    )
    journal = artifact_store.get_journal()
    assert [entry["phase_id"] for entry in journal if entry["status"] == "succeeded"] == [
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_2_venv_create",
        "phase_3_entry_script",
        "phase_35_static_validate",
    ]
    assert {entry["status"] for entry in journal} <= {"succeeded", "validation_failed"}
