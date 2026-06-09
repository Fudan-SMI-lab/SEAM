# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedParameter=false

import json
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from typing_extensions import override

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner, PhaseSpec, SessionManagerLike
from core.prompt_loader import PromptLoader
from core.types import PhaseDefinition, RuntimeSkillsConfig, SubWorkflowDefinition, WorkflowDefinition
from core.validator_engine import ValidationResult, ValidatorEngine
from validators.validate_project_analysis import validate as validate_project_analysis


class MockSession:
    responses: list[str]
    calls: list[tuple[str, int | None]]
    session_id: str

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = []
        self.session_id = "mock-session"

    def send_command(self, prompt: str, timeout: int | None = 600) -> str:
        self.calls.append((prompt, timeout))
        if not self.responses:
            raise AssertionError("MockSession exhausted responses")
        return self.responses.pop(0)


class NoopSessionManager:
    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        return f"{role}-{lifecycle}"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        raise AssertionError(f"Unexpected manager send for {session_id}: {command} ({timeout})")


class RecordingSessionManager:
    def __init__(self, response: str) -> None:
        self.response: str = response
        self.get_or_create_calls: list[dict[str, str]] = []
        self.send_calls: list[tuple[str, str, int | None, int | None]] = []

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        self.get_or_create_calls.append({"role": role, "lifecycle": lifecycle})
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        self.send_calls.append((session_id, command, timeout, retries))
        return self.response


class MockSessionManager:
    responses: dict[str, list[str]]
    get_or_create_calls: list[dict[str, str]]
    send_calls: list[tuple[str, str, int | None]]

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self.responses = {key: list(value) for key, value in responses.items()}
        self.get_or_create_calls = []
        self.send_calls = []

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        self.get_or_create_calls.append({"role": role, "lifecycle": lifecycle})
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        self.send_calls.append((session_id, command, timeout))
        if command.startswith("# Phase 3.5"):
            return self.responses["phase_35"].pop(0)
        if "Phase 3" in command:
            return self.responses["phase_3"].pop(0)
        if "Phase 2" in command:
            return self.responses["phase_2"].pop(0)
        if "Phase 1" in command:
            return self.responses["phase_1"].pop(0)
        if "Phase 0" in command:
            return self.responses["phase_0"].pop(0)
        raise AssertionError(f"Unexpected prompt: {command}")


class StaticPromptLoader(PromptLoader):
    @override
    def load_prompt(self, phase_id: str, context: dict[str, str] | None = None) -> str:
        del context
        return f"BASE PROMPT {phase_id}"


def build_runner(base_dir: Path, session_mgr: SessionManagerLike | None = None) -> tuple[PhaseRunner, ArtifactStore]:
    artifact_store = ArtifactStore(str(base_dir), "testrun")
    runner = PhaseRunner(
        session_mgr=session_mgr or NoopSessionManager(),
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )
    return runner, artifact_store


def write_tiny_cuda_custom_op_project(root: Path) -> None:
    scripts_dir = root / "test_data_and_scripts"
    scripts_dir.mkdir(parents=True)
    _ = (scripts_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    _ = (root / "requirements.txt").write_text("torch\n", encoding="utf-8")
    _ = (root / "kernel.cu").write_text('extern "C" void add_cuda() { }\n', encoding="utf-8")


def write_runtime_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    _ = skill_path.write_text(content, encoding="utf-8")
    return skill_path


def runtime_workflow(
    phases: list[PhaseDefinition] | None = None,
    sub_workflows: dict[str, SubWorkflowDefinition] | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="runtime-test",
        version="1.0",
        phases=phases or [],
        terminals=["complete"],
        agents={
            "main_engineer": {
                "role": "main_engineer",
                "lifecycle": "persistent",
                "runtime_skills": RuntimeSkillsConfig(include=["agent-skill"]),
            }
        },
        sub_workflows=sub_workflows or {},
    )


def valid_phase_0_output() -> dict[str, object]:
    return {
        "platform": "npu",
        "npu_detected": True,
        "python_version": "3.10.12",
        "cann_version": "8.0.RC1",
        "ascendc_available": True,
        "driver_version": "24.1",
    }


def test_run_single_phase_saves_phase_0_output(tmp_path: Path) -> None:
    runner, artifact_store = build_runner(tmp_path)
    session = MockSession([json.dumps(valid_phase_0_output())])

    result = runner.run_single_phase(session, "phase_0", {})

    assert result["platform"] == "npu"
    assert result["npu_detected"] is True
    assert "python_version" in result

    saved = artifact_store.load_phase_output("0_env_detect")
    assert saved is not None
    assert saved["platform"] == "npu"
    assert saved["npu_detected"] is True
    assert session.calls[0][1] is None


def test_run_single_phase_appends_agent_and_phase_runtime_skills(tmp_path: Path) -> None:
    _ = write_runtime_skill(tmp_path, "agent-skill", "# Agent Skill\n\nAgent guidance")
    _ = write_runtime_skill(tmp_path, "phase-skill", "# Phase Skill\n\nPhase guidance")
    workflow = runtime_workflow(
        phases=[
            PhaseDefinition(
                id="phase_0_env_detect",
                name="Phase 0",
                prompt_template="phase_0_env_detect_npu",
                output_schema={},
                validator="env_detect",
                agent="main_engineer",
                runtime_skills=RuntimeSkillsConfig(
                    include=["phase-skill"],
                    inject_full=True,
                ),
            )
        ]
    )
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    runner = PhaseRunner(
        NoopSessionManager(),
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        workflow=workflow,
        framework_config={"runtime_skill_repo_root": str(tmp_path)},
    )
    session = MockSession([json.dumps(valid_phase_0_output())])

    result = runner.run_single_phase(session, "phase_0", {"project_dir": str(tmp_path)})

    assert result["platform"] == "npu"
    sent_prompt = session.calls[0][0]
    assert sent_prompt.startswith("BASE PROMPT phase_0_env_detect_npu\n\n## Explicit Runtime Skills")
    assert "### agent-skill" in sent_prompt
    assert "### phase-skill" in sent_prompt
    assert "Agent guidance" in sent_prompt
    assert "Phase guidance" in sent_prompt


def test_old_constructor_without_workflow_does_not_inject_runtime_skills(tmp_path: Path) -> None:
    _ = write_runtime_skill(tmp_path, "agent-skill", "# Agent Skill\n\nAgent guidance")
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    runner = PhaseRunner(
        NoopSessionManager(),
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
    )
    session = MockSession([json.dumps(valid_phase_0_output())])

    _ = runner.run_single_phase(session, "phase_0", {"project_dir": str(tmp_path)})

    sent_prompt = session.calls[0][0]
    assert "## Explicit Runtime Skills" not in sent_prompt


def test_run_phase_1_5_appends_runtime_skills(tmp_path: Path) -> None:
    _ = write_runtime_skill(tmp_path, "agent-skill", "# Agent Skill\n\nAgent guidance")
    _ = write_runtime_skill(tmp_path, "constraint-skill", "# Constraint Skill\n\nConstraint guidance")
    workflow = runtime_workflow(
        phases=[
            PhaseDefinition(
                id="phase_1_5_constraint_summary",
                name="Phase 1.5",
                prompt_template="phase_1_5_constraint_summary",
                output_schema={},
                agent="main_engineer",
                runtime_skills=RuntimeSkillsConfig(
                    include=["constraint-skill"],
                    inject_full=True,
                ),
            )
        ]
    )
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = RecordingSessionManager(
        json.dumps({"constraint_summary": "Use NPU only", "constraint_count": 1})
    )
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        workflow=workflow,
        framework_config={"runtime_skills": {"repo_root": str(tmp_path)}},
    )

    summary = runner.run_phase_1_5(
        "persistent-main",
        session_mgr,
        artifact_store,
        project_dir=str(tmp_path),
        user_constraints="Use NPU only",
    )

    assert summary == "Use NPU only"
    sent_prompt = session_mgr.send_calls[0][1]
    assert "## Explicit Runtime Skills" in sent_prompt
    assert "### agent-skill" in sent_prompt
    assert "### constraint-skill" in sent_prompt
    assert "Constraint guidance" in sent_prompt


