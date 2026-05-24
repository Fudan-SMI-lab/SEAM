# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedParameter=false

import json
import os
import sys
from pathlib import Path
from typing import cast, final

import pytest
from typing_extensions import override

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner, PhaseSpec, SessionManagerLike
from core.prompt_loader import PromptLoader
from core.types import PhaseDefinition, RuntimeSkillsConfig, SubWorkflowDefinition, WorkflowDefinition
from core.validator_engine import ValidationResult, ValidatorEngine


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
        del agent
        return f"{role}-{lifecycle}"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
        raise AssertionError(f"Unexpected manager send for {session_id}: {command} ({timeout})")


class RecordingSessionManager:
    def __init__(self, response: str) -> None:
        self.response: str = response
        self.get_or_create_calls: list[dict[str, str]] = []
        self.send_calls: list[tuple[str, str, int | None]] = []

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        del agent
        self.get_or_create_calls.append({"role": role, "lifecycle": lifecycle})
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
        self.send_calls.append((session_id, command, timeout))
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
        del agent
        self.get_or_create_calls.append({"role": role, "lifecycle": lifecycle})
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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


def write_runtime_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / ".memory" / "skills" / name
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
                prompt_template="phase_0_env_detect",
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
    assert sent_prompt.startswith("BASE PROMPT phase_0_env_detect\n\n## Explicit Runtime Skills")
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
    assert session_mgr.send_calls[0][2] is None


def test_phase_runner_direct_llm_paths_ignore_configured_phase_timeout(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = RecordingSessionManager(json.dumps({"constraint_summary": "ok"}))
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"session_timeout_phase": "42"},
    )

    _ = runner.run_phase_1_5(
        "persistent-main",
        session_mgr,
        artifact_store,
        project_dir=str(tmp_path),
        user_constraints="",
    )

    assert session_mgr.send_calls[0][2] is None


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
        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
    assert len(session_mgr.send_calls) == 2


def test_run_review_check_retries_status_only_response(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")

    class SequentialReviewSessionManager(NoopSessionManager):
        def __init__(self) -> None:
            self.responses: list[str] = [
                json.dumps({"status": "in_progress", "message": "reviewing"}),
                json.dumps({
                    "verdict": "accept",
                    "cpu_fallback_detected": False,
                    "cpu_fallback_necessary": False,
                    "alternative_suggestions": "",
                    "reasoning": "ok",
                }),
            ]
            self.send_calls: list[tuple[str, str, int | None]] = []

        @override
        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
    assert len(session_mgr.send_calls) == 2
    assert "status/progress-only JSON" in session_mgr.send_calls[1][1]


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
    assert "Required keys missing: platform." in second_prompt


def test_phase2_progress_text_retries_to_complete_json(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path)
    session = MockSession([
        "I am creating the virtual environment now and will report back shortly.",
        json.dumps(
            {
                "venv_path": str(tmp_path / ".venv"),
                "python_path": str(tmp_path / ".venv" / "bin" / "python"),
                "installed_packages": ["torch", "torch_npu"],
            }
        ),
    ])

    result = runner.run_single_phase(session, "phase_2", {"max_retry": 2})

    assert result["venv_path"] == str(tmp_path / ".venv")
    assert result["installed_packages"] == ["torch", "torch_npu"]
    assert len(session.calls) == 2
    assert "response contained no parseable JSON object" in session.calls[1][0]


def test_phase3_status_only_json_retries_to_complete_schema(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path)
    session = MockSession([
        json.dumps({"status": "in_progress", "message": "selecting entry script"}),
        json.dumps({"entry_script_path": "train.py", "run_command": "python train.py"}),
    ])

    result = runner.run_single_phase(session, "phase_3", {"max_retry": 2})

    assert result["entry_script_path"] == "train.py"
    assert result["run_command"] == "python train.py"
    assert len(session.calls) == 2
    assert "status/progress-only JSON" in session.calls[1][0]


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
    assert "Required keys missing: platform, npu_detected." in correction_prompt


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


def test_phase_runner_uses_configured_backend_agent_for_main_engineer(tmp_path: Path) -> None:
    class AgentRecordingSessionManager:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def get_or_create(
            self,
            role: str,
            lifecycle: str,
            agent: str = "",
            title: str = "",
            working_dir: str = "",
            initial_prompt: str = "",
        ) -> str:
            self.calls.append({
                "role": role,
                "lifecycle": lifecycle,
                "agent": agent,
                "title": title,
                "working_dir": working_dir,
                "initial_prompt": initial_prompt,
            })
            return "main-session"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
            del session_id, command, timeout
            return json.dumps(valid_phase_0_output())

    workflow = runtime_workflow(
        phases=[
            PhaseDefinition(
                id="phase_0_env_detect",
                name="Phase 0",
                prompt_template="phase_0_env_detect",
                output_schema={},
                validator="env_detect",
                agent="main_engineer",
            )
        ]
    )
    assert workflow.agents is not None
    workflow.agents["main_engineer"]["agent"] = "Sisyphus-Junior"
    session_mgr = AgentRecordingSessionManager()
    artifact_store = ArtifactStore(str(tmp_path), "agent-config")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        workflow=workflow,
    )

    _ = runner._get_main_session(session_mgr)

    assert session_mgr.calls[0]["agent"] == "Sisyphus-Junior"


class Phase6SessionManager:
    responses: dict[str, list[str]]
    phase_6_responses: list[str]

    def __init__(
        self,
        phase_responses: dict[str, list[str]] | None = None,
        phase_6_response: str | list[str] = "",
    ) -> None:
        self.responses = {k: list(v) for k, v in (phase_responses or {}).items()}
        self.phase_6_responses = list(phase_6_response) if isinstance(phase_6_response, list) else [phase_6_response]
        self.get_or_create_calls: list[dict[str, str]] = []
        self.send_calls: list[tuple[str, str, int | None]] = []

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        del agent
        self.get_or_create_calls.append({"role": role, "lifecycle": lifecycle})
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
        self.send_calls.append((session_id, command, timeout))
        if "Phase 6" in command or "phase_6" in command:
            if len(self.phase_6_responses) > 1:
                return self.phase_6_responses.pop(0)
            return self.phase_6_responses[0]
        for phase_key in ("phase_3", "phase_2", "phase_1", "phase_0"):
            if f"Phase {phase_key[-1]}" in command:
                return self.responses[phase_key].pop(0)
        raise AssertionError(f"Unexpected prompt: {command}")


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


