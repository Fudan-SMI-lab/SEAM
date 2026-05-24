"""Mock-based tests for WorkflowExecutor."""
import logging
import json
import pytest
import tempfile
import os
import time
from unittest.mock import MagicMock, patch
from pathlib import Path
from typing import cast

from core.types import (
    PhaseDefinition,
    RuntimeSkillsConfig,
    WorkflowDefinition,
    SubWorkflowDefinition,
    TransitionDefinition,
)
from core.workflow_executor import WorkflowExecutor, CUSTOM_OP_OPERATOR_POLL_TIMEOUT_DEFAULT, CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT
from core.artifact_store import ArtifactStore
from core.experience_store import ExperienceStore
from core.telemetry_bridge import TelemetryBridge
from core.prompt_loader import PromptLoader
from core.config import load_workflow
from core.validator_engine import ValidatorEngine
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_entry_static import validate as validate_entry_static
from validators.validate_project_analysis import validate as validate_project_analysis
from validators.validate_venv import validate as validate_venv


def write_runtime_skill(root: Path, name: str, content: str | None = None) -> Path:
    skill_dir = root / ".memory" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content or f"# {name}\n\nUse this guidance.", encoding="utf-8")
    return skill_path


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d


@pytest.fixture
def basic_workflow(temp_dir):
    return WorkflowDefinition(
        name="test", version="1.0",
        phases=[
            PhaseDefinition(id="phase_a", name="A", prompt_template="test.md", output_schema={},
                           type="llm", agent="main_engineer", validator=None,
                           transitions={"on_success": "phase_b"}),
            PhaseDefinition(id="phase_b", name="B", prompt_template="test.md", output_schema={},
                           type="llm", agent="main_engineer", validator=None,
                           transitions={"on_success": "complete"}),
        ],
        terminals=["complete", "failed"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )


@pytest.fixture
def executor(basic_workflow, temp_dir):
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator_engine = MagicMock()
    return WorkflowExecutor(
        basic_workflow, session_mgr, artifact_store, prompt_loader, validator_engine,
        project_dir=temp_dir, output_dir=temp_dir
    )


class TestWorkflowExecutorInit:
    def test_constructor(self, executor):
        assert executor.workflow.name == "test"
        assert executor.state == {}
        assert executor.phase_results == {}

    def test_phase_index_built(self, executor):
        assert "phase_a" in executor.phase_index
        assert "phase_b" in executor.phase_index
        assert executor.phase_index["phase_a"] == 0


class TestExecute:
    def test_basic_execute_flow(self, executor, temp_dir):
        executor.hook_manager = MagicMock()

        result = executor.execute({"PROJECT_DIR": temp_dir})
        assert isinstance(result, dict)

    def test_execute_emits_live_phase_telemetry(self, basic_workflow, temp_dir):
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        artifact_store.save_phase_output.return_value = "raw.json"
        artifact_store.mark_validated.return_value = "canonical.json"
        artifact_store.write_journal.return_value = "journal.jsonl"
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "prompt"
        validator_engine = MagicMock()
        session_mgr.get_or_create.return_value = "session-main"
        session_mgr.send_command.return_value = '{"ok": true}'
        telemetry_bridge = TelemetryBridge(str(Path(temp_dir) / "telemetry"))

        executor = WorkflowExecutor(
            basic_workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator_engine,
            project_dir=temp_dir,
            output_dir=temp_dir,
            telemetry_bridge=telemetry_bridge,
        )

        result = executor.execute({"PROJECT_DIR": temp_dir})

        assert result["status"] == "complete"
        phase_metrics = {metric["phase_id"]: metric for metric in telemetry_bridge._phase_timings.values()}
        assert phase_metrics["phase_a"]["status"] == "success"
        assert phase_metrics["phase_b"]["status"] == "success"
        event_types = [event["event_type"] for event in telemetry_bridge._events]
        assert event_types.count("phase_start") == 2
        assert event_types.count("phase_end") == 2

    def test_execute_emits_skipped_phase_telemetry(self, temp_dir):
        workflow = WorkflowDefinition(
            name="test",
            version="1.0",
            phases=[
                PhaseDefinition(
                    id="phase_a",
                    name="A",
                    prompt_template="test.md",
                    output_schema={},
                    type="llm",
                    condition="${context.RUN_PHASE_A} == 'yes'",
                    transitions={"on_skipped": "complete", "on_success": "complete"},
                ),
            ],
            terminals=["complete"],
        )
        telemetry_bridge = TelemetryBridge(str(Path(temp_dir) / "telemetry"))
        executor = WorkflowExecutor(
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=temp_dir,
            output_dir=temp_dir,
            telemetry_bridge=telemetry_bridge,
        )

        result = executor.execute({"PROJECT_DIR": temp_dir, "RUN_PHASE_A": "no"})

        assert result["phase_results"]["phase_a"]["status"] == "skipped"
        assert telemetry_bridge._phase_timings["phase_a"]["status"] == "skipped"

    def test_execute_emits_dispatch_phase_telemetry(self, temp_dir):
        workflow = WorkflowDefinition(
            name="test",
            version="1.0",
            phases=[
                PhaseDefinition(
                    id="phase_dispatch",
                    name="Dispatch",
                    prompt_template="",
                    output_schema={},
                    type="dispatch",
                    params={
                        "route_field": "${context.ROUTE}",
                        "routes": {"next": "phase_b"},
                    },
                ),
                PhaseDefinition(
                    id="phase_b",
                    name="B",
                    prompt_template="test.md",
                    output_schema={},
                    type="llm",
                    transitions={"on_success": "complete"},
                ),
            ],
            terminals=["complete"],
        )
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session-main"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "prompt"
        telemetry_bridge = TelemetryBridge(str(Path(temp_dir) / "telemetry"))
        executor = WorkflowExecutor(
            workflow,
            session_mgr,
            MagicMock(),
            prompt_loader,
            MagicMock(),
            project_dir=temp_dir,
            output_dir=temp_dir,
            telemetry_bridge=telemetry_bridge,
        )

        result = executor.execute({"PROJECT_DIR": temp_dir, "ROUTE": "next"})

        assert result["phase_results"]["phase_dispatch"]["status"] == "dispatched"
        assert telemetry_bridge._phase_timings["phase_dispatch"]["status"] == "dispatched"


class TestConditionEvaluation:
    def test_condition_true(self, executor):
        result = executor._evaluate_condition(
            "${context.X} != ''",
            state={},
            context={"X": "abc"},
        )
        assert result is True

    def test_condition_empty_embedded_template_false(self, executor):
        result = executor._evaluate_condition(
            "${context.USER_CONSTRAINTS} != ''",
            state={},
            context={"USER_CONSTRAINTS": ""},
        )
        assert result is False

    def test_phase_1_5_skips_without_user_constraints(self, temp_dir):
        workflow = WorkflowDefinition(
            name="skip-empty-constraints",
            version="1.0",
            phases=[
                PhaseDefinition(
                    id="phase_1_5_constraint_summary",
                    name="Constraint Summary",
                    prompt_template="phase_1_5_constraint_summary",
                    output_schema={},
                    type="llm",
                    condition="${context.USER_CONSTRAINTS} != ''",
                    transitions={"on_skip": "phase_2_venv_create", "on_success": "phase_2_venv_create"},
                ),
            ],
            terminals=["phase_2_venv_create"],
        )
        session_mgr = MagicMock()
        executor = WorkflowExecutor(
            workflow,
            session_mgr,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        result = executor.execute({"PROJECT_DIR": temp_dir, "USER_CONSTRAINTS": ""})

        assert result["phase_results"]["phase_1_5_constraint_summary"]["status"] == "skipped"
        session_mgr.get_or_create.assert_not_called()
        session_mgr.send_command.assert_not_called()

    def test_condition_false(self, executor):
        result = executor._evaluate_condition(
            "$.X == ''",
            state={},
            context={},
            loop_state={"X": ""},
        )
        assert result is True

    def test_condition_dollar_shorthand(self, executor):
        result = executor._evaluate_condition(
            "$.exit_code == 0",
            state={},
            context={},
            loop_state={"exit_code": 0},
        )
        assert result is True

    def test_condition_and_operator(self, executor):
        result = executor._evaluate_condition(
            "$.a == 1 and $.b == 2",
            state={},
            context={},
            loop_state={"a": 1, "b": 2},
        )
        assert result is True

    def test_condition_or_operator(self, executor):
        result = executor._evaluate_condition(
            "$.a == 1 or $.b == 2",
            state={},
            context={},
            loop_state={"a": 0, "b": 2},
        )
        assert result is True

    def test_condition_not_operator(self, executor):
        result = executor._evaluate_condition(
            "not $.failed",
            state={},
            context={},
            loop_state={"failed": False},
        )
        assert result is True


class TestResolveInputMapping:
    def test_basic_mapping(self, executor):
        phase = PhaseDefinition(
            id="test", name="test", prompt_template="x", output_schema={},
            input_mapping={"project": "${context.PROJECT_DIR}", "max": "${globals.max}"},
        )
        result = executor._resolve_input_mapping(
            phase, state={},
            context={"PROJECT_DIR": "/tmp/test"},
            loop_vars=None, loop_state=None, loop_history=None, step_outputs=None,
        )
        assert result["project"] == "/tmp/test"
        executor.workflow.globals = {"max": 5}
        result = executor._resolve_input_mapping(
            phase, state={},
            context={"PROJECT_DIR": "/tmp/test"},
            loop_vars=None, loop_state=None, loop_history=None, step_outputs=None,
        )
        assert result["max"] == 5


class TestTransitionResolution:
    def test_on_success(self, executor):
        phase = PhaseDefinition(
            id="a", name="A", prompt_template="x", output_schema={},
            transitions={"success": "b", "failure": "fail"},
        )
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == "b"

    def test_on_failure(self, executor):
        phase = PhaseDefinition(
            id="a", name="A", prompt_template="x", output_schema={},
            transitions={"success": "b", "failure": "error_recovery"},
        )
        next_id = executor._get_next_phase_id(phase, "failure", {}, {})
        assert next_id == "error_recovery"

    def test_yaml_shaped_transition_keys(self, executor):
        phase = PhaseDefinition(
            id="a", name="A", prompt_template="x", output_schema={},
            transitions={"on_success": "b", "on_failure": "error_recovery", "on_skip": "skip_target"},
        )
        assert executor._get_next_phase_id(phase, "success", {}, {}) == "b"
        assert executor._get_next_phase_id(phase, "failure", {}, {}) == "error_recovery"
        assert executor._get_next_phase_id(phase, "skipped", {}, {}) == "skip_target"

    def test_default_next(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == executor.workflow.phases[1].id

    def test_failure_without_transition_stops(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(phase, "failure", {}, {})

        assert next_id is None

    def test_failure_with_only_success_transition_stops(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"on_success": "b"},
        )
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(phase, "failure", {}, {})

        assert next_id is None

    def test_failure_like_status_uses_on_failure_transition(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"on_success": "b", "on_failure": "complete"},
        )
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(
            phase,
            "stagnation_fail_closed_missing_strict_opp_evidence",
            {},
            {},
        )

        assert next_id == "complete"

    def test_failure_like_status_without_failure_transition_stops(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"on_success": "b"},
        )
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(
            phase,
            "fail_closed_missing_strict_opp_evidence",
            {},
            {},
        )

        assert next_id is None

    def test_failure_like_exact_status_transition_wins(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={
                "fail_closed_missing_strict_opp_evidence": "custom_fail",
                "on_failure": "complete",
            },
        )
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(
            phase,
            "fail_closed_missing_strict_opp_evidence",
            {},
            {},
        )

        assert next_id == "custom_fail"

    def test_failure_like_transition_definition_uses_on_failure(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transition=TransitionDefinition(on_success="b", on_failure="complete"),
        )
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(phase, "stagnation", {}, {})

        assert next_id == "complete"

    def test_skipped_without_transition_still_defaults_next(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        next_id = executor._get_next_phase_id(phase, "skipped", {}, {})

        assert next_id == executor.workflow.phases[1].id


class TestShellPhase:
    def test_shell_success(self, executor, temp_dir):
        phase = PhaseDefinition(id="shell", name="S", prompt_template="", output_schema={},
                               type="shell", on_failure="continue")
        setattr(phase, "command", "echo hello")

        state = {}
        loop_state = {}
        status, output = executor._execute_shell_phase(phase, state, {}, loop_state=loop_state)

        assert status == "success"
        assert loop_state.get("script_exit_code") == 0

    def test_shell_failure_continue(self, executor, temp_dir):
        phase = PhaseDefinition(id="shell", name="S", prompt_template="", output_schema={},
                               type="shell", on_failure="continue")
        setattr(phase, "command", "exit 1")

        status, output = executor._execute_shell_phase(phase, {}, {}, loop_state={})
        assert status == "success"


class TestStagnation:
    def test_detect_same_error(self, executor):
        loop_state = {}
        error = "Error: module not found\n  at line 1"

        stagnated = executor._check_stagnation(error, loop_state, threshold=3)
        assert not stagnated
        assert loop_state["stagnation_count"] == 1

        stagnated = executor._check_stagnation(error, loop_state, threshold=3)
        assert not stagnated
        assert loop_state["stagnation_count"] == 2

        stagnated = executor._check_stagnation(error, loop_state, threshold=3)
        assert stagnated
        assert loop_state["stagnation_count"] == 3

    def test_reset_on_different_error(self, executor):
        loop_state = {}
        executor._check_stagnation("Error: A", loop_state, threshold=3)
        assert loop_state["stagnation_count"] == 1

        stagnated = executor._check_stagnation("Error: B", loop_state, threshold=3)
        assert not stagnated
        assert loop_state["stagnation_count"] == 1


class TestStopConditions:
    def test_stop_condition_match(self, executor):
        loop_state = {"exit_code": 0}
        stop_conds = [
            {"condition": "$.exit_code == 0", "status": "success"},
            {"condition": "$.exit_code != 0", "status": "failure"},
        ]
        result = executor._check_stop_conditions(stop_conds, loop_state, {})
        assert result == "success"

    def test_no_stop_condition_match(self, executor):
        loop_state = {"exit_code": 1}
        stop_conds = [
            {"condition": "$.exit_code == 0", "status": "success"},
        ]
        result = executor._check_stop_conditions(stop_conds, loop_state, {})
        assert result is None


def _executor_for_experience_context(tmp_path: Path) -> WorkflowExecutor:
    workflow = WorkflowDefinition(name="experience_context", version="1.0", phases=[], terminals=[])
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    return WorkflowExecutor(
        workflow,
        MagicMock(),
        artifact_store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )


def test_experience_query_context_uses_direct_script_stderr(tmp_path: Path):
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    query_ctx = executor._build_experience_query_context(
        phase,
        state={},
        context={},
        step_outputs={"script_stderr": "direct failure text"},
        loop_history=[],
    )

    assert query_ctx["error_stderr"] == "direct failure text"


def test_experience_query_context_preserves_nested_run_entry_script_stderr(tmp_path: Path):
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    query_ctx = executor._build_experience_query_context(
        phase,
        state={},
        context={},
        step_outputs={"run_entry_script": {"stderr": "nested failure text"}},
        loop_history=[],
    )

    assert query_ctx["error_stderr"] == "nested failure text"


def test_experience_query_context_marks_native_custom_op_gate(tmp_path: Path):
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    query_ctx = executor._build_experience_query_context(
        phase,
        state={
            "phase_3_entry_script": {
                "entry_script_kind": "custom_op_full_validation",
                "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            }
        },
        context={},
        step_outputs={"script_stderr": "ModuleNotFoundError: pointnet2_ops._ext"},
        loop_history=[],
    )

    assert query_ctx["custom_op_native_gate_required"] == "true"
    assert query_ctx["custom_op_evidence_policy"] == (
        "require_real_ascend_cann_acl_opp_native_artifacts_no_aten_only"
    )
    assert "exclude_custom_op_experiences" not in query_ctx


def test_experience_query_context_excludes_custom_op_experiences_for_ordinary_projects(tmp_path: Path):
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="fix_operator",
        name="Fix Operator",
        prompt_template="repair_operator_fixer",
        output_schema={},
        type="llm",
        agent="operator_fixer",
    )

    query_ctx = executor._build_experience_query_context(
        phase,
        state={
            "phase_3_entry_script": {
                "run_command": "python validate.py",
                "operator_unit_count": 0,
                "custom_op_detected": False,
            },
            "phase_35_static_validate": {"custom_op_static_required": False},
        },
        context={},
        step_outputs={"script_stderr": "RuntimeError: aclnn operator is not supported on NPU"},
        loop_history=[],
    )

    assert query_ctx["roles"] == ["operator_fixer"]
    assert query_ctx["exclude_custom_op_experiences"] == "true"
    assert "custom_op_native_gate_required" not in query_ctx


def test_experience_query_context_zero_custom_op_contract_omits_native_gate(tmp_path: Path):
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    query_ctx = executor._build_experience_query_context(
        phase,
        state={
            "phase_3_entry_script": {
                "run_command": "python validate.py",
                "operator_unit_count": 0,
                "custom_op_static_required": False,
                "custom_op_detected": False,
                "reports_dir": "migration_reports",
            },
            "phase_35_static_validate": {"custom_op_static_required": False},
        },
        context={},
        step_outputs={"script_stderr": "FlashAttention2 has been toggled on but flash_attn is missing"},
        loop_history=[],
    )

    assert "custom_op_native_gate_required" not in query_ctx
    assert "custom_op_evidence_policy" not in query_ctx
    assert query_ctx["exclude_custom_op_experiences"] == "true"


class TestRuntimeSkillPromptAssembly:
    def _executor_for_runtime_skills(self, workflow, skill_root: Path, experience_store=None):
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator_engine = MagicMock()
        session_mgr.get_or_create.return_value = "session_123"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader.load_prompt.return_value = "BASE PROMPT"
        executor = WorkflowExecutor(
            workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator_engine,
            framework_config={"runtime_skill_repo_root": str(skill_root)},
            project_dir=str(skill_root),
            output_dir=str(skill_root),
            experience_store=experience_store,
        )
        return executor, session_mgr, prompt_loader

    def test_top_level_llm_appends_agent_and_phase_runtime_skills(self, tmp_path: Path):
        write_runtime_skill(tmp_path, "agent-skill", "# Agent Skill\n\nAgent guidance")
        write_runtime_skill(tmp_path, "phase-skill", "# Phase Skill\n\nPhase guidance")
        phase = PhaseDefinition(
            id="phase_runtime",
            name="Runtime",
            prompt_template="runtime_prompt",
            output_schema={},
            type="llm",
            agent="main_engineer",
            runtime_skills=RuntimeSkillsConfig(
                include=["phase-skill"],
                inject_full=True,
            ),
        )
        workflow = WorkflowDefinition(
            name="runtime_test",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            agents={
                "main_engineer": {
                    "role": "main_engineer",
                    "lifecycle": "persistent",
                    "runtime_skills": RuntimeSkillsConfig(include=["agent-skill"]),
                },
            },
        )
        executor, session_mgr, _prompt_loader = self._executor_for_runtime_skills(
            workflow, tmp_path
        )

        executor._execute_llm_phase(phase, {}, {})

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert sent_prompt.startswith("BASE PROMPT\n\n## Explicit Runtime Skills")
        assert "### agent-skill" in sent_prompt
        assert "### phase-skill" in sent_prompt
        assert "Agent guidance" in sent_prompt
        assert "Phase guidance" in sent_prompt

    def test_dynamic_experience_skips_promoted_skill_already_explicit(self, tmp_path: Path):
        duplicate_path = write_runtime_skill(tmp_path, "duplicate-skill")
        phase = PhaseDefinition(
            id="phase_with_experience",
            name="Experience",
            prompt_template="experience_prompt",
            output_schema={},
            type="llm",
            agent="main_engineer",
            retrieve_experience=True,
            runtime_skills=RuntimeSkillsConfig(include=["duplicate-skill"]),
        )
        workflow = WorkflowDefinition(
            name="dedupe_test",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
        )
        query_result = {
            "selected_experiences": [
                {
                    "id": "promoted-duplicate-skill",
                    "skill_name": "duplicate-skill",
                    "title": "Dynamic Duplicate Guidance",
                    "file_path": str(duplicate_path),
                    "category": "dependency",
                    "subtype": "torch-npu",
                    "relevance_score": 0.99,
                },
                {
                    "id": "promoted-unique-skill",
                    "skill_name": "unique-skill",
                    "title": "Dynamic Unique Guidance",
                    "file_path": str(tmp_path / ".memory" / "skills" / "unique-skill" / "SKILL.md"),
                    "category": "dependency",
                    "subtype": "torch-npu",
                    "relevance_score": 0.85,
                },
            ],
            "summary": "keep summary",
            "warning": "keep warning",
        }
        executor, session_mgr, _prompt_loader = self._executor_for_runtime_skills(
            workflow, tmp_path, experience_store=MagicMock()
        )
        bundle = executor._resolve_runtime_skill_bundle(phase, "main_engineer")
        filtered = executor._dedupe_dynamic_experiences(query_result, bundle, phase.id)
        assert filtered["summary"] == "keep summary"
        assert filtered["warning"] == "keep warning"
        assert [item["title"] for item in filtered["selected_experiences"]] == [
            "Dynamic Unique Guidance"
        ]
        assert len(query_result["selected_experiences"]) == 2

        with patch("core.experience_query.ExperienceQuerier.query", return_value=query_result):
            executor._execute_llm_phase(phase, {}, {})

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert "## Explicit Runtime Skills" in sent_prompt
        assert "### duplicate-skill" in sent_prompt
        assert "## Relevant Past Experiences" in sent_prompt
        assert "Dynamic Unique Guidance" in sent_prompt
        assert "Dynamic Duplicate Guidance" not in sent_prompt



def test_experience_action_cards_include_readable_paths():
    from core.experience_injector import ExperienceInjector

    injected = ExperienceInjector().inject(None, {
        "selected_experiences": [{
            "id": "dep-exp",
            "type": "document",
            "title": "Dependency Fix",
            "target_roles": ["dependency_fixer"],
            "target_phases": ["phase_5_validation"],
            "relevance_score": 0.9,
            "reasoning": "same torch-npu failure",
            "file_path": "/tmp/dep.md",
            "asset_paths": ["/tmp/rule.yaml"],
            "root_cause": "should stay compact at non-critical relevance",
            "fix_steps": ["Do not inject this by default"],
        }]
    })

    assert "## Relevant Past Experiences" in injected
    assert "### Experience Card 1: Dependency Fix" in injected
    assert "- id: `dep-exp`" in injected
    assert "- target_roles: dependency_fixer" in injected
    assert "- target_phases: phase_5_validation" in injected
    assert "`/tmp/dep.md`" in injected
    assert "`/tmp/rule.yaml`" in injected
    assert "Read applicable paths first" in injected
    assert "fix_steps" not in injected


def test_fix_prompt_inherits_analyze_error_selected_experiences(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
                "retrieve_experience": True,
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"code_adapter": "fix_code"},
            },
            {
                "id": "fix_code",
                "type": "llm",
                "prompt_template": "fix_prompt",
                "agent": "code_adapter",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="inherit_exp",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "code_adapter": {"role": "code_adapter", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.side_effect = [
        '{"repair_role": "code_adapter", "category": "code", "root_cause": "cuda call", "suggested_fix": "use npu"}',
        '{"fixed": true}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, ctx: f"{template}\n{ctx.get('experience_action_cards', '')}"

    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )
    query_result = {
        "selected_experiences": [{
            "id": "code-exp",
            "type": "skill",
            "title": "CUDA Call Fix",
            "target_roles": ["code_adapter"],
            "target_phases": ["phase_5_validation"],
            "relevance_score": 0.88,
            "reasoning": "same cuda call",
            "file_path": str(tmp_path / ".memory" / "skills" / "cuda" / "SKILL.md"),
        }],
        "summary": "selected",
        "warning": "",
    }

    with patch("core.experience_query.ExperienceQuerier.query", return_value=query_result):
        result = executor._run_sub_workflow(
            sub_workflow,
            loop_vars={"entry_script": "python main.py"},
            state={},
            context={},
            sub_wf_phases=sub_workflow.phases,
            step_outputs={"script_exit_code": 1, "script_stderr": "cuda error"},
            loop_history=[],
            loop_state={},
        )

    assert result["step_outputs"]["selected_experiences"][0]["id"] == "code-exp"
    assert result["step_outputs"]["repair_dispatch"]["dispatched_to"] == "fix_code"
    fix_prompt = session_mgr.send_command.call_args_list[-1][0][1]
    assert "## Analyzer-Selected Experience Action Cards" in fix_prompt
    assert "CUDA Call Fix" in fix_prompt
    assert "Read applicable paths yourself" in fix_prompt
    assert "used_experience_ids" in fix_prompt


def test_operator_fix_phase_writes_runtime_artifacts_and_sends_slim_prompt(tmp_path: Path):
    write_runtime_skill(tmp_path, "operator-runtime-skill")
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "retrieve_experience": True,
                "runtime_skills": {"include": ["operator-runtime-skill"], "missing": "ignore"},
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="slim_operator",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.side_effect = [
        '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported custom op", "suggested_fix": "port custom op"}',
        '{"fixed": true}',
    ]
    real_loader = PromptLoader(Path(__file__).resolve().parent.parent / "prompts")

    def load_prompt(template: str, ctx: dict[str, str]) -> str:
        if template == "repair_operator_fixer":
            return real_loader.load_prompt(template, ctx)
        return template

    prompt_loader.load_prompt.side_effect = load_prompt
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        framework_config={"runtime_skill_repo_root": str(tmp_path)},
        project_dir=str(tmp_path / "project with spaces!"),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={
            "script_stderr": "RuntimeError: unsupported custom op",
            "experience_action_cards": ["Read /skills/custom-op/SKILL.md"],
        },
        loop_history=[],
        loop_state={},
    )

    assert result["step_outputs"]["repair_dispatch"]["dispatched_to"] == "fix_operator"
    fix_prompt = session_mgr.send_command.call_args_list[-1][0][1]
    assert "This is a generic operator-incompatibility repair" in fix_prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in fix_prompt
    assert "第1、2、3、5、6、7点要求" not in fix_prompt
    assert ".skills" not in fix_prompt
    assert "repair_role" not in fix_prompt
    assert "category" not in fix_prompt
    assert "root_cause" not in fix_prompt
    assert "suggested_fix" not in fix_prompt
    assert "constraint_summary" not in fix_prompt
    assert "env_context" not in fix_prompt
    assert "last_review" not in fix_prompt
    assert "unsupported custom op" not in fix_prompt
    assert "port custom op" not in fix_prompt
    assert "RuntimeError: unsupported custom op" not in fix_prompt
    assert "Ascend NPU 原生修复" in fix_prompt
    assert "CPU fallback" in fix_prompt
    assert "不要启动后台检索/后台 agents 后提前返回" in fix_prompt
    assert "modified_files: []" in fix_prompt
    assert "modified_files" in fix_prompt
    assert "agent_diagnostics" in fix_prompt
    assert "## Analyzer-Selected Experience Action Cards" not in fix_prompt
    assert "Read /skills/custom-op/SKILL.md" not in fix_prompt
    assert "## Explicit Runtime Skills" in fix_prompt
    assert "### operator-runtime-skill" in fix_prompt

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    runtime_error = runtime_dir / "runtime_error_project_with_spaces_.md"
    runtime_card = runtime_dir / "runtimeCard_project_with_spaces_.md"
    operator_context = runtime_dir / "operatorRepairContext_project_with_spaces_.md"
    assert str(runtime_error.resolve()) in fix_prompt
    assert str(runtime_card.resolve()) in fix_prompt
    assert str(operator_context.resolve()) not in fix_prompt
    assert not operator_context.exists()
    assert str(tmp_path / "project with spaces!") in fix_prompt
    assert "python main.py" in fix_prompt
    error_text = runtime_error.read_text(encoding="utf-8")
    card_text = runtime_card.read_text(encoding="utf-8")
    assert "# Operator Fixer" in error_text
    assert "## Execution Failure" in error_text
    assert "## Error Classification" in error_text
    assert "Migration Constraints" not in error_text
    assert "Hard Rules" not in error_text
    assert "## Experience Card 1" in card_text
    assert "Read /skills/custom-op/SKILL.md" in card_text


def test_operator_fix_session_error_is_retryable_iteration_without_validated_artifact(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "on_failure": "break",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="operator_error_guard",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    session_mgr.send_command.side_effect = [
        '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported custom op", "suggested_fix": "port custom op"}',
        '{"ok": false, "error": "Compaction response is incomplete"}',
        '{"ok": false, "error": "Compaction response is incomplete"}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={"script_stderr": "RuntimeError: unsupported custom op"},
        loop_history=[],
        loop_state={},
    )

    assert result["status"] == "communication_error"
    assert result["step_outputs"]["repair_dispatch"]["dispatched_to"] == "fix_operator"
    assert result["step_outputs"]["fix_operator"]["communication_error"] is True
    assert result["step_outputs"]["fix_operator"]["retryable"] is True
    assert "Compaction response is incomplete" in result["step_outputs"]["fix_operator"]["error"]
    saved_phase_ids = [call.args[0] for call in artifact_store.save_phase_output.call_args_list]
    validated_phase_ids = [call.args[0] for call in artifact_store.mark_validated.call_args_list]
    assert "fix_operator" not in saved_phase_ids
    assert "fix_operator" not in validated_phase_ids


def test_operator_fix_empty_response_stays_retryable_without_fresh_session(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "on_failure": "break",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="operator_empty_retry",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    session_mgr.send_command.side_effect = [
        '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported custom op", "suggested_fix": "port custom op"}',
        '{"ok": false, "error": "Empty session response"}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={"script_stderr": "RuntimeError: unsupported custom op"},
        loop_history=[],
        loop_state={},
    )

    assert result["status"] == "communication_error"
    assert result["step_outputs"]["fix_operator"]["communication_error"] is True
    assert result["step_outputs"]["fix_operator"]["retryable"] is True
    assert "Empty session response" in result["step_outputs"]["fix_operator"]["error"]
    session_mgr.create_session.assert_not_called()
    called_sessions = [call.args[0] for call in session_mgr.send_command.call_args_list]
    assert called_sessions == ["session:error_analyzer", "session:operator_fixer"]


def test_operator_fix_session_error_continues_phase5_loop(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=2,
        stagnation_threshold=10,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "${loop_vars.entry_script}",
                "on_failure": "continue",
            },
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "on_failure": "break",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="operator_loop_retry",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_classification = '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "strict OPP missing", "suggested_fix": "build OPP"}'
    session_mgr.send_command.side_effect = [
        operator_classification,
        '{"ok": false, "error": "Empty session response"}',
        operator_classification,
        '{"fixed": true, "used_experience_ids": [], "ignored_experience_ids": []}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    entry_cmd = "python -c \"import sys; sys.stderr.write('original operator failure'); sys.exit(1)\""
    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": entry_cmd, "project_dir": str(tmp_path)},
        ),
        state={},
        context={},
    )

    assert result["status"] == "failure"
    assert result["iterations"] == 2
    assert result["loop_history"][0]["status"] == "communication_error"
    assert result["loop_history"][1]["status"] == "success"
    assert "original operator failure" in result["loop_state"]["script_stderr"]
    assert result["loop_state"]["fix_operator"]["fixed"] is True
    session_mgr.create_session.assert_not_called()
    called_sessions = [call.args[0] for call in session_mgr.send_command.call_args_list]
    assert called_sessions == [
        "session:error_analyzer",
        "session:operator_fixer",
        "session:error_analyzer",
        "session:operator_fixer",
    ]


def test_operator_fix_communication_error_does_not_trigger_stagnation(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=3,
        stagnation_threshold=1,
        phases=[
            {"id": "run_entry_script", "type": "shell", "command": "${loop_vars.entry_script}", "on_failure": "continue"},
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "on_failure": "break",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="operator_comm_retry",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.side_effect = ["session:operator_fixer_retry_1", "session:operator_fixer_retry_2"]
    operator_classification = '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "strict OPP missing", "suggested_fix": "build OPP"}'
    session_mgr.send_command.side_effect = [
        operator_classification,
        *('{"ok": false, "error": "Session still running with no response"}' for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
        operator_classification,
        *('{"ok": false, "error": "Session still running with no response"}' for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
        operator_classification,
        *('{"ok": false, "error": "Session still running with no response"}' for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    entry_cmd = "python -c \"import sys; sys.stderr.write('same operator failure'); sys.exit(1)\""
    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": entry_cmd, "project_dir": str(tmp_path)},
        ),
        state={"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": entry_cmd}},
        context={},
    )

    assert result["status"] == "failure"
    assert result["iterations"] == 3
    assert [entry["status"] for entry in result["loop_history"]] == ["communication_error", "communication_error", "communication_error"]
    assert result["loop_state"]["stagnation_count"] == 0
    assert session_mgr.create_session.call_count == 2
    called_sessions = [call.args[0] for call in session_mgr.send_command.call_args_list]
    assert called_sessions.count("session:error_analyzer") == 3
    assert called_sessions.count("session:operator_fixer") == CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT
    assert called_sessions.count("session:operator_fixer_retry_1") == CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT
    assert called_sessions.count("session:operator_fixer_retry_2") == CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT


def test_custom_op_imp_operator_fix_uses_fresh_session_after_prior_communication_error(tmp_path: Path):
    workflow = WorkflowDefinition(
        name="imp-operator-session-retry",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={"operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_imp_fix_operator_retry"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}
    loop_state = {
        "imp_fix_operator": {
            "status": "communication_error",
            "communication_error": True,
            "retryable": True,
            "error": "Remote end closed connection without response",
        }
    }

    sid = executor._resolve_sub_workflow_llm_session(
        agent_id="operator_fixer",
        phase_id="imp_fix_operator",
        state=state,
        loop_state=loop_state,
        use_custom_op_gate_polling=True,
    )

    assert sid == "session:operator_fixer_imp_fix_operator_retry"
    session_mgr.create_session.assert_called_once()
    session_mgr.get_or_create.assert_not_called()


def test_custom_op_operator_retryable_incomplete_output_keeps_persistent_session(tmp_path: Path):
    workflow = WorkflowDefinition(
        name="operator-incomplete-keeps-session",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={"operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    session_mgr.get_or_create.return_value = "session:operator_fixer"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}
    loop_state = {
        "fix_operator": {
            "status": "communication_error",
            "communication_error": True,
            "retryable": True,
            "error": "custom-op operator repair returned incomplete before strict OPP final gate FULL_PASS",
        }
    }

    sid = executor._resolve_sub_workflow_llm_session(
        agent_id="operator_fixer",
        phase_id="fix_operator",
        state=state,
        loop_state=loop_state,
        use_custom_op_gate_polling=True,
    )

    assert sid == "session:operator_fixer"
    session_mgr.create_session.assert_not_called()
    session_mgr.get_or_create.assert_called_once_with(role="operator_fixer", lifecycle="persistent")


def test_operator_fix_partial_prose_is_retryable_not_stagnation(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=3,
        stagnation_threshold=1,
        phases=[
            {"id": "run_entry_script", "type": "shell", "command": "${loop_vars.entry_script}", "on_failure": "continue"},
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "on_failure": "break",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="operator_partial_retry",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    operator_classification = '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "strict OPP missing", "suggested_fix": "build OPP"}'
    partial_response = "I read this as implementation-continuation for the custom-op repair: I’ll inspect the validator requirements first."
    session_mgr.send_command.side_effect = [
        operator_classification,
        *(partial_response for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
        operator_classification,
        *(partial_response for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
        operator_classification,
        *(partial_response for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    entry_cmd = "python -c \"import sys; sys.stderr.write('same operator failure'); sys.exit(1)\""
    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": entry_cmd, "project_dir": str(tmp_path)},
        ),
        state={"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": entry_cmd}},
        context={},
    )

    assert result["status"] == "failure"
    assert result["iterations"] == 3
    assert [entry["status"] for entry in result["loop_history"]] == ["communication_error", "communication_error", "communication_error"]
    assert result["loop_state"]["stagnation_count"] == 0
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["communication_error"] is True
    assert fix_output["retryable"] is True
    assert "strict OPP final gate FULL_PASS" in fix_output["error"]



def _run_single_llm_subphase(
    tmp_path: Path,
    phase: dict[str, object],
    framework_config: dict[str, object] | None = None,
):
    agent_id = str(phase.get("agent") or "main_engineer")
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[phase],
    )
    workflow = WorkflowDefinition(
        name="single_subphase",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={agent_id: {"role": agent_id, "lifecycle": "persistent"}},
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.return_value = '{"fixed": true}'
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: f"prompt:{template}"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        framework_config=framework_config,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    executor._run_sub_workflow(
        sub_workflow,
        loop_vars={},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={},
        loop_history=[],
        loop_state={},
    )
    return session_mgr


def test_fix_operator_without_explicit_timeout_has_no_phase_deadline_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="core.workflow_executor")

    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "fix_operator",
            "type": "llm",
            "prompt_template": "repair_operator_fixer",
            "agent": "operator_fixer",
        },
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None
    log_text = caplog.text
    assert "phase_id=fix_operator" in log_text
    assert "agent_id=operator_fixer" in log_text
    assert "session_id=session:operator_fixer" in log_text
    assert "timeout=None" in log_text
    assert "prompt_length=" in log_text
    assert "raw_response_length=" in log_text


def test_repair_subphase_ignores_configured_session_timeout_repair(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "fix_code",
            "type": "llm",
            "prompt_template": "repair_code_adapter",
            "agent": "code_adapter",
        },
        framework_config={"session_timeout_repair": "123"},
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None


def test_invalid_repair_timeout_config_is_ignored_without_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING, logger="core.workflow_executor")

    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "fix_operator",
            "type": "llm",
            "prompt_template": "repair_operator_fixer",
            "agent": "operator_fixer",
        },
        framework_config={"session_timeout_repair": "not-an-int"},
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None
    assert "Invalid session_timeout_repair" not in caplog.text


def test_explicit_subphase_timeout_is_not_used_as_session_deadline(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "imp_fix_operator",
            "type": "llm",
            "prompt_template": "repair_operator_fixer",
            "agent": "operator_fixer",
            "timeout": 77,
        },
        framework_config={"session_timeout_repair": "123"},
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None


def test_analyze_error_ignores_configured_repair_timeout(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "analyze_error",
            "type": "llm",
            "prompt_template": "analyze_prompt",
            "agent": "error_analyzer",
        },
        framework_config={"session_timeout_repair": "123"},
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None


def test_analyze_error_specific_timeout_is_not_used_as_session_deadline(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "analyze_error",
            "type": "llm",
            "prompt_template": "analyze_prompt",
            "agent": "error_analyzer",
        },
        framework_config={
            "session_timeout_analyze_error": "45",
            "session_timeout_repair": "123",
        },
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None


def test_analyze_error_without_explicit_timeout_has_no_phase_deadline(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "analyze_error",
            "type": "llm",
            "prompt_template": "analyze_prompt",
            "agent": "error_analyzer",
        },
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None


def test_non_repair_non_analyzer_subphase_timeout_remains_unbounded_without_explicit_timeout(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "diagnose_context",
            "type": "llm",
            "prompt_template": "diagnose_prompt",
            "agent": "error_analyzer",
        },
        framework_config={"session_timeout_repair": "123"},
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] is None


def test_top_level_llm_timeout_resolver_honors_phase_timeout_and_config(tmp_path: Path) -> None:
    executor = WorkflowExecutor(
        WorkflowDefinition(name="timeout-resolvers", version="1.0", phases=[], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={
            "session_timeout_phase": "33",
            "session_timeout_repair": "44",
            "session_timeout_analyze_error": "55",
        },
    )
    top_level = PhaseDefinition(
        id="phase_2_venv_create",
        name="Venv",
        prompt_template="phase_2_venv_create",
        output_schema={},
        type="llm",
        timeout=77,
    )
    configured_top_level = PhaseDefinition(
        id="phase_6_report",
        name="Report",
        prompt_template="phase_6_report",
        output_schema={},
        type="llm",
    )
    analyze = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        timeout=88,
    )
    repair = PhaseDefinition(
        id="fix_operator",
        name="Repair",
        prompt_template="repair_operator_fixer",
        output_schema={},
        type="llm",
        timeout=99,
    )

    assert executor._resolve_top_level_llm_timeout(top_level) == 77
    assert executor._resolve_top_level_llm_timeout(configured_top_level) == 33
    assert executor._resolve_sub_workflow_llm_timeout(analyze) is None
    assert executor._resolve_sub_workflow_llm_timeout(repair) is None





def test_workflow_executor_forces_custom_op_gate_analysis_to_operator_dispatch(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"code_adapter": "fix_code", "operator_fixer": "fix_operator"},
            },
            {
                "id": "fix_code",
                "type": "llm",
                "prompt_template": "fix_code_prompt",
                "agent": "code_adapter",
            },
            {
                "id": "fix_operator",
                "type": "llm",
                "prompt_template": "fix_operator_prompt",
                "agent": "operator_fixer",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="forced_operator_dispatch",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "code_adapter": {"role": "code_adapter", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.side_effect = [
        json.dumps({
            "repair_role": "code_adapter",
            "category": "pathing",
            "root_cause": "stale Path.relative_to(PROJECT_DIR) failure",
            "suggested_fix": "adjust path handling",
        }),
        json.dumps({"fixed": True}),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"custom_op_operator_incomplete_max_continuations": 0},
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python validate.py"},
        state={
            "phase_3_entry_script": {
                "entry_script_kind": "custom_op_full_validation",
                "run_command": "python validate.py",
                "reports_dir": str(tmp_path / "migration_reports"),
            }
        },
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={
            "script_stderr": (
                "Custom-op final evidence gate failed: full_migration_status is FULL_MIGRATION_INCOMPLETE; "
                "closed_pass_entries=0; remaining_entries=4; custom_call_count_total=0; zero_call_detected=true"
            ),
        },
        loop_history=[{
            "iteration": 1,
            "status": "success",
            "error_category": "pathing",
            "repair_role": "code_adapter",
            "agent_diagnostics": "Remaining failure is custom-op/operator evidence incompleteness",
        }],
        loop_state={},
    )

    assert result["step_outputs"]["error_analysis"]["category"] == "operator"
    assert result["step_outputs"]["error_analysis"]["repair_role"] == "operator_fixer"
    assert result["step_outputs"]["repair_dispatch"]["dispatched_to"] == "fix_operator"
    called_sessions = [call.args[0] for call in session_mgr.send_command.call_args_list]
    assert called_sessions == ["session:error_analyzer", "session:operator_fixer"]


def test_workflow_executor_plain_dependency_pathing_is_not_forced_to_operator(tmp_path: Path):
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="analyze_prompt",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="plain_pathing", version="1.0", phases=[], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {
            "repair_role": "code_adapter",
            "category": "pathing",
            "root_cause": "plain import path failure",
            "suggested_fix": "fix PYTHONPATH",
        },
        {
            "failure_log": "ModuleNotFoundError: No module named 'torch_npu'",
            "entry_script_contract": "(No Phase 3 entry-script contract available)",
            "previous_outputs": "(No previous repair attempts)",
        },
        {},
    )

    assert normalized["category"] == "pathing"
    assert normalized["repair_role"] == "code_adapter"


def test_workflow_executor_phase1_normalization_uses_context_project_dir(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Phase 1",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase1-normalize", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path / "fallback_project"),
        output_dir=str(tmp_path),
    )
    trusted_project = tmp_path / "trusted_project"
    untrusted_project = tmp_path / "untrusted_project"

    normalized = executor._normalize_llm_output(
        phase,
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


def test_workflow_executor_phase1_normalization_derives_expanded_variant_count(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Phase 1",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase1-normalize", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
            "custom_op_surface": {
                "expanded_operator_instances_count": 1,
                "expanded_operator_variants": [
                    {"unit_identity": "op:variant=a"},
                    {"unit_identity": "op:variant=b"},
                ],
            },
        },
        {"project_dir": str(tmp_path)},
        {},
    )

    surface = normalized["custom_op_surface"]
    assert isinstance(surface, dict)
    assert surface["expanded_operator_instances_count"] == 2


def test_workflow_executor_phase1_normalization_expands_source_template_base_units(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Phase 1",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase1-template-normalize", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
            "custom_op_surface": {
                "custom_op_detected": True,
                "variant_axes_detected": True,
                "variant_axes": {"ndim": ["1d", "2d"], "dtype": ["float", "double"], "device": ["cuda"]},
                "fine_grained_operator_units": ["alpha:forward_cuda", "beta:forward_cuda", "cache:save_gpu"],
                "discovered_operator_names": [
                    "alpha_${ndim}_${dtype}_forward_cuda",
                    "beta_${ndim}_${dtype}_forward_cuda",
                    "cache_${ndim}_${dtype}_save_gpu",
                ],
                "native_operator_symbols": ["alpha:forward_cuda", "beta:forward_cuda", "cache:save_gpu"],
                "source_evidence": ["src/backend.py:enumerates ndim 1, 2 and dtype float,double"],
                "expanded_operator_variants": [
                    {
                        "unit_identity": "alpha:forward_cuda:ndim=1d:dtype=float:device=cuda",
                        "base_unit_identity": "alpha:forward_cuda",
                        "axis_values": {"ndim": "1d", "dtype": "float", "device": "cuda"},
                        "source_evidence": ["src/backend.py:sample"],
                        "candidate_public_api_routes": ["pkg.alpha.forward"],
                    }
                ],
            },
        },
        {"project_dir": str(tmp_path)},
        {},
    )

    surface = cast(dict[str, object], normalized["custom_op_surface"])
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    variant_ids = {cast(str, variant["unit_identity"]) for variant in variants}
    axes = cast(dict[str, list[str]], surface["variant_axes"])

    assert surface["expanded_operator_instances_count"] == 12
    assert "beta:forward_cuda:ndim=2d:dtype=double:device=cuda" in variant_ids
    assert "cache:save_gpu:ndim=2d:dtype=double:device=gpu" in variant_ids
    assert axes["device"] == ["cuda", "gpu"]


def test_workflow_executor_phase1_normalization_expands_global_source_template_axes(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Phase 1",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase1-global-template-normalize", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
            "custom_op_surface": {
                "custom_op_detected": True,
                "variant_axes_detected": True,
                "variant_axes": {
                    "ndim": ["1d", "2d"],
                    "accuracy": ["2", "4"],
                    "dtype": ["float", "double"],
                    "block_size": [64, 128],
                    "device": ["cuda"],
                },
                "fine_grained_operator_units": [
                    "solver_alpha:forward_cuda",
                    "solver_beta:backward_cuda",
                    "solver_gamma:update_gpu",
                    "snapshot:save_gpu",
                    "metadata:describe",
                ],
                "discovered_operator_names": [
                    "solver_alpha_forward_cuda",
                    "solver_beta_backward_cuda",
                    "solver_gamma_update_gpu",
                    "snapshot_save_gpu",
                    "metadata_describe",
                ],
                "native_operator_symbols": [
                    "solver_alpha_2d_4_float_forward_cuda",
                    "solver_beta_2d_4_float_backward_cuda",
                    "solver_gamma_2d_4_float_update_gpu",
                    "snapshot_save_2d_float_gpu",
                    "metadata_describe",
                ],
                "source_evidence": [
                    "src/loader.py builds native names over ndim, accuracy, dtype, pass, and device",
                    "src/loader.py enumerates ndim 1d,2d; accuracy 2,4; dtype float,double for CUDA/GPU bindings",
                ],
                "expanded_operator_variants": [
                    {
                        "unit_identity": "solver_alpha:forward_cuda:ndim=1d:accuracy=2:dtype=float:device=cuda",
                        "base_unit_identity": "solver_alpha:forward_cuda",
                        "axis_values": {"ndim": "1d", "accuracy": "2", "dtype": "float", "device": "cuda"},
                    }
                ],
            },
        },
        {"project_dir": str(tmp_path)},
        {},
    )

    surface = cast(dict[str, object], normalized["custom_op_surface"])
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    variant_ids = {cast(str, variant["unit_identity"]) for variant in variants}
    bases = {cast(str, variant["base_unit_identity"]) for variant in variants}

    assert surface["expanded_operator_instances_count"] == 28
    assert bases == {"solver_alpha:forward_cuda", "solver_beta:backward_cuda", "solver_gamma:update_gpu", "snapshot:save_gpu"}
    assert "solver_beta:backward_cuda:ndim=1d:accuracy=2:dtype=double:device=cuda" in variant_ids
    assert "solver_gamma:update_gpu:ndim=2d:accuracy=4:dtype=float:device=gpu" in variant_ids
    assert "snapshot:save_gpu:ndim=2d:dtype=double:device=gpu" in variant_ids
    assert all("snapshot:save_gpu:ndim=1d:accuracy" not in variant_id for variant_id in variant_ids)
    assert all("metadata:describe" not in variant_id for variant_id in variant_ids)
    assert all("block_size" not in variant_id for variant_id in variant_ids)


def test_workflow_executor_phase1_normalization_expands_arbitrary_template_axes(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Phase 1",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase1-generic-axis-normalize", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
            "custom_op_surface": {
                "custom_op_detected": True,
                "variant_axes_detected": True,
                "variant_axes": {"boundary_condition": ["absorbing", "periodic"], "mode": ["fast", "accurate"], "device": ["cuda"]},
                "fine_grained_operator_units": ["solver:apply_cuda"],
                "discovered_operator_names": ["solver_${boundary_condition}_${mode}_apply_cuda"],
                "native_operator_symbols": ["solver:apply_cuda"],
                "source_evidence": ["src/register.py:generated symbols use ${boundary_condition} and ${mode}"],
                "expanded_operator_variants": [
                    {
                        "unit_identity": "solver:apply_cuda:boundary_condition=absorbing:mode=fast:device=cuda",
                        "base_unit_identity": "solver:apply_cuda",
                        "axis_values": {"boundary_condition": "absorbing", "mode": "fast", "device": "cuda"},
                        "source_evidence": ["src/register.py:sample"],
                        "candidate_public_api_routes": ["pkg.solver.apply"],
                    }
                ],
            },
        },
        {"project_dir": str(tmp_path)},
        {},
    )

    surface = cast(dict[str, object], normalized["custom_op_surface"])
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    variant_ids = {cast(str, variant["unit_identity"]) for variant in variants}

    assert surface["expanded_operator_instances_count"] == 4
    assert "solver:apply_cuda:boundary_condition=periodic:mode=accurate:device=cuda" in variant_ids


def _workflow_executor_for_custom_op_gate(tmp_path: Path) -> WorkflowExecutor:
    return WorkflowExecutor(
        WorkflowDefinition(name="custom-op-gate", version="1.0", phases=[], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )


def _expanded_variant_contract(project_dir: Path) -> dict[str, object]:
    from tests.test_validator_engine import _valid_custom_op_contract

    contract = _valid_custom_op_contract(str(project_dir / "validate_custom_ops_full.py"), str(project_dir))
    contract["expanded_variant_inventory"] = {
        "variant_axes_detected": True,
        "unit_identities": ["ScalarFwd2D", "ScalarBwd2D"],
        "expanded_operator_instances_count": 2,
    }
    contract["variant_axis_coverage"] = {"all_axes_covered": True, "axes": {"direction": ["forward", "backward"]}}
    contract["per_variant_performance_report"] = {"required": True, "one_entry_per_expanded_variant": True}
    return contract


def _write_one_row_custom_op_gate(project_dir: Path, inventory: dict[str, object]) -> None:
    from tests.test_validator_engine import _valid_custom_op_final_gate, _write_custom_op_manifest, _write_strict_opp_fixture

    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_custom_op_manifest(project_dir, ["ScalarFwd2D"])
    _write_strict_opp_fixture(project_dir)
    gate = _valid_custom_op_final_gate()
    gate["expanded_variant_inventory"] = inventory
    (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(gate), encoding="utf-8")


def test_workflow_executor_custom_op_final_gate_false_variant_metadata_cannot_bypass_expanded_closure(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_one_row_custom_op_gate(
        project_dir,
        {"variant_axes_detected": False, "unit_identities": ["ScalarFwd2D"], "expanded_operator_instances_count": 1},
    )
    executor = _workflow_executor_for_custom_op_gate(project_dir)
    loop_state: dict[str, object] = {}

    status, result = executor._execute_custom_op_final_gate(
        {"phase_3_entry_script": _expanded_variant_contract(project_dir)},
        {"PROJECT_DIR": str(project_dir)},
        None,
        loop_state,
    )

    assert status == "success"
    assert result["passed"] is False
    assert any("expanded variant unit identities" in error for error in result["errors"])
    assert loop_state["script_exit_code"] == 1


def test_workflow_executor_custom_op_final_gate_subset_unit_identities_cannot_pass_larger_contract(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_one_row_custom_op_gate(
        project_dir,
        {"variant_axes_detected": True, "unit_identities": ["ScalarFwd2D"], "expanded_operator_instances_count": 1},
    )
    executor = _workflow_executor_for_custom_op_gate(project_dir)

    _status, result = executor._execute_custom_op_final_gate(
        {"phase_3_entry_script": _expanded_variant_contract(project_dir)},
        {"PROJECT_DIR": str(project_dir)},
        None,
        {},
    )

    assert result["passed"] is False
    assert any("missing: ScalarBwd2D" in error for error in result["errors"])


def test_phase5_workflow_route_returns_all_routes_and_preserves_custom_precedence(tmp_path: Path) -> None:
    executor = _workflow_executor_for_custom_op_gate(tmp_path)
    assert executor._phase5_workflow_route({}) == "ordinary_cuda"
    assert executor._phase5_workflow_route({"phase_1_project_analysis": {"migration_route": "vllm_serving"}}) == "vllm_serving"
    assert executor._phase5_workflow_route(
        {"phase_3_entry_script": {"entry_script_kind": "sglang_serving_validation", "serving_framework": "sglang"}}
    ) == "sglang_serving"
    assert executor._phase5_workflow_route({"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation"}}) == "custom_op"
    assert executor._phase5_workflow_route({"phase_3_entry_script": _expanded_variant_contract(tmp_path)}) == "custom_op_with_variants"
    assert executor._phase5_workflow_route(
        {
            "phase_1_project_analysis": {"migration_route": "vllm_serving"},
            "phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation"},
        }
    ) == "custom_op"
def test_phase5_entry_command_does_not_expand_environment_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target_script = tmp_path / "expanded_target.py"
    target_script.write_text("from pathlib import Path\nPath('expanded-ran').write_text('yes')\n", encoding="utf-8")
    monkeypatch.setenv("PY_SCRIPT", str(target_script))
    workflow = WorkflowDefinition(
        name="entry-no-shell-expansion",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="",
        output_schema={},
        type="shell",
        on_failure="continue",
    )
    setattr(phase, "command", "${loop_vars.entry_script}")

    status, output = executor._execute_shell_phase(
        phase,
        state={},
        context={},
        loop_vars={"entry_script": "python $PY_SCRIPT"},
        loop_state={},
    )

    assert status == "success"
    assert output["exit_code"] != 0
    assert "expanded_target.py" not in output["stderr"]
    assert not (tmp_path / "expanded-ran").exists()


def test_phase5_entry_command_does_not_expand_globs_or_tilde(tmp_path: Path) -> None:
    recorder = tmp_path / "record_args.py"
    recorder.write_text(
        "import json, sys\nfrom pathlib import Path\nPath('args.json').write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    (tmp_path / "match_a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "match_b.txt").write_text("b", encoding="utf-8")
    workflow = WorkflowDefinition(
        name="entry-no-glob-expansion",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="",
        output_schema={},
        type="shell",
        on_failure="break",
    )
    setattr(phase, "command", "${loop_vars.entry_script}")

    status, output = executor._execute_shell_phase(
        phase,
        state={},
        context={},
        loop_vars={"entry_script": f"python {recorder.name} *.txt ~"},
        loop_state={},
    )

    assert status == "success"
    assert output["exit_code"] == 0
    assert json.loads((tmp_path / "args.json").read_text(encoding="utf-8")) == ["*.txt", "~"]


def test_phase5_entry_command_preserves_safe_single_process_execution(tmp_path: Path) -> None:
    train_script = tmp_path / "train.py"
    train_script.write_text(
        "import argparse\nfrom pathlib import Path\nparser = argparse.ArgumentParser()\nparser.add_argument('--config')\nargs = parser.parse_args()\nPath('safe-command-ok').write_text(args.config)\n",
        encoding="utf-8",
    )
    (tmp_path / "cfg.yaml").write_text("ok: true", encoding="utf-8")
    workflow = WorkflowDefinition(
        name="entry-safe-command",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="",
        output_schema={},
        type="shell",
        on_failure="break",
    )
    setattr(phase, "command", "${loop_vars.entry_script}")
    loop_state: dict[str, object] = {}

    status, output = executor._execute_shell_phase(
        phase,
        state={},
        context={},
        loop_vars={"entry_script": "python train.py --config cfg.yaml"},
        loop_state=loop_state,
    )

    assert status == "success"
    assert output["exit_code"] == 0
    assert (tmp_path / "safe-command-ok").read_text(encoding="utf-8") == "cfg.yaml"
    assert loop_state["script_exit_code"] == 0
    assert loop_state["script_stderr"] == ""

def test_subworkflow_llm_exhausted_validation_retries_fail_without_mark_validated(tmp_path: Path) -> None:
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "validator": "always_fail",
            },
            {
                "id": "run_after_invalid_analysis",
                "type": "shell",
                "command": "python should_not_run.py",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="subworkflow-validation-failure",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={"error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"}},
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("always_fail", lambda _data: {"passed": False, "errors": ["invalid repair classification"], "warnings": []})
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.return_value = "session:error_analyzer"
    session_mgr.send_command.side_effect = [
        json.dumps({"repair_role": "code_adapter"}),
        json.dumps({"repair_role": "dependency_fixer"}),
        json.dumps({"repair_role": "operator_fixer"}),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor._execute_shell_phase = MagicMock(return_value=("success", {"ran": True}))

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={},
        loop_history=[],
        loop_state={},
    )

    assert result["status"] == "failure"
    assert result["step_outputs"]["analyze_error"]["validation_errors"] == ["invalid repair classification"]
    artifact_store.save_phase_output.assert_called_once_with("analyze_error", result["step_outputs"]["analyze_error"])
    artifact_store.mark_validated.assert_not_called()
    executor._execute_shell_phase.assert_not_called()
    assert session_mgr.send_command.call_count == 3


def test_subworkflow_llm_validation_retry_then_valid_succeeds_and_marks_validated(tmp_path: Path) -> None:
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "validator": "repair_classification",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="subworkflow-validation-retry-success",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={"error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"}},
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator(
        "repair_classification",
        lambda data: {
            "passed": data.get("repair_role") == "code_adapter",
            "errors": [] if data.get("repair_role") == "code_adapter" else ["missing valid repair role"],
            "warnings": [],
        },
    )
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.return_value = "session:error_analyzer"
    session_mgr.send_command.side_effect = [
        json.dumps({"repair_role": "unknown"}),
        json.dumps({"repair_role": "code_adapter", "category": "code"}),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={},
        loop_history=[],
        loop_state={},
    )

    assert result["status"] == "success"
    assert result["step_outputs"]["analyze_error"]["repair_role"] == "code_adapter"
    assert result["step_outputs"]["analyze_error"]["category"] == "code"
    assert "validation_errors" not in result["step_outputs"]["analyze_error"]
    artifact_store.save_phase_output.assert_called_once_with("analyze_error", result["step_outputs"]["analyze_error"])
    artifact_store.mark_validated.assert_called_once_with("analyze_error", result["step_outputs"]["analyze_error"])
    assert session_mgr.send_command.call_count == 2

def test_dependency_fix_phase_writes_runtime_artifacts_and_sends_slim_prompt(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "analyze_error",
                "type": "llm",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"dependency_fixer": "fix_dependency"},
            },
            {
                "id": "fix_dependency",
                "type": "llm",
                "prompt_template": "repair_dependency_fixer",
                "agent": "dependency_fixer",
                "retrieve_experience": True,
                "runtime_skills": {"include": ["unused"], "missing": "ignore"},
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="slim_dependency",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "dependency_fixer": {"role": "dependency_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.side_effect = [
        '{"repair_role": "dependency_fixer", "category": "dependency", "root_cause": "torch_npu missing", "suggested_fix": "install torch_npu"}',
        '{"fixed": true}',
    ]
    real_loader = PromptLoader(Path(__file__).resolve().parent.parent / "prompts")

    def load_prompt(template: str, ctx: dict[str, str]) -> str:
        if template == "repair_dependency_fixer":
            return real_loader.load_prompt(template, ctx)
        return template

    prompt_loader.load_prompt.side_effect = load_prompt
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path / "dependency project with spaces!"),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={
            "script_stderr": "ModuleNotFoundError: No module named 'torch_npu'",
            "experience_action_cards": ["Read /skills/dependency/SKILL.md"],
        },
        loop_history=[],
        loop_state={},
    )

    assert result["step_outputs"]["repair_dispatch"]["dispatched_to"] == "fix_dependency"
    fix_prompt = session_mgr.send_command.call_args_list[-1][0][1]
    assert len(fix_prompt.splitlines()) == 3
    assert "## Analyzer-Selected Experience Action Cards" not in fix_prompt
    assert "Read /skills/dependency/SKILL.md" not in fix_prompt
    assert "# unused" not in fix_prompt

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    runtime_error = runtime_dir / "runtime_error_dependency_project_with_spaces_.md"
    runtime_card = runtime_dir / "runtimeCard_dependency_project_with_spaces_.md"
    assert str(runtime_error.resolve()) in fix_prompt
    assert str(runtime_card.resolve()) in fix_prompt
    error_text = runtime_error.read_text(encoding="utf-8")
    card_text = runtime_card.read_text(encoding="utf-8")
    assert "# Dependency Fixer" in error_text
    assert "## Execution Failure" in error_text
    assert "## Error Classification" in error_text
    assert "Migration Constraints" not in error_text
    assert "Hard Rules" not in error_text
    assert "## Experience Card 1" in card_text
    assert "Read /skills/dependency/SKILL.md" in card_text