def test_run_review_check_maps_subworkflow_runtime_skills(tmp_path: Path) -> None:
    _ = write_runtime_skill(tmp_path, "agent-skill", "# Agent Skill\n\nAgent guidance")
    _ = write_runtime_skill(tmp_path, "review-skill", "# Review Skill\n\nReview guidance")
    workflow = runtime_workflow(
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                phases=[
                    {
                        "id": "review_gate",
                        "type": "review",
                        "agent": "main_engineer",
                        "prompt_template": "phase_5_review",
                        "runtime_skills": {
                            "include": ["review-skill"],
                            "inject_full": True,
                        },
                    }
                ],
            )
        }
    )
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = RecordingSessionManager(
        json.dumps(
            {
                "verdict": "accept",
                "cpu_fallback_detected": False,
                "cpu_fallback_necessary": False,
                "alternative_suggestions": "",
                "reasoning": "ok",
            }
        )
    )
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        workflow=workflow,
        framework_config={"runtime_skill_repo_root": str(tmp_path)},
    )

    result = runner.run_review_check(
        "review-session",
        session_mgr,
        str(tmp_path),
        repair_history="| Iteration | Status |",
    )

    assert result["verdict"] == "accept"
    sent_prompt = session_mgr.send_calls[0][1]
    assert "## Explicit Runtime Skills" in sent_prompt
    assert "### agent-skill" in sent_prompt
    assert "### review-skill" in sent_prompt
    assert "Review guidance" in sent_prompt


def test_run_review_check_session_error_envelope_fails_closed(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = RecordingSessionManager(
        '{"ok": false, "error": "Compaction response is incomplete"}'
    )
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
    )

    result = runner.run_review_check(
        "review-session",
        session_mgr,
        str(tmp_path),
        repair_history="| Iteration | Status |",
    )

    assert result["verdict"] == "session_error"
    assert result["session_error"] == "Compaction response is incomplete"
    assert "Compaction response is incomplete" in str(result["reasoning"])
    assert len(session_mgr.send_calls) == 1


def test_run_review_check_json_example_text_not_session_error(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")

    class SequentialReviewSessionManager(NoopSessionManager):
        def __init__(self) -> None:
            self.responses: list[str] = [
                'Example envelope: {"ok": false, "error": "not transport"}\n'
                + '{"verdict": "accept", "cpu_fallback_detected": false, '
                + '"cpu_fallback_necessary": false, "alternative_suggestions": "", "reasoning": "ok"}',
                '{"verdict": "accept", "cpu_fallback_detected": false, '
                + '"cpu_fallback_necessary": false, "alternative_suggestions": "", "reasoning": "ok"}',
            ]
            self.send_calls: list[tuple[str, str, int | None]] = []

        @override
        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            self.send_calls.append((session_id, command, timeout))
            return self.responses.pop(0)

    session_mgr = SequentialReviewSessionManager()
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
    )

    result = runner.run_review_check(
        "review-session",
        session_mgr,
        str(tmp_path),
        repair_history="| Iteration | Status |",
    )

    assert result["verdict"] == "accept"
    assert "session_error" not in result
    assert len(session_mgr.send_calls) == 1


def test_validation_failure_retries_are_written_to_journal(tmp_path: Path) -> None:
    runner, artifact_store = build_runner(tmp_path)
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])

    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    journal = artifact_store.get_journal()
    assert [entry["attempt"] for entry in journal] == [1, 2]
    assert [entry["status"] for entry in journal] == ["validation_failed", "validation_failed"]


def test_retry_sends_correction_prompt_not_full_prompt(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path)
    session = MockSession([
        json.dumps({"npu_detected": True}),
        json.dumps({"platform": "npu", "npu_detected": True}),
    ])

    def retrying_env_detect_validator(data: dict[str, object]) -> dict[str, object]:
        if "platform" not in data:
            return {
                "passed": False,
                "errors": ["Missing required field 'platform'"],
                "warnings": [],
            }
        return {"passed": True, "errors": [], "warnings": []}

    runner.validator.register_validator("env_detect", retrying_env_detect_validator)

    result = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    assert result["platform"] == "npu"
    assert len(session.calls) == 2
    first_prompt, _ = session.calls[0]
    second_prompt, _ = session.calls[1]
    assert second_prompt != first_prompt
    assert "failed validation" in second_prompt
    assert "Missing required field 'platform'" in second_prompt
    assert "Required or invalid fields called out by validation: platform." in second_prompt


def test_correction_prompt_includes_error_details(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path)
    session = MockSession([
        json.dumps({"npu_detected": "yes"}),
        json.dumps({"platform": "npu", "npu_detected": True}),
    ])

    def detailed_env_detect_validator(data: dict[str, object]) -> dict[str, object]:
        errors: list[str] = []
        if "platform" not in data:
            errors.append("Missing required field 'platform'")
        if not isinstance(data.get("npu_detected"), bool):
            errors.append("field 'npu_detected' must be a boolean")
        return {"passed": not errors, "errors": errors, "warnings": []}

    runner.validator.register_validator("env_detect", detailed_env_detect_validator)

    _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    correction_prompt, _ = session.calls[1]
    assert "Missing required field 'platform'" in correction_prompt
    assert "field 'npu_detected' must be a boolean" in correction_prompt
    assert "Required or invalid fields called out by validation: platform, npu_detected." in correction_prompt


def test_correction_prompt_tells_custom_op_agent_to_create_missing_script(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path)
    phase = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")
    validation = ValidationResult(
        passed=False,
        errors=["entry_script_path must point to an existing file for custom-op contracts"],
        warnings=[],
    )

    correction_prompt = runner._build_correction_prompt(
        phase=phase,
        validation=validation,
        previous_prompt="phase 3 prompt",
    )

    assert "create or select the referenced custom-op validation script" in correction_prompt
    assert "entry_script_path points to a real file" in correction_prompt


def test_run_phase_0_to_3_uses_persistent_main_engineer_session(tmp_path: Path) -> None:
    responses = {
        "phase_0": [json.dumps(valid_phase_0_output())],
        "phase_1": [
            json.dumps(
                {
                    "project_dir": str(tmp_path),
                    "dependencies": ["torch", "numpy"],
                    "cuda_detected": False,
                    "entry_script": "train.py",
                }
            )
        ],
        "phase_2": [
            json.dumps(
                {
                    "venv_path": str(tmp_path / ".venv"),
                    "python_path": str(tmp_path / ".venv" / "bin" / "python"),
                    "installed_packages": ["torch", "torch_npu"],
                }
            )
        ],
        "phase_3": [
            json.dumps(
                {
                    "entry_script_path": str(tmp_path / "train.py"),
                    "run_command": f"{tmp_path / '.venv' / 'bin' / 'python'} train.py",
                }
            )
        ],
        "phase_35": [
            json.dumps(
                {
                    "validation_passed": True,
                    "issues": [],
                    "fix_plan": "No issues found. Script is headless-compliant.",
                }
            )
        ],
    }
    session_mgr = MockSessionManager(responses)
    runner, artifact_store = build_runner(tmp_path, session_mgr=session_mgr)

    outputs = runner.run_phase_0_to_3(str(tmp_path), session_mgr, artifact_store)

    assert session_mgr.get_or_create_calls == [
        {"role": "main_engineer", "lifecycle": "persistent"}
    ]
    assert list(outputs) == [
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_2_venv_create",
        "phase_3_entry_script",
        "phase_35_static_validate",
    ]


class Phase6SessionManager:
    responses: dict[str, list[str]]
    phase_6_response: str

    def __init__(
        self,
        phase_responses: dict[str, list[str]] | None = None,
        phase_6_response: str = "",
    ) -> None:
        self.responses = {k: list(v) for k, v in (phase_responses or {}).items()}
        self.phase_6_response = phase_6_response
        self.get_or_create_calls: list[dict[str, str]] = []
        self.send_calls: list[tuple[str, str, int | None, int | None]] = []

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        self.get_or_create_calls.append({"role": role, "lifecycle": lifecycle})
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        self.send_calls.append((session_id, command, timeout, retries))
        if "Phase 6" in command or "phase_6" in command:
            return self.phase_6_response
        for phase_key in ("phase_3", "phase_2", "phase_1", "phase_0"):
            if f"Phase {phase_key[-1]}" in command:
                return self.responses[phase_key].pop(0)
        raise AssertionError(f"Unexpected prompt: {command}")