def test_run_phase_6_retries_status_only_response_before_saving(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    status_only_response = json.dumps({"status": "in_progress", "message": "writing reports"})
    phase_6_json = json.dumps({"report_paths": [], "migration_summary": {}})
    session_mgr = Phase6SessionManager(
        phase_6_response=[
            status_only_response,
            phase_6_json,
        ]
    )
    runner = PhaseRunner(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    result = runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)

    assert result["phase_id"] == "phase_6_report"
    assert len(session_mgr.send_calls) == 2
    assert "status/progress-only JSON" in session_mgr.send_calls[1][1]
    journal = artifact_store.get_journal()
    phase_6_entries = [entry for entry in journal if entry["phase_id"] == "phase_6_report"]
    assert [entry["status"] for entry in phase_6_entries] == ["response_shape_failed", "succeeded"]
    failed_attempt = json.loads(Path(str(phase_6_entries[0]["raw_path"])).read_text(encoding="utf-8"))
    assert failed_attempt["raw_response"] == status_only_response
    assert failed_attempt["validation_errors"]
    assert any("status/progress-only JSON" in error for error in failed_attempt["validation_errors"])
    assert phase_6_entries[0]["canonical_path"] == ""


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
            del agent
            return "sess"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
            del agent
            return "x"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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


def test_phase_runner_phase1_normalization_uses_prompt_context_project_dir(tmp_path: Path) -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore(str(tmp_path), "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis")
    trusted_project = tmp_path / "trusted_project"
    untrusted_project = tmp_path / "untrusted_project"

    normalized = runner._normalize_output(
        spec,
        {
            "project_dir": str(untrusted_project),
            "dependencies": ["torch"],
            "cuda_detected": False,
            "entry_script": "train.py",
        },
        {"project_dir": str(trusted_project)},
        {},
    )

    assert normalized["project_dir"] == str(trusted_project)


def test_phase_runner_phase3_legacy_text_mentions_do_not_force_custom_op_context() -> None:
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

    assert "entry_script_kind" not in normalized
    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is True


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

    zero_surface_with_stale_text = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "custom_op_surface": {"custom_op_detected": False},
                    "operator_unit_count": 0,
                    "notes": "custom_op_final_gate and torch.ops were checked; no custom operators found",
                },
                "phase_35_static_validate": {"custom_op_static_required": False},
            },
            "previous_outputs_text": "custom_op_final_gate mentioned in logs",
        },
    )
    assert "entry_script_kind" not in zero_surface_with_stale_text

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


def test_phase_runner_propagates_phase1_expanded_variant_contract() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "validate_custom_ops_full.py", "run_command": "python validate_custom_ops_full.py", "required_checks": []},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "custom_op_surface": {
                        "custom_op_detected": True,
                        "variant_axes_detected": True,
                        "variant_axes": {"ndim": [1, 2]},
                        "expanded_operator_instances_count": 2,
                        "expanded_operator_variants": [
                            {"unit_identity": "op:ndim=1"},
                            {"unit_identity": "op:ndim=2"},
                        ],
                    }
                }
            }
        },
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    assert normalized["expanded_variant_inventory"] == {
        "variant_axes_detected": True,
        "unit_identities": ["op:ndim=1", "op:ndim=2"],
        "expanded_operator_instances_count": 2,
    }
    assert normalized["variant_axis_coverage"] == {"all_axes_covered": True, "axes": {"ndim": [1, 2]}}
    assert normalized["per_variant_performance_report"] == {"required": True, "one_entry_per_expanded_variant": True}
    required_checks = normalized["required_checks"]
    assert isinstance(required_checks, list)
    assert set(required_checks) >= {
        "expanded_variant_inventory",
        "variant_axis_coverage",
        "per_variant_performance_report",
    }


def test_phase_runner_phase3_expanded_variant_subset_is_overwritten_from_phase1() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {
            "entry_script_path": "validate_custom_ops_full.py",
            "run_command": "python validate_custom_ops_full.py",
            "required_checks": [],
            "expanded_variant_inventory": {
                "variant_axes_detected": False,
                "unit_identities": ["op:ndim=1"],
                "expanded_operator_instances_count": 1,
            },
            "variant_axis_coverage": {"all_axes_covered": False, "axes": {"ndim": [1]}},
        },
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "custom_op_surface": {
                        "custom_op_detected": True,
                        "variant_axes_detected": True,
                        "variant_axes": {"ndim": [1, 2]},
                        "expanded_operator_instances_count": 2,
                        "expanded_operator_variants": [
                            {"unit_identity": "op:ndim=1"},
                            {"unit_identity": "op:ndim=2"},
                        ],
                    }
                }
            }
        },
    )

    assert normalized["expanded_variant_inventory"] == {
        "variant_axes_detected": True,
        "unit_identities": ["op:ndim=1", "op:ndim=2"],
        "expanded_operator_instances_count": 2,
    }
    assert normalized["variant_axis_coverage"] == {"all_axes_covered": True, "axes": {"ndim": [1, 2]}}