def test_slim_repair_prompt_phase_predicate_covers_direct_and_improvement_roles() -> None:
    for phase_id in (
        "fix_dependency",
        "imp_fix_dependency",
        "fix_operator",
        "imp_fix_operator",
    ):
        assert WorkflowExecutor._is_slim_repair_prompt_phase(phase_id)
    assert not WorkflowExecutor._is_slim_repair_prompt_phase("fix_code")
    assert not WorkflowExecutor._is_slim_repair_prompt_phase("imp_fix_code")


def test_improvement_operator_fix_writes_runtime_artifacts_and_sends_slim_prompt(tmp_path: Path):
    write_runtime_skill(tmp_path, "improvement-operator-runtime-skill")
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        phases=[
            {
                "id": "improvement_dispatch",
                "type": "dispatch",
                "route_field": "${improvement_plan.repair_role}",
                "routes": {"operator_fixer": "imp_fix_operator"},
            },
            {
                "id": "imp_fix_operator",
                "type": "llm",
                "prompt_template": "repair_operator_fixer",
                "agent": "operator_fixer",
                "retrieve_experience": True,
                "runtime_skills": {"include": ["improvement-operator-runtime-skill"], "missing": "ignore"},
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="slim_improvement_operator",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={"operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"}},
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.return_value = '{"fixed": true}'
    real_loader = PromptLoader(Path(__file__).resolve().parent.parent / "prompts")

    def load_prompt(template: str, ctx: dict[str, str]) -> str:
        if template == "repair_operator_fixer":
            return real_loader.load_prompt(template, ctx)
        return template

    prompt_loader.load_prompt.side_effect = load_prompt
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        framework_config={"runtime_skill_repo_root": str(tmp_path)},
        project_dir=str(tmp_path / "review project!"),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={
            "script_stderr": "Review rejected custom operator setup",
            "review_verdict": {"reasoning": "operator implementation still incomplete"},
            "improvement_plan": {
                "category": "operator",
                "repair_role": "operator_fixer",
                "suggested_direction": "port custom op to AscendC",
            },
            "experience_action_cards": ["Read /skills/runtime-card/SKILL.md"],
        },
        loop_history=[],
        loop_state={},
    )

    assert result["step_outputs"]["improvement_dispatch"]["dispatched_to"] == "imp_fix_operator"
    fix_prompt = session_mgr.send_command.call_args_list[-1][0][1]
    assert "This is a generic operator-incompatibility repair" in fix_prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in fix_prompt
    assert "第1、2、3、5、6、7点要求" not in fix_prompt
    assert ".skills" not in fix_prompt
    assert "Ascend NPU 原生修复" in fix_prompt
    assert "CPU fallback" in fix_prompt
    assert "Review rejected custom operator setup" not in fix_prompt
    assert "Read /skills/runtime-card/SKILL.md" not in fix_prompt
    assert "modified_files" in fix_prompt
    assert "agent_diagnostics" in fix_prompt
    assert "## Explicit Runtime Skills" in fix_prompt
    assert "### improvement-operator-runtime-skill" in fix_prompt

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    runtime_error = runtime_dir / "runtime_error_review_project_.md"
    runtime_card = runtime_dir / "runtimeCard_review_project_.md"
    operator_context = runtime_dir / "operatorRepairContext_review_project_.md"
    assert str(runtime_error.resolve()) in fix_prompt
    assert str(runtime_card.resolve()) in fix_prompt
    assert str(operator_context.resolve()) not in fix_prompt
    assert not operator_context.exists()
    assert str(tmp_path / "review project!") in fix_prompt
    assert "python main.py" in fix_prompt
    error_text = runtime_error.read_text(encoding="utf-8")
    card_text = runtime_card.read_text(encoding="utf-8")
    assert "# Operator Fixer" in error_text
    assert "## Execution Failure" in error_text
    assert "Review rejected custom operator setup" in error_text
    assert "port custom op to AscendC" in error_text
    assert "## Experience Card 1" in card_text
    assert "Read /skills/runtime-card/SKILL.md" in card_text