class Phase6TimeoutSessionManager(Phase6SessionManager):
    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        self.send_calls.append((session_id, command, timeout, retries))
        raise TimeoutError("phase 6 timed out")


def test_run_phase_6_saves_reports_and_manifest(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")

    for candidate, data in {
        "phase_0_env_detect": {"platform": "npu", "npu_detected": True, "python_version": "3.10.12"},
        "phase_1_project_analysis": {
            "project_dir": str(tmp_path),
            "dependencies": ["torch", "numpy"],
            "cuda_detected": False,
            "entry_script": "train.py",
        },
        "phase_2_venv_create": {
            "venv_path": str(tmp_path / ".venv"),
            "python_path": str(tmp_path / ".venv" / "bin" / "python"),
            "installed_packages": ["torch", "torch_npu"],
        },
        "phase_3_entry_script": {
            "entry_script_path": str(tmp_path / "train.py"),
            "run_command": f"{tmp_path / '.venv' / 'bin' / 'python'} train.py",
        },
    }.items():
        _ = artifact_store.save_phase_output(candidate, data, attempt=1)
        _ = artifact_store.mark_validated(candidate, data)

    report_dir = os.path.join(artifact_store.artifact_dir, "reports")
    expected_paths = [
        os.path.join(report_dir, "API_KEY_REPORT.md"),
        os.path.join(report_dir, "OPENCODE_OPERATIONS_LOG.md"),
        os.path.join(report_dir, "TOOLS_EXECUTION_REPORT.md"),
        os.path.join(report_dir, "SUMMARY_REPORT.md"),
        os.path.join(report_dir, "LOCAL_TOOL_OPTIMIZATION_REPORT.md"),
    ]

    phase_6_json = json.dumps({
        "report_paths": expected_paths,
        "migration_summary": {"files_migrated": 12, "files_skipped": 3},
    })

    session_mgr = Phase6SessionManager(phase_6_response=phase_6_json)

    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)

    assert result["phase_id"] == "phase_6_report"
    assert result["report_paths"] == expected_paths
    assert result["migration_summary"] == {"files_migrated": 12, "files_skipped": 3}

    saved = artifact_store.load_phase_output("phase_6_report")
    assert saved is not None
    assert saved["phase_id"] == "phase_6_report"

    journal = artifact_store.get_journal()
    phase_6_entries = [e for e in journal if e["phase_id"] == "phase_6_report"]
    assert len(phase_6_entries) == 1


def test_run_phase_6_fallback_on_session_error(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    artifact_store.save_phase_output(
        "phase_5_validation",
        {"status": "success", "script_exit_code": 0},
        attempt=1,
    )
    artifact_store.mark_validated(
        "phase_5_validation",
        {"status": "success", "script_exit_code": 0},
    )

    session_mgr = Phase6SessionManager(
        phase_6_response=json.dumps({"ok": False, "error": "Session still running"})
    )
    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)
    summary = cast(dict[str, object], result["migration_summary"])
    report_paths = cast(list[str], result["report_paths"])

    assert result["fallback"] is True
    assert summary["overall_status"] == "pass"
    assert summary["files_migrated"] == 0
    assert summary["files_skipped"] == 0
    assert summary["phase5_status"] == "success"
    assert summary["migration_success"] is True
    assert summary["phase5_terminal_failure"] is False
    assert session_mgr.send_calls[0][2] == 3600
    assert session_mgr.send_calls[0][3] == 0
    assert all(Path(path).exists() for path in report_paths)

    saved = artifact_store.load_phase_output("phase_6_report")
    assert saved is not None
    assert saved["fallback"] is True

    phase_6_entries = [e for e in artifact_store.get_journal() if e["phase_id"] == "phase_6_report"]
    assert phase_6_entries[-1]["status"] == "fallback"


def test_run_phase_6_fallback_preserves_phase5_success_as_success(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    artifact_store.save_phase_output(
        "phase_5_validation",
        {"status": "success", "script_exit_code": 0},
        attempt=1,
    )
    artifact_store.mark_validated(
        "phase_5_validation",
        {"status": "success", "script_exit_code": 0},
    )
    session_mgr = Phase6SessionManager(
        phase_6_response=json.dumps({"ok": False, "error": "Conversation history too large to compact"})
    )
    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)
    summary = cast(dict[str, object], result["migration_summary"])
    report_paths = cast(list[str], result["report_paths"])
    summary_report = Path(report_paths[0]).read_text(encoding="utf-8")

    assert result["fallback"] is True
    assert summary["overall_status"] == "pass"
    assert summary["migration_success"] is True
    assert summary["phase5_status"] == "success"
    assert summary["phase5_terminal_failure"] is False
    assert "- Phase 5 status: success" in summary_report
    assert "- Migration success: True" in summary_report
    assert "- Phase 5 terminal failure: False" in summary_report


def test_run_phase_6_fallback_on_timeout_exception(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = Phase6TimeoutSessionManager()
    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)
    report_paths = cast(list[str], result["report_paths"])

    assert result["fallback"] is True
    assert result["fallback_reason"] == "phase 6 timed out"
    assert session_mgr.send_calls[0][2] == 3600
    assert session_mgr.send_calls[0][3] == 0
    assert all(Path(path).exists() for path in report_paths)


def test_run_phase_6_fallback_on_incomplete_response(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = Phase6SessionManager(phase_6_response=json.dumps({"raw_response": "no reports"}))
    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)
    summary = cast(dict[str, object], result["migration_summary"])
    report_paths = cast(list[str], result["report_paths"])

    assert result["fallback"] is True
    assert summary["overall_status"] == "partial"
    assert all(Path(path).exists() for path in report_paths)


def test_run_phase_6_appends_runtime_skills(tmp_path: Path) -> None:
    _ = write_runtime_skill(tmp_path, "agent-skill", "# Agent Skill\n\nAgent guidance")
    _ = write_runtime_skill(tmp_path, "report-skill", "# Report Skill\n\nReport guidance")
    workflow = runtime_workflow(
        phases=[
            PhaseDefinition(
                id="phase_6_report",
                name="Phase 6",
                prompt_template="phase_6_report",
                output_schema={},
                agent="main_engineer",
                runtime_skills=RuntimeSkillsConfig(
                    include=["report-skill"],
                    inject_full=True,
                ),
            )
        ]
    )
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = RecordingSessionManager(
        json.dumps({"report_paths": [], "migration_summary": {}})
    )
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        workflow=workflow,
        framework_config={"runtime_skill_repo_root": str(tmp_path)},
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)

    assert result["phase_id"] == "phase_6_report"
    sent_prompt = session_mgr.send_calls[0][1]
    assert "## Explicit Runtime Skills" in sent_prompt
    assert "### agent-skill" in sent_prompt
    assert "### report-skill" in sent_prompt
    assert "Report guidance" in sent_prompt


def test_run_phase_0_to_1_returns_outputs(tmp_path: Path) -> None:
    class MockSM:
        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            return "sess"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            del session_id, command, timeout
            return '{"ok": true}'

    def always_pass(_data: dict[str, object]) -> dict[str, object]:
        return {"passed": True, "errors": [], "warnings": []}

    session_mgr = MockSM()
    store = ArtifactStore(str(tmp_path), "test-run")
    loader = PromptLoader()
    engine = ValidatorEngine()
    runner = PhaseRunner(session_mgr, store, loader, engine)

    for name in ("env_detect", "project_analysis"):
        engine.register_validator(name, always_pass)

    outputs = runner.run_phase_0_to_1(str(tmp_path), session_mgr, store)
    assert "phase_0_env_detect" in outputs
    assert "phase_1_project_analysis" in outputs