def test_phase_runner_phase1_synthesizes_expanded_variants_from_sample_axis_keys() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis")

    normalized = runner._normalize_output(
        spec,
        {
            "project_dir": "/untrusted",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": {
                "custom_op_detected": True,
                "variant_axes_detected": True,
                "variant_axes": {
                    "rank": ["one", "two"],
                    "precision": ["fp16", "fp32"],
                    "block_size": [64, 128],
                },
                "fine_grained_operator_units": ["generic_kernel_forward", "generic_kernel_backward"],
                "native_operator_symbols": ["generic_kernel_forward", "generic_kernel_backward"],
                "expanded_operator_variants": [
                    {
                        "unit_identity": "generic_kernel_forward:rank=one:precision=fp16",
                        "base_unit_identity": "generic_kernel_forward",
                        "axis_values": {"rank": "one", "precision": "fp16"},
                    },
                    {
                        "unit_identity": "generic_kernel_backward:rank=one",
                        "base_unit_identity": "generic_kernel_backward",
                        "axis_values": {"rank": "one"},
                    },
                ],
            },
        },
        {"project_dir": "/trusted"},
        {},
    )

    surface = normalized["custom_op_surface"]
    assert isinstance(surface, dict)
    surface_dict = cast(dict[str, object], surface)
    raw_variants = surface_dict["expanded_operator_variants"]
    assert isinstance(raw_variants, list)
    variants = cast(list[object], raw_variants)
    variant_rows = [cast(dict[str, object], item) for item in variants if isinstance(item, dict)]
    identities = {str(item["unit_identity"]) for item in variant_rows}
    assert identities == {
        "generic_kernel_forward:rank=one:precision=fp16",
        "generic_kernel_forward:rank=one:precision=fp32",
        "generic_kernel_forward:rank=two:precision=fp16",
        "generic_kernel_forward:rank=two:precision=fp32",
        "generic_kernel_backward:rank=one",
        "generic_kernel_backward:rank=two",
    }
    assert surface_dict["expanded_operator_instances_count"] == 6
    assert all("block_size" not in str(identity) for identity in identities)


def test_phase_runner_phase1_preserves_larger_declared_variant_count_when_synthesis_unavailable() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis")

    normalized = runner._normalize_output(
        spec,
        {
            "project_dir": "/untrusted",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": {
                "custom_op_detected": True,
                "variant_axes_detected": True,
                "variant_axes": {"shape": ["small", "large", "wide"]},
                "fine_grained_operator_units": ["generic_kernel"],
                "native_operator_symbols": ["generic_kernel"],
                "expanded_operator_instances_count": 3,
                "expanded_operator_variants": [
                    {"unit_identity": "generic_kernel:shape=small"},
                    {"unit_identity": "generic_kernel:shape=large"},
                ],
            },
        },
        {"project_dir": "/trusted"},
        {},
    )

    surface = normalized["custom_op_surface"]
    assert isinstance(surface, dict)
    surface_dict = cast(dict[str, object], surface)
    raw_variants = surface_dict["expanded_operator_variants"]
    assert isinstance(raw_variants, list)
    assert len(raw_variants) == 2
    assert surface_dict["expanded_operator_instances_count"] == 3


def _runner_variant_phase1_output(project_dir: Path, variant_ids: list[str]) -> dict[str, object]:
    return {
        "project_dir": str(project_dir),
        "dependencies": ["torch"],
        "cuda_detected": True,
        "entry_script": "train.py",
        "custom_op_surface": {
            "custom_op_detected": True,
            "fine_grained_operator_units": ["scalar_forward"],
            "variant_axes_detected": True,
            "expanded_operator_instances_count": len(variant_ids),
            "expanded_operator_variants": [
                {"unit_identity": variant_id, "base_unit_identity": "scalar_forward", "axis_values": {}}
                for variant_id in variant_ids
            ],
        },
    }


def _runner_phase1_report(variant_ids: list[str], *, verdict: str = "complete") -> dict[str, object]:
    return {
        "phase_id": "phase_1_project_analysis",
        "track": "custom_op_variant",
        "verdict": verdict,
        "phase1_inventory": {
            "fine_grained_operator_units": ["scalar_forward"],
            "variant_axes_detected": True,
            "expanded_operator_instances_count": len(variant_ids),
            "expanded_unit_identities": variant_ids,
        },
        "source_evidence_inventory": {
            "fine_grained_operator_units": ["scalar_forward"],
            "variant_axes": {"dtype": ["float", "double"]},
            "expanded_unit_identities": variant_ids,
        },
        "missing_units": [],
        "extra_units": [],
        "missing_variants": [] if verdict == "complete" else [variant_ids[-1]],
        "extra_variants": [],
        "collapsed_or_representative_rows": [],
        "unresolved_source_groups": [],
        "evidence": ["ops/scalar.cu:1"],
    }


def _runner_phase3_report(variant_ids: list[str], *, verdict: str = "complete") -> dict[str, object]:
    return {
        "phase_id": "phase_3_entry_script",
        "track": "custom_op_variant",
        "verdict": verdict,
        "phase1_verified_inventory": {
            "fine_grained_operator_units": ["scalar_forward"],
            "expanded_unit_identities": variant_ids,
        },
        "phase3_contract_inventory": {
            "covered_unit_identities": ["scalar_forward"],
            "covered_variant_identities": variant_ids if verdict == "complete" else variant_ids[:1],
            "entry_script_path": "validate_custom_ops_full.py",
        },
        "validation_script_evidence": ["validate_custom_ops_full.py enumerates variants"],
        "missing_units": [],
        "missing_variants": [] if verdict == "complete" else variant_ids[1:],
        "representative_only_coverage": [] if verdict == "complete" else ["only first variant covered"],
        "non_executable_or_missing_checks": [],
    }


def _runner_generic_template_phase1_output(project_dir: Path) -> dict[str, object]:
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True, exist_ok=True)
    _ = (source_dir / "kernels.cu").write_text(
        """#define KERNEL_CAT_I(name, dtype) name##_##dtype
#define KERNEL_CAT(name, dtype) KERNEL_CAT_I(name, dtype)
#define DISPATCH_DTYPE(dtype) KERNEL_CAT(scalar_forward, dtype)()
void DISPATCH_DTYPE(float) {}
void DISPATCH_DTYPE(double) {}
""",
        encoding="utf-8",
    )
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    return {
        "project_dir": str(project_dir),
        "dependencies": ["torch"],
        "cuda_detected": True,
        "entry_script": "train.py",
        "custom_op_surface": {
            "custom_op_detected": True,
            "fine_grained_operator_units": ["scalar_forward"],
            "variant_axes_detected": True,
            "variant_axes": {"dtype": ["float", "double"]},
            "discovered_operator_names": ["scalar_forward_${dtype}"],
            "native_operator_symbols": ["KERNEL_CAT(scalar_forward, dtype)"],
            "source_evidence": ["src/kernels.cu:KERNEL_CAT(scalar_forward, dtype)"],
            "expanded_operator_instances_count": len(variant_ids),
            "expanded_operator_variants": [
                {
                    "unit_identity": variant_id,
                    "base_unit_identity": "scalar_forward",
                    "axis_values": {"dtype": variant_id.rsplit("=", 1)[-1]},
                    "source_evidence": ["src/kernels.cu:KERNEL_CAT(scalar_forward, dtype)"],
                }
                for variant_id in variant_ids
            ],
        },
    }