def test_fix_phase_reports_experience_usage_and_updates_counters(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=2,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "python -c \"import pathlib, sys; p=pathlib.Path('flag'); sys.exit(0 if p.exists() else 1)\"",
                "on_failure": "continue",
            },
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
                "retrieve_experience": True,
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"code_adapter": "fix_code"},
            },
            {
                "id": "fix_code",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_prompt",
                "agent": "code_adapter",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="usage_exp",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "code_adapter": {"role": "code_adapter", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({
        "id": "code-exp",
        "type": "skill",
        "status": "promoted",
        "title": "CUDA Call Fix",
        "target_roles": ["code_adapter"],
        "target_phases": ["phase_5_validation"],
    })
    store.upsert_index({
        "id": "ignored-exp",
        "type": "skill",
        "status": "promoted",
        "title": "Irrelevant Fix",
        "target_roles": ["code_adapter"],
        "target_phases": ["phase_5_validation"],
    })
    store.upsert_catalog_entry({
        "id": "code-exp",
        "type": "skill",
        "status": "promoted",
        "title": "CUDA Call Fix",
    })
    store.upsert_catalog_entry({
        "id": "ignored-exp",
        "type": "skill",
        "status": "promoted",
        "title": "Irrelevant Fix",
    })
    telemetry_bridge = TelemetryBridge(str(tmp_path / "telemetry"))
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            return '{"repair_role": "code_adapter", "category": "code", "root_cause": "cuda", "suggested_fix": "use npu"}'
        (tmp_path / "flag").write_text("fixed", encoding="utf-8")
        return json.dumps({
            "fixed": True,
            "used_experience_ids": ["code-exp"],
            "experience_actions_taken": {"code-exp": ["replaced cuda call"]},
            "ignored_experience_ids": ["ignored-exp"],
            "ignored_reasons": {"ignored-exp": "not relevant to this CUDA call"},
        })

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        telemetry_bridge=telemetry_bridge,
        experience_store=store,
    )
    query_result = {
        "selected_experiences": [
            {"id": "code-exp", "type": "skill", "title": "CUDA Call Fix"},
            {"id": "ignored-exp", "type": "skill", "title": "Irrelevant Fix"},
        ],
        "summary": "selected",
        "warning": "",
    }

    with patch("core.experience_query.ExperienceQuerier.query", return_value=query_result):
        result = executor._execute_loop_phase(
            PhaseDefinition(
                id="phase_5_validation",
                name="Validation",
                prompt_template="",
                output_schema={},
                type="loop",
                sub_workflow="repair_loop",
            ),
            state={},
            context={},
        )

    first_history = result["loop_history"][0]
    assert first_history["experience_usage"]["used_experience_ids"] == ["code-exp"]
    assert first_history["experience_usage"]["ignored_experience_ids"] == ["ignored-exp"]
    assert first_history["experience_usage"]["by_phase"]["fix_code"]["ignored_reasons"] == {
        "ignored-exp": "not relevant to this CUDA call"
    }
    assert result["loop_history"][1]["experience_verification"]["passed"] is True
    assert result["loop_history"][1]["experience_verification"]["source_phase_ids"] == ["fix_code"]
    assert result["loop_state"]["experience_verifications"][0]["experience_ids"] == ["code-exp"]
    catalog_by_id = {entry["id"]: entry for entry in store.read_catalog()}
    legacy_by_id = {entry["id"]: entry for entry in store.read_index()}
    assert catalog_by_id["code-exp"]["usage"]["selected_count"] == 1
    assert catalog_by_id["code-exp"]["usage"]["used_count"] == 1
    assert catalog_by_id["code-exp"]["usage"]["verification_success_count"] == 1
    assert catalog_by_id["ignored-exp"]["usage"]["selected_count"] == 1
    assert catalog_by_id["ignored-exp"]["usage"]["ignored_count"] == 1
    assert legacy_by_id["code-exp"]["usage"]["used_count"] == 1
    assert legacy_by_id["ignored-exp"]["usage"]["ignored_count"] == 1
    event_types = [event["event_type"] for event in telemetry_bridge._events]
    assert "experience_selected" in event_types
    assert "experience_used" in event_types
    assert "experience_ignored" in event_types
    assert "experience_verification" in event_types
    selected_event = next(event for event in telemetry_bridge._events if event["event_type"] == "experience_selected")
    assert selected_event["details"]["action_card_count"] == 2
    assert "CUDA Call Fix" in selected_event["details"]["action_cards"][0]
    ignored_event = next(event for event in telemetry_bridge._events if event["event_type"] == "experience_ignored")
    assert ignored_event["details"]["ignored_reasons"] == {
        "ignored-exp": "not relevant to this CUDA call"
    }


def test_failed_next_validation_records_experience_verification_failure(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=2,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "python -c \"import sys; sys.exit(1)\"",
                "on_failure": "continue",
            },
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
                "retrieve_experience": True,
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"code_adapter": "fix_code"},
            },
            {
                "id": "fix_code",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_prompt",
                "agent": "code_adapter",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="usage_failure_exp",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "code_adapter": {"role": "code_adapter", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({"id": "code-exp", "type": "skill", "status": "promoted", "title": "CUDA Call Fix"})
    store.upsert_catalog_entry({"id": "code-exp", "type": "skill", "status": "promoted", "title": "CUDA Call Fix"})
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            return '{"repair_role": "code_adapter", "category": "code", "root_cause": "cuda", "suggested_fix": "use npu"}'
        return json.dumps({
            "fixed": False,
            "used_experience_ids": ["code-exp"],
            "experience_actions_taken": {"code-exp": ["attempted cuda replacement"]},
            "ignored_experience_ids": [],
            "ignored_reasons": {},
        })

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=store,
    )
    query_result = {
        "selected_experiences": [{"id": "code-exp", "type": "skill", "title": "CUDA Call Fix"}],
        "summary": "selected",
        "warning": "",
    }

    with patch("core.experience_query.ExperienceQuerier.query", return_value=query_result):
        result = executor._execute_loop_phase(
            PhaseDefinition(
                id="phase_5_validation",
                name="Validation",
                prompt_template="",
                output_schema={},
                type="loop",
                sub_workflow="repair_loop",
            ),
            state={},
            context={},
        )

    verification = result["loop_history"][1]["experience_verification"]
    assert verification["experience_ids"] == ["code-exp"]
    assert verification["source_phase_ids"] == ["fix_code"]
    assert verification["passed"] is False
    catalog_entry = store.read_catalog()[0]
    legacy_entry = store.read_index()[0]
    assert catalog_entry["usage"]["verification_failure_count"] == 1
    assert catalog_entry["failure_count"] == 1
    assert legacy_entry["usage"]["verification_failure_count"] == 1
    assert result["loop_state"]["pending_experience_verifications"] == [
        {"phase_id": "fix_code", "experience_ids": ["code-exp"], "created_iteration": 2}
    ]


def test_loop_history_preserves_per_iteration_error_analysis_role(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=2,
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "python -c \"import sys; sys.exit(1)\"",
                "on_failure": "continue",
            },
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {
                    "dependency_fixer": "fix_dependency",
                    "operator_fixer": "fix_operator",
                },
            },
            {
                "id": "fix_dependency",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_dependency_prompt",
                "agent": "dependency_fixer",
            },
            {
                "id": "fix_operator",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_operator_prompt",
                "agent": "operator_fixer",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="mixed_roles",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "dependency_fixer": {"role": "dependency_fixer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    analyzer_outputs = iter([
        {"repair_role": "dependency_fixer", "category": "dependency", "root_cause": "missing", "suggested_fix": "install"},
        {"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported", "suggested_fix": "replace op"},
    ])

    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            return json.dumps(next(analyzer_outputs))
        return json.dumps({"fixed": True})

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
        ),
        state={},
        context={},
    )

    history = result["loop_history"]
    assert history[0]["error_category"] == "dependency"
    assert history[0]["repair_role"] == "dependency_fixer"
    assert history[1]["error_category"] == "operator"
    assert history[1]["repair_role"] == "operator_fixer"
    prompt_contexts = {
        call.args[0]: call.args[1]
        for call in prompt_loader.load_prompt.call_args_list
        if call.args[0] in {"fix_dependency_prompt", "fix_operator_prompt"}
    }
    assert "runtime_error_artifact_path" in prompt_contexts["fix_dependency_prompt"]
    assert "runtime_card_artifact_path" in prompt_contexts["fix_dependency_prompt"]
    assert "runtime_error_artifact_path" in prompt_contexts["fix_operator_prompt"]
    assert "runtime_card_artifact_path" in prompt_contexts["fix_operator_prompt"]
    assert "operator_custom_op_guidance" in prompt_contexts["fix_operator_prompt"]
    assert "operator_repair_context_artifact_path" not in prompt_contexts["fix_operator_prompt"]

    formatted = executor._format_error_analyzer_history(
        history,
        step_outputs={},
        state={"error_analysis": {"category": "operator", "repair_role": "operator_fixer"}},
    )
    assert "| Iter 1 | success |" in formatted
    assert "| Iter 1 | success |" in formatted and "dependency | dependency_fixer |" in formatted
    assert "| Iter 2 | success |" in formatted and "operator | operator_fixer |" in formatted
    assert "Latest error category: operator (repair role: operator_fixer)" in formatted

    legacy_formatted = executor._format_error_analyzer_history(
        [{"iteration": 1, "status": "success", "duration": 0.1}],
        step_outputs={},
        state={},
    )
    assert "| Iter 1 | success | 0.1 | unknown | (none) |" in legacy_formatted