def test_run_phase_0_to_1_accepts_user_constraints() -> None:
    import inspect

    sig = inspect.signature(PhaseRunner.run_phase_0_to_1)
    assert "user_constraints" in sig.parameters


def test_run_phase_2_to_3_accepts_constraint_summary() -> None:
    import inspect

    sig = inspect.signature(PhaseRunner.run_phase_2_to_3)
    assert "constraint_summary" in sig.parameters


def test_build_prompt_context_has_constraint_keys() -> None:
    class MockSM:
        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            return "x"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            del session_id, command, timeout
            return "{}"

    runner = PhaseRunner(MockSM(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis")
    ctx: dict[str, object] = {
        "project_dir": "/tmp",
        "previous_outputs": {},
        "constraint_summary": "R1",
        "user_constraints": "UC",
    }
    result = runner._build_prompt_context(spec, ctx)
    assert result["constraint_summary"] == "R1"
    assert result["user_constraints"] == "UC"


def test_phase_runner_phase3_legacy_output_fails_when_custom_op_context_required() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "notes": "project uses custom operator bindings via torch.ops",
                }
            }
        },
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is False
    assert any("required_report_paths" in error for error in validation.errors)


def test_phase_runner_phase3_legacy_output_passes_without_custom_op_context() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {"previous_outputs": {"phase_1_project_analysis": {"notes": "plain training script"}}},
    )

    assert "entry_script_kind" not in normalized
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is True


def test_phase_runner_phase3_negative_custom_op_notes_do_not_force_custom_op_context() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    for notes in (
        "no custom operators found",
        "no CUDA custom operators",
        "custom_op_detected: false",
    ):
        normalized = runner._normalize_output(
            spec,
            {"entry_script_path": "train.py", "run_command": "python train.py"},
            {"project_dir": "/tmp/project"},
            {"previous_outputs": {"phase_1_project_analysis": {"notes": notes}}},
        )

        assert "entry_script_kind" not in normalized
        validation = runner.validator.validate("entry_script", normalized)
        assert validation.passed is True


def test_phase_runner_phase3_structured_custom_op_surface_controls_custom_op_context() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    false_surface = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "custom_op_surface": {
                        "custom_op_detected": False,
                        "operator_families": ["custom operators not present"],
                    },
                    "notes": "looked for torch.ops and found no custom operators",
                }
            },
            "previous_outputs_text": "looked for torch.ops",
        },
    )
    assert "entry_script_kind" not in false_surface
    validation = runner.validator.validate("entry_script", false_surface)
    assert validation.passed is True

    true_surface = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "custom_op_surface": {
                        "custom_op_detected": True,
                        "fine_grained_operator_units": ["my_kernel_forward"],
                    }
                }
            }
        },
    )
    assert true_surface["entry_script_kind"] == "custom_op_full_validation"

    contract_output = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_3_entry_script": {
                    "operator_discovery_sources": ["source", "bindings"],
                    "validation_obligations": ["runtime_project_api"],
                }
            }
        },
    )
    assert contract_output["entry_script_kind"] == "custom_op_full_validation"