def test_phase1_assisted_report_accepts_grouped_source_variant_counts() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = ["scalar_forward:*2"]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = ["scalar_forward:*2"]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_grouped_axis_pattern_variant_counts() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    grouped = "scalar_forward:ndim={1}:dtype={float,double}:device=cuda:*2"
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = [grouped]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = [grouped]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_leading_grouped_count_variant_tokens() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    grouped = "scalar_forward:*2:ndim={1}:dtype={float,double}:device=cuda"
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = [grouped]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = [grouped]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_brace_grouped_count_variant_tokens() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    grouped = "scalar_forward:*2{ndim=[1];dtype=[float,double];device=cuda}"
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = [grouped]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = [grouped]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_parenthesized_grouped_count_variant_tokens() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    grouped = "scalar_forward:*2(ndim=1;dtype=float,double;device=cuda)"
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = [grouped]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = [grouped]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_slash_grouped_axis_variant_rows() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        *[
            f"scalar:forward_cuda:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda"
            for ndim in ["1d", "2d", "3d"]
            for accuracy in ["2", "4"]
            for dtype in ["float", "double"]
        ],
        *[
            f"storage:save_snapshot_gpu:ndim={ndim}:dtype={dtype}:device=gpu"
            for ndim in ["1d", "2d", "3d"]
            for dtype in ["float", "double"]
        ],
        *[
            f"storage:load_snapshot_gpu:ndim={ndim}:dtype={dtype}:device=gpu"
            for ndim in ["1d", "2d", "3d"]
            for dtype in ["float", "double"]
        ],
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["scalar:forward_cuda", "storage:save_snapshot_gpu", "storage:load_snapshot_gpu"]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = list(cast(list[object], surface["fine_grained_operator_units"]))
    phase1_inventory["expanded_unit_identities"] = [
        "scalar:forward_cuda:ndim=1d/2d/3d:accuracy=2/4:dtype=float/double:device=cuda",
        "storage:save_snapshot_gpu:ndim=1d/2d/3d:dtype=float/double:device=gpu",
        "storage:load_snapshot_gpu:ndim=1d/2d/3d:dtype=float/double:device=gpu",
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = list(cast(list[object], surface["fine_grained_operator_units"]))
    source_inventory["expanded_unit_identities"] = list(cast(list[object], phase1_inventory["expanded_unit_identities"]))

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_range_and_comma_grouped_axis_variant_rows() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        *[
            f"scalar:forward_cuda:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda"
            for ndim in ["1", "2", "3"]
            for accuracy in ["2", "4", "6", "8"]
            for dtype in ["float", "double"]
        ],
        *[
            f"storage_snapshot:save_snapshot_gpu:ndim={ndim}:dtype={dtype}:device=gpu"
            for ndim in ["1", "2", "3"]
            for dtype in ["float", "double"]
        ],
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["scalar:forward_cuda", "storage_snapshot:save_snapshot_gpu"]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = list(cast(list[object], surface["fine_grained_operator_units"]))
    phase1_inventory["expanded_unit_identities"] = [
        "scalar:forward_cuda:ndim=1..3:accuracy=2,4,6,8:dtype=float,double:device=cuda",
        "storage_snapshot:save_snapshot_gpu:ndim=1..3:dtype=float,double:device=gpu",
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = list(cast(list[object], surface["fine_grained_operator_units"]))
    source_inventory["expanded_unit_identities"] = list(cast(list[object], phase1_inventory["expanded_unit_identities"]))

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_summary_count_variant_rows() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
        "storage:load_snapshot_gpu:ndim=1:dtype=float",
        "storage:load_snapshot_gpu:ndim=1:dtype=double",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    phase1_inventory["expanded_unit_identities"] = [
        "1 scalar unit expanded over ndim={1} x dtype={float,double} x device={cuda} = 2 concrete identities",
        "1 storage unit expanded over ndim={1} x dtype={float,double} = 2 concrete identities",
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    source_inventory["expanded_unit_identities"] = list(cast(list[object], phase1_inventory["expanded_unit_identities"]))

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_total_variants_summary_rows() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
        "storage:load_snapshot_gpu:ndim=1:dtype=float",
        "storage:load_snapshot_gpu:ndim=1:dtype=double",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    phase1_inventory["expanded_unit_identities"] = [
        "1 scalar unit expands over 2 dtype values = 2 variants",
        "1 storage unit expands over 2 dtype values = 2 variants",
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    source_inventory["expanded_unit_identities"] = [
        "scalar/storage summarized source evidence",
        "total concrete source-required variants = 4",
    ]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_arithmetic_total_summary_rows() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar_forward:ndim=1:dtype=float:device=cuda",
        "scalar_forward:ndim=1:dtype=double:device=cuda",
        "storage:load_snapshot_gpu:ndim=1:dtype=float",
        "storage:load_snapshot_gpu:ndim=1:dtype=double",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    phase1_inventory["expanded_unit_identities"] = [
        "scalar_forward:*2(ndim=1,dtype=float|double,device=cuda)",
        "storage:load_snapshot_gpu:*2(ndim=1,dtype=float|double)",
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = ["scalar_forward", "storage:load_snapshot_gpu"]
    source_inventory["expanded_unit_identities"] = [
        "scalar source expands scalar_forward:*2 = 2",
        "storage source expands storage:load_snapshot_gpu:*2 = 2",
        "total expanded variants covered by source evidence = 2 + 2 = 4",
    ]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_accepts_brace_expanded_base_and_axis_patterns() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar:forward_cuda:ndim=1:dtype=float:device=cuda",
        "scalar:forward_cuda:ndim=1:dtype=double:device=cuda",
        "scalar:backward_cuda:ndim=1:dtype=float:device=cuda",
        "scalar:backward_cuda:ndim=1:dtype=double:device=cuda",
        "storage:save_snapshot_gpu:ndim=1:dtype=float",
        "storage:save_snapshot_gpu:ndim=1:dtype=double",
        "storage:load_snapshot_gpu:ndim=1:dtype=float",
        "storage:load_snapshot_gpu:ndim=1:dtype=double",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = [
        "scalar:forward_cuda",
        "scalar:backward_cuda",
        "storage:save_snapshot_gpu",
        "storage:load_snapshot_gpu",
    ]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = list(cast(list[object], surface["fine_grained_operator_units"]))
    phase1_inventory["expanded_unit_identities"] = [
        "scalar:{forward_cuda,backward_cuda}:ndim={1}:dtype={float,double}:device=cuda",
        "storage:{save_snapshot_gpu,load_snapshot_gpu}:ndim={1}:dtype={float,double}",
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = list(cast(list[object], surface["fine_grained_operator_units"]))
    source_inventory["expanded_unit_identities"] = list(cast(list[object], phase1_inventory["expanded_unit_identities"]))

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_rejects_wrong_grouped_source_variant_counts() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = ["scalar_forward:*1"]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = ["scalar_forward:*1"]

    errors = validate_phase1_assisted_report(report, phase_output)

    assert any("does not cover normalized Phase 1 output" in error for error in errors)


def test_phase1_assisted_report_accepts_source_axes_without_duplicate_variant_list() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["expanded_unit_identities"] = []
    source_inventory["variant_axes"] = {"scalar_forward": {"dtype": ["float", "double"], "ndim": ["1"]}}

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase1_assisted_report_rejects_placeholder_source_variant_alias_even_with_axes() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    report = _runner_phase1_report(variant_ids)
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["expanded_unit_identities"] = ["same_as_phase1_inventory_expanded_unit_identities"]
    source_inventory["variant_axes"] = {"scalar_forward": {"dtype": ["float", "double"], "ndim": ["1"]}}

    errors = validate_phase1_assisted_report(report, phase_output)

    assert any("placeholder aliases" in error for error in errors)
    assert any("does not cover normalized Phase 1 output" in error for error in errors)


def test_phase1_inventory_uses_deterministic_variant_count_when_declared_count_is_stale(tmp_path: Path) -> None:
    from core.assisted_verification import phase1_inventory

    variant_ids = ["solver:apply_cuda:ndim=1:dtype=float:device=cuda"]
    phase_output = _runner_variant_phase1_output(tmp_path, variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["solver:apply_cuda"]
    surface["variant_axes"] = {"ndim": ["1", "2"], "dtype": ["float", "double"], "device": ["cuda"]}
    surface["expanded_operator_instances_count"] = 1
    surface["discovered_operator_names"] = ["solver_${ndim}_${dtype}_apply_cuda"]
    surface["native_operator_symbols"] = ["solver_${ndim}_${dtype}_apply_cuda"]
    surface["source_evidence"] = ["src/solver.cu:generated symbols use ${ndim} and ${dtype}"]

    inventory = phase1_inventory(phase_output)

    assert inventory.expanded_operator_instances_count == 4
    assert len(inventory.expanded_unit_identities) == 4


def test_phase1_assisted_report_accepts_count_and_grouped_source_evidence_without_duplicate_phase1_list() -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    variant_ids = [
        "scalar:forward_cuda:ndim=1:dtype=float:device=cuda",
        "scalar:forward_cuda:ndim=1:dtype=double:device=cuda",
        "storage:load_snapshot_gpu:ndim=1:dtype=float",
        "storage:load_snapshot_gpu:ndim=1:dtype=double",
    ]
    phase_output = _runner_variant_phase1_output(Path("/tmp/project"), variant_ids)
    surface = cast(dict[str, object], phase_output["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["scalar:forward_cuda", "storage:load_snapshot_gpu"]
    report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["fine_grained_operator_units"] = ["scalar:forward_cuda", "storage:load_snapshot_gpu"]
    phase1_inventory["expanded_unit_identities"] = []
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = ["scalar:forward_cuda", "storage:load_snapshot_gpu"]
    source_inventory["variant_axes"] = {"ndim": ["1"], "dtype": ["float", "double"], "device": ["cuda"]}
    source_inventory["expanded_unit_identities"] = [
        "scalar family: 1 base unit x ndim={1} x dtype={float,double} x device=cuda = 2 concrete instances",
        "storage family: 1 base unit x ndim={1} x dtype={float,double} = 2 concrete instances",
    ]

    assert validate_phase1_assisted_report(report, phase_output) == []


@final
class SequencedAssistedSessionManager:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self._prompts: list[str] = []
        self._get_or_create_calls: list[dict[str, str]] = []

    @property
    def prompts(self) -> list[str]:
        return self._prompts

    @property
    def get_or_create_calls(self) -> list[dict[str, str]]:
        return self._get_or_create_calls

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        self._get_or_create_calls.append({"role": role, "lifecycle": lifecycle, "agent": agent})
        return f"{role}-{lifecycle}-{agent or 'default'}"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
        del session_id, timeout
        self._prompts.append(command)
        if not self._responses:
            raise AssertionError("SequencedAssistedSessionManager exhausted responses")
        return json.dumps(self._responses.pop(0))


def test_phase_runner_phase1_assisted_mismatch_retries_with_corrected_json(tmp_path: Path) -> None:
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    session_mgr = SequencedAssistedSessionManager([
        _runner_variant_phase1_output(tmp_path, variant_ids[:1]),
        _runner_phase1_report(variant_ids, verdict="incomplete"),
        _runner_variant_phase1_output(tmp_path, variant_ids),
        _runner_phase1_report(variant_ids),
    ])
    artifact_store = ArtifactStore(str(tmp_path), "assisted-phase1")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    runner.validator.register_validator("project_analysis", lambda _data: {"passed": True, "errors": [], "warnings": []})

    result = runner.run_single_phase("main-session", "phase_1", {"project_dir": str(tmp_path)})

    surface = cast(dict[str, object], result["custom_op_surface"])
    assert surface["expanded_operator_instances_count"] == 2
    assert "failed the assisted custom-op completeness verifier" in session_mgr.prompts[2]
    assert session_mgr.get_or_create_calls[0] == {
        "role": "custom_op_verifier",
        "lifecycle": "persistent",
        "agent": "Sisyphus-Junior",
    }
    assisted = cast(dict[str, object], result["assisted_verification"])
    assert cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])["status"] == "complete"
    assert artifact_store.load_phase_output("1_project_analysis") is not None


def test_phase_runner_phase1_assisted_verifier_repairs_false_negative_report(tmp_path: Path) -> None:
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    false_negative = _runner_phase1_report(variant_ids, verdict="incomplete")
    cast(dict[str, object], false_negative["phase1_inventory"])["expanded_operator_instances_count"] = 1
    cast(dict[str, object], false_negative["phase1_inventory"])["expanded_unit_identities"] = ["scalar_forward"]
    cast(dict[str, object], false_negative["source_evidence_inventory"])["expanded_unit_identities"] = ["scalar_forward"]
    false_negative["missing_variants"] = ["1 variant unaccounted for relative to normalized count"]
    repaired = _runner_phase1_report(variant_ids)
    session_mgr = SequencedAssistedSessionManager([
        _runner_variant_phase1_output(tmp_path, variant_ids),
        false_negative,
        repaired,
    ])
    artifact_store = ArtifactStore(str(tmp_path), "assisted-report-repair")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    runner.validator.register_validator("project_analysis", lambda _data: {"passed": True, "errors": [], "warnings": []})

    result = runner.run_single_phase("main-session", "phase_1", {"project_dir": str(tmp_path)})

    assert len(session_mgr.prompts) == 3
    assert "previous assisted-verification JSON report failed semantic validation" in session_mgr.prompts[2]
    assert "failed the assisted custom-op completeness verifier" not in session_mgr.prompts[2]
    assisted = cast(dict[str, object], result["assisted_verification"])
    assert cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])["status"] == "complete"


def test_phase_runner_phase1_assisted_verifier_repairs_placeholder_source_inventory_report(tmp_path: Path) -> None:
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    placeholder_report = _runner_phase1_report(variant_ids)
    source_inventory = cast(dict[str, object], placeholder_report["source_evidence_inventory"])
    source_inventory["expanded_unit_identities"] = ["same_as_phase1_inventory_expanded_unit_identities"]
    source_inventory["variant_axes"] = {"scalar_forward": {"dtype": ["float", "double"], "ndim": ["1"]}}
    repaired = _runner_phase1_report(variant_ids)
    session_mgr = SequencedAssistedSessionManager([
        _runner_variant_phase1_output(tmp_path, variant_ids),
        placeholder_report,
        repaired,
    ])
    artifact_store = ArtifactStore(str(tmp_path), "assisted-placeholder-repair")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    runner.validator.register_validator("project_analysis", lambda _data: {"passed": True, "errors": [], "warnings": []})

    result = runner.run_single_phase("main-session", "phase_1", {"project_dir": str(tmp_path)})

    assert len(session_mgr.prompts) == 3
    assert "previous assisted-verification JSON report failed semantic validation" in session_mgr.prompts[2]
    assert "same_as_* placeholder aliases" in session_mgr.prompts[2]
    assisted = cast(dict[str, object], result["assisted_verification"])
    assert cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])["status"] == "complete"


def test_phase1_assisted_report_accepts_structured_source_inventory(tmp_path: Path) -> None:
    from core.assisted_verification import validate_phase1_assisted_report

    phase_output = _runner_variant_phase1_output(tmp_path, ["scalar_forward:dtype=float", "scalar_forward:dtype=double"])
    report = _runner_phase1_report(["scalar_forward:dtype=float", "scalar_forward:dtype=double"])
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["fine_grained_operator_units"] = [
        {
            "unit_identity": "scalar_forward",
            "source_evidence": ["scalar.cu:FUNC(forward)"],
        }
    ]

    assert validate_phase1_assisted_report(report, phase_output) == []


def test_phase_runner_phase1_assisted_uses_deterministic_report_for_stale_grouped_verifier(tmp_path: Path) -> None:
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    stale_report = _runner_phase1_report(variant_ids)
    phase1_inventory = cast(dict[str, object], stale_report["phase1_inventory"])
    phase1_inventory["expanded_operator_instances_count"] = 1
    phase1_inventory["expanded_unit_identities"] = ["scalar_forward"]
    source_inventory = cast(dict[str, object], stale_report["source_evidence_inventory"])
    source_inventory["expanded_operator_instances_count"] = 1
    source_inventory["expanded_unit_identities"] = ["scalar_forward"]
    session_mgr = SequencedAssistedSessionManager([
        _runner_generic_template_phase1_output(tmp_path),
        stale_report,
        stale_report,
    ])
    artifact_store = ArtifactStore(str(tmp_path), "assisted-deterministic-fallback")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    runner.validator.register_validator("project_analysis", lambda _data: {"passed": True, "errors": [], "warnings": []})

    result = runner.run_single_phase("main-session", "phase_1", {"project_dir": str(tmp_path)})

    assert len(session_mgr.prompts) == 3
    assert "previous assisted-verification JSON report failed semantic validation" in session_mgr.prompts[2]
    assisted = cast(dict[str, object], result["assisted_verification"])
    summary = cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])
    assert summary["status"] == "complete"
    canonical_obj = artifact_store.load_phase_output("phase_1_custom_op_completeness_check")
    assert isinstance(canonical_obj, dict)
    canonical = cast(dict[str, object], canonical_obj)
    assert canonical["deterministic_completion"] is True
    assert cast(dict[str, object], canonical["phase1_inventory"])["expanded_unit_identities"] == variant_ids
    assert cast(dict[str, object], canonical["source_evidence_inventory"])["expanded_unit_identities"] == variant_ids


def test_phase_runner_phase1_assisted_does_not_fallback_on_real_missing_variants(tmp_path: Path) -> None:
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    incomplete_report = _runner_phase1_report(variant_ids, verdict="incomplete")
    cast(dict[str, object], incomplete_report["phase1_inventory"])["expanded_unit_identities"] = [variant_ids[0]]
    cast(dict[str, object], incomplete_report["source_evidence_inventory"])["expanded_unit_identities"] = [variant_ids[0]]
    session_mgr = SequencedAssistedSessionManager([
        _runner_generic_template_phase1_output(tmp_path),
        incomplete_report,
    ])
    artifact_store = ArtifactStore(str(tmp_path), "assisted-real-missing-no-fallback")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    runner.validator.register_validator("project_analysis", lambda _data: {"passed": True, "errors": [], "warnings": []})
    runner.max_retry = 1

    with pytest.raises(ValueError, match="failed validation"):
        _ = runner.run_single_phase("main-session", "phase_1", {"project_dir": str(tmp_path)})

    assert len(session_mgr.prompts) == 2
    canonical_obj = artifact_store.load_phase_output("phase_1_custom_op_completeness_check")
    assert canonical_obj is None


def test_phase_runner_phase3_assisted_variant_mismatch_retries_with_corrected_json(tmp_path: Path) -> None:
    script = tmp_path / "validate_custom_ops_full.py"
    _ = script.write_text("print('validate')\n", encoding="utf-8")
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    initial_phase3 = {"entry_script_path": str(script), "run_command": f"python {script}"}
    corrected_phase3 = {**initial_phase3, "required_checks": ["all-expanded-variants"]}
    session_mgr = SequencedAssistedSessionManager([
        initial_phase3,
        _runner_phase3_report(variant_ids, verdict="incomplete"),
        corrected_phase3,
        _runner_phase3_report(variant_ids),
    ])
    artifact_store = ArtifactStore(str(tmp_path), "assisted-phase3")
    runner = PhaseRunner(
        session_mgr,
        artifact_store,
        StaticPromptLoader(),
        ValidatorEngine(),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    runner.validator.register_validator("entry_script", lambda _data: {"passed": True, "errors": [], "warnings": []})

    result = runner.run_single_phase(
        "main-session",
        "phase_3",
        {
            "project_dir": str(tmp_path),
            "previous_outputs": {"phase_1_project_analysis": _runner_variant_phase1_output(tmp_path, variant_ids)},
        },
    )

    assert "all-expanded-variants" in cast(list[object], result["required_checks"])
    assert "failed the assisted custom-op validation-coverage verifier" in session_mgr.prompts[2]
    assert session_mgr.get_or_create_calls[0] == {
        "role": "custom_op_verifier",
        "lifecycle": "persistent",
        "agent": "Sisyphus-Junior",
    }
    assisted = cast(dict[str, object], result["assisted_verification"])
    assert cast(dict[str, object], assisted["phase_3_custom_op_contract_coverage_check"])["status"] == "complete"
    assert artifact_store.load_phase_output("3_entry_script") is not None


def test_phase3_assisted_report_accepts_total_variant_summary_with_unit_coverage(tmp_path: Path) -> None:
    from core.assisted_verification import validate_phase3_assisted_report

    _ = (tmp_path / "validate_custom_ops_full.py").write_text("print('ok')\n", encoding="utf-8")
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase1_output = _runner_variant_phase1_output(tmp_path, variant_ids)
    report = _runner_phase3_report(variant_ids)
    contract = cast(dict[str, object], report["phase3_contract_inventory"])
    contract["covered_variant_identities"] = [
        "2 unique identities generated by validate_custom_ops_full.py expanded_variants(), matching expected variant count and axes"
    ]

    assert validate_phase3_assisted_report(report, {"entry_script_path": "validate_custom_ops_full.py"}, phase1_output) == []


def test_phase3_assisted_report_rejects_wrong_total_variant_summary(tmp_path: Path) -> None:
    from core.assisted_verification import validate_phase3_assisted_report

    _ = (tmp_path / "validate_custom_ops_full.py").write_text("print('ok')\n", encoding="utf-8")
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase1_output = _runner_variant_phase1_output(tmp_path, variant_ids)
    report = _runner_phase3_report(variant_ids)
    contract = cast(dict[str, object], report["phase3_contract_inventory"])
    contract["covered_variant_identities"] = [
        "1 unique identity generated by validate_custom_ops_full.py expanded_variants(), matching expected variant count and axes"
    ]

    errors = validate_phase3_assisted_report(report, {"entry_script_path": "validate_custom_ops_full.py"}, phase1_output)

    assert any("missing variant coverage" in error for error in errors)


def test_phase_runner_does_not_propagate_collapsed_phase1_variant_contract() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "validate_custom_ops_full.py", "run_command": "python validate_custom_ops_full.py", "required_checks": []},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "custom_op_surface": {
                        "custom_op_detected": True,
                        "variant_axes_detected": True,
                        "variant_axes": {"ndim": ["1d|2d|3d"], "dtype": ["float|double"]},
                        "expanded_operator_instances_count": 1,
                        "expanded_operator_variants": [
                            {"unit_identity": "deepwave_scalar:forward_cuda:{ndim=1d|2d|3d,dtype=float|double}"},
                        ],
                    }
                }
            }
        },
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    assert "expanded_variant_inventory" not in normalized
    assert "variant_axis_coverage" not in normalized
    assert "per_variant_performance_report" not in normalized