def _entry_script_revision_workflow(max_iterations: int = 3, max_revisions: int = 2) -> WorkflowDefinition:
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=max_iterations,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "${loop_vars.entry_script}",
                "on_failure": "continue",
            },
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"code_adapter": "fix_code"},
            },
            {
                "id": "fix_code",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_prompt",
                "agent": "code_adapter",
            },
        ],
    )
    return WorkflowDefinition(
        name="entry_revision",
        version="1.0",
        globals={"max_entry_script_revisions": max_revisions},
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "code_adapter": {"role": "code_adapter", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )


def _entry_script_revision_executor(tmp_path: Path, workflow: WorkflowDefinition) -> WorkflowExecutor:
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    validator.validate.return_value = MagicMock(passed=True, errors=[])
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.session_mgr = session_mgr
    return executor


def test_entry_script_action_revises_next_loop_command_and_skips_repair(tmp_path: Path):
    workflow = _entry_script_revision_workflow(max_iterations=3, max_revisions=2)
    executor = _entry_script_revision_executor(tmp_path, workflow)
    revised_script = tmp_path / "final_evidence_validate.py"
    revised_script.write_text("from pathlib import Path\nPath('entry-ok').write_text('ok')\n", encoding="utf-8")
    revised_command = f"python {revised_script}"
    executor.session_mgr.send_command.return_value = json.dumps({
        "repair_role": "code_adapter",
        "category": "validation",
        "root_cause": "Phase 3 command used the wrong script",
        "suggested_fix": "Regenerate the command",
        "entry_script_action": {
            "needed": True,
            "action": "regenerate",
            "reason": "Use the generated validation script",
            "entry_script_path": str(revised_script),
            "run_command": revised_command,
        },
    })
    state = {
        "phase_3_entry_script": {
            "entry_script_path": "old.py",
            "run_command": "python -c \"import sys; sys.exit(1)\"",
            "phase5_entry_script_revision_allowed": True,
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}"},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert (tmp_path / "entry-ok").read_text(encoding="utf-8") == "ok"
    assert state["phase_3_entry_script"]["run_command"] == revised_command
    assert state["phase_3_entry_script"]["entry_script_path"] == str(revised_script)
    assert result["loop_state"]["entry_script"] == revised_command
    assert result["loop_state"]["entry_script_revision_count"] == 1
    assert result["loop_history"][0]["entry_script_action"]["applied"] is True
    assert result["loop_history"][0]["entry_script_action"]["revision_number"] == 1
    assert "repair_dispatch" not in result["loop_history"][0]["step_outputs_summary"]
    called_sessions = [call.args[0] for call in executor.session_mgr.send_command.call_args_list]
    assert called_sessions == ["session:error_analyzer"]


def test_entry_script_action_max_revision_limit_records_without_applying(tmp_path: Path):
    workflow = _entry_script_revision_workflow(max_iterations=2, max_revisions=1)
    executor = _entry_script_revision_executor(tmp_path, workflow)
    first_revision_script = tmp_path / "first_revision.py"
    first_revision_script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    blocked_revision_script = tmp_path / "blocked_revision.py"
    blocked_revision_script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    first_revision = f"python {first_revision_script}"
    blocked_revision = f"python {blocked_revision_script}"

    # Use an iterator because loop_state is not stored on executor.state until the loop returns.
    analyzer_outputs = iter([first_revision, blocked_revision])

    def respond_with_iterator(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            command = next(analyzer_outputs)
            return json.dumps({
                "repair_role": "code_adapter",
                "category": "validation",
                "root_cause": "entry command mismatch",
                "suggested_fix": "revise command",
                "entry_script_action": {
                    "needed": True,
                    "action": "modify",
                    "reason": "adjust command",
                    "entry_script_path": "",
                    "run_command": command,
                },
            })
        return json.dumps({"fixed": True})

    executor.session_mgr.send_command.side_effect = respond_with_iterator
    state = {"phase_3_entry_script": {"entry_script_path": "old.py", "run_command": "python -c \"import sys; sys.exit(1)\"", "phase5_entry_script_revision_allowed": True}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}"},
        ),
        state=state,
        context={},
    )

    requests = result["loop_state"]["entry_script_revision_requests"]
    assert result["loop_state"]["entry_script_revision_count"] == 1
    assert requests[0]["applied"] is True
    assert requests[1]["applied"] is False
    assert requests[1]["blocked_reason"] == "max_revisions_exceeded"
    assert state["phase_3_entry_script"]["run_command"] == first_revision
    assert result["loop_history"][1]["entry_script_action"]["applied"] is False
    assert result["loop_history"][1]["entry_script_action"]["blocked_reason"] == "max_revisions_exceeded"
    assert result["loop_history"][1]["repair_role"] == "code_adapter"
    called_sessions = [call.args[0] for call in executor.session_mgr.send_command.call_args_list]
    assert called_sessions == ["session:error_analyzer", "session:error_analyzer", "session:code_adapter"]


def test_entry_script_action_blocks_when_phase3_contract_flag_false(tmp_path: Path):
    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    state = {"phase_3_entry_script": {"entry_script_path": "old.py", "run_command": "python old.py"}}
    loop_vars = {"entry_script": "python old.py"}
    loop_state: dict[str, object] = {
        "entry_script_revision_count": 0,
        "entry_script_revision_requests": [],
        "max_entry_script_revisions": 2,
    }

    result = executor._maybe_apply_entry_script_action(
        {
            "entry_script_action": {
                "needed": True,
                "action": "modify",
                "reason": "use generated full validation",
                "entry_script_path": "new.py",
                "run_command": "python new.py",
            }
        },
        loop_vars,
        state,
        {},
        loop_state,
    )

    assert result is not None
    assert result["applied"] is False
    assert result["blocked_reason"] == "revision_not_allowed"
    assert state["phase_3_entry_script"]["run_command"] == "python old.py"
    assert loop_vars["entry_script"] == "python old.py"


@pytest.mark.parametrize(
    "run_command",
    [
        "python new.py && rm -rf /tmp/nope",
        "python new.py; touch /tmp/pwned",
        "python new.py | tee /tmp/pwned",
        "python new.py || touch /tmp/pwned",
        "python `touch /tmp/pwned`.py",
        "python $(touch /tmp/pwned).py",
        "python new.py > /tmp/pwned",
        "python new.py 2>/tmp/pwned",
        "python new.py< /tmp/input",
        "python new.py\npython other.py",
        "python new.py\rpython other.py",
        "python new.py & python other.py",
    ],
)
def test_entry_script_action_blocks_unsafe_revised_command(tmp_path: Path, run_command: str):
    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    state = {
        "phase_3_entry_script": {
            "entry_script_path": "old.py",
            "run_command": "python old.py",
            "phase5_entry_script_revision_allowed": True,
        }
    }
    loop_vars = {"entry_script": "python old.py"}
    loop_state: dict[str, object] = {
        "entry_script_revision_count": 0,
        "entry_script_revision_requests": [],
        "max_entry_script_revisions": 2,
    }

    result = executor._maybe_apply_entry_script_action(
        {
            "entry_script_action": {
                "needed": True,
                "action": "modify",
                "reason": "unsafe shell control",
                "entry_script_path": "new.py",
                "run_command": run_command,
            }
        },
        loop_vars,
        state,
        {},
        loop_state,
    )

    assert result is not None
    assert result["applied"] is False
    assert result["blocked_reason"] == "unsafe_run_command"
    assert state["phase_3_entry_script"]["run_command"] == "python old.py"


def test_workflow_executor_phase3_legacy_text_mentions_do_not_force_custom_op_context(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase3-text-custom-op-mentions", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "phase_1 says CUDAExtension custom operator is required"},
        {"phase_1_project_analysis": {"notes": "CUDAExtension custom operator"}},
    )

    assert "entry_script_kind" not in normalized
    result = validate_entry_script(normalized)
    assert result["passed"] is True


def test_workflow_executor_phase3_legacy_output_passes_without_custom_op_context(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase3-legacy", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "plain project"},
        {"phase_1_project_analysis": {"notes": "plain training"}},
    )

    assert "entry_script_kind" not in normalized
    result = validate_entry_script(normalized)
    assert result["passed"] is True


def test_workflow_executor_phase3_negative_custom_op_notes_do_not_force_custom_op_context(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase3-negative-custom-op", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    for notes in (
        "no custom operators found",
        "no CUDA custom operators",
        "custom_op_detected: false",
    ):
        normalized = executor._normalize_llm_output(
            phase,
            {"entry_script_path": "train.py", "run_command": "python train.py"},
            {"previous_outputs": notes},
            {"phase_1_project_analysis": {"notes": notes}},
        )

        assert "entry_script_kind" not in normalized
        result = validate_entry_script(normalized)
        assert result["passed"] is True


def test_workflow_executor_phase3_structured_custom_op_surface_controls_custom_op_context(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase3-structured-custom-op", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    false_surface = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "looked for torch.ops"},
        {
            "phase_1_project_analysis": {
                "custom_op_surface": {
                    "custom_op_detected": False,
                    "operator_families": ["custom operators not present"],
                },
                "notes": "looked for torch.ops and found no custom operators",
            }
        },
    )
    assert "entry_script_kind" not in false_surface
    result = validate_entry_script(false_surface)
    assert result["passed"] is True

    true_surface = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": {}},
        {
            "phase_1_project_analysis": {
                "custom_op_surface": {
                    "custom_op_detected": True,
                    "fine_grained_operator_units": ["my_kernel_forward"],
                }
            }
        },
    )
    assert true_surface["entry_script_kind"] == "custom_op_full_validation"

    contract_output = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": {}},
        {
            "phase_3_entry_script": {
                "operator_discovery_sources": ["source", "bindings"],
                "validation_obligations": ["runtime_project_api"],
            }
        },
    )
    assert contract_output["entry_script_kind"] == "custom_op_full_validation"

    shallow_script = tmp_path / "validate_custom_ops_full.py"
    shallow_script.write_text("print('shallow')\n", encoding="utf-8")
    variant_output = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "validate_custom_ops_full.py", "run_command": "python validate_custom_ops_full.py", "required_checks": []},
        {"previous_outputs": {}},
        {
            "phase_1_project_analysis": {
                "custom_op_surface": {
                    "custom_op_detected": True,
                    "variant_axes_detected": True,
                    "variant_axes": {"ndim": [1, 2]},
                    "expanded_operator_instances_count": 2,
                    "fine_grained_operator_units": ["scalar_forward"],
                    "expanded_operator_variants": [
                        {"unit_identity": "scalar_forward:dtype=float"},
                        {"unit_identity": "scalar_forward:dtype=double"},
                    ],
                }
            }
        },
    )
    assert variant_output["entry_script_kind"] == "custom_op_full_validation"
    assert variant_output["expanded_variant_inventory"] == {
        "variant_axes_detected": True,
        "unit_identities": ["scalar_forward:dtype=float", "scalar_forward:dtype=double"],
        "expanded_operator_instances_count": 2,
    }
    assert set(variant_output["required_checks"]) >= {
        "expanded_variant_inventory",
        "variant_axis_coverage",
        "per_variant_performance_report",
    }
    hardened_text = shallow_script.read_text(encoding="utf-8")
    assert "migration_manifest.json" in hardened_text
    assert "runtime_coverage.json" in hardened_text
    assert "performance.json" in hardened_text
    assert "build rows do not close over every per-expanded-variant unit_identity" in hardened_text
    assert "CANN build provenance" in hardened_text
    assert "OPP install provenance" in hardened_text
    assert "op_kernel/AscendC source evidence" in hardened_text
    assert "scalar_forward:dtype=float" in hardened_text
    assert "scalar_forward:dtype=double" in hardened_text

    from core.assisted_verification import validate_phase3_assisted_report

    report = _assisted_phase3_complete_report([])
    report["verdict"] = "incomplete"
    contract = cast(dict[str, object], report["phase3_contract_inventory"])
    contract["covered_variant_identities"] = ["2 unique identities generated by validate_custom_ops_full.py expanded_variants(), matching expected variant count and axes"]
    report["representative_only_coverage"] = [
        "Per-expanded-variant execution is enforced through required manifest/runtime/performance report evidence rather than direct calls during Phase 3."
    ]
    report["non_executable_or_missing_checks"] = [
        "migration_reports/migration_manifest.json missing",
        "migration_reports/runtime_coverage.json missing",
        "migration_reports/performance.json missing",
        "migration_reports/build.json missing",
        "migration_reports/custom_op_final_gate.json missing",
    ]
    assert validate_phase3_assisted_report(report, variant_output, {
        "project_dir": str(tmp_path),
        "custom_op_surface": {
            "custom_op_detected": True,
            "variant_axes_detected": True,
            "expanded_operator_instances_count": 2,
            "fine_grained_operator_units": ["scalar_forward"],
            "expanded_operator_variants": [
                {"unit_identity": "scalar_forward:dtype=float"},
                {"unit_identity": "scalar_forward:dtype=double"},
            ],
        },
    }) == []


def test_workflow_executor_phase3_zero_custom_op_contract_suppresses_stale_context(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase3-zero-custom-op", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    output = executor._normalize_llm_output(
        phase,
        {"entry_script_path": "validate.py", "run_command": "python validate.py"},
        {"previous_outputs": "custom_op_final_gate is mentioned in generated reports"},
        {
            "phase_1_project_analysis": {
                "custom_op_surface": {"custom_op_detected": False},
                "operator_unit_count": 0,
                "notes": "looked for torch.ops and found no custom operators",
            },
            "phase_35_static_validate": {"custom_op_static_required": False},
            "custom_op_final_gate": {"custom_op_detected": False, "inventory_count": 0},
        },
    )

    assert "entry_script_kind" not in output


def test_workflow_executor_phase35_injects_custom_op_marker_before_validation(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="Static Validate",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
        validator="entry_static",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase35-marker",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("entry_static", validate_entry_static)
    script_path = tmp_path / "validate_custom_ops_full.py"
    script_path.write_text(
        "print('custom-op validation script covers all required checks')\n",
        encoding="utf-8",
    )
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [
        json.dumps({
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Legacy static pass shape.",
        }),
        json.dumps({
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Full custom-op static pass shape.",
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
        }),
    ]
    prompt_loader.load_prompt.return_value = "prompt"
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(
        phase,
        {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "entry_script_path": str(script_path)}},
        {},
    )

    assert status == "success"
    assert output["custom_op_static_required"] is True
    assert output["entry_script_kind"] == "custom_op_full_validation"
    assert output["script_runs_project_api_custom_ops"] is True
    assert session_mgr.send_command.call_count == 2