def test_phase_35_prompt_context_includes_previous_outputs() -> None:
    class MockSM:
        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            return "x"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            del session_id, command, timeout
            return "{}"

    runner = PhaseRunner(MockSM(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_35", "phase_35_static_validate", "entry_static")
    ctx: dict[str, object] = {
        "project_dir": "/tmp/project",
        "previous_outputs": {
            "phase_3_entry_script": {
                "entry_script_path": "/tmp/project/migration_reports/final_evidence_validate.py",
                "entry_script_kind": "custom_op_full_validation",
                "reports_dir": "/tmp/project/migration_reports",
                "required_checks": ["remaining_entries_zero"],
            }
        },
    }

    result = runner._build_prompt_context(spec, ctx)

    assert result["entry_script_path"] == "/tmp/project/migration_reports/final_evidence_validate.py"
    assert "phase_3_entry_script" in result["previous_outputs"]
    assert "custom_op_full_validation" in result["previous_outputs"]


def test_phase_35_phase3_shaped_response_becomes_retryable_static_failure(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path)
    spec = PhaseSpec("phase_35", "phase_35_static_validate", "entry_static")

    result = runner._normalize_output(
        spec,
        {
            "entry_script_path": str(tmp_path / "validate_entry.py"),
            "run_command": f"{tmp_path / '.venv' / 'bin' / 'python'} validate_entry.py",
            "runtime_entry_script_revision_allowed": True,
        },
        {"project_dir": str(tmp_path)},
        {
            "project_dir": str(tmp_path),
            "previous_outputs": {
                "phase_3_entry_script": {
                    "entry_script_path": str(tmp_path / "demo_gradio.py"),
                    "run_command": "python demo_gradio.py",
                }
            },
        },
    )

    issues = cast(list[str], result["issues"])
    assert result["validation_passed"] is False
    assert "Phase 3.5 returned a Phase 3 entry-script contract" in issues[0]
    assert "Retry Phase 3" in str(result["fix_plan"])
    proposed = cast(dict[str, object], result["proposed_phase_3_entry_script"])
    assert proposed["entry_script_path"] == str(tmp_path / "validate_entry.py")
    assert proposed["runtime_entry_script_revision_allowed"] is True


def test_run_phase_6_uses_persistent_session(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")

    session_mgr = Phase6SessionManager(
        phase_6_response=json.dumps({
            "report_paths": [],
            "migration_summary": {"files_migrated": 0, "files_skipped": 0},
        })
    )

    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    _ = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)

    assert session_mgr.get_or_create_calls == [
        {"role": "main_engineer", "lifecycle": "persistent"}
    ]



def test_run_phase_2_to_3_retries_phase_3_after_phase_35_failure(tmp_path: Path) -> None:
    class RetrySessionManager:
        phase3_prompts: list[str]
        phase35_prompts: list[str]
        phase3_outputs: list[dict[str, object]]
        phase35_outputs: list[dict[str, object]]

        def __init__(self) -> None:
            self.phase3_prompts = []
            self.phase35_prompts = []
            self.phase3_outputs = [
                {
                    "entry_script_path": str(tmp_path / "bad.py"),
                    "run_command": "python bad.py",
                },
                {
                    "entry_script_path": str(tmp_path / "good.py"),
                    "run_command": "python good.py",
                },
            ]
            self.phase35_outputs = [
                {
                    "validation_passed": False,
                    "issues": ["interactive input remains"],
                    "fix_plan": "Choose a non-interactive entry point.",
                },
                {
                    "validation_passed": False,
                    "issues": ["interactive input remains"],
                    "fix_plan": "Choose a non-interactive entry point.",
                },
                {
                    "validation_passed": True,
                    "issues": [],
                    "fix_plan": "No issues found. Script is headless-compliant.",
                },
            ]

        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            del role, lifecycle
            return "persistent-main"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            del session_id, timeout
            if command.startswith("# Phase 2"):
                return json.dumps(
                    {
                        "venv_path": str(tmp_path / ".venv"),
                        "python_path": str(tmp_path / ".venv" / "bin" / "python"),
                        "installed_packages": ["torch", "torch_npu"],
                    }
                )
            if command.startswith("# Phase 3.5") or command.startswith("Your previous output for phase_35_static_validate"):
                self.phase35_prompts.append(command)
                return json.dumps(self.phase35_outputs.pop(0))
            if command.startswith("# Phase 3 -") or command.startswith("Your previous output for phase_3_entry_script"):
                self.phase3_prompts.append(command)
                return json.dumps(self.phase3_outputs.pop(0))
            raise AssertionError(f"Unexpected prompt: {command}")

    session_mgr = RetrySessionManager()
    runner, artifact_store = build_runner(tmp_path, session_mgr=session_mgr)
    runner.max_retry = 2

    outputs = runner.run_phase_2_to_3(
        str(tmp_path),
        session_mgr,
        artifact_store,
        prior_outputs={
            "phase_0_env_detect": valid_phase_0_output(),
            "phase_1_project_analysis": {
                "project_dir": str(tmp_path),
                "dependencies": ["torch"],
                "cuda_detected": False,
                "entry_script": "train.py",
            },
        },
    )

    assert len(session_mgr.phase3_prompts) == 2
    assert len(session_mgr.phase35_prompts) == 3
    assert "Phase 3.5 VALIDATION FAILED" in session_mgr.phase3_prompts[1]
    assert "interactive input remains" in session_mgr.phase3_prompts[1]
    assert outputs["phase_3_entry_script"]["run_command"] == "python good.py"
    assert outputs["phase_35_static_validate"]["validation_passed"] is True
    assert "phase_35_static_validate_failure" not in outputs


def custom_op_phase3_output(tmp_path: Path, *, create_script: bool = True) -> dict[str, object]:
    script_path = tmp_path / "validate_custom_ops_full.py"
    if create_script:
        _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")
    return {
        "entry_script_path": str(script_path),
        "run_command": f"{tmp_path / '.venv' / 'bin' / 'python'} {script_path}",
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(tmp_path / "migration_reports"),
        "operator_discovery_sources": [
            "source",
            "bindings",
            "wrappers",
            "autograd",
            "aliases",
            "launch",
            "setup",
            "tests",
        ],
        "operator_inventory_schema": {
            "semantic_rows": "one row per fine-grained source-discovered operator unit",
            "fine_grained_operator_units": "complete source-discovered unit list",
            "unit_identity": "stable unit id",
            "variant_or_signature": "source-discovered variant/signature",
            "native_operator_symbols": "native/exported symbols per row",
            "kernel_functions": "CUDA/Ascend kernel functions per row",
            "kernel_launch_sites": "kernel launch sites per row",
            "public_entry_mapping": "public API to unit mapping per row",
            "source_evidence": "source files/functions per row",
            "inventory_granularity": "fine_grained",
            "out_of_scope_source_groups": "excluded source families with reason",
        },
        "required_report_paths": [
            f"{tmp_path / 'migration_reports'}/operator_inventory.json",
            f"{tmp_path / 'migration_reports'}/migration_manifest.json",
            f"{tmp_path / 'migration_reports'}/preflight.json",
            f"{tmp_path / 'migration_reports'}/baseline.json",
            f"{tmp_path / 'migration_reports'}/runtime_coverage.json",
            f"{tmp_path / 'migration_reports'}/performance.json",
            f"{tmp_path / 'migration_reports'}/build.json",
            f"{tmp_path / 'migration_reports'}/implementation_resolution.json",
            f"{tmp_path / 'migration_reports'}/custom_op_final_gate.json",
            f"{tmp_path / 'migration_reports'}/evidence_validation.json",
            f"{tmp_path / 'migration_reports'}/summary.json",
        ],
        "required_checks": [
            "inventory_manifest_equality",
            "closed_pass_count_equals_manifest_entries",
            "remaining_entries_zero",
            "full_migration_status_full_pass",
            "fine_grained_operator_unit_inventory",
            "kernel_launch_site_inventory",
            "public_entry_mapping",
            "inventory_granularity_fine",
            "per_entry_target_custom_op_artifact_evidence",
            "per_entry_adapter_evidence",
            "per_entry_parity_evidence",
            "integration_e2e_evidence",
            "same_run_runtime_coverage",
            "performance_evidence",
            "complete_performance_report",
            "overall_speedup_report",
            "no_fallback_no_zero_call_no_builtin_contamination",
            "native_operator_symbol_inventory",
        ],
        "validation_obligations": [
            "project_local_artifact",
            "runtime_project_api",
            "numeric_performance",
            "complete_speedup_report",
            "overall_speedup_report",
            "no_fallback",
        ],
        "runtime_entry_script_revision_allowed": True,
    }


def custom_op_phase35_output() -> dict[str, object]:
    return {
        "validation_passed": True,
        "issues": [],
        "fix_plan": "Custom-op validation script is headless-compliant.",
        "custom_op_requirements_checked": True,
        "script_source_driven_inventory": True,
        "script_emits_fine_grained_units": True,
        "script_maps_public_api_to_units": True,
        "script_discovers_full_inventory": True,
        "script_records_native_operator_symbols": True,
        "script_runs_project_api_custom_ops": True,
        "script_rejects_report_only_success": True,
        "script_requires_project_local_artifacts": True,
        "script_requires_numeric_performance": True,
        "script_checks_no_fallback": True,
    }


class CustomOpPhase35SessionManager:
    phase35_prompts: list[str]
    phase35_outputs: list[dict[str, object]]
    tmp_path: Path

    def __init__(self, tmp_path: Path, phase35_outputs: list[dict[str, object]]) -> None:
        self.tmp_path = tmp_path
        self.phase35_prompts = []
        self.phase35_outputs = list(phase35_outputs)

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        del role, lifecycle
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        del session_id, timeout
        if command.startswith("# Phase 2"):
            return json.dumps(
                {
                    "venv_path": str(self.tmp_path / ".venv"),
                    "python_path": str(self.tmp_path / ".venv" / "bin" / "python"),
                    "installed_packages": ["torch", "torch_npu"],
                }
            )
        if command.startswith("# Phase 3.5") or "phase_35_static_validate" in command:
            self.phase35_prompts.append(command)
            return json.dumps(self.phase35_outputs.pop(0))
        if "Phase 3" in command:
            return json.dumps(custom_op_phase3_output(self.tmp_path))
        raise AssertionError(f"Unexpected prompt: {command}")


def test_run_phase_2_to_3_rejects_missing_custom_op_entry_script_before_phase35(tmp_path: Path) -> None:
    class MissingScriptSessionManager:
        phase35_prompts: list[str]

        def __init__(self) -> None:
            self.phase35_prompts = []

        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            del role, lifecycle
            return "persistent-main"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            del session_id, timeout
            if command.startswith("# Phase 2"):
                return json.dumps(
                    {
                        "venv_path": str(tmp_path / ".venv"),
                        "python_path": str(tmp_path / ".venv" / "bin" / "python"),
                        "installed_packages": ["torch", "torch_npu"],
                    }
                )
            if command.startswith("# Phase 3.5") or "phase_35_static_validate" in command:
                self.phase35_prompts.append(command)
                return json.dumps(custom_op_phase35_output())
            if "Phase 3" in command:
                return json.dumps(custom_op_phase3_output(tmp_path, create_script=False))
            raise AssertionError(f"Unexpected prompt: {command}")

    session_mgr = MissingScriptSessionManager()
    runner, artifact_store = build_runner(tmp_path, session_mgr=session_mgr)
    runner.max_retry = 1

    outputs = runner.run_phase_2_to_3(
        str(tmp_path),
        session_mgr,
        artifact_store,
        prior_outputs={
            "phase_0_env_detect": valid_phase_0_output(),
            "phase_1_project_analysis": {
                "project_dir": str(tmp_path),
                "dependencies": ["torch"],
                "cuda_detected": True,
                "entry_script": "validate_custom_ops_full.py",
                "custom_op_surface": {"custom_op_detected": True},
            },
        },
    )

    # Script is auto-created by _normalize_output → ensure_strict_non_variant_custom_op_validation_script
    assert outputs["phase_3_entry_script"]["entry_script_path"] == str(tmp_path / "validate_custom_ops_full.py")
    assert session_mgr.phase35_prompts != []


def test_run_phase_2_to_3_accepts_relative_custom_op_entry_script(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _ = (project_dir / "validate_custom_ops_full.py").write_text(
        "print('custom-op validation')\n",
        encoding="utf-8",
    )

    class RelativeScriptSessionManager:
        phase35_prompts: list[str]

        def __init__(self) -> None:
            self.phase35_prompts = []

        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            del role, lifecycle
            return "persistent-main"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
            del session_id, timeout
            if command.startswith("# Phase 2"):
                return json.dumps(
                    {
                        "venv_path": str(project_dir / ".venv"),
                        "python_path": str(project_dir / ".venv" / "bin" / "python"),
                        "installed_packages": ["torch", "torch_npu"],
                    }
                )
            if command.startswith("# Phase 3.5") or "phase_35_static_validate" in command:
                self.phase35_prompts.append(command)
                return json.dumps(custom_op_phase35_output())
            if "Phase 3" in command:
                return json.dumps(
                    {
                        **custom_op_phase3_output(project_dir),
                        "entry_script_path": "validate_custom_ops_full.py",
                        "run_command": f"{project_dir / '.venv' / 'bin' / 'python'} validate_custom_ops_full.py",
                    }
                )
            raise AssertionError(f"Unexpected prompt: {command}")

    session_mgr = RelativeScriptSessionManager()
    runner, artifact_store = build_runner(project_dir, session_mgr=session_mgr)
    runner.max_retry = 1

    outputs = runner.run_phase_2_to_3(
        str(project_dir),
        session_mgr,
        artifact_store,
        prior_outputs={
            "phase_0_env_detect": valid_phase_0_output(),
            "phase_1_project_analysis": {
                "project_dir": str(project_dir),
                "dependencies": ["torch"],
                "cuda_detected": True,
                "entry_script": "validate_custom_ops_full.py",
                "custom_op_surface": {"custom_op_detected": True},
            },
        },
    )

    assert len(session_mgr.phase35_prompts) == 1
    assert outputs["phase_3_entry_script"]["entry_script_path"] == "validate_custom_ops_full.py"
    assert outputs["phase_35_static_validate"]["validation_passed"] is True


def test_phase_35_injects_custom_op_marker_and_retries_legacy_static_output(tmp_path: Path) -> None:
    session_mgr = CustomOpPhase35SessionManager(
        tmp_path,
        [
            {
                "validation_passed": True,
                "issues": [],
                "fix_plan": "No issues found. Script is headless-compliant.",
            },
            custom_op_phase35_output(),
        ],
    )
    runner, artifact_store = build_runner(tmp_path, session_mgr=session_mgr)
    runner.max_retry = 2

    outputs = runner.run_phase_2_to_3(
        str(tmp_path),
        session_mgr,
        artifact_store,
        prior_outputs={
            "phase_0_env_detect": valid_phase_0_output(),
            "phase_1_project_analysis": {
                "project_dir": str(tmp_path),
                "dependencies": ["torch"],
                "cuda_detected": True,
                "entry_script": "validate_custom_ops_full.py",
            },
        },
    )

    assert len(session_mgr.phase35_prompts) == 2
    assert outputs["phase_35_static_validate"]["custom_op_static_required"] is True
    assert outputs["phase_35_static_validate"]["entry_script_kind"] == "custom_op_full_validation"


def test_phase_35_custom_op_context_with_all_booleans_passes(tmp_path: Path) -> None:
    session_mgr = CustomOpPhase35SessionManager(tmp_path, [custom_op_phase35_output()])
    runner, artifact_store = build_runner(tmp_path, session_mgr=session_mgr)

    outputs = runner.run_phase_2_to_3(
        str(tmp_path),
        session_mgr,
        artifact_store,
        prior_outputs={
            "phase_0_env_detect": valid_phase_0_output(),
            "phase_1_project_analysis": {
                "project_dir": str(tmp_path),
                "dependencies": ["torch"],
                "cuda_detected": True,
                "entry_script": "validate_custom_ops_full.py",
            },
        },
    )

    assert len(session_mgr.phase35_prompts) == 1
    assert outputs["phase_35_static_validate"]["custom_op_static_required"] is True
    assert outputs["phase_35_static_validate"]["script_runs_project_api_custom_ops"] is True
    assert outputs["phase_35_static_validate"]["script_records_native_operator_symbols"] is True


def test_runtime_skill_repo_root_relative_path_resolves_against_execution_root(tmp_path: Path) -> None:
    from core.paths import execution_root

    skill_root_name = "__relative_runtime_skills__"
    skill_repo_root = execution_root() / skill_root_name
    _ = write_runtime_skill(skill_repo_root, "agent-skill", "# Agent Skill\n\nAgent guidance")
    try:
        workflow = runtime_workflow(
            phases=[
                PhaseDefinition(
                    id="phase_0_env_detect",
                    name="Phase 0",
                    prompt_template="phase_0_env_detect_npu",
                    output_schema={},
                    validator="env_detect",
                    agent="main_engineer",
                    runtime_skills=RuntimeSkillsConfig(include=["agent-skill"], inject_full=True),
                )
            ]
        )
        artifact_store = ArtifactStore(str(tmp_path), "testrun")
        runner = PhaseRunner(
            NoopSessionManager(),
            artifact_store,
            StaticPromptLoader(),
            ValidatorEngine(),
            workflow=workflow,
            framework_config={"runtime_skill_repo_root": skill_root_name},
        )
        session = MockSession([json.dumps(valid_phase_0_output())])

        old_cwd = os.getcwd()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        os.chdir(cwd)
        try:
            _ = runner.run_single_phase(session, "phase_0", {"project_dir": str(tmp_path)})
        finally:
            os.chdir(old_cwd)

        sent_prompt = session.calls[0][0]
        assert "### agent-skill" in sent_prompt
        assert "Agent guidance" in sent_prompt
    finally:
        import shutil

        shutil.rmtree(skill_repo_root, ignore_errors=True)


# ── PhaseRunner container context injection ──────────────────────────


class TestPhaseRunnerContainerContext:
    def test_set_container_context_stores_dict(self):
        runner = PhaseRunner(
            _make_session_mgr(),
            _make_artifact_store(),
            PromptLoader(str(PROJECT_ROOT / "prompts")),
            ValidatorEngine(),
        )
        ctx = {"execution_backend_mode": "container", "container_name_or_id": "c1"}
        runner.set_container_context(ctx)
        assert runner._container_context == ctx
        ctx["mutated"] = "x"
        assert "mutated" not in runner._container_context

    def test_build_prompt_context_includes_container_keys(self):
        runner = PhaseRunner(
            _make_session_mgr(),
            _make_artifact_store(),
            PromptLoader(str(PROJECT_ROOT / "prompts")),
            ValidatorEngine(),
        )
        runner.set_container_context({
            "execution_backend_mode": "container",
            "container_name_or_id": "c1",
            "container_env_facts": '{"status":"ok"}',
            "container_python_version": "3.10.0",
        })
        phase_spec = PhaseSpec("phase_0", "phase_0_env_detect", "env_detect")
        context: dict[str, object] = {"project_dir": "/tmp/proj", "user_constraints": ""}
        result = runner._build_prompt_context(phase_spec, context)
        assert result["execution_backend_mode"] == "container"
        assert result["container_name_or_id"] == "c1"
        assert result["container_env_facts"] == '{"status":"ok"}'
        assert result["container_python_version"] == "3.10.0"

    def test_container_context_setdefault_preserves_existing(self):
        runner = PhaseRunner(
            _make_session_mgr(),
            _make_artifact_store(),
            PromptLoader(str(PROJECT_ROOT / "prompts")),
            ValidatorEngine(),
        )
        runner.set_container_context({"project_dir": "/container/dir"})
        phase_spec = PhaseSpec("phase_0", "phase_0_env_detect", "env_detect")
        context: dict[str, object] = {"project_dir": "/host/proj"}
        result = runner._build_prompt_context(phase_spec, context)
        assert result["project_dir"] == "/host/proj"

    def test_empty_container_context_adds_local_execution_defaults(self):
        runner = PhaseRunner(
            _make_session_mgr(),
            _make_artifact_store(),
            PromptLoader(str(PROJECT_ROOT / "prompts")),
            ValidatorEngine(),
        )
        assert runner._container_context == {}
        phase_spec = PhaseSpec("phase_0", "phase_0_env_detect", "env_detect")
        result = runner._build_prompt_context(phase_spec, {"project_dir": "/tmp"})
        assert result["execution_backend_mode"] == "local"
        assert "local" in result["execution_environment_context"]


def _make_session_mgr() -> SessionManagerLike:
    def _noop():
        return "s1"
    class M:
        def get_or_create(self, *a, **kw): return "s1"
        def send_command(self, *a, **kw): return '{"ok": true}'
    return M()


def _make_artifact_store(tmp_path: Path | None = None) -> ArtifactStore:
    root = tmp_path or Path("/tmp")
    return ArtifactStore(str(root), "test-artifacts")


def _unused_legacy_artifact_store():
    class S:
        def __init__(self):
            self.artifact_dir = "/tmp/test-artifacts"
            self.saved = {}
        def save_phase_output(self, *a, **kw): return "raw"
        def mark_validated(self, *a, **kw): return "v"
        def write_journal(self, *a, **kw): return "j"
        def load_phase_output(self, *a, **kw): return None
    return S()


# ── Phase-aware previous_outputs filtering ────────────────────────


def test_phase_35_previous_outputs_excludes_early_phases() -> None:
    """Phase 3.5 should receive only phase_3_entry_script, not Phase 0/1/2."""
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_35", "phase_35_static_validate", "entry_static")
    ctx: dict[str, object] = {
        "project_dir": "/tmp/project",
        "previous_outputs": {
            "phase_0_env_detect": {"platform": "npu", "npu_detected": True},
            "phase_1_project_analysis": {"entry_script": "train.py", "dependencies": ["torch"]},
            "phase_2_venv_create": {"venv_path": "/tmp/.venv"},
            "phase_3_entry_script": {
                "entry_script_path": "/tmp/project/train.py",
                "entry_script_kind": "custom_op_full_validation",
            },
        },
    }
    result = runner._build_prompt_context(spec, ctx)
    parsed_previous = json.loads(result["previous_outputs"])
    assert "phase_3_entry_script" in parsed_previous
    assert "phase_0_env_detect" not in parsed_previous
    assert "phase_1_project_analysis" not in parsed_previous
    assert "phase_2_venv_create" not in parsed_previous
    assert result["entry_script_path"] == "/tmp/project/train.py"


def test_phase_1_5_does_not_get_duplicated_previous_outputs() -> None:
    """Phase 1.5 context should receive empty previous_outputs (no duplication of Phase 0/1)."""
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_1_5", "phase_1_5_constraint_summary", "constraint_summary")
    ctx: dict[str, object] = {
        "project_dir": "/tmp/project",
        "previous_outputs": {
            "phase_0_env_detect": {"platform": "npu"},
            "phase_1_project_analysis": {"entry_script": "train.py"},
        },
    }
    result = runner._build_prompt_context(spec, ctx)
    parsed = json.loads(result["previous_outputs"])
    assert parsed == {}
    assert "phase_0_env_detect" not in parsed
    assert "phase_1_project_analysis" not in parsed


def test_early_phases_get_empty_previous_outputs() -> None:
    """Phase 0/1/2/3 receive empty previous_outputs; _SHARED_SESSION_PHASES omit the key."""
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    ctx_with_noise: dict[str, object] = {
        "project_dir": "/tmp/project",
        "previous_outputs": {
            "phase_0_env_detect": {"platform": "npu"},
            "phase_1_project_analysis": {"entry_script": "train.py"},
        },
    }
    for prompt_id in ("phase_0_env_detect", "phase_1_project_analysis", "phase_2_venv_create", "phase_3_entry_script"):
        spec = PhaseSpec(prompt_id.rsplit("_", 1)[0], prompt_id, prompt_id.split("_", 1)[-1])
        result = runner._build_prompt_context(spec, ctx_with_noise)
        if prompt_id in PhaseRunner._SHARED_SESSION_PHASES:
            assert "previous_outputs" not in result, f"{prompt_id} is shared-session and should omit key"
        else:
            assert result.get("previous_outputs") == "{}", f"{prompt_id} should get empty previous_outputs"


def test_phase_6_still_receives_all_previous_outputs() -> None:
    """Phase 6/report should still receive the full previous_outputs (no whitelist entry)."""
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_6", "phase_6_report", "report")
    ctx: dict[str, object] = {
        "project_dir": "/tmp/project",
        "previous_outputs": {
            "phase_0_env_detect": {"platform": "npu"},
            "phase_1_project_analysis": {"entry_script": "train.py"},
            "phase_3_entry_script": {"entry_script_path": "/tmp/train.py"},
            "phase_4_rule_migration": {"files_migrated": 10},
        },
    }
    result = runner._build_prompt_context(spec, ctx)
    parsed = json.loads(result["previous_outputs"])
    assert "phase_0_env_detect" in parsed
    assert "phase_1_project_analysis" in parsed
    assert "phase_3_entry_script" in parsed
    assert "phase_4_rule_migration" in parsed


# ── disable_custom_op_contract_injection flag regression ──────────────────


def test_phase_runner_disable_custom_op_injection_prevents_injection() -> None:
    """When PhaseRunner is given a workflow with disable_custom_op_contract_injection=True,
    custom-op signals do NOT trigger entry_script_kind injection."""
    wf = WorkflowDefinition(
        name="no-custom-injection",
        version="1.0",
        phases=[],
        terminals=["complete"],
        globals={"disable_custom_op_contract_injection": True},
    )
    runner = PhaseRunner(
        NoopSessionManager(),
        ArtifactStore("/tmp", "t"),
        PromptLoader(),
        ValidatorEngine(),
        workflow=wf,
    )
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "notes": "project uses custom operator bindings via torch.ops",
                }
            }
        },
    )

    assert "entry_script_kind" not in normalized
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is True