def test_phase_runner_phase3_hardens_shallow_variant_validation_script(tmp_path: Path) -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore(str(tmp_path), "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")
    script = tmp_path / "validate_custom_ops_full.py"
    _ = script.write_text("print('shallow')\n", encoding="utf-8")
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "validate_custom_ops_full.py", "run_command": "python validate_custom_ops_full.py", "required_checks": []},
        {"project_dir": str(tmp_path)},
        {"previous_outputs": {"phase_1_project_analysis": _runner_variant_phase1_output(tmp_path, variant_ids)}},
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    assert normalized["expanded_variant_inventory"] == {
        "variant_axes_detected": True,
        "unit_identities": variant_ids,
        "expanded_operator_instances_count": 2,
    }
    assert normalized["entry_script_path"] == str(script)
    assert str(script) in str(normalized["run_command"])
    hardened_text = script.read_text(encoding="utf-8")
    assert "migration_manifest.json" in hardened_text
    assert "runtime_coverage.json" in hardened_text
    assert "performance.json" in hardened_text
    assert "build rows do not close over every per-expanded-variant unit_identity" in hardened_text
    assert "CANN build provenance" in hardened_text
    assert "OPP install provenance" in hardened_text
    assert "op_kernel/AscendC source evidence" in hardened_text
    assert "scalar_forward:dtype=float" in hardened_text
    assert "scalar_forward:dtype=double" in hardened_text