def test_workflow_executor_phase35_injects_expanded_variant_marker_before_validation(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="Static Validate",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
        validator="entry_static",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase35-variant-marker",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("entry_static", validate_entry_static)
    custom_static_without_variant = {
        "validation_passed": True,
        "issues": [],
        "fix_plan": "Custom-op static pass without variant booleans.",
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
    variant_static = {
        **custom_static_without_variant,
        "fix_plan": "Expanded-variant custom-op static pass.",
        "script_discovers_expanded_variant_inventory": True,
        "script_checks_variant_axis_coverage": True,
        "script_requires_per_variant_performance": True,
    }
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [json.dumps(custom_static_without_variant), json.dumps(variant_static)]
    prompt_loader.load_prompt.return_value = "prompt"
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(
        phase,
        {
            "phase_3_entry_script": {
                "entry_script_kind": "custom_op_full_validation",
                "expanded_variant_inventory": {
                    "variant_axes_detected": True,
                    "unit_identities": ["op:ndim=1"],
                    "expanded_operator_instances_count": 1,
                },
            }
        },
        {},
    )

    assert status == "success"
    assert output["expanded_variant_static_required"] is True
    assert output["script_requires_per_variant_performance"] is True
    assert session_mgr.send_command.call_count == 2


def test_workflow_executor_phase35_exhausted_validation_retries_fail_without_mark_validated(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="Static Validate",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
        validator="entry_static",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase35-marker-failure",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("entry_static", validate_entry_static)
    legacy_static_output = {
        "validation_passed": True,
        "issues": [],
        "fix_plan": "Legacy static pass shape.",
    }
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [json.dumps(legacy_static_output) for _ in range(3)]
    prompt_loader.load_prompt.return_value = "prompt"
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(
        phase,
        {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation"}},
        {},
    )

    assert status == "failure"
    assert output["custom_op_static_required"] is True
    assert any("custom-op static validation missing booleans" in error for error in output["validation_errors"])


def test_workflow_executor_phase35_rejects_generated_short_e2e_timeout(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="Static Validate",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
        validator="entry_static",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase35-script-timeout",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    script_path = tmp_path / "validate_custom_ops_full.py"
    script_path.write_text(
        "import subprocess, sys\nsubprocess.run([sys.executable, 'test_e2e_fwi.py'], timeout=600)\n",
        encoding="utf-8",
    )
    static_output = {
        "validation_passed": True,
        "issues": [],
        "fix_plan": "Custom-op static pass shape.",
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
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("entry_static", validate_entry_static)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [json.dumps(static_output) for _ in range(3)]
    prompt_loader.load_prompt.return_value = "prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(
        phase,
        {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "entry_script_path": str(script_path)}},
        {},
    )

    assert status == "failure"
    assert output["entry_script_path"] == str(script_path)
    assert any("short internal subprocess timeout=600" in error for error in output["validation_errors"])
    artifact_store.save_phase_output.assert_called_once()
    artifact_store.mark_validated.assert_not_called()
    assert session_mgr.send_command.call_count == 3


def test_workflow_executor_project_analysis_correction_prompt_requires_full_schema() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "dependencies must be a list; cuda_detected must be a boolean; "
        "custom_op_surface must be present and custom_op_detected must be true when CUDA/native custom-op units are discovered from source",
        phase_id="phase_1_project_analysis",
        validator_name="project_analysis",
    )

    assert "phase 'phase_1_project_analysis'" in prompt
    assert "complete replacement JSON object" in prompt
    assert "`project_dir`, `dependencies`, `cuda_detected`, and `entry_script`" in prompt
    assert "`custom_op_surface` with `custom_op_detected: true`" in prompt
    assert "`source`, `bindings`, `wrappers`, `autograd`, `aliases`, `launch`, `setup`, and `tests`" in prompt
    assert "descriptive evidence in the evidence fields" in prompt
    assert "Do not return meta/status-only fields" in prompt
    assert "`status`" in prompt
    assert "`note`" in prompt
    assert "`no_action_required`" in prompt
    assert "patch, diff, delta" in prompt


def test_workflow_executor_project_analysis_variant_correction_prompt_guides_full_inventory() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "custom_op_surface.expanded_operator_instances_count must equal expanded_operator_variants length; "
        "custom_op_surface.expanded_operator_variants axis_values.ndim missing source-enumerated axis values: 2d, 3d; "
        "custom_op_surface.expanded_operator_variants axis_values.dtype missing source-enumerated axis values: double",
        phase_id="phase_1_project_analysis",
        validator_name="project_analysis",
    )

    assert "complete replacement JSON object" in prompt
    assert "full replacement Phase 1 JSON" in prompt
    assert "expanded_operator_instances_count` exactly to the number of objects listed" in prompt
    assert "do not claim a Cartesian-product count unless every concrete row is actually present" in prompt
    assert "includes every source-enumerated target value" in prompt
    assert "do not keep only the common sample" in prompt
    assert "must expand beyond the distinct base-unit count" in prompt
    assert "concrete `source_evidence` plus public or framework route evidence" in prompt
    assert "heterogeneous base units" in prompt
    assert "only the axes relevant to that base unit" in prompt


def test_workflow_executor_retries_status_only_project_analysis_correction_until_full_schema(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="project-analysis-correction",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", lambda d: {"passed": True, "errors": [], "warnings": []})
    valid_output = {
        "project_dir": str(tmp_path),
        "dependencies": ["torch"],
        "cuda_detected": True,
        "entry_script": "train.py",
    }
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [
        json.dumps({"project_dir": str(tmp_path), "dependencies": "torch"}),
        json.dumps({"status": "no_action_required", "note": "Phase 1 JSON was already corrected", "project_dir": str(tmp_path)}),
        json.dumps(valid_output),
    ]
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["dependencies"] == ["torch"]
    assert session_mgr.send_command.call_count == 3
    first_correction = session_mgr.send_command.call_args_list[1].args[1]
    second_correction = session_mgr.send_command.call_args_list[2].args[1]
    assert "complete replacement JSON object" in first_correction
    assert "`project_dir`, `dependencies`, `cuda_detected`, and `entry_script`" in first_correction
    assert "`no_action_required`" in first_correction
    assert "complete replacement JSON object" in second_correction
    assert "status" in second_correction
    assert "note" in second_correction
    artifact_store.mark_validated.assert_called_once()


def _assisted_variant_phase1_output(project_dir: Path, variant_ids: list[str]) -> dict[str, object]:
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


def _assisted_phase1_complete_report(variant_ids: list[str]) -> dict[str, object]:
    return {
        "phase_id": "phase_1_project_analysis",
        "track": "custom_op_variant",
        "verdict": "complete",
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
        "missing_variants": [],
        "extra_variants": [],
        "collapsed_or_representative_rows": [],
        "unresolved_source_groups": [],
        "evidence": ["ops/scalar.cu:1"],
    }


def _assisted_phase3_complete_report(variant_ids: list[str]) -> dict[str, object]:
    return {
        "phase_id": "phase_3_entry_script",
        "track": "custom_op_variant",
        "verdict": "complete",
        "phase1_verified_inventory": {
            "fine_grained_operator_units": ["scalar_forward"],
            "expanded_unit_identities": variant_ids,
        },
        "phase3_contract_inventory": {
            "covered_unit_identities": ["scalar_forward"],
            "covered_variant_identities": variant_ids,
            "entry_script_path": "validate_custom_ops_full.py",
        },
        "validation_script_evidence": ["validate_custom_ops_full.py enumerates all variants"],
        "missing_units": [],
        "missing_variants": [],
        "representative_only_coverage": [],
        "non_executable_or_missing_checks": [],
    }


def test_workflow_executor_assisted_verification_skips_ordinary_cuda_without_server_call(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="ordinary-assisted-skip",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.return_value = json.dumps({
        "project_dir": str(tmp_path),
        "dependencies": ["torch"],
        "cuda_detected": True,
        "entry_script": "train.py",
        "custom_op_surface": {"custom_op_detected": False},
    })
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(phase, {}, {"PROJECT_DIR": str(tmp_path)})

    assert status == "success"
    assert session_mgr.send_command.call_count == 1
    assisted = cast(dict[str, object], output["assisted_verification"])
    assert cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])["status"] == "skipped"


def test_workflow_executor_phase1_assisted_session_error_accepts_local_complete_inventory(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase1-assisted-session-error",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    session_mgr.send_command.side_effect = [
        json.dumps(_assisted_variant_phase1_output(tmp_path, variant_ids)),
        json.dumps({"ok": False, "error": "OpenAI上游返回{上游请求参数无效}"}),
    ]
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", lambda d: {"passed": True, "errors": [], "warnings": []})
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(phase, {}, {"PROJECT_DIR": str(tmp_path)})

    assert status == "success"
    assisted = cast(dict[str, object], output["assisted_verification"])
    summary = cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])
    assert summary["status"] == "complete"
    assert summary["raw_artifact"] == "raw.json"
    assert "warnings" in summary
    assert session_mgr.send_command.call_count == 2


def test_workflow_executor_phase1_assisted_session_error_rejects_incomplete_local_inventory(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase1-assisted-session-error-incomplete",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    incomplete = _assisted_variant_phase1_output(tmp_path, ["scalar_forward:dtype=float"])
    surface = cast(dict[str, object], incomplete["custom_op_surface"])
    surface["expanded_operator_instances_count"] = 2
    session_mgr.send_command.side_effect = [
        json.dumps(incomplete),
        json.dumps({"ok": False, "error": "OpenAI上游返回{上游请求参数无效}"}),
        json.dumps(incomplete),
        json.dumps({"ok": False, "error": "OpenAI上游返回{上游请求参数无效}"}),
        json.dumps(incomplete),
        json.dumps({"ok": False, "error": "OpenAI上游返回{上游请求参数无效}"}),
    ]
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(phase, {}, {"PROJECT_DIR": str(tmp_path)})

    assert status == "failure"
    assert "validation_errors" in output
    assert any("assisted verifier session failed" in error for error in output["validation_errors"])


def test_workflow_executor_phase3_assisted_session_error_accepts_local_valid_contract(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    script = project_dir / "validate_custom_ops_full.py"
    script.write_text("print('validate')\n", encoding="utf-8")
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase3-assisted-session-error",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 3 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    contract = _expanded_variant_contract(project_dir)
    inventory = cast(dict[str, object], contract["expanded_variant_inventory"])
    inventory["unit_identities"] = variant_ids
    inventory["expanded_operator_instances_count"] = len(variant_ids)
    session_mgr.send_command.side_effect = [
        json.dumps(contract),
        json.dumps({"ok": False, "error": "OpenAI上游返回{上游请求参数无效}"}),
    ]
    validator = ValidatorEngine()
    validator.register_validator("entry_script", validate_entry_script)
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(project_dir),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )
    state = {"phase_1_project_analysis": _assisted_variant_phase1_output(project_dir, variant_ids)}

    status, output = executor._execute_llm_phase(phase, state, {"PROJECT_DIR": str(project_dir)})

    assert status == "success"
    assisted = cast(dict[str, object], output["assisted_verification"])
    summary = cast(dict[str, object], assisted["phase_3_custom_op_contract_coverage_check"])
    assert summary["status"] == "complete"
    assert "warnings" in summary
    assert session_mgr.send_command.call_count == 2


def test_workflow_executor_phase1_assisted_mismatch_triggers_full_json_correction(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase1-assisted-correction",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    initial = _assisted_variant_phase1_output(tmp_path, ["scalar_forward:dtype=float"])
    corrected = _assisted_variant_phase1_output(tmp_path, ["scalar_forward:dtype=float", "scalar_forward:dtype=double"])
    incomplete_report = {
        **_assisted_phase1_complete_report(["scalar_forward:dtype=float", "scalar_forward:dtype=double"]),
        "verdict": "incomplete",
        "missing_variants": ["scalar_forward:dtype=double"],
    }
    session_mgr.send_command.side_effect = [
        json.dumps(initial),
        json.dumps(incomplete_report),
        json.dumps(corrected),
        json.dumps(_assisted_phase1_complete_report(["scalar_forward:dtype=float", "scalar_forward:dtype=double"])),
    ]
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(phase, {}, {"PROJECT_DIR": str(tmp_path)})

    assert status == "success"
    surface = cast(dict[str, object], output["custom_op_surface"])
    assert surface["expanded_operator_instances_count"] == 2
    verifier_call = session_mgr.get_or_create.call_args_list[1]
    assert verifier_call.kwargs == {
        "role": "custom_op_verifier",
        "lifecycle": "persistent",
        "agent": "Sisyphus-Junior",
    }
    correction_prompt = session_mgr.send_command.call_args_list[2].args[1]
    assert "failed the assisted custom-op completeness verifier" in correction_prompt
    assisted = cast(dict[str, object], output["assisted_verification"])
    summary = cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])
    assert summary["status"] == "complete"
    assert summary["canonical_artifact"] == "canonical.json"


def test_workflow_executor_phase1_assisted_repairs_placeholder_source_inventory_report(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase1-assisted-placeholder-repair",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    placeholder_report = _assisted_phase1_complete_report(variant_ids)
    source_inventory = cast(dict[str, object], placeholder_report["source_evidence_inventory"])
    source_inventory["expanded_unit_identities"] = ["same_as_phase1_inventory_expanded_unit_identities"]
    source_inventory["variant_axes"] = {"scalar_forward": {"dtype": ["float", "double"], "ndim": ["1"]}}
    session_mgr.send_command.side_effect = [
        json.dumps(_assisted_variant_phase1_output(tmp_path, variant_ids)),
        json.dumps(placeholder_report),
        json.dumps(_assisted_phase1_complete_report(variant_ids)),
    ]
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(phase, {}, {"PROJECT_DIR": str(tmp_path)})

    assert status == "success"
    assert session_mgr.send_command.call_count == 3
    repair_prompt = session_mgr.send_command.call_args_list[2].args[1]
    assert "previous assisted-verification JSON report failed semantic validation" in repair_prompt
    assert "same_as_* placeholder aliases" in repair_prompt
    assisted = cast(dict[str, object], output["assisted_verification"])
    summary = cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])
    assert summary["status"] == "complete"
    assert summary["canonical_artifact"] == "canonical.json"