def test_phase_runner_custom_op_route_disabled_strips_agent_contract() -> None:
    wf = WorkflowDefinition(
        name="normal-entry-route",
        version="1.0",
        phases=[],
        terminals=["complete"],
        globals={"custom_op_route_enabled": False},
    )
    runner = PhaseRunner(
        NoopSessionManager(),
        ArtifactStore("/tmp", "t"),
        PromptLoader(),
        ValidatorEngine(),
        workflow=wf,
    )
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {
            "entry_script_path": "train.py",
            "run_command": "python train.py",
            "entry_script_kind": "custom_op_full_validation",
            "reports_dir": "/tmp/project/migration_reports",
            "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            "required_checks": ["same_run_runtime_coverage"],
            "operator_discovery_sources": ["source"],
            "operator_inventory_schema": {"semantic_rows": "one row per operator"},
            "performance_report_schema": {"entries": "per unit"},
            "validation_obligations": ["no_fallback"],
            "runtime_entry_script_revision_allowed": True,
        },
        {"project_dir": "/tmp/project"},
        {"previous_outputs": {"phase_1_project_analysis": {"custom_op_surface": {"custom_op_detected": True}}}},
    )

    for field in (
        "entry_script_kind",
        "reports_dir",
        "required_report_paths",
        "required_checks",
        "operator_discovery_sources",
        "operator_inventory_schema",
        "performance_report_schema",
        "validation_obligations",
    ):
        assert field not in normalized
    assert normalized["runtime_entry_script_revision_allowed"] is True
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is True