def test_phase_runner_phase3_non_custom_entry_script_is_not_hardened(tmp_path: Path) -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore(str(tmp_path), "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")
    script = tmp_path / "train.py"
    _ = script.write_text("print('ordinary')\n", encoding="utf-8")

    normalized = runner._normalize_output(
        spec,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"project_dir": str(tmp_path)},
        {
            "previous_outputs": {
                "phase_1_project_analysis": {
                    "project_dir": str(tmp_path),
                    "custom_op_surface": {"custom_op_detected": False},
                    "entry_script": "train.py",
                }
            }
        },
    )

    assert normalized["entry_script_path"] == "train.py"
    assert normalized["run_command"] == "python train.py"
    assert "entry_script_kind" not in normalized
    assert script.read_text(encoding="utf-8") == "print('ordinary')\n"


def test_phase_runner_phase35_requires_expanded_variant_static_context() -> None:
    runner = PhaseRunner(NoopSessionManager(), ArtifactStore("/tmp", "t"), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_35", "phase_35_static_validate", "entry_static")

    normalized = runner._normalize_output(
        spec,
        {"validation_passed": True, "issues": [], "fix_plan": "Static pass."},
        {"project_dir": "/tmp/project"},
        {
            "previous_outputs": {
                "phase_3_entry_script": {
                    "entry_script_kind": "custom_op_full_validation",
                    "expanded_variant_inventory": {
                        "variant_axes_detected": True,
                        "unit_identities": ["op:ndim=1"],
                        "expanded_operator_instances_count": 1,
                    },
                }
            }
        },
    )

    assert normalized["custom_op_static_required"] is True
    assert normalized["expanded_variant_static_required"] is True


def test_phase_35_prompt_context_includes_previous_outputs() -> None:
    class MockSM:
        def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
            del agent
            return "x"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
            del agent
            del role, lifecycle
            return "persistent-main"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
                return json.dumps(self.phase35_outputs.pop(0))
            if "Phase 3" in command:
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
            "candidate_public_api_routes": "candidate public API routes per row",
            "candidate_framework_integration_routes": "candidate framework integration routes per row",
            "route_evidence_fields": "final rows include public_api_route_evidence or framework_integration_route_evidence",
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
            "per_entry_opp_custom_op_artifact_evidence",
            "per_entry_adapter_evidence",
            "per_entry_parity_evidence",
            "integration_e2e_evidence",
            "per_entry_public_api_or_framework_integration_route_evidence",
            "correlate_route_evidence_to_manifest_rows",
            "reject_direct_or_builtin_only_routes",
            "same_run_runtime_coverage",
            "performance_evidence",
            "complete_performance_report",
            "overall_speedup_report",
            "strict_ascend_c_cann_opp_artifacts",
            "op_host_op_kernel_source_evidence",
            "cann_opp_build_install_provenance",
            "generated_opp_package_artifacts",
            "reject_npuextension_aten_only_as_opp_evidence",
            "reject_non_opp_producer_evidence",
            "project_root_artifact_existence",
            "final_chinese_per_row_table_parity",
            "no_fallback_no_zero_call_no_builtin_contamination",
            "native_operator_symbol_inventory",
        ],
        "validation_obligations": [
            "project_local_artifact",
            "strict_opp_artifact",
            "op_host_op_kernel_source",
            "cann_opp_build_install",
            "generated_opp_package_artifacts",
            "reject_npuextension_aten_only",
            "reject_non_opp_producer_evidence",
            "project_root_artifact_existence",
            "runtime_project_api",
            "per_row_public_or_framework_route_evidence",
            "reject_direct_builtin_only_routes",
            "numeric_performance",
            "complete_speedup_report",
            "overall_speedup_report",
            "final_chinese_per_row_table",
            "no_fallback",
        ],
        "phase5_entry_script_revision_allowed": True,
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
        "script_requires_strict_opp_producer_evidence": True,
        "script_rejects_non_opp_producer_success": True,
        "script_runs_project_api_custom_ops": True,
        "script_requires_per_row_route_evidence": True,
        "script_correlates_route_evidence_to_manifest_rows": True,
        "script_rejects_direct_or_builtin_only_routes": True,
        "script_rejects_report_only_success": True,
        "script_requires_project_local_artifacts": True,
        "script_requires_project_root_artifact_existence": True,
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
        del agent
        del role, lifecycle
        return "persistent-main"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
            del agent
            del role, lifecycle
            return "persistent-main"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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

    with pytest.raises(ValueError, match="existing file for custom-op contracts"):
        _ = runner.run_phase_2_to_3(
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

    assert session_mgr.phase35_prompts == []


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
            del agent
            del role, lifecycle
            return "persistent-main"

        def send_command(self, session_id: str, command: str, timeout: int | None = 600) -> str:
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
                    prompt_template="phase_0_env_detect",
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