def test_workflow_executor_phase1_assisted_accepts_brace_grouped_variant_summary(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase1-assisted-brace-groups",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    grouped = "scalar_forward:*2{dtype=[float,double]}"
    report = _assisted_phase1_complete_report(variant_ids)
    cast(dict[str, object], report["phase1_inventory"])["expanded_unit_identities"] = [grouped]
    cast(dict[str, object], report["source_evidence_inventory"])["expanded_unit_identities"] = [grouped]
    session_mgr.send_command.side_effect = [
        json.dumps(_assisted_variant_phase1_output(tmp_path, variant_ids)),
        json.dumps(report),
    ]
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(phase, {}, {"PROJECT_DIR": str(tmp_path)})

    assert status == "success"
    assert session_mgr.send_command.call_count == 2
    assisted = cast(dict[str, object], output["assisted_verification"])
    summary = cast(dict[str, object], assisted["phase_1_custom_op_completeness_check"])
    assert summary["status"] == "complete"
    assert summary["canonical_artifact"] == "canonical.json"


def test_workflow_executor_phase1_assisted_rejects_source_incomplete_sampled_variants(tmp_path: Path) -> None:
    initial = _assisted_variant_phase1_output(tmp_path, [
        "wave:forward_cuda:accuracy=2:dtype=float:device=cuda",
        "wave:forward_cuda:accuracy=2:dtype=double:device=cuda",
    ])
    surface = cast(dict[str, object], initial["custom_op_surface"])
    surface["fine_grained_operator_units"] = ["wave:forward_cuda"]
    surface["discovered_operator_names"] = ["wave_forward_cuda"]
    surface["native_operator_symbols"] = ["wave_iso_2d_2_float_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/wave.cu:FUNC(forward)"]
    surface["source_evidence"] = [
        "src/backend.py:builds wave symbols with ${ndim}, ${accuracy}, ${dtype}, and device cuda",
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates accuracy 2, 4",
        "src/backend.py:enumerates dtype float and double",
    ]
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4"], "dtype": ["float", "double"], "device": ["cuda"]}
    for variant in cast(list[dict[str, object]], surface["expanded_operator_variants"]):
        variant["base_unit_identity"] = "wave:forward_cuda"
        variant["source_evidence"] = ["src/wave.cu:FUNC(forward)"]
        variant["candidate_public_api_routes"] = ["pkg.wave.forward"]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "wave:forward_cuda",
            "source_evidence": ["src/backend.py:wave symbol uses ndim accuracy dtype", "src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        }
    ]
    from core.assisted_verification import validate_phase1_assisted_report

    errors = validate_phase1_assisted_report(
        _assisted_phase1_complete_report([
            "wave:forward_cuda:accuracy=2:dtype=float:device=cuda",
            "wave:forward_cuda:accuracy=2:dtype=double:device=cuda",
        ]),
        initial,
    )

    assert any("source-template expansion" in error for error in errors)
    assert any("missing variants" in error for error in errors)


def test_phase1_assisted_report_accepts_exact_arithmetic_grouped_variant_summary(tmp_path: Path) -> None:
    variant_ids = [
        f"wave:forward_cuda:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda"
        for ndim in ["1", "2", "3"]
        for accuracy in ["2", "4"]
        for dtype in ["float", "double"]
    ]
    output = _assisted_variant_phase1_output(tmp_path, variant_ids)
    report = _assisted_phase1_complete_report([])
    phase1_inventory = cast(dict[str, object], report["phase1_inventory"])
    phase1_inventory["expanded_operator_instances_count"] = len(variant_ids)
    phase1_inventory["expanded_unit_identities"] = [
        "1 propagator unit x ndim={1,2,3} x accuracy={2,4} x dtype={float,double} x device={cuda} = 12"
    ]
    source_inventory = cast(dict[str, object], report["source_evidence_inventory"])
    source_inventory["expanded_unit_identities"] = [
        "wave:{forward_cuda}:ndim={1,2,3}:accuracy={2,4}:dtype={float,double}:device=cuda"
    ]

    from core.assisted_verification import validate_phase1_assisted_report

    assert validate_phase1_assisted_report(report, output) == []


def test_workflow_executor_phase3_assisted_variant_coverage_mismatch_triggers_correction(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry Script",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase3-assisted-correction",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    script = tmp_path / "validate_custom_ops_full.py"
    script.write_text("print('validate')\n", encoding="utf-8")
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase3_output = {"entry_script_path": str(script), "run_command": f"python {script}"}
    corrected_phase3_output = {
        **phase3_output,
        "required_checks": ["variant:scalar_forward:dtype=float", "variant:scalar_forward:dtype=double"],
    }
    incomplete_report = {
        **_assisted_phase3_complete_report(variant_ids),
        "verdict": "incomplete",
        "missing_variants": ["scalar_forward:dtype=double"],
        "representative_only_coverage": ["only dtype=float covered"],
    }
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    artifact_store.save_phase_output.return_value = "raw.json"
    artifact_store.mark_validated.return_value = "canonical.json"
    artifact_store.write_journal.return_value = "journal.jsonl"
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "phase 3 prompt"
    session_mgr.get_or_create.return_value = "session:any"
    session_mgr.send_command.side_effect = [
        json.dumps(phase3_output),
        json.dumps(incomplete_report),
        json.dumps(corrected_phase3_output),
        json.dumps(_assisted_phase3_complete_report(variant_ids)),
    ]
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config={"assisted_verification": {"enabled": True}},
    )

    status, output = executor._execute_llm_phase(
        phase,
        {"phase_1_project_analysis": _assisted_variant_phase1_output(tmp_path, variant_ids)},
        {"PROJECT_DIR": str(tmp_path)},
    )

    assert status == "success"
    required_checks = cast(list[object], output["required_checks"])
    assert "variant:scalar_forward:dtype=float" in required_checks
    assert "variant:scalar_forward:dtype=double" in required_checks
    verifier_call = session_mgr.get_or_create.call_args_list[1]
    assert verifier_call.kwargs == {
        "role": "custom_op_verifier",
        "lifecycle": "persistent",
        "agent": "Sisyphus-Junior",
    }
    correction_prompt = session_mgr.send_command.call_args_list[2].args[1]
    assert "failed the assisted custom-op validation-coverage verifier" in correction_prompt
    assisted = cast(dict[str, object], output["assisted_verification"])
    assert cast(dict[str, object], assisted["phase_3_custom_op_contract_coverage_check"])["status"] == "complete"


def test_phase3_assisted_report_accepts_future_phase5_report_contract(tmp_path: Path) -> None:
    script = tmp_path / "validate_custom_ops_full.py"
    script.write_text(
        """
REQUIRED_REPORTS = ["migration_manifest.json", "runtime_coverage.json", "performance.json", "build.json", "custom_op_final_gate.json"]
REPORTS_DIR = "migration_reports"
def fail(message):
    raise SystemExit(message)
def load_json(name):
    fail(f"required report missing: {name}")
def validate_reports(inventory):
    rows = []
    row_by_id = {row.get("unit_identity"): row for row in rows}
    expected = [variant["unit_identity"] for variant in inventory["expanded_variant_inventory"]]
    if set(row_by_id) != set(expected):
        fail("manifest does not close over Phase 1 expanded variants")
    build_rows = []
    build_by_id = {row.get("unit_identity"): row for row in build_rows}
    if set(build_by_id) != set(expected):
        fail("build rows do not close over every per-expanded-variant unit_identity")
    for build_row in build_rows:
        if not build_row.get("cann_build_provenance") or not build_row.get("opp_install_provenance"):
            fail("build row missing CANN/OPP install provenance")
    variant_axis_coverage = inventory["variant_axis_coverage"]
    if not variant_axis_coverage.get("all_axes_covered"):
        fail("variant axis coverage missing")
    per_variant = []
    if not per_variant:
        fail("performance report missing per-variant entries")
""",
        encoding="utf-8",
    )
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase1_output = _assisted_variant_phase1_output(tmp_path, variant_ids)
    report = _assisted_phase3_complete_report([])
    report["verdict"] = "incomplete"
    contract = cast(dict[str, object], report["phase3_contract_inventory"])
    contract["covered_variant_identities"] = ["Script generates all 2 canonical Phase 1 expanded identities"]
    report["representative_only_coverage"] = [
        "Per-expanded-variant execution is enforced through required manifest/runtime/performance report evidence rather than direct calls during Phase 3."
    ]
    report["non_executable_or_missing_checks"] = [
        "migration_reports/migration_manifest.json missing",
        "migration_reports/runtime_coverage.json missing",
        "migration_reports/performance.json missing",
        "migration_reports/build.json missing",
        "migration_reports/custom_op_final_gate.json missing",
    ]

    from core.assisted_verification import validate_phase3_assisted_report

    assert validate_phase3_assisted_report(report, {"entry_script_path": str(script)}, phase1_output) == []


def test_phase3_assisted_report_rejects_build_json_existence_only_future_contract(tmp_path: Path) -> None:
    script = tmp_path / "validate_custom_ops_full.py"
    script.write_text(
        """
REQUIRED_REPORTS = ["migration_manifest.json", "runtime_coverage.json", "performance.json", "build.json", "custom_op_final_gate.json"]
REPORTS_DIR = "migration_reports"
def fail(message):
    raise SystemExit(message)
def load_json(name):
    fail(f"required report missing: {name}")
def validate_reports(inventory):
    build_json = load_json("build.json")
    if build_json is None:
        fail("required report missing: build.json")
    expected = [variant["unit_identity"] for variant in inventory["expanded_variant_inventory"]]
    row_by_id = {unit_identity: {} for unit_identity in expected}
    if set(row_by_id) != set(expected):
        fail("manifest does not close over Phase 1 expanded variants")
    per_variant = []
    if not per_variant:
        fail("performance report missing per-variant entries")
""",
        encoding="utf-8",
    )
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase1_output = _assisted_variant_phase1_output(tmp_path, variant_ids)
    report = _assisted_phase3_complete_report([])
    report["verdict"] = "incomplete"
    contract = cast(dict[str, object], report["phase3_contract_inventory"])
    contract["covered_variant_identities"] = ["Script generates all 2 canonical Phase 1 expanded identities"]
    report["representative_only_coverage"] = [
        "Per-expanded-variant execution is enforced through required manifest/runtime/performance report evidence rather than direct calls during Phase 3."
    ]
    report["non_executable_or_missing_checks"] = [
        "migration_reports/build.json missing",
    ]

    from core.assisted_verification import validate_phase3_assisted_report

    errors = validate_phase3_assisted_report(report, {"entry_script_path": str(script)}, phase1_output)

    assert any("verdict must be complete" in error for error in errors)
    assert any("representative-only coverage" in error for error in errors)


def test_phase3_assisted_report_rejects_invalid_python_validation_script(tmp_path: Path) -> None:
    script = tmp_path / "validate_custom_ops_full.py"
    script.write_text(
        """
def validate_custom_ops():
    try:
        import torch
        import torch_npu
import torch_npu
""",
        encoding="utf-8",
    )
    variant_ids = ["scalar_forward:dtype=float", "scalar_forward:dtype=double"]
    phase1_output = _assisted_variant_phase1_output(tmp_path, variant_ids)
    report = _assisted_phase3_complete_report(variant_ids)

    from core.assisted_verification import validate_phase3_assisted_report

    errors = validate_phase3_assisted_report(report, {"entry_script_path": str(script)}, phase1_output)

    assert any("assisted Phase 3 validation script is not valid Python" in error for error in errors)


def test_top_level_llm_phase_records_session_failure_with_phase_deadline(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="timeout-failure",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = ArtifactStore(str(tmp_path), "timeout-run")
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.side_effect = ["session:main_retry_1", "session:main_retry_2"]
    session_mgr.send_command.side_effect = TimeoutError("poll_s timed out")
    prompt_loader.load_prompt.return_value = "prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "failure"
    assert output["phase_id"] == "phase_1_project_analysis"
    assert output["status"] == "failure"
    assert output["failure_kind"] == "timeout"
    assert output["timeout_seconds"] == 30000
    assert output["session_ref"] == "session:main"
    assert "poll_s timed out" in str(output["error"])
    assert session_mgr.send_command.call_args.kwargs["timeout"] == 30000
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main"]
    session_mgr.create_session.assert_not_called()
    journal = artifact_store.get_journal()
    assert journal[-1]["phase_id"] == "phase_1_project_analysis"
    assert journal[-1]["status"] == "failure"
    assert journal[-1]["failure_kind"] == "timeout"
    assert journal[-1]["timeout_seconds"] == 30000
    assert journal[-1]["session_ref"] == "session:main"
    assert "poll_s timed out" in journal[-1]["error"]


def test_top_level_llm_phase_does_not_fresh_retry_busy_empty_response(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="empty-response-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry"
    session_mgr.send_command.return_value = json.dumps({"ok": False, "error": "Empty session response"})
    artifact_store.save_phase_output.return_value = str(tmp_path / "raw.json")
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "failure"
    assert output["failure_kind"] == "session_error"
    assert output["session_ref"] == "session:main"
    assert "Empty session response" in output["error"]
    assert session_mgr.send_command.call_count == 1
    assert session_mgr.send_command.call_args_list[0].args[0] == "session:main"
    session_mgr.create_session.assert_not_called()
    artifact_store.mark_validated.assert_not_called()


def test_top_level_llm_phase_does_not_fresh_retry_unknown_server_error(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="unknown-error-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_unknown"
    session_mgr.send_command.return_value = json.dumps({
        "ok": False,
        "error": 'POST /session/abc/message failed: {"name":"UnknownError","data":{"message":"Unexpected server error. Check server logs for details."}}',
    })
    artifact_store.save_phase_output.return_value = str(tmp_path / "raw.json")
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "failure"
    assert output["failure_kind"] == "session_error"
    assert output["session_ref"] == "session:main"
    assert "UnknownError" in output["error"]
    assert session_mgr.send_command.call_count == 1
    assert session_mgr.send_command.call_args_list[0].args[0] == "session:main"
    session_mgr.create_session.assert_not_called()
    artifact_store.mark_validated.assert_not_called()


def test_top_level_llm_phase_fresh_retries_phase35_progress_response(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="Static Validate",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
        validator=None,
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase35-progress-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        "I’ve got the generated validator in place; now I’m statically checking it for any headless blockers or short internal timeouts.",
        json.dumps({"validation_passed": True, "issues": [], "fix_plan": "No issues found."}),
    ]
    prompt_loader.load_prompt.return_value = "phase 35 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["validation_passed"] is True
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main_retry_1"]


def test_top_level_llm_phase_fresh_retries_partial_progress_response(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="partial-progress-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        "I’ve identified the native CUDA entry points; I’m pulling the generated symbol logic now so the variant metadata is grounded in source, not inferred.",
        json.dumps({"project_dir": str(tmp_path), "dependencies": ["torch"], "cuda_detected": False, "entry_script": "train.py"}),
    ]
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["entry_script"] == "train.py"
    assert session_mgr.send_command.call_count == 2
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main_retry_1"]
    assert session_mgr.create_session.call_count == 1


def test_top_level_llm_phase_fresh_retries_remote_closed_correction_prompt(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="remote-close-correction-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        json.dumps({"project_dir": str(tmp_path), "dependencies": "torch"}),
        json.dumps({"ok": False, "error": "POST /session/abc/message failed: Remote end closed connection without response"}),
        json.dumps({"project_dir": str(tmp_path), "dependencies": ["torch"], "cuda_detected": False, "entry_script": "train.py"}),
    ]
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["entry_script"] == "train.py"
    assert session_mgr.send_command.call_count == 3
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main", "session:main_retry_1"]
    assert session_mgr.create_session.call_count == 1


def test_top_level_llm_phase_allows_two_fresh_retries_for_compaction_then_success(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="two-retry-success",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.side_effect = ["session:main_retry_1", "session:main_retry_2"]
    session_mgr.send_command.side_effect = [
        json.dumps({"ok": False, "error": "Compaction response is incomplete"}),
        json.dumps({"ok": False, "error": "Compaction response is incomplete"}),
        json.dumps({"project_dir": str(tmp_path), "dependencies": ["torch"], "cuda_detected": False, "entry_script": "train.py"}),
    ]
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["entry_script"] == "train.py"
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == [
        "session:main",
        "session:main_retry_1",
        "session:main_retry_2",
    ]
    assert session_mgr.create_session.call_count == 2


def test_top_level_llm_phase_retry_exhaustion_uses_final_retry_session_ref(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="retry-exhausted",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.side_effect = ["session:main_retry_1", "session:main_retry_2"]
    session_mgr.send_command.side_effect = [
        json.dumps({"ok": False, "error": "Compaction response is incomplete"}),
        json.dumps({"ok": False, "error": "Compaction response is incomplete"}),
        json.dumps({"ok": False, "error": "Compaction response is incomplete"}),
    ]
    artifact_store.save_phase_output.return_value = str(tmp_path / "raw.json")
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "failure"
    assert output["failure_kind"] == "session_error"
    assert output["session_ref"] == "session:main_retry_2"
    assert "Compaction response is incomplete" in output["error"]
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == [
        "session:main",
        "session:main_retry_1",
        "session:main_retry_2",
    ]
    assert session_mgr.create_session.call_count == 2


def test_top_level_llm_phase_does_not_fresh_retry_timeout_json_response(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_2_venv_create",
        name="Venv",
        prompt_template="phase_2_venv_create",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="timeout-json-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_timeout"
    session_mgr.send_command.return_value = json.dumps({"ok": False, "error": "POST /session/abc/message timed out after 1830 seconds"})
    artifact_store.save_phase_output.return_value = str(tmp_path / "raw.json")
    prompt_loader.load_prompt.return_value = "phase 2 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "failure"
    assert output["failure_kind"] == "timeout"
    assert output["session_ref"] == "session:main"
    assert "timed out" in output["error"]
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main"]
    session_mgr.create_session.assert_not_called()


def test_top_level_llm_phase_does_not_validate_or_fresh_retry_upstream_stream_error(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_1_project_analysis",
        name="Project Analysis",
        prompt_template="phase_1_project_analysis",
        output_schema={},
        type="llm",
        validator="project_analysis",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="stream-error-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("project_analysis", validate_project_analysis)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_stream"
    session_mgr.send_command.return_value = json.dumps({
        "type": "error",
        "sequence_number": 0,
        "error": {
            "type": "upstream_error",
            "code": "stream_read_error",
            "message": "stream_read_error",
        },
    })
    prompt_loader.load_prompt.return_value = "phase 1 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "failure"
    assert output["failure_kind"] == "session_error"
    assert output["session_ref"] == "session:main"
    assert "stream_read_error" in output["error"]
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main"]
    session_mgr.create_session.assert_not_called()
    saved_outputs = [call.args[1] for call in artifact_store.save_phase_output.call_args_list]
    assert all("validation_errors" not in saved for saved in saved_outputs)


def test_top_level_llm_phase_phase2_retries_progress_only_json(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_2_venv_create",
        name="Venv",
        prompt_template="phase_2_venv_create",
        output_schema={},
        type="llm",
        validator="venv",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase2-shape-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("venv", validate_venv)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        "I am setting up the virtual environment and will report when complete.",
        json.dumps(
            {
                "venv_path": str(tmp_path / ".venv"),
                "python_path": str(tmp_path / ".venv" / "bin" / "python"),
                "installed_packages": ["torch", "torch_npu"],
            }
        ),
    ]
    prompt_loader.load_prompt.return_value = "phase 2 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["venv_path"] == str(tmp_path / ".venv")
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main"]
    assert "response contained no parseable JSON object" in session_mgr.send_command.call_args_list[1].args[1]


def test_top_level_llm_phase_phase6_retries_status_only_json(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_6_report",
        name="Report",
        prompt_template="phase_6_report",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase6-shape-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        json.dumps({"status": "in_progress", "message": "writing reports"}),
        json.dumps({"report_paths": ["/tmp/report.md"], "migration_summary": {"files_migrated": 1}}),
    ]
    prompt_loader.load_prompt.return_value = "phase 6 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["report_paths"] == ["/tmp/report.md"]
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main"]
    assert "status/progress-only JSON" in session_mgr.send_command.call_args_list[1].args[1]


def test_top_level_llm_phase_phase6_retries_wrong_type_json(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_6_report",
        name="Report",
        prompt_template="phase_6_report",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase6-shape-retry-types",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        json.dumps({"report_paths": "/tmp/report.md", "migration_summary": []}),
        json.dumps({"report_paths": ["/tmp/report.md"], "migration_summary": {"files_migrated": 1}}),
    ]
    prompt_loader.load_prompt.return_value = "phase 6 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["report_paths"] == ["/tmp/report.md"]
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main"]
    retry_prompt = session_mgr.send_command.call_args_list[1].args[1]
    assert "report_paths must be a list" in retry_prompt
    assert "migration_summary must be an object" in retry_prompt


def test_top_level_llm_phase_phase35_retries_status_only_json(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="Static Validate",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
        validator="entry_static",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="phase35-shape-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    validator.register_validator("entry_static", validate_entry_static)
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.create_session.return_value = "session:main_retry_1"
    session_mgr.send_command.side_effect = [
        json.dumps({"status": "in_progress", "message": "checking validation script"}),
        json.dumps({"validation_passed": True, "issues": [], "fix_plan": "No issues found."}),
    ]
    prompt_loader.load_prompt.return_value = "phase 35 prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert output["validation_passed"] is True
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main"]
    assert "status/progress-only JSON" in session_mgr.send_command.call_args_list[1].args[1]


def test_workflow_review_phase_retries_status_only_json(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="review_gate",
        name="Review",
        prompt_template="phase_5_review",
        output_schema={},
        type="review",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="review-shape-retry",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [
        json.dumps({"status": "in_progress", "message": "reviewing"}),
        json.dumps({
            "verdict": "accept",
            "cpu_fallback_detected": False,
            "cpu_fallback_necessary": False,
            "alternative_suggestions": "",
            "reasoning": "ok",
        }),
    ]
    prompt_loader.load_prompt.return_value = "review prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    loop_state: dict[str, object] = {}

    output = executor._execute_review_phase(
        phase,
        {},
        {"PROJECT_DIR": str(tmp_path)},
        {},
        loop_state,
        [],
        None,
        {},
    )

    assert output["verdict"] == "accept"
    assert loop_state["review_verdict_status"] == "accept"
    assert [call.args[0] for call in session_mgr.send_command.call_args_list] == ["session:main", "session:main"]
    assert "status/progress-only JSON" in session_mgr.send_command.call_args_list[1].args[1]


def test_top_level_llm_phase_uses_configured_phase_timeout(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_2_venv_create",
        name="Venv",
        prompt_template="phase_2_venv_create",
        output_schema={},
        type="llm",
        agent="main_engineer",
    )
    workflow = WorkflowDefinition(
        name="configured-timeout",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = ValidatorEngine()
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.return_value = json.dumps(
        {
            "venv_path": str(tmp_path / ".venv"),
            "python_path": str(tmp_path / ".venv" / "bin" / "python"),
            "installed_packages": ["torch", "torch_npu"],
        }
    )
    prompt_loader.load_prompt.return_value = "prompt"
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        framework_config={"session_timeout_phase": "33"},
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, _ = executor._execute_llm_phase(phase, {}, {})

    assert status == "success"
    assert session_mgr.send_command.call_args.kwargs["timeout"] == 33


def test_entry_script_action_needed_false_string_does_not_apply_or_count(tmp_path: Path):
    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    state = {"phase_3_entry_script": {"run_command": "python old.py"}}
    loop_vars = {"entry_script": "python old.py"}
    step_outputs: dict[str, object] = {}
    loop_state: dict[str, object] = {
        "entry_script_revision_count": 0,
        "entry_script_revision_requests": [],
        "max_entry_script_revisions": 2,
    }

    result = executor._maybe_apply_entry_script_action(
        {
            "entry_script_action": {
                "needed": "false",
                "action": "modify",
                "reason": "string false should not revise",
                "entry_script_path": "new.py",
                "run_command": "python new.py",
            }
        },
        loop_vars,
        state,
        step_outputs,
        loop_state,
    )

    assert result is not None
    assert result["needed"] is False
    assert result["applied"] is False
    assert result["blocked_reason"] == "not_needed"
    assert loop_state["entry_script_revision_count"] == 0
    assert loop_state["entry_script_revision_requests"] == []
    assert loop_vars["entry_script"] == "python old.py"
    assert state["phase_3_entry_script"]["run_command"] == "python old.py"
    assert step_outputs == {}


def test_entry_script_action_needed_string_normalization():
    normalize = WorkflowExecutor._normalize_entry_script_action

    for value in (True, "true", "1", "yes"):
        assert normalize({"needed": value})["needed"] is True

    for value in (False, "false", "0", "no", "maybe", "", None):
        action = {} if value is None else {"needed": value}
        assert normalize(action)["needed"] is False


def test_analyze_error_prompt_has_entry_script_action_schema_and_contract_context(tmp_path: Path):
    prompt_content = (Path(__file__).resolve().parent.parent / "prompts" / "phase_error_recovery.md").read_text(encoding="utf-8")
    assert "entry_script_contract" in prompt_content
    assert "entry_script_action" in prompt_content
    assert '"needed": false' in prompt_content
    assert '"action": "none"' in prompt_content
    assert '"run_command": ""' in prompt_content
    assert "reason freely" not in prompt_content

    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    input_ctx: dict[str, str] = {}
    executor._inject_sub_workflow_context(
        input_ctx,
        "analyze_error",
        {"script_stderr": "failed"},
        {"entry_script": "python old.py"},
        {
            "phase_3_entry_script": {
                "entry_script_path": "old.py",
                "run_command": "python old.py",
                "required_report_paths": ["migration_reports/full.md"],
                "required_checks": ["full_validation"],
            }
        },
        [],
    )

    contract = json.loads(input_ctx["entry_script_contract"])
    assert contract["run_command"] == "python old.py"
    assert contract["required_report_paths"] == ["migration_reports/full.md"]
    assert contract["required_checks"] == ["full_validation"]


def test_missing_experience_usage_fields_normalize_to_empty(tmp_path: Path):
    executor = _executor_for_experience_context(tmp_path)
    output = {"fixed": True}

    usage = executor._normalize_experience_usage_report(output)

    assert usage == {
        "used_experience_ids": [],
        "experience_actions_taken": [],
        "ignored_experience_ids": [],
        "ignored_reasons": {},
    }


def _custom_op_gate_payload() -> dict[str, object]:
    return {
        "inventory_count": 1,
        "manifest_entries": 1,
        "closed_pass_entries": 1,
        "remaining_entries": 0,
        "full_migration_status": "FULL_PASS",
        "project_e2e_passed": True,
        "report_parity_passed": True,
        "performance_report": {
            "complete": True,
            "unit_count": 1,
            "path": "migration_reports/performance.json",
            "project_api_invoked": True,
            "baseline_device": "cpu",
            "custom_device": "ascend_opp",
            "overall_baseline_seconds": 0.05,
            "overall_custom_seconds": 0.04,
            "overall_speedup_vs_baseline": 1.25,
            "overall_project_api_invoked": True,
            "overall_all_units_replaced": True,
            "overall_baseline_device": "cpu",
            "overall_custom_device": "ascend_opp",
            "entries": [
                {
                    "unit_identity": "op_1",
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cpu",
                    "custom_device": "ascend_opp",
                }
            ],
        },
        "source_inventory": {
            "discovery_complete": True,
            "discovery_sources_checked": [
                "source",
                "bindings",
                "wrappers",
                "autograd",
                "aliases",
                "launch",
                "setup",
                "tests",
            ],
            "out_of_scope_source_groups": [],
            "entries": [
                {
                    "name": "op_1",
                    "unit_identity": "op_1",
                    "variant_or_signature": "op_1(float32)",
                    "inventory_granularity": "fine_grained",
                    "native_operator_symbols": ["op_1_forward"],
                    "kernel_functions": ["op_1_kernel"],
                    "kernel_launch_sites": ["csrc/op_1.cpp:launch"],
                    "public_entry_mapping": {"python_api": "pkg.op_1"},
                    "source_evidence": ["csrc/op_1.cpp"],
                    "source_path": "csrc/op_1.cpp",
                }
            ],
        },
        "rows": [
            {
                "row_id": "op_1",
                "unit_identity": "op_1",
                "variant_or_signature": "op_1(float32)",
                "inventory_granularity": "fine_grained",
                "status": "PASS",
                "native_operator_symbols": ["op_1_forward"],
                "kernel_functions": ["op_1_kernel"],
                "kernel_launch_sites": ["csrc/op_1.cpp:launch"],
                "public_entry_mapping": {"python_api": "pkg.op_1"},
                "source_evidence": ["csrc/op_1.cpp"],
                "opp_custom_op_artifact_evidence": {
                    "path": "opp/op_1/libop_1.so",
                    "runtime_loaded_artifact_path": "opp/op_1/libop_1.so",
                    "op_host_source_path": "opp/op_1/op_host/op_1.cpp",
                    "op_kernel_source_path": "opp/op_1/op_kernel/op_1.cpp",
                    "build_script_path": "opp/op_1/build.sh",
                    "install_log_path": "migration_reports/opp_install.log",
                    "generated_header_path": "opp/op_1/build_out/autogen/op_1.h",
                    "op_info_path": "opp/op_1/build_out/op_info/op_1.json",
                    "kernel_meta_path": "opp/op_1/build_out/kernel_meta/op_1.o",
                    "project_local": True,
                    "built": True,
                    "installed": True,
                    "native_artifact": True,
                    "compiled_extension": True,
                    "build_provenance": {
                        "command": "bash opp/op_1/build.sh",
                        "log_path": "migration_reports/build.log",
                    },
                },
                "adapter_evidence": {"imported": True},
                "parity_evidence": {"passed": True},
                "integration_e2e_evidence": {
                    "passed": True,
                    "project_api_invoked": True,
                    "custom_op_route_executed": True,
                    "native_custom_op_route_executed": True,
                },
                "public_api_route_evidence": {
                    "unit_identity": "op_1",
                    "route_type": "public_api",
                    "entrypoint": "pkg.op_1",
                    "same_run": True,
                    "custom_call_count": 2,
                    "public_api_invoked": True,
                    "native_custom_op_route_executed": True,
                },
                "same_run_runtime_coverage": {
                    "custom_call_count": 2,
                    "same_run": True,
                    "project_api_route": True,
                    "native_custom_op_route_executed": True,
                },
                "performance_evidence": {
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cpu",
                    "custom_device": "ascend_opp",
                },
                "no_fallback_no_zero_call_no_builtin_contamination": {
                    "passed": True,
                    "fallback_detected": False,
                    "zero_call_detected": False,
                    "builtin_contamination_detected": False,
                    "baseline_only_detected": False,
                    "stub_detected": False,
                },
            }
        ],
    }


def _write_native_custom_op_gate_artifacts(project_dir: Path) -> None:
    artifact_path = project_dir / "opp" / "op_1" / "libop_1.so"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00libascendcl aclrt op_host op_kernel kernel_operator aicore")
    host_source = project_dir / "opp" / "op_1" / "op_host" / "op_1.cpp"
    kernel_source = project_dir / "opp" / "op_1" / "op_kernel" / "op_1.cpp"
    host_source.parent.mkdir(parents=True, exist_ok=True)
    kernel_source.parent.mkdir(parents=True, exist_ok=True)
    _ = host_source.write_text("#include <acl/acl.h>\n// op_host registration\n", encoding="utf-8")
    _ = kernel_source.write_text("#include <kernel_operator.h>\n// op_kernel AscendC aicore\n", encoding="utf-8")
    build_script = project_dir / "opp" / "op_1" / "build.sh"
    _ = build_script.write_text("cmake -S . -B build_out && cmake --build build_out && cmake --install build_out\n", encoding="utf-8")
    generated_header = project_dir / "opp" / "op_1" / "build_out" / "autogen" / "op_1.h"
    op_info = project_dir / "opp" / "op_1" / "build_out" / "op_info" / "op_1.json"
    kernel_meta = project_dir / "opp" / "op_1" / "build_out" / "kernel_meta" / "op_1.o"
    generated_header.parent.mkdir(parents=True, exist_ok=True)
    op_info.parent.mkdir(parents=True, exist_ok=True)
    kernel_meta.parent.mkdir(parents=True, exist_ok=True)
    _ = generated_header.write_text("// generated CANN header\n", encoding="utf-8")
    _ = op_info.write_text('{"op":"op_1"}\n', encoding="utf-8")
    _ = kernel_meta.write_bytes(b"\x7fELF\x02\x01\x01\x00kernel_operator op_kernel")
    build_log = project_dir / "migration_reports" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    build_log_text = (
        "CANN OPP build: op_host/op_1.cpp op_kernel/op_1.cpp kernel_operator.h -lascendcl\n"
        "install package to vendors/customize/op_impl/ai_core/tbe\n"
    )
    _ = build_log.write_text(build_log_text, encoding="utf-8")
    _ = (project_dir / "migration_reports" / "opp_install.log").write_text("install OPP package into ASCEND_OPP_PATH vendors/customize\n", encoding="utf-8")
    _ = (project_dir / "migration_reports" / "migration_manifest.json").write_text(
        json.dumps({"required_units": ["op_1"]}),
        encoding="utf-8",
    )


def _custom_op_gate_workflow(max_iterations: int = 1, run_entry_command: str = "python -c \"print('ok')\"") -> WorkflowDefinition:
    return WorkflowDefinition(
        name="custom_gate",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={"error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"}},
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                type="loop",
                max_iterations=max_iterations,
                stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
                phases=[
                    {
                        "id": "run_entry_script",
                        "type": "shell",
                        "command": run_entry_command,
                        "on_failure": "continue",
                    },
                    {
                        "id": "custom_op_final_gate",
                        "type": "builtin",
                        "condition": "$.script_exit_code == 0",
                        "params": {"operation": "custom_op_final_gate"},
                    },
                    {
                        "id": "analyze_error",
                        "type": "llm",
                        "condition": "$.script_exit_code != 0",
                        "prompt_template": "analyze_prompt",
                        "agent": "error_analyzer",
                        "output_as": "error_analysis",
                    },
                ],
            )
        },
    )


def _custom_op_gate_operator_repair_workflow(max_iterations: int = 1, run_entry_command: str = "python -c \"print('ok')\"") -> WorkflowDefinition:
    return WorkflowDefinition(
        name="custom_gate_operator_repair",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                type="loop",
                max_iterations=max_iterations,
                stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
                phases=[
                    {
                        "id": "run_entry_script",
                        "type": "shell",
                        "command": run_entry_command,
                        "on_failure": "continue",
                    },
                    {
                        "id": "custom_op_final_gate",
                        "type": "builtin",
                        "condition": "$.script_exit_code == 0",
                        "params": {"operation": "custom_op_final_gate"},
                    },
                    {
                        "id": "analyze_error",
                        "type": "llm",
                        "condition": "$.script_exit_code != 0",
                        "prompt_template": "analyze_prompt",
                        "agent": "error_analyzer",
                        "output_as": "error_analysis",
                    },
                    {
                        "id": "repair_dispatch",
                        "type": "dispatch",
                        "condition": "$.script_exit_code != 0",
                        "params": {
                            "route_field": "${error_analysis.repair_role}",
                            "routes": {"operator_fixer": "fix_operator"},
                        },
                    },
                    {
                        "id": "fix_operator",
                        "type": "llm",
                        "prompt_template": "repair_operator_fixer",
                        "agent": "operator_fixer",
                    },
                ],
            )
        },
    )


def _write_custom_op_gate_writer_script(project_dir: Path, payload: dict[str, object]) -> str:
    script_path = project_dir / "write_custom_op_final_gate.py"
    script_path.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"project_dir = Path({json.dumps(str(project_dir))})\n"
        f"payload = json.loads({json.dumps(json.dumps(payload))})\n"
        "reports_dir = project_dir / 'migration_reports'\n"
        "reports_dir.mkdir(parents=True, exist_ok=True)\n"
        "(reports_dir / 'custom_op_final_gate.json').write_text(json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f"python {script_path}"


def _custom_op_gate_executor(tmp_path: Path, run_entry_command: str = "python -c \"print('ok')\"") -> WorkflowExecutor:
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.return_value = json.dumps({
        "repair_role": "code_adapter",
        "category": "validation",
        "root_cause": "final evidence gate failed",
        "suggested_fix": "complete custom-op evidence",
    })
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    return WorkflowExecutor(
        _custom_op_gate_workflow(run_entry_command=run_entry_command),
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )



def test_rule_based_migration_builtin_uses_migrator_api_and_updates_project(tmp_path: Path) -> None:
    source_file = tmp_path / "model.py"
    source_file.write_text(
        "import torch\n"
        "device = 'cuda'\n"
        "with torch.cuda.amp.autocast():\n"
        "    tensor = torch.ones(1).cuda()\n",
        encoding="utf-8",
    )
    workflow = WorkflowDefinition(name="rule-builtin", version="1.0", phases=[], terminals=["complete"])
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="phase_4_rule_migration",
        name="Rule Migration",
        prompt_template="",
        output_schema={},
        type="builtin",
    )
    setattr(phase, "params", {"operation": "rule_based_migration", "pattern": "*.py"})

    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    migrated = source_file.read_text(encoding="utf-8")
    assert status == "success"
    assert output["operation"] == "rule_based_migration"
    assert output["result"]["summary"]["total_files"] == 1
    assert output["result"]["summary"]["total_replacements"] >= 4
    assert "import torch_npu" in migrated
    assert "torch.npu.amp" in migrated
    assert ".npu()" in migrated
    assert "'npu'" in migrated


def test_builtin_phase_missing_operation_fails(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(name="rule-builtin", version="1.0", phases=[], terminals=["complete"])
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="phase_4_rule_migration",
        name="Rule Migration",
        prompt_template="",
        output_schema={},
        type="builtin",
    )

    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    assert status == "failure"
    assert output == {
        "error": "Builtin phase 'phase_4_rule_migration' is missing required operation",
        "operation": "",
    }


def test_builtin_phase_missing_operation_does_not_fall_through(tmp_path: Path) -> None:
    bad_phase = PhaseDefinition(
        id="phase_4_rule_migration",
        name="Rule Migration",
        prompt_template="",
        output_schema={},
        type="builtin",
        transitions={"on_success": "phase_5_validation"},
    )
    next_phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="builtin",
        params={"operation": "stagnation_check"},
    )
    workflow = WorkflowDefinition(
        name="rule-builtin",
        version="1.0",
        phases=[bad_phase, next_phase],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor.execute({})

    assert result["phase_results"]["phase_4_rule_migration"]["status"] == "failure"
    assert "phase_5_validation" not in result["phase_results"]


def test_experience_memory_workflow_has_custom_op_final_gate_after_entry_script() -> None:
    workflow_path = Path(__file__).resolve().parent.parent / "workflows" / "experience_memory_test.yaml"
    workflow = load_workflow(str(workflow_path))

    gate_phase = next(
        phase for phase in workflow.sub_workflows["repair_loop"].phases
        if isinstance(phase, dict) and phase.get("id") == "custom_op_final_gate"
    )

    assert isinstance(gate_phase, dict)
    assert gate_phase["type"] == "builtin"
    assert gate_phase["params"] == {"operation": "custom_op_final_gate"}


def test_experience_memory_workflow_has_serving_final_gate_after_entry_script() -> None:
    workflow_path = Path(__file__).resolve().parent.parent / "workflows" / "experience_memory_test.yaml"
    workflow = load_workflow(str(workflow_path))

    phase_ids = [phase.get("id") for phase in workflow.sub_workflows["repair_loop"].phases if isinstance(phase, dict)]
    assert phase_ids.index("run_entry_script") < phase_ids.index("serving_final_gate") < phase_ids.index("analyze_error")
    gate_phase = next(
        phase for phase in workflow.sub_workflows["repair_loop"].phases
        if isinstance(phase, dict) and phase.get("id") == "serving_final_gate"
    )
    assert gate_phase["params"] == {"operation": "serving_final_gate"}


def test_experience_memory_custom_op_gate_skips_for_non_custom_contract(tmp_path: Path) -> None:
    workflow_path = Path(__file__).resolve().parent.parent / "workflows" / "experience_memory_test.yaml"
    workflow = load_workflow(str(workflow_path))

    phase_ids = [phase.get("id") for phase in workflow.sub_workflows["repair_loop"].phases if isinstance(phase, dict)]
    assert "custom_op_final_gate" in phase_ids

def test_missing_custom_op_final_gate_blocks_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    executor = _custom_op_gate_executor(tmp_path)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert "Custom-op final evidence gate failed" in result["loop_state"]["script_stderr"]
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is False
    executor.session_mgr.send_command.assert_called_once()


def _serving_contract(tmp_path: Path, route: str = "vllm_serving", framework: str = "vllm") -> dict[str, object]:
    return {
        "entry_script_kind": "vllm_serving_validation" if route == "vllm_serving" else "sglang_serving_validation",
        "migration_route": route,
        "serving_framework": framework,
        "run_command": "python validate_serving.py",
        "serving_reports_dir": "migration_reports/serving",
        "required_report_paths": ["migration_reports/serving/serving_final_gate.json"],
    }


def _serving_gate_payload(route: str = "vllm_serving", framework: str = "vllm") -> dict[str, object]:
    return {
        "migration_route": route,
        "serving_framework": framework,
        "full_migration_status": "FULL_PASS",
        "project_test_files": ["tests/test_serving_api.py"],
        "expected_outputs": ["generated text returned"],
        "required_checks": [
            "project_demo_or_test_execution",
            "serving_api_request_validation",
            "readiness_probe_passed",
            "npu_execution_evidence",
            "no_cuda_fallback",
            "no_cpu_fallback",
            "fresh_serving_report",
            "route_framework_match",
        ],
        "readiness_probe": {"passed": True, "status_code": 200},
        "request_validation": {"passed": True, "project_fixture": "tests/request.json"},
        "npu_execution_evidence": {"passed": True, "device": "npu:0"},
        "project_demo_or_test_executed": True,
        "serving_api_validated": True,
        "npu_execution_observed": True,
        "cuda_fallback_detected": False,
        "cpu_fallback_detected": False,
        "import_only": False,
        "smoke_only": False,
    }


def _execute_serving_gate(executor: WorkflowExecutor, state: dict[str, object], loop_state: dict[str, object]) -> dict[str, object]:
    phase = PhaseDefinition(id="serving_final_gate", name="Serving Gate", prompt_template="", output_schema={}, type="builtin")
    setattr(phase, "params", {"operation": "serving_final_gate"})
    status, result = executor._execute_builtin_phase(
        phase,
        state=state,
        context={"PROJECT_DIR": executor.project_dir},
        loop_state=loop_state,
    )
    assert status == "success"
    return result


def test_serving_final_gate_fails_closed_on_missing_report(tmp_path: Path) -> None:
    executor = _workflow_executor_for_custom_op_gate(tmp_path)
    phase = PhaseDefinition(id="serving_final_gate", name="Serving Gate", prompt_template="", output_schema={}, type="builtin")
    setattr(phase, "params", {"operation": "serving_final_gate"})
    loop_state: dict[str, object] = {"run_entry_script_started_at": 10.0}

    status, result = executor._execute_builtin_phase(
        phase,
        state={"phase_3_entry_script": _serving_contract(tmp_path)},
        context={"PROJECT_DIR": str(tmp_path)},
        loop_state=loop_state,
    )

    assert status == "success"
    assert result["passed"] is False
    assert loop_state["script_exit_code"] == 1
    assert "missing" in str(loop_state["script_stderr"])


def test_serving_final_gate_missing_report_blocks_phase5_success(tmp_path: Path) -> None:
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "python -c \"print('serving validation wrapper passed')\"",
                "on_failure": "continue",
            },
            {
                "id": "serving_final_gate",
                "type": "builtin",
                "condition": "$.script_exit_code == 0",
                "params": {"operation": "serving_final_gate"},
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="serving_gate_blocks_success",
        version="1.0",
        phases=[],
        terminals=["complete"],
        sub_workflows={"repair_loop": sub_workflow},
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate_serving.py", "project_dir": str(tmp_path)},
        ),
        state={"phase_3_entry_script": _serving_contract(tmp_path)},
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert result["loop_state"]["serving_final_gate"]["passed"] is False
    assert "serving final gate report missing" in result["loop_state"]["script_stderr"]


def test_serving_final_gate_fails_closed_on_stale_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports" / "serving"
    reports_dir.mkdir(parents=True)
    gate_path = reports_dir / "serving_final_gate.json"
    _ = gate_path.write_text(json.dumps(_serving_gate_payload()), encoding="utf-8")
    executor = _workflow_executor_for_custom_op_gate(tmp_path)
    loop_state: dict[str, object] = {"run_entry_script_started_at": gate_path.stat().st_mtime + 100}

    result = _execute_serving_gate(executor, {"phase_3_entry_script": _serving_contract(tmp_path)}, loop_state)

    assert result["passed"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("stale" in str(error) for error in errors)
    assert loop_state["script_exit_code"] == 1


def test_serving_final_gate_fails_closed_on_invalid_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports" / "serving"
    reports_dir.mkdir(parents=True)
    _ = (reports_dir / "serving_final_gate.json").write_text(json.dumps({"full_migration_status": "SMOKE_ONLY"}), encoding="utf-8")
    executor = _workflow_executor_for_custom_op_gate(tmp_path)
    loop_state: dict[str, object] = {"run_entry_script_started_at": 0.0}

    result = _execute_serving_gate(executor, {"phase_3_entry_script": _serving_contract(tmp_path)}, loop_state)

    assert result["passed"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("FULL_PASS" in str(error) or "migration_route" in str(error) for error in errors)


def test_serving_final_gate_passes_on_strict_full_pass(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports" / "serving"
    reports_dir.mkdir(parents=True)
    _ = (reports_dir / "serving_final_gate.json").write_text(json.dumps(_serving_gate_payload()), encoding="utf-8")
    executor = _workflow_executor_for_custom_op_gate(tmp_path)
    loop_state: dict[str, object] = {"run_entry_script_started_at": 0.0}

    result = _execute_serving_gate(executor, {"phase_3_entry_script": _serving_contract(tmp_path)}, loop_state)

    assert result["passed"] is True
    assert "script_exit_code" not in loop_state


def test_custom_op_opp_preflight_blocks_phase5_before_entry_script(tmp_path: Path) -> None:
    executor = _custom_op_gate_executor(tmp_path)
    executor._execute_shell_phase = MagicMock(wraps=executor._execute_shell_phase)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(tmp_path / "migration_reports"),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert "Custom-op OPP preflight failed" in result["loop_state"]["script_stderr"]
    assert result["loop_state"]["custom_op_opp_preflight"]["passed"] is False
    assert result["loop_state"].get("custom_op_final_gate") is None


def test_incomplete_performance_report_blocks_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    payload = _custom_op_gate_payload()
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report["complete"] = False
    executor = _custom_op_gate_executor(
        tmp_path,
        run_entry_command=_write_custom_op_gate_writer_script(tmp_path, payload),
    )
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert any("performance_report.complete" in error for error in result["loop_state"]["custom_op_final_gate"]["errors"])


def test_custom_op_final_gate_ignores_outside_project_reports_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside_reports"
    outside.mkdir()
    (outside / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
    _write_native_custom_op_gate_artifacts(tmp_path)
    executor = _custom_op_gate_executor(tmp_path)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(outside),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    gate = result["loop_state"]["custom_op_final_gate"]
    assert gate["passed"] is False
    assert gate["path"] == str((tmp_path / "migration_reports" / "custom_op_final_gate.json").resolve())


def test_custom_op_final_gate_rejects_oversized_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    _ = (reports_dir / "custom_op_final_gate.json").write_text("{" + " " * (5 * 1024 * 1024), encoding="utf-8")
    executor = _custom_op_gate_executor(tmp_path)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert any("too large" in error for error in result["loop_state"]["custom_op_final_gate"]["errors"])


def test_valid_custom_op_final_gate_allows_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    executor = _custom_op_gate_executor(
        tmp_path,
        run_entry_command=_write_custom_op_gate_writer_script(tmp_path, _custom_op_gate_payload()),
    )
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is True
    executor.session_mgr.send_command.assert_not_called()


def test_expanded_variant_contract_blocks_collapsed_phase5_gate_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    expanded_units = ["op_1:ndim=1", "op_1:ndim=2"]
    _ = (reports_dir / "migration_manifest.json").write_text(
        json.dumps({"required_units": expanded_units}),
        encoding="utf-8",
    )
    executor = _custom_op_gate_executor(
        tmp_path,
        run_entry_command=_write_custom_op_gate_writer_script(tmp_path, _custom_op_gate_payload()),
    )
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
            "expanded_variant_inventory": {
                "variant_axes_detected": True,
                "unit_identities": expanded_units,
                "expanded_operator_instances_count": len(expanded_units),
            },
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    gate = result["loop_state"]["custom_op_final_gate"]
    assert gate["passed"] is False
    assert any("expanded variant unit identities" in error for error in gate["errors"])


def test_stale_custom_op_final_gate_blocks_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    gate_path = reports_dir / "custom_op_final_gate.json"
    gate_path.write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
    os.utime(gate_path, (1, 1))
    executor = _custom_op_gate_executor(tmp_path)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    gate = result["loop_state"]["custom_op_final_gate"]
    assert gate["passed"] is False
    assert any("stale" in error and "current run_entry_script" in error for error in gate["errors"])
    assert "Custom-op final evidence gate failed" in result["loop_state"]["script_stderr"]


def test_operator_full_pass_repair_output_does_not_recover_stale_gate_without_current_rerun(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    gate_path = reports_dir / "custom_op_final_gate.json"
    gate_path.write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
    os.utime(gate_path, (1, 1))
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    stale_full_pass_claim = json.dumps({
        "summary": "FULL_PASS: final gate closure is inventory_count=1, manifest_entries=1, closed_pass_entries=1, remaining_entries=0, full_migration_status=FULL_PASS.",
        "verification": {"result": {"status": "PASS"}},
    })
    session_mgr.send_command.side_effect = [
        json.dumps({"repair_role": "operator_fixer", "category": "operator", "root_cause": "stale gate", "suggested_fix": "regenerate full gate"}),
        *(stale_full_pass_claim for _ in range(CUSTOM_OP_OPERATOR_MAX_POLLS_DEFAULT)),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        _custom_op_gate_operator_repair_workflow(run_entry_command="python -c \"print('late rerun')\""),
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py", "reports_dir": str(reports_dir)}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["communication_error"] is True
    assert "stale before strict OPP final gate FULL_PASS" in fix_output["error"]
    assert session_mgr.send_command.call_count == 3


def test_operator_full_pass_repair_output_does_not_recover_invalid_gate(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    payload = _custom_op_gate_payload()
    payload["remaining_entries"] = 1
    payload["full_migration_status"] = "INCOMPLETE"
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["status"] = "INCOMPLETE"
    rows[0]["opp_custom_op_artifact_evidence"] = {}
    gate_path = reports_dir / "custom_op_final_gate.json"
    gate_path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(gate_path, (1, 1))
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.side_effect = [
        json.dumps({"repair_role": "operator_fixer", "category": "operator", "root_cause": "stale gate", "suggested_fix": "regenerate full gate"}),
        json.dumps({
            "summary": "FULL_PASS: claimed final gate closure.",
            "verification": {"result": {"status": "PASS"}},
        }),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        _custom_op_gate_operator_repair_workflow(run_entry_command="python -c \"print('late rerun')\""),
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py", "reports_dir": str(reports_dir)}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is False
    assert result["loop_state"]["fix_operator"].get("custom_op_final_gate_recovered") is None


def test_non_custom_project_skips_custom_op_final_gate(tmp_path: Path) -> None:
    executor = _custom_op_gate_executor(tmp_path)
    state = {"phase_3_entry_script": {"run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    assert result["loop_state"]["custom_op_final_gate"] == {
        "operation": "custom_op_final_gate",
        "skipped": True,
        "passed": True,
    }
    executor.session_mgr.send_command.assert_not_called()


def test_non_custom_project_with_reports_dir_skips_custom_op_final_gate(tmp_path: Path) -> None:
    executor = _custom_op_gate_executor(tmp_path)
    state = {
        "phase_3_entry_script": {
            "run_command": "python validate.py",
            "reports_dir": str(tmp_path / "migration_reports"),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    assert result["loop_state"]["custom_op_final_gate"] == {
        "operation": "custom_op_final_gate",
        "skipped": True,
        "passed": True,
    }
    assert result["loop_state"].get("custom_op_opp_preflight") is None
    executor.session_mgr.send_command.assert_not_called()


def test_custom_op_gate_stagnation_gets_fail_closed_terminal_status(tmp_path: Path) -> None:
    _write_native_custom_op_gate_artifacts(tmp_path)
    payload = _custom_op_gate_payload()
    payload["closed_pass_entries"] = 0
    payload["remaining_entries"] = 1
    payload["full_migration_status"] = "INCOMPLETE"
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["status"] = "INCOMPLETE"
    rows[0]["opp_custom_op_artifact_evidence"] = {}
    run_entry_command = _write_custom_op_gate_writer_script(tmp_path, payload)
    workflow = _custom_op_gate_workflow(max_iterations=5, run_entry_command=run_entry_command)
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.return_value = json.dumps({
        "repair_role": "code_adapter",
        "category": "validation",
        "root_cause": "missing strict Ascend C/CANN OPP evidence",
        "suggested_fix": "add real op_host/op_kernel/build/runtime OPP evidence",
    })
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "stagnation_fail_closed_missing_strict_opp_evidence"
    assert result["status"] != "success"
    fail_closed = result["loop_state"]["custom_op_fail_closed"]
    assert fail_closed["migration_passed"] is False
    assert fail_closed["blocker"] == "missing_strict_ascend_cann_opp_evidence"
    assert fail_closed["remaining_entries"] == 1
    assert fail_closed["full_migration_status"] == "INCOMPLETE"
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is False


def test_non_custom_entry_script_kind_skips_custom_op_final_gate(tmp_path: Path) -> None:
    executor = _custom_op_gate_executor(tmp_path)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "standard_validation",
            "run_command": "python validate.py",
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "${state.phase_3_entry_script.run_command}", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    assert result["loop_state"]["custom_op_final_gate"] == {
        "operation": "custom_op_final_gate",
        "skipped": True,
        "passed": True,
    }
    executor.session_mgr.send_command.assert_not_called()


def _operator_recovery_workflow(max_iterations: int = 1) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="operator_gate_recovery",
        version="1.0",
        phases=[],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "operator_fixer": {"role": "operator_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                type="loop",
                max_iterations=max_iterations,
                stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
                phases=[
                    {
                        "id": "run_entry_script",
                        "type": "shell",
                        "command": "python -c \"import sys; sys.exit(1)\"",
                        "on_failure": "continue",
                    },
                    {
                        "id": "analyze_error",
                        "type": "llm",
                        "condition": "$.script_exit_code != 0",
                        "prompt_template": "analyze_prompt",
                        "agent": "error_analyzer",
                        "output_as": "error_analysis",
                    },
                    {
                        "id": "repair_dispatch",
                        "type": "dispatch",
                        "condition": "$.script_exit_code != 0",
                        "route_field": "${error_analysis.repair_role}",
                        "routes": {"operator_fixer": "fix_operator"},
                    },
                    {
                        "id": "fix_operator",
                        "type": "llm",
                        "condition": "$.script_exit_code != 0",
                        "prompt_template": "repair_operator_fixer",
                        "agent": "operator_fixer",
                        "on_failure": "break",
                    },
                ],
            )
        },
    )


def _operator_recovery_executor(
    tmp_path: Path,
    session_mgr: MagicMock,
    framework_config: dict[str, object] | None = None,
    prompt_contexts: dict[str, dict[str, object]] | None = None,
    max_iterations: int = 1,
) -> WorkflowExecutor:
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")

    def load_prompt(template: str, ctx: dict[str, object]) -> str:
        if prompt_contexts is not None:
            prompt_contexts[template] = dict(ctx)
        return template

    prompt_loader.load_prompt.side_effect = load_prompt
    return WorkflowExecutor(
        _operator_recovery_workflow(max_iterations=max_iterations),
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        framework_config=framework_config,
    )


def test_operator_fix_no_response_recovers_from_current_valid_full_pass_gate(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_timeouts: list[int | None] = []
    operator_retries: list[int | None] = []
    operator_recovery_waits: list[int | None] = []

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_timeouts.append(timeout)
        operator_retries.append(retries)
        operator_recovery_waits.append(recovery_wait_timeout)
        gate_path = reports_dir / "custom_op_final_gate.json"
        gate_path.write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        future = time.time() + 2.0
        os.utime(gate_path, (future, future))
        return '{"ok": false, "error": "Session still running with no response"}'

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["custom_op_final_gate_recovered"] is True
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is True
    assert operator_timeouts
    assert all(timeout is None for timeout in operator_timeouts)
    assert operator_retries
    assert all(retries == 0 for retries in operator_retries)
    assert operator_recovery_waits == [CUSTOM_OP_OPERATOR_POLL_TIMEOUT_DEFAULT]


def test_operator_fix_configured_poll_timeout_is_not_used_as_session_deadline(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_timeouts: list[int | None] = []
    operator_retries: list[int | None] = []
    operator_recovery_waits: list[int | None] = []

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_timeouts.append(timeout)
        operator_retries.append(retries)
        operator_recovery_waits.append(recovery_wait_timeout)
        return '{"ok": false, "error": "Session still running with no response"}'

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(
        tmp_path,
        session_mgr,
        framework_config={"custom_op_operator_poll_timeout": 120},
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert operator_timeouts
    assert all(timeout is None for timeout in operator_timeouts)
    assert operator_retries
    assert all(retries == 0 for retries in operator_retries)
    assert operator_recovery_waits == [120] * 3


def test_custom_op_operator_incomplete_response_is_retryable_not_completed(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_timeouts: list[int | None] = []
    operator_retries: list[int | None] = []
    operator_recovery_waits: list[int | None] = []

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_timeouts.append(timeout)
        operator_retries.append(retries)
        operator_recovery_waits.append(recovery_wait_timeout)
        return json.dumps({
            "status": "FAILED",
            "repair_status": "INCOMPLETE",
            "fixed": False,
            "summary": "OPP artifacts are still missing",
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["communication_error"] is True
    assert fix_output["retryable"] is True
    assert "strict OPP final gate FULL_PASS" in fix_output["error"]
    session_mgr.create_session.assert_not_called()
    assert operator_timeouts
    assert all(timeout is None for timeout in operator_timeouts)
    assert operator_retries
    assert all(retries == 0 for retries in operator_retries)
    assert operator_recovery_waits == [CUSTOM_OP_OPERATOR_POLL_TIMEOUT_DEFAULT] * 2


def test_custom_op_operator_terminal_fail_closed_reports_continue_same_session_by_default(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_prompts: list[str] = []

    def write_incomplete_gate() -> None:
        payload = _custom_op_gate_payload()
        payload["status"] = "FAIL"
        payload["full_migration_status"] = "INCOMPLETE"
        payload["closed_pass_entries"] = 0
        payload["remaining_entries"] = 2
        payload["rows"] = []
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(payload), encoding="utf-8")
        (reports_dir / "summary.json").write_text(
            json.dumps({
                "status": "INCOMPLETE",
                "full_migration_status": "INCOMPLETE",
                "manifest_entries": 2,
                "closed_pass_entries": 0,
                "remaining_entries": 2,
                "blocking_gaps": ["route evidence has no positive custom call count"],
            }),
            encoding="utf-8",
        )

    def send_command(session_id: str, prompt: str, timeout: int | None = None, retries: int | None = None, recovery_wait_timeout: int | None = None) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_prompts.append(prompt)
        if len(operator_prompts) == 1:
            write_incomplete_gate()
            return json.dumps({
                "status": "success",
                "summary": "Generated fail-closed reports for remaining custom-op route coverage",
                "modified_files": [str(reports_dir / "custom_op_final_gate.json")],
            })
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        return json.dumps({
            "summary": "FULL_PASS after continuing same operator repair session",
            "verification": {"result": {"status": "PASS"}},
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {
        "phase_1_project_analysis": {"operator_unit_count": 2},
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
            "operator_inventory_schema": {"fine_grained_operator_units": ["op:forward", "op:backward"]},
        },
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert len(operator_prompts) == 2
    assert "Continue the same custom-op `fix_operator` repair" in operator_prompts[1]
    assert result["loop_state"]["fix_operator"]["custom_op_final_gate_recovered"] is True


def test_custom_op_operator_terminal_fail_closed_reports_continue_same_session_when_configured(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    operator_prompts: list[str] = []

    def write_incomplete_gate() -> None:
        payload = _custom_op_gate_payload()
        payload["status"] = "FAIL"
        payload["full_migration_status"] = "INCOMPLETE"
        payload["closed_pass_entries"] = 0
        payload["remaining_entries"] = 2
        payload["rows"] = []
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(payload), encoding="utf-8")
        (reports_dir / "summary.json").write_text(
            json.dumps({
                "status": "INCOMPLETE",
                "full_migration_status": "INCOMPLETE",
                "manifest_entries": 2,
                "closed_pass_entries": 0,
                "remaining_entries": 2,
                "blocking_gaps": ["route evidence has no positive custom call count"],
            }),
            encoding="utf-8",
        )

    def send_command(
        session_id: str,
        prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_prompts.append(prompt)
        if len(operator_prompts) == 1:
            write_incomplete_gate()
            return json.dumps({
                "status": "success",
                "summary": "Generated fail-closed reports for remaining custom-op route coverage",
                "modified_files": [str(reports_dir / "custom_op_final_gate.json")],
            })
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        return json.dumps({
            "summary": "FULL_PASS after continuing same operator repair session",
            "verification": {"result": {"status": "PASS"}},
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(
        tmp_path,
        session_mgr,
        framework_config={"custom_op_operator_incomplete_max_continuations": 1},
    )
    state = {
        "phase_1_project_analysis": {"operator_unit_count": 2},
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
            "operator_inventory_schema": {"fine_grained_operator_units": ["op:forward", "op:backward"]},
        },
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert len(operator_prompts) == 2
    assert operator_prompts[0] == "repair_operator_fixer"
    assert "Continue the same custom-op `fix_operator` repair" not in operator_prompts[0]
    continuation = operator_prompts[1]
    assert "Custom-op operator repair progress" in continuation
    assert "total_target_operator_variant_inventory: 2" in continuation
    assert "all_target_operators_and_variants: op:forward, op:backward" in continuation
    assert "completed_evidence_count: 0" in continuation
    assert "remaining_operator_variant_gaps: op:forward, op:backward" in continuation
    assert "Current fail-closed report summary" in continuation
    assert "route evidence has no positive custom call count" in continuation
    assert "Phase 1 source discovery plus the Phase 3 entry-script contract" in continuation
    assert "repair every source-discovered operator" in continuation
    assert "operatorRepairContext" in continuation


def test_custom_op_outer_loop_prompt_requires_prior_operator_attempt(tmp_path: Path) -> None:
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
        }
    }

    assert executor._should_use_custom_op_operator_outer_loop_prompt(
        phase_id="fix_operator",
        state=state,
        loop_history=[{"iteration": 1, "step_outputs_summary": {"fix_code": "dict"}}],
        loop_state={"fix_code": {"summary": "previous code repair"}},
    ) is False
    assert executor._should_use_custom_op_operator_outer_loop_prompt(
        phase_id="fix_operator",
        state=state,
        loop_history=[{"iteration": 1, "step_outputs_summary": {"fix_operator": "dict"}}],
        loop_state={"fix_operator": {"summary": "previous operator repair"}},
    ) is True


def test_custom_op_operator_outer_loop_second_attempt_uses_compact_progress_prompt(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    operator_prompts: list[str] = []

    def write_partial_gate() -> None:
        payload = _custom_op_gate_payload()
        payload["status"] = "FAIL"
        payload["full_migration_status"] = "INCOMPLETE"
        payload["inventory_count"] = 2
        payload["closed_pass_entries"] = 1
        payload["remaining_entries"] = 1
        payload["rows"] = [{"unit_identity": "op:forward", "status": "PASS"}]
        payload["blocking_gaps"] = ["op:backward missing same-run runtime coverage"]
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(payload), encoding="utf-8")
        (reports_dir / "summary.json").write_text(
            json.dumps({
                "status": "INCOMPLETE",
                "closed_pass_entries": 1,
                "remaining_entries": 1,
                "blocking_gaps": ["op:backward missing OPP artifact evidence"],
            }),
            encoding="utf-8",
        )

    def send_command(
        session_id: str,
        prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_prompts.append(prompt)
        if len(operator_prompts) == 1:
            write_partial_gate()
            return json.dumps({
                "status": "FAILED",
                "repair_status": "INCOMPLETE",
                "fixed": False,
                "summary": "one custom-op unit still lacks strict evidence",
            })
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        return json.dumps({"summary": "FULL_PASS after compact outer-loop repair"})

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(
        tmp_path,
        session_mgr,
        framework_config={"custom_op_operator_incomplete_max_continuations": 0},
        max_iterations=2,
    )
    state = {
        "phase_1_project_analysis": {"operator_unit_count": 2},
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
            "operator_inventory_schema": {"fine_grained_operator_units": ["op:forward", "op:backward"]},
        },
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] in {"failure", "success"}
    assert len(operator_prompts) == 2
    assert operator_prompts[0] == "repair_operator_fixer"
    second_prompt = operator_prompts[1]
    assert second_prompt != "repair_operator_fixer"
    assert "later outer repair-loop iteration" in second_prompt
    assert "Previous repair results/history" in second_prompt
    assert "total_target_operator_variant_inventory: 2" in second_prompt
    assert "completed_evidence_count: 1" in second_prompt
    assert "completed_evidence_units: op:forward" in second_prompt
    assert "remaining_operator_variant_gaps: op:backward" in second_prompt
    assert "op:backward missing same-run runtime coverage" in second_prompt
    assert "repair_operator_fixer prompt" in second_prompt


def test_custom_op_operator_continuation_prompt_reports_expanded_variant_progress_from_partial_gate(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    partial_gate = {
        "status": "FAIL",
        "full_migration_status": "INCOMPLETE",
        "inventory_count": 2,
        "closed_pass_entries": 1,
        "remaining_entries": 1,
        "rows": [
            {"unit_identity": "op:forward:dtype=float", "status": "PASS"},
        ],
        "blocking_gaps": ["op:forward:dtype=double missing runtime coverage"],
    }
    (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(partial_gate), encoding="utf-8")
    (reports_dir / "summary.json").write_text(
        json.dumps({
            "status": "INCOMPLETE",
            "closed_pass_entries": 1,
            "remaining_entries": 1,
            "blocking_gaps": ["op:forward:dtype=double missing OPP artifact evidence"],
        }),
        encoding="utf-8",
    )
    executor = _workflow_executor_for_custom_op_gate(tmp_path)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate_custom_ops_full.py",
            "reports_dir": str(reports_dir),
            "expanded_variant_inventory": {
                "variant_axes_detected": True,
                "expanded_operator_instances_count": 2,
                "unit_identities": ["op:forward:dtype=float", "op:forward:dtype=double"],
            },
        },
    }

    prompt = executor._custom_op_operator_continuation_prompt(
        incomplete_error="strict final gate is not FULL_PASS",
        raw_response=json.dumps({"status": "INCOMPLETE"}),
        continuation_index=1,
        max_continuations=1,
        state=state,
        context={},
        loop_vars={"project_dir": str(tmp_path)},
    )

    assert "target_inventory_source: Phase 1/Phase 3 expanded_variant_inventory.operator+variant unit_identities" in prompt
    assert "total_target_operator_variant_inventory: 2" in prompt
    assert "all_target_operators_and_variants: op:forward:dtype=float, op:forward:dtype=double" in prompt
    assert "completed_evidence_count: 1" in prompt
    assert "completed_evidence_units: op:forward:dtype=float" in prompt
    assert "remaining_or_unknown_count: 1" in prompt
    assert "remaining_operator_variant_gaps: op:forward:dtype=double" in prompt
    assert "custom_op_final_gate_report: status=FAIL, full_migration_status=INCOMPLETE" in prompt
    assert "op:forward:dtype=double missing runtime coverage" in prompt
    assert "op:forward:dtype=double missing OPP artifact evidence" in prompt


def test_custom_op_operator_nested_failed_summary_continues_same_session_by_default(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_prompts: list[str] = []
    operator_timeouts: list[int | None] = []

    def send_command(
        session_id: str,
        prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_prompts.append(prompt)
        operator_timeouts.append(timeout)
        if len(operator_prompts) == 1:
            return json.dumps({
                "modified_files": [str(tmp_path / "test_pointnet2.py")],
                "summary": "FAILED/INCOMPLETE: malformed test fixed but active custom-op contract still fails.",
                "agent_diagnostics": {
                    "validation": {
                        "validate_custom_ops_full.py": "FAIL",
                        "current_runtime_blocker": "ModuleNotFoundError: No module named 'pointnet2_ops._ext'",
                        "missing_reports": [
                            "migration_manifest.json",
                            "performance.json",
                            "custom_op_final_gate.json",
                        ],
                    }
                },
            })
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        return json.dumps({
            "summary": "FULL_PASS after continuing same operator repair session",
            "verification": {"result": {"status": "PASS"}},
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert len(operator_prompts) == 2
    assert "Continue the same custom-op `fix_operator` repair" in operator_prompts[1]
    assert operator_timeouts == [None, None]
    assert result["loop_state"]["fix_operator"]["custom_op_final_gate_recovered"] is True
    session_mgr.create_session.assert_not_called()


def test_custom_op_operator_nested_failed_summary_continues_same_session_when_configured(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_prompts: list[str] = []
    operator_timeouts: list[int | None] = []

    def send_command(
        session_id: str,
        prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_prompts.append(prompt)
        operator_timeouts.append(timeout)
        if len(operator_prompts) == 1:
            return json.dumps({
                "modified_files": [str(tmp_path / "test_pointnet2.py")],
                "summary": "FAILED/INCOMPLETE: malformed test fixed but active custom-op contract still fails.",
                "agent_diagnostics": {
                    "validation": {
                        "validate_custom_ops_full.py": "FAIL",
                        "current_runtime_blocker": "ModuleNotFoundError: No module named 'pointnet2_ops._ext'",
                        "missing_reports": [
                            "migration_manifest.json",
                            "performance.json",
                            "custom_op_final_gate.json",
                        ],
                    }
                },
            })
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        return json.dumps({
            "summary": "FULL_PASS after continuing same operator repair session",
            "verification": {"result": {"status": "PASS"}},
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(
        tmp_path,
        session_mgr,
        framework_config={"custom_op_operator_incomplete_max_continuations": 1},
    )
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert len(operator_prompts) == 2
    assert "Continue the same custom-op `fix_operator` repair" in operator_prompts[1]
    assert "Previous incomplete response excerpt" in operator_prompts[1]
    assert operator_timeouts == [None, None]
    session_mgr.create_session.assert_not_called()


def test_custom_op_operator_prompt_context_includes_phase1_phase3_full_repair_scope(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    prompt_contexts: dict[str, dict[str, object]] = {}

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        return json.dumps({
            "status": "FAILED",
            "repair_status": "INCOMPLETE",
            "fixed": False,
            "summary": "OPP artifacts are still missing",
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr, prompt_contexts=prompt_contexts)
    state = {
        "phase_1_project_analysis": {
            "operator_unit_count": 2,
            "custom_op_surface": {
                "custom_op_detected": True,
                "variant_axes_detected": True,
                "expanded_operator_instances_count": 2,
                "fine_grained_operator_units": ["op:forward", "op:backward"],
                "expanded_operator_variants": [
                    {"unit_identity": "op:forward:dtype=float"},
                    {"unit_identity": "op:forward:dtype=double"},
                ],
                "variant_axes": {"dtype": ["float", "double"]},
            },
        },
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate_custom_ops_full.py",
            "reports_dir": str(reports_dir),
            "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            "required_checks": ["all variants close"],
            "operator_inventory_schema": {"fine_grained_operator_units": ["op:forward", "op:backward"]},
            "expanded_variant_inventory": {
                "variant_axes_detected": True,
                "expanded_operator_instances_count": 2,
                "unit_identities": ["op:forward:dtype=float", "op:forward:dtype=double"],
            },
        },
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate_custom_ops_full.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    ctx = prompt_contexts["repair_operator_fixer"]
    scope = str(ctx["phase1_phase3_repair_scope"])
    acceptance = str(ctx["strict_custom_op_acceptance_contract"])
    assert "workflow_route=custom_op_with_variants" in scope
    assert "phase3.run_command=python validate_custom_ops_full.py" in scope
    assert "phase1.custom_op_surface.expanded_operator_variants_count=2" in scope
    assert "op:forward:dtype=float" in scope
    assert "op:forward:dtype=double" in scope
    assert "migration_reports/custom_op_final_gate.json" in acceptance
    assert "all variants close" in acceptance
    assert "validate_custom_op_final_gate" in acceptance
    assert "operatorRepairContext" in str(ctx["operator_custom_op_guidance"])
    progress = str(ctx["operator_repair_progress_block"])
    assert "total_target_operator_variant_inventory: 2" in progress
    assert "all_target_operators_and_variants: op:forward:dtype=float, op:forward:dtype=double" in progress
    assert "remaining_operator_variant_gaps: op:forward:dtype=float, op:forward:dtype=double" in progress


def test_operator_fix_remote_closed_recovers_at_loop_boundary_from_valid_reports(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        raise RuntimeError("POST /session/abc/message failed: Remote end closed connection without response")

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    assert result["loop_state"]["fix_operator"]["custom_op_final_gate_recovered"] is True


def test_operator_fix_spoofed_final_gate_recovery_without_valid_gate_fails_closed(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        return json.dumps({
            "custom_op_final_gate_recovered": True,
            "custom_op_final_gate": {"passed": True},
            "summary": "claimed recovered final gate",
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["communication_error"] is True
    assert "did not produce current custom_op_final_gate.json" in fix_output["error"]


def test_operator_fix_full_pass_claim_without_valid_reports_fails_closed(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = timeout, retries, recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        return json.dumps({
            "status": "success",
            "summary": "FULL_PASS after repair",
            "verification": {"result": {"status": "PASS"}},
            "modified_files": [str(tmp_path / "adapter.py")],
        })

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["communication_error"] is True
    assert "did not produce current custom_op_final_gate.json" in fix_output["error"]
    assert "custom_op_final_gate_recovered" not in fix_output


@pytest.mark.parametrize("gate_mode", ["missing", "invalid", "stale"])
def test_operator_fix_no_response_without_current_valid_gate_stays_retryable(tmp_path: Path, gate_mode: str) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    if gate_mode == "stale":
        stale_gate = reports_dir / "custom_op_final_gate.json"
        stale_gate.write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
        os.utime(stale_gate, (1, 1))
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    operator_timeouts: list[int | None] = []
    operator_retries: list[int | None] = []

    def send_command(
        session_id: str,
        _prompt: str,
        timeout: int | None = None,
        retries: int | None = None,
        recovery_wait_timeout: int | None = None,
    ) -> str:
        _ = recovery_wait_timeout
        if session_id == "session:error_analyzer":
            return json.dumps({
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "strict OPP final gate missing",
                "suggested_fix": "finish custom-op gate",
            })
        operator_timeouts.append(timeout)
        operator_retries.append(retries)
        if gate_mode == "invalid":
            payload = _custom_op_gate_payload()
            payload["full_migration_status"] = "PARTIAL"
            (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(payload), encoding="utf-8")
        return '{"ok": false, "error": "Session still running with no response"}'

    session_mgr.send_command.side_effect = send_command
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation", "run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    fix_output = result["loop_state"]["fix_operator"]
    assert fix_output["communication_error"] is True
    assert fix_output["retryable"] is True
    assert "custom_op_final_gate_recovered" not in fix_output
    assert operator_timeouts
    assert all(timeout is None for timeout in operator_timeouts)
    assert operator_retries
    assert all(retries == 0 for retries in operator_retries)


def test_non_custom_operator_no_response_does_not_recover_from_gate_file(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_payload()), encoding="utf-8")
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.create_session.return_value = "session:operator_fixer_retry"
    session_mgr.send_command.side_effect = [
        json.dumps({
            "repair_role": "operator_fixer",
            "category": "operator",
            "root_cause": "ordinary operator failure",
            "suggested_fix": "rewrite op",
        }),
        '{"ok": false, "error": "Session still running with no response"}',
        '{"ok": false, "error": "Session still running with no response"}',
    ]
    executor = _operator_recovery_executor(tmp_path, session_mgr)
    state = {"phase_3_entry_script": {"run_command": "python validate.py"}}

    result = executor._execute_loop_phase(
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={"entry_script": "python validate.py", "project_dir": str(tmp_path)},
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["fix_operator"]["communication_error"] is True


class FakePhase7SessionManager:
    def __init__(self, response: dict[str, object]):
        self.response = response
        self.created_roles = []
        self.sent = []

    def get_or_create(self, role: str, lifecycle: str) -> str:
        self.created_roles.append((role, lifecycle))
        return f"session:{role}"

    def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
        self.sent.append({"session_id": session_id, "command": command, "timeout": timeout})
        return json.dumps(self.response)


def test_phase7a_orchestration_uses_artifact_backed_evaluator_and_persists_candidates(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "model.py").write_text("import torch\nprint('npu fix')\n", encoding="utf-8")

    artifact_store = ArtifactStore(str(tmp_path), "run-1")
    Path(artifact_store.validated_dir, "phase_1_project_analysis_canonical.json").write_text(json.dumps({
        "project_dir": str(project_root),
        "dependencies": ["torch"],
        "unique_project_marker": "artifact-project-context",
    }), encoding="utf-8")
    Path(artifact_store.validated_dir, "phase_5_validation_canonical.json").write_text(json.dumps({
        "final_status": "success",
        "unique_validation_marker": "artifact-validation-context",
    }), encoding="utf-8")
    Path(artifact_store.raw_dir, "phase_run_entry_script_attempt1.json").write_text(json.dumps({
        "stderr": "missing torch_npu before fix",
        "unique_raw_marker": "artifact-raw-context",
    }), encoding="utf-8")
    Path(artifact_store.journal_path).write_text(
        json.dumps({"phase_id": "phase_5_validation", "unique_journal_marker": "artifact-journal-context"}) + "\n",
        encoding="utf-8",
    )

    store = ExperienceStore(str(tmp_path))
    session_mgr = FakePhase7SessionManager({
        "evaluation_summary": "Found dependency pattern",
        "project_source_root": str(project_root),
        "candidates": [{
            "title": "Install torch-npu after CPU torch",
            "problem_description": "Generic dependency fix",
            "rough_fix_approach": "Pin CPU torch then install torch-npu",
            "artifact_evidence": ["validated/phase_5_validation_canonical.json", "raw/phase_run_entry_script_attempt1.json"],
            "involved_code_files": [{"path": "model.py", "role": "entry"}],
            "recommended_type": "skill",
            "category": "dependency",
            "subtype": "torch_npu_install",
            "tags": ["torch-npu", "pip"],
            "confidence": 0.92,
        }],
    })
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase7", version="1.0", phases=[], terminals=[]),
        session_mgr,
        artifact_store,
        MagicMock(),
        MagicMock(),
        project_dir=str(project_root),
        output_dir=str(tmp_path),
        experience_store=store,
    )
    phase = PhaseDefinition(
        id="phase_7a_evaluate",
        name="Evaluate",
        prompt_template="",
        output_schema={},
        type="orchestration",
        handler="experience_evaluator.ExperienceEvaluator.evaluate",
    )

    result = executor._execute_orchestration_phase(phase, {}, {})

    assert result["status"] == "success"
    assert result["total_candidates"] == 1
    sent_prompt = session_mgr.sent[0]["command"]
    assert "artifact-project-context" in sent_prompt
    assert "artifact-validation-context" in sent_prompt
    assert "artifact-raw-context" in sent_prompt
    assert "artifact-journal-context" in sent_prompt
    candidates = store.read_candidates("run-1")
    assert candidates[0]["candidate_id"] == "candidate-001"
    assert candidates[0]["project_source_root"] == str(project_root)
    assert (tmp_path / ".memory" / "memory" / "staging" / "run-1" / "evaluation_summary.md").read_text(encoding="utf-8") == "Found dependency pattern"


def test_phase7b_orchestration_refines_candidates_and_updates_catalog_manifest(tmp_path: Path):
    artifact_store = ArtifactStore(str(tmp_path), "run-1")
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({
        "id": "run-0-exp-existing",
        "type": "skill",
        "status": "staging",
        "category": "dependency",
        "subtype": "torch_npu_install",
        "tags": ["torch-npu", "pip"],
        "title": "Existing torch-npu install fix",
        "confidence": 0.7,
    })
    store.write_candidate("run-1", "candidate-001", {
        "candidate_id": "candidate-001",
        "skill_name": "torch-npu-install-order",
        "title": "Install torch-npu after CPU torch",
        "problem_description": "torch-npu dependency resolution failed",
        "rough_fix_approach": "Install CPU torch first, then torch-npu",
        "recommended_type": "skill",
        "category": "dependency",
        "subtype": "torch_npu_install",
        "tags": ["torch-npu", "pip"],
        "confidence": 0.95,
        "fix_steps": ["Install CPU torch before torch-npu"],
    })
    executor = WorkflowExecutor(
        WorkflowDefinition(name="phase7", version="1.0", phases=[], terminals=[]),
        None,
        artifact_store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=store,
    )
    phase = PhaseDefinition(
        id="phase_7b_refine",
        name="Refine",
        prompt_template="",
        output_schema={},
        type="orchestration",
        handler="experience_dispatcher.ExperienceDispatcher.dispatch_and_refine",
    )

    result = executor._execute_orchestration_phase(phase, {}, {})

    assert result["status"] == "success"
    assert result["refined_experiences"][0]["type"] == "skill"
    catalog = store.read_catalog()
    assert catalog[0]["id"] == "promoted-torch-npu-install-order"
    assert catalog[0]["target_roles"] == ["dependency_fixer"]
    assert catalog[0]["target_phases"] == ["phase_2_venv_create", "phase_5_validation"]
    manifest = json.loads(Path(store.manifest_path).read_text(encoding="utf-8"))
    assert manifest["counts"]["by_status"] == {"promoted": 1}
    legacy_statuses = {entry["id"]: entry["status"] for entry in store.read_index()}
    assert legacy_statuses["promoted-torch-npu-install-order"] == "promoted"
    assert legacy_statuses["run-0-exp-existing"] == "consumed"


def test_runtime_skill_repo_root_relative_path_resolves_against_execution_root(tmp_path: Path) -> None:
    from core.paths import execution_root

    skill_root_name = "__relative_runtime_skills__"
    skill_repo_root = execution_root() / skill_root_name
    write_runtime_skill(skill_repo_root, "agent-skill", "# Agent Skill\n\nAgent guidance")
    try:
        phase = PhaseDefinition(
            id="phase_runtime",
            name="Runtime",
            prompt_template="runtime_prompt",
            output_schema={},
            type="llm",
            agent="main_engineer",
            runtime_skills=RuntimeSkillsConfig(include=["agent-skill"], inject_full=True),
        )
        workflow = WorkflowDefinition(
            name="runtime_test",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
        )
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator_engine = MagicMock()
        session_mgr.get_or_create.return_value = "session_123"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader.load_prompt.return_value = "BASE PROMPT"
        executor = WorkflowExecutor(
            workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator_engine,
            framework_config={"runtime_skill_repo_root": skill_root_name},
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )

        old_cwd = os.getcwd()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        os.chdir(cwd)
        try:
            _ = executor._execute_llm_phase(phase, {}, {})
        finally:
            os.chdir(old_cwd)

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert "### agent-skill" in sent_prompt
        assert "Agent guidance" in sent_prompt
    finally:
        import shutil

        shutil.rmtree(skill_repo_root, ignore_errors=True)