def test_phase_runner_ordinary_route_restores_normal_entry_contract() -> None:
    runner = PhaseRunner(
        NoopSessionManager(),
        ArtifactStore("/tmp", "t"),
        PromptLoader(),
        ValidatorEngine(),
        workflow=WorkflowDefinition(name="ordinary-route", version="1.0", phases=[], terminals=["complete"]),
    )
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {
            "entry_script_path": "validate_custom_ops_full.py",
            "run_command": "python validate_custom_ops_full.py",
            "entry_script_kind": "custom_op_full_validation",
            "reports_dir": "/tmp/project/migration_reports",
            "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            "required_checks": ["custom_op_final_gate"],
        },
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "migration_route": "ordinary_cuda",
                    "entry_script": "train.py",
                    "notes": "mentions torch.ops in docs but route is ordinary_cuda",
                },
                "phase_2_venv_create": {"python_path": "/tmp/project/.venv/bin/python"},
            }
        },
    )

    assert "entry_script_kind" not in normalized
    assert "reports_dir" not in normalized
    assert "required_report_paths" not in normalized
    assert "required_checks" not in normalized
    assert normalized["entry_script_path"] == "/tmp/project/train.py"
    assert normalized["run_command"] == "/tmp/project/.venv/bin/python /tmp/project/train.py"
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is True


def test_phase_runner_without_flag_injects_as_before() -> None:
    """Without any workflow globals (backward-compatible path), custom-op signals
    still trigger entry_script_kind: custom_op_full_validation injection."""
    runner = PhaseRunner(
        NoopSessionManager(),
        ArtifactStore("/tmp", "t"),
        PromptLoader(),
        ValidatorEngine(),
        workflow=WorkflowDefinition(name="legacy", version="1.0", phases=[], terminals=["complete"]),
    )
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "notes": "project uses custom operator bindings via torch.ops",
                }
            }
        },
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is False
    assert any("required_report_paths" in error for error in validation.errors)


def test_phase_runner_no_workflow_still_injects() -> None:
    """Backward compatibility: PhaseRunner without any WorkflowDefinition
    (self.workflow is None) still injects custom-op contract fields."""
    runner = PhaseRunner(
        NoopSessionManager(),
        ArtifactStore("/tmp", "t"),
        PromptLoader(),
        ValidatorEngine(),
        workflow=None,  # explicit None — old constructor style
    )
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "notes": "project uses custom operator bindings via torch.ops",
                }
            }
        },
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is False


# ── Phase boundary injection tests ─────────────────────────────────────

from core.phase_boundary import inject_phase_boundary  # noqa: E402


class BoundaryTestPromptLoader(PromptLoader):
    """Captures the prompt context for boundary presence checks."""
    def __init__(self):
        super().__init__()
        self.last_prompt_id = ""
        self.last_context: dict[str, str] = {}

    def load_prompt(self, phase_id: str, context: dict[str, str] | None = None) -> str:
        self.last_prompt_id = phase_id
        self.last_context = dict(context or {})
        return f"# {phase_id} prompt\n\nSome instructions."


def test_boundary_injected_in_run_single_phase(tmp_path: Path) -> None:
    """PhaseRunner._run_single_phase injects boundary guidance."""
    artifact_store = ArtifactStore(str(tmp_path), "test-boundary")
    prompt_loader = BoundaryTestPromptLoader()
    runner = PhaseRunner(
        RecordingSessionManager(
            json.dumps({"ok": True, "result": "valid"})
        ),
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
    )
    from validators.validate_env_detect import validate as v_env
    runner.validator.register_validator("env_detect", v_env)

    session = MockSession([
        json.dumps({"platform": "npu", "npu_detected": True, "python_version": "3.10",
                     "cann_version": "8.0", "ascendc_available": True, "driver_version": "24.1"}),
    ])
    result = runner.run_single_phase(session, "phase_0", {"max_retry": 1})
    assert result.get("platform") == "npu"

    first_prompt = session.calls[0][0]
    assert "## Phase Boundary" in first_prompt
    assert "current phase" in first_prompt.lower()
    assert "later phases" in first_prompt.lower()


def test_boundary_not_injected_when_disabled(tmp_path: Path) -> None:
    """Boundary is omitted when phase_boundary_guidance_enabled is False."""
    artifact_store = ArtifactStore(str(tmp_path), "test-boundary-off")
    prompt_loader = BoundaryTestPromptLoader()
    runner = PhaseRunner(
        RecordingSessionManager(
            json.dumps({"ok": True, "result": "valid"})
        ),
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
        framework_config={"phase_boundary_guidance_enabled": False},
    )
    from validators.validate_env_detect import validate as v_env
    runner.validator.register_validator("env_detect", v_env)

    session = MockSession([
        json.dumps({"platform": "npu", "npu_detected": True, "python_version": "3.10",
                     "cann_version": "8.0", "ascendc_available": True, "driver_version": "24.1"}),
    ])
    result = runner.run_single_phase(session, "phase_0", {"max_retry": 1})
    assert result.get("platform") == "npu"

    first_prompt = session.calls[0][0]
    assert "## Phase Boundary" not in first_prompt


def test_boundary_avoids_framework_name_in_phase_prompts(tmp_path: Path) -> None:
    """Phase prompts with boundary do not leak the framework name."""
    artifact_store = ArtifactStore(str(tmp_path), "test-nofw")
    prompt_loader = BoundaryTestPromptLoader()
    runner = PhaseRunner(
        RecordingSessionManager(
            json.dumps({"ok": True, "result": "valid"})
        ),
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
    )
    from validators.validate_env_detect import validate as v_env
    runner.validator.register_validator("env_detect", v_env)

    session = MockSession([
        json.dumps({"platform": "npu", "npu_detected": True, "python_version": "3.10",
                     "cann_version": "8.0", "ascendc_available": True, "driver_version": "24.1"}),
    ])
    result = runner.run_single_phase(session, "phase_0", {"max_retry": 1})
    assert result.get("platform") == "npu"

    first_prompt = session.calls[0][0]
    assert "OpenCode" not in first_prompt
    assert "SEAM" not in first_prompt


def test_phase_runner_phase1_rejects_untrusted_sidecar_without_response_json(tmp_path: Path) -> None:
    runner, artifact_store = build_runner(tmp_path)
    project_dir = tmp_path / "custom_project"
    project_dir.mkdir()
    write_tiny_cuda_custom_op_project(project_dir)
    _ = (Path(artifact_store.artifact_dir) / "phase_1_project_analysis.json").write_text(
        json.dumps({
            "project_dir": str(project_dir),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test_data_and_scripts/main.py",
        }),
        encoding="utf-8",
    )
    session = MockSession([
        "Phase 1 complete. JSON output saved to `.sm-artifacts/testrun/phase_1_project_analysis.json`.",
        "Still saved to `.sm-artifacts/testrun/phase_1_project_analysis.json`.",
        "Still saved to `.sm-artifacts/testrun/phase_1_project_analysis.json`.",
    ])

    with pytest.raises(ValueError, match="response contained no parseable JSON object"):
        runner.run_single_phase(session, "phase_1", {"project_dir": str(project_dir), "max_retry": 1})


def test_phase_runner_phase1_status_only_response_fails_shape(tmp_path: Path) -> None:
    runner, _artifact_store = build_runner(tmp_path)
    spec = PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis")

    validation = runner._validate_phase_response_shape(spec, {"status": "complete"}, "{}")

    assert validation.passed is False
    assert any("status/progress-only" in error for error in validation.errors)


def test_phase_runner_phase1_normalizer_discovers_project_fields(tmp_path: Path) -> None:
    project_dir = tmp_path / "custom_project"
    project_dir.mkdir()
    write_tiny_cuda_custom_op_project(project_dir)
    runner, _artifact_store = build_runner(tmp_path)
    spec = PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis")

    normalized = runner._normalize_output(spec, {"route": "custom_op"}, {"project_dir": str(project_dir)}, {})

    assert normalized["dependencies"] == ["torch"]
    assert normalized["cuda_detected"] is True
    assert normalized["entry_script"] == "test_data_and_scripts/main.py"
    assert normalized["migration_route"] == "custom_op"
    assert validate_project_analysis(normalized)["passed"] is True


def test_run_phase_6_fallback_preserves_phase5_stagnation_as_failure(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    artifact_store.save_phase_output(
        "phase_5_validation",
        {"status": "stagnation", "final_status": "stagnation", "script_exit_code": 1},
        attempt=1,
    )
    artifact_store.mark_validated(
        "phase_5_validation",
        {"status": "stagnation", "final_status": "stagnation", "script_exit_code": 1},
    )
    session_mgr = Phase6SessionManager(
        phase_6_response=json.dumps({"ok": False, "error": "Conversation history too large to compact - exceeds model context limit"})
    )
    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)
    summary = cast(dict[str, object], result["migration_summary"])
    report_paths = cast(list[str], result["report_paths"])
    summary_report = Path(report_paths[0]).read_text(encoding="utf-8")

    assert result["fallback"] is True
    assert summary["overall_status"] == "partial"
    assert summary["migration_success"] is False
    assert summary["phase5_status"] == "stagnation"
    assert summary["phase5_terminal_failure"] is True
    assert "- Phase 5 status: stagnation" in summary_report
    assert "- Migration success: False" in summary_report
    assert "- Phase 5 terminal failure: True" in summary_report
