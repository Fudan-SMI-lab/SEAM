# pylint: disable=too-many-lines; silent
"""Mock-based tests for WorkflowExecutor."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from core.artifact_store import ArtifactStore
from core.config import load_workflow
from core.execution_backend import ContainerBackend
from core.experience_store import ExperienceStore
from core.prompt_loader import PromptLoader
from core.telemetry_bridge import TelemetryBridge
from core.types import (
    ExecutionBackendConfig,
    ExperienceConfig,
    PhaseDefinition,
    RuntimeSkillsConfig,
    SubWorkflowDefinition,
    TransitionDefinition,
    WorkflowDefinition,
)
from core.validator_engine import ValidatorEngine
from core.workflow_executor import WorkflowExecutor
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_entry_static import validate as validate_entry_static


def write_runtime_skill(root: Path, name: str, content: str | None = None) -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content or f"# {name}\n\nUse this guidance.", encoding="utf-8")
    return skill_path


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d


@pytest.fixture
def basic_workflow(temp_dir):  # pylint: disable=redefined-outer-name,unused-argument; silent
    return WorkflowDefinition(
        name="test",
        version="1.0",
        phases=[
            PhaseDefinition(
                id="phase_a",
                name="A",
                prompt_template="test.md",
                output_schema={},
                type="llm",
                agent="main_engineer",
                validator=None,
                transitions={"on_success": "phase_b"},
            ),
            PhaseDefinition(
                id="phase_b",
                name="B",
                prompt_template="test.md",
                output_schema={},
                type="llm",
                agent="main_engineer",
                validator=None,
                transitions={"on_success": "complete"},
            ),
        ],
        terminals=["complete", "failed"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )


@pytest.fixture
def executor(basic_workflow, temp_dir):  # pylint: disable=redefined-outer-name; silent
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator_engine = MagicMock()
    return WorkflowExecutor(
        basic_workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator_engine,
        project_dir=temp_dir,
        output_dir=temp_dir,
    )


class TestWorkflowExecutorInit:
    def test_constructor(self, executor):  # pylint: disable=redefined-outer-name; silent
        assert executor.workflow.name == "test"
        assert executor.state == {}
        assert executor.phase_results == {}

    def test_phase_index_built(self, executor):  # pylint: disable=redefined-outer-name; silent
        assert "phase_a" in executor.phase_index
        assert "phase_b" in executor.phase_index
        assert executor.phase_index["phase_a"] == 0


class TestExecute:  # pylint: disable=too-few-public-methods; silent
    # pylint: disable-next=redefined-outer-name; silent
    def test_basic_execute_flow(self, executor, temp_dir):
        executor.hook_manager = MagicMock()

        result = executor.execute({"PROJECT_DIR": temp_dir})
        assert isinstance(result, dict)


class TestConditionEvaluation:
    def test_condition_true(self, executor):  # pylint: disable=redefined-outer-name; silent
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            "${context.X} != ''",
            state={},
            context={"X": "abc"},
        )
        assert result is True

    def test_condition_false(self, executor):  # pylint: disable=redefined-outer-name; silent
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            "$.X == ''",
            state={},
            context={},
            loop_state={"X": ""},
        )
        assert result is True

    # pylint: disable-next=redefined-outer-name; silent
    def test_condition_dollar_shorthand(self, executor):
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            "$.exit_code == 0",
            state={},
            context={},
            loop_state={"exit_code": 0},
        )
        assert result is True

    # pylint: disable-next=redefined-outer-name; silent
    def test_condition_and_operator(self, executor):
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            "$.a == 1 and $.b == 2",
            state={},
            context={},
            loop_state={"a": 1, "b": 2},
        )
        assert result is True

    def test_condition_or_operator(self, executor):  # pylint: disable=redefined-outer-name; silent
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            "$.a == 1 or $.b == 2",
            state={},
            context={},
            loop_state={"a": 0, "b": 2},
        )
        assert result is True

    # pylint: disable-next=redefined-outer-name; silent
    def test_condition_not_operator(self, executor):
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            "not $.failed",
            state={},
            context={},
            loop_state={"failed": False},
        )
        assert result is True


class TestResolveInputMapping:  # pylint: disable=too-few-public-methods; silent
    def test_basic_mapping(self, executor):  # pylint: disable=redefined-outer-name; silent
        phase = PhaseDefinition(
            id="test",
            name="test",
            prompt_template="x",
            output_schema={},
            input_mapping={"project": "${context.PROJECT_DIR}", "max": "${globals.max}"},
        )
        result = executor._resolve_input_mapping(  # pylint: disable=protected-access; silent
            phase,
            state={},
            context={"PROJECT_DIR": "/tmp/test"},
            loop_vars=None,
            loop_state=None,
            loop_history=None,
            step_outputs=None,
        )
        assert result["project"] == "/tmp/test"
        executor.workflow.globals = {"max": 5}
        result = executor._resolve_input_mapping(  # pylint: disable=protected-access; silent
            phase,
            state={},
            context={"PROJECT_DIR": "/tmp/test"},
            loop_vars=None,
            loop_state=None,
            loop_history=None,
            step_outputs=None,
        )
        assert result["max"] == 5


class TestTransitionResolution:
    def test_on_success(self, executor):  # pylint: disable=redefined-outer-name; silent
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"success": "b", "failure": "fail"},
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == "b"

    def test_on_failure(self, executor):  # pylint: disable=redefined-outer-name; silent
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"success": "b", "failure": "error_recovery"},
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "failure", {}, {})
        assert next_id == "error_recovery"

    # pylint: disable-next=redefined-outer-name; silent
    def test_yaml_shaped_transition_keys(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={
                "on_success": "b",
                "on_failure": "error_recovery",
                "on_skip": "skip_target",
            },
        )
        # pylint: disable-next=protected-access; silent
        assert executor._get_next_phase_id(phase, "success", {}, {}) == "b"
        # pylint: disable-next=protected-access; silent
        assert executor._get_next_phase_id(phase, "failure", {}, {}) == "error_recovery"
        # pylint: disable-next=protected-access; silent
        assert executor._get_next_phase_id(phase, "skipped", {}, {}) == "skip_target"

    def test_default_next(self, executor):  # pylint: disable=redefined-outer-name; silent
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == executor.workflow.phases[1].id

    # pylint: disable-next=redefined-outer-name; silent
    def test_failure_without_transition_stops(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "failure", {}, {})

        assert next_id is None

    # pylint: disable-next=redefined-outer-name; silent
    def test_failure_with_only_success_transition_stops(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"on_success": "b"},
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "failure", {}, {})

        assert next_id is None

    # pylint: disable-next=redefined-outer-name; silent
    def test_skipped_without_transition_still_defaults_next(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "skipped", {}, {})

        assert next_id == executor.workflow.phases[1].id

    # pylint: disable-next=redefined-outer-name; silent
    def test_stagnation_without_routing_terminates(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "stagnation", {}, {})

        assert next_id is None

    # pylint: disable-next=redefined-outer-name; silent
    def test_reject_exhausted_without_routing_terminates(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "reject_exhausted", {}, {})

        assert next_id is None

    # pylint: disable-next=redefined-outer-name; silent
    def test_arbitrary_non_success_status_terminates(self, executor):
        phase = PhaseDefinition(id="a", name="A", prompt_template="x", output_schema={})
        executor.phase_index = {"a": 0}

        for status in ("accept", "unknown_fail", "stagnation", "reject_exhausted"):
            # pylint: disable-next=protected-access; silent
            next_id = executor._get_next_phase_id(phase, status, {}, {})
            assert next_id is None, f"{status} should terminate, not fall through"

    # pylint: disable-next=redefined-outer-name; silent
    def test_stagnation_explicit_dict_routing_honored(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"stagnation": "error_recovery"},
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "stagnation", {}, {})

        assert next_id == "error_recovery"

    # pylint: disable-next=redefined-outer-name; silent
    def test_reject_exhausted_explicit_dict_routing_honored(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"reject_exhausted": "review_cleanup"},
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "reject_exhausted", {}, {})

        assert next_id == "review_cleanup"

    # pylint: disable-next=redefined-outer-name; silent
    def test_on_stagnation_yaml_dict_routing_honored(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"on_stagnation": "stagnation_recovery"},
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "stagnation", {}, {})

        assert next_id == "stagnation_recovery"

    # pylint: disable-next=redefined-outer-name; silent
    def test_on_reject_exhausted_yaml_dict_routing_honored(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transitions={"on_reject_exhausted": "review_cleanup"},
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "reject_exhausted", {}, {})

        assert next_id == "review_cleanup"

    # pylint: disable-next=redefined-outer-name; silent
    def test_transition_definition_on_stagnation_honored(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transition=TransitionDefinition(on_stagnation="stagnation_recovery"),
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "stagnation", {}, {})

        assert next_id == "stagnation_recovery"

    # pylint: disable-next=redefined-outer-name; silent
    def test_transition_definition_on_reject_exhausted_honored(self, executor):
        phase = PhaseDefinition(
            id="a",
            name="A",
            prompt_template="x",
            output_schema={},
            transition=TransitionDefinition(on_reject_exhausted="exhausted_cleanup"),
        )
        executor.phase_index = {"a": 0}

        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "reject_exhausted", {}, {})

        assert next_id == "exhausted_cleanup"


class TestShellPhase:
    # pylint: disable-next=redefined-outer-name,unused-argument; silent
    def test_shell_success(self, executor, temp_dir):
        phase = PhaseDefinition(
            id="shell",
            name="S",
            prompt_template="",
            output_schema={},
            type="shell",
            on_failure="continue",
        )
        setattr(phase, "command", "echo hello")

        state = {}
        loop_state = {}
        # pylint: disable-next=protected-access,unused-variable; silent
        status, output = executor._execute_shell_phase(phase, state, {}, loop_state=loop_state)

        assert status == "success"
        assert loop_state.get("script_exit_code") == 0

    # pylint: disable-next=redefined-outer-name,unused-argument; silent
    def test_shell_failure_continue(self, executor, temp_dir):
        phase = PhaseDefinition(
            id="shell",
            name="S",
            prompt_template="",
            output_schema={},
            type="shell",
            on_failure="continue",
        )
        setattr(phase, "command", "exit 1")

        # pylint: disable-next=protected-access,unused-variable; silent
        status, output = executor._execute_shell_phase(phase, {}, {}, loop_state={})
        assert status == "success"


class TestStagnation:
    def test_detect_same_error(self, executor):  # pylint: disable=redefined-outer-name; silent
        loop_state = {}
        error = "Error: module not found\n  at line 1"

        # pylint: disable-next=protected-access; silent
        stagnated = executor._check_stagnation(error, loop_state, threshold=3)
        assert not stagnated
        assert loop_state["stagnation_count"] == 1

        # pylint: disable-next=protected-access; silent
        stagnated = executor._check_stagnation(error, loop_state, threshold=3)
        assert not stagnated
        assert loop_state["stagnation_count"] == 2

        # pylint: disable-next=protected-access; silent
        stagnated = executor._check_stagnation(error, loop_state, threshold=3)
        assert stagnated
        assert loop_state["stagnation_count"] == 3

    # pylint: disable-next=redefined-outer-name; silent
    def test_reset_on_different_error(self, executor):
        loop_state = {}
        # pylint: disable-next=protected-access; silent
        executor._check_stagnation("Error: A", loop_state, threshold=3)
        assert loop_state["stagnation_count"] == 1

        # pylint: disable-next=protected-access; silent
        stagnated = executor._check_stagnation("Error: B", loop_state, threshold=3)
        assert not stagnated
        assert loop_state["stagnation_count"] == 1


class TestStopConditions:
    def test_stop_condition_match(self, executor):  # pylint: disable=redefined-outer-name; silent
        loop_state = {"exit_code": 0}
        stop_conds = [
            {"condition": "$.exit_code == 0", "status": "success"},
            {"condition": "$.exit_code != 0", "status": "failure"},
        ]
        # pylint: disable-next=protected-access; silent
        result = executor._check_stop_conditions(stop_conds, loop_state, {})
        assert result == "success"

    # pylint: disable-next=redefined-outer-name; silent
    def test_no_stop_condition_match(self, executor):
        loop_state = {"exit_code": 1}
        stop_conds = [
            {"condition": "$.exit_code == 0", "status": "success"},
        ]
        # pylint: disable-next=protected-access; silent
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
    # pylint: disable-next=redefined-outer-name; silent
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    # pylint: disable-next=protected-access; silent
    query_ctx = executor._build_experience_query_context(
        phase,
        state={},
        context={},
        step_outputs={"script_stderr": "direct failure text"},
        loop_history=[],
    )

    assert query_ctx["error_stderr"] == "direct failure text"


def test_experience_query_context_preserves_nested_run_entry_script_stderr(tmp_path: Path):
    # pylint: disable-next=redefined-outer-name; silent
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    # pylint: disable-next=protected-access; silent
    query_ctx = executor._build_experience_query_context(
        phase,
        state={},
        context={},
        step_outputs={"run_entry_script": {"stderr": "nested failure text"}},
        loop_history=[],
    )

    assert query_ctx["error_stderr"] == "nested failure text"


def test_experience_query_context_marks_native_custom_op_gate(tmp_path: Path):
    """Generic workflow name infers generic_accelerator policy."""
    # pylint: disable-next=redefined-outer-name; silent
    executor = _executor_for_experience_context(tmp_path)
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    # pylint: disable-next=protected-access; silent
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
    assert query_ctx["custom_op_evidence_policy"] == ("require_real_custom_op_artifacts")


def test_npu_workflow_keeps_legacy_custom_op_evidence_policy(tmp_path: Path):
    """Legacy NPU workflow name must still produce NPU-specific policy string."""
    workflow = WorkflowDefinition(name="npu_migration_v2", version="1.0", phases=[], terminals=[])
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        artifact_store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    # pylint: disable-next=protected-access; silent
    query_ctx = executor._build_experience_query_context(
        phase,
        state={
            "phase_3_entry_script": {
                "entry_script_kind": "custom_op_full_validation",
                "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            }
        },
        context={},
        step_outputs={},
        loop_history=[],
    )

    assert query_ctx["custom_op_native_gate_required"] == "true"
    assert query_ctx["custom_op_evidence_policy"] == (
        "require_real_ascend_cann_acl_opp_native_artifacts_no_aten_only"
    )


def test_ppu_workflow_gets_ppu_evidence_policy(tmp_path: Path):
    """PPU workflow name infers PPU policy string."""
    workflow = WorkflowDefinition(name="ppu_migration_v2", version="1.0", phases=[], terminals=[])
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        artifact_store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="phase_error_recovery",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )

    # pylint: disable-next=protected-access; silent
    query_ctx = executor._build_experience_query_context(
        phase,
        state={
            "phase_3_entry_script": {
                "entry_script_kind": "custom_op_full_validation",
                "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            }
        },
        context={},
        step_outputs={},
        loop_history=[],
    )

    assert query_ctx["custom_op_native_gate_required"] == "true"
    assert query_ctx["custom_op_evidence_policy"] == ("require_real_ppu_custom_op_artifacts")


class TestRuntimeSkillPromptAssembly:
    def _executor_for_runtime_skills(self, workflow, skill_root: Path, experience_store=None):
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator_engine = MagicMock()
        session_mgr.get_or_create.return_value = "session_123"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader.load_prompt.return_value = "BASE PROMPT"
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
        # pylint: disable-next=redefined-outer-name; silent
        executor, session_mgr, _prompt_loader = self._executor_for_runtime_skills(
            workflow, tmp_path
        )

        executor._execute_llm_phase(phase, {}, {})  # pylint: disable=protected-access; silent

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
                    "file_path": str(tmp_path / "skills" / "unique-skill" / "SKILL.md"),
                    "category": "dependency",
                    "subtype": "torch-npu",
                    "relevance_score": 0.85,
                },
            ],
            "summary": "keep summary",
            "warning": "keep warning",
        }
        # pylint: disable-next=redefined-outer-name; silent
        executor, session_mgr, _prompt_loader = self._executor_for_runtime_skills(
            workflow, tmp_path, experience_store=MagicMock()
        )
        # pylint: disable-next=protected-access; silent
        bundle = executor._resolve_runtime_skill_bundle(phase, "main_engineer")
        # pylint: disable-next=protected-access; silent
        filtered = executor._dedupe_dynamic_experiences(query_result, bundle, phase.id)
        assert filtered["summary"] == "keep summary"
        assert filtered["warning"] == "keep warning"
        assert [item["title"] for item in filtered["selected_experiences"]] == [
            "Dynamic Unique Guidance"
        ]
        assert len(query_result["selected_experiences"]) == 2

        with patch("core.experience_query.ExperienceQuerier.query", return_value=query_result):
            executor._execute_llm_phase(phase, {}, {})  # pylint: disable=protected-access; silent

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert "## Explicit Runtime Skills" in sent_prompt
        assert "### duplicate-skill" in sent_prompt
        assert "## Relevant Past Experiences" in sent_prompt
        assert "Dynamic Unique Guidance" in sent_prompt
        assert "Dynamic Duplicate Guidance" not in sent_prompt


def test_experience_action_cards_include_readable_paths():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.experience_injector import ExperienceInjector

    injected = ExperienceInjector().inject(
        None,
        {
            "selected_experiences": [
                {
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
                }
            ]
        },
    )

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
        # pylint: disable-next=line-too-long; silent
        '{"repair_role": "code_adapter", "category": "code", "root_cause": "cuda call", "suggested_fix": "use npu"}',
        '{"fixed": true}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, ctx: (
        f"{template}\n{ctx.get('experience_action_cards', '')}"
    )

    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
        "selected_experiences": [
            {
                "id": "code-exp",
                "type": "skill",
                "title": "CUDA Call Fix",
                "target_roles": ["code_adapter"],
                "target_phases": ["phase_5_validation"],
                "relevance_score": 0.88,
                "reasoning": "same cuda call",
                "file_path": str(tmp_path / "skills" / "cuda" / "SKILL.md"),
            }
        ],
        "summary": "selected",
        "warning": "",
    }

    with patch("core.experience_query.ExperienceQuerier.query", return_value=query_result):
        result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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


# pylint: disable-next=too-many-locals,too-many-statements; silent
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
        # pylint: disable-next=line-too-long; silent
        '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported custom op", "suggested_fix": "port custom op"}',
        '{"fixed": true}',
    ]
    real_loader = PromptLoader(Path(__file__).resolve().parent.parent / "prompts")

    def load_prompt(template: str, ctx: dict[str, str]) -> str:
        if template == "repair_operator_fixer":
            return real_loader.load_prompt(template, ctx)
        return template

    prompt_loader.load_prompt.side_effect = load_prompt
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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


def test_operator_fix_session_error_fails_subworkflow_without_validated_artifact(tmp_path: Path):
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
        # pylint: disable-next=line-too-long; silent
        '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported custom op", "suggested_fix": "port custom op"}',
        '{"ok": false, "error": "Compaction response is incomplete"}',
        '{"ok": false, "error": "Compaction response is incomplete"}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={"script_stderr": "RuntimeError: unsupported custom op"},
        loop_history=[],
        loop_state={},
    )

    assert result["status"] == "failure"
    assert result["step_outputs"]["repair_dispatch"]["dispatched_to"] == "fix_operator"
    assert "Compaction response is incomplete" in result["step_outputs"]["fix_operator"]["error"]
    saved_phase_ids = [call.args[0] for call in artifact_store.save_phase_output.call_args_list]
    validated_phase_ids = [call.args[0] for call in artifact_store.mark_validated.call_args_list]
    assert "fix_operator" not in saved_phase_ids
    assert "fix_operator" not in validated_phase_ids


def test_operator_fix_empty_response_retries_in_fresh_session(tmp_path: Path):
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
        # pylint: disable-next=line-too-long; silent
        '{"repair_role": "operator_fixer", "category": "operator", "root_cause": "unsupported custom op", "suggested_fix": "port custom op"}',
        '{"ok": false, "error": "Empty session response"}',
        '{"fixed": true, "used_experience_ids": [], "ignored_experience_ids": []}',
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
        sub_workflow,
        loop_vars={"entry_script": "python main.py"},
        state={},
        context={},
        sub_wf_phases=sub_workflow.phases,
        step_outputs={"script_stderr": "RuntimeError: unsupported custom op"},
        loop_history=[],
        loop_state={},
    )

    assert result["status"] == "success"
    assert result["step_outputs"]["fix_operator"]["fixed"] is True
    session_mgr.create_session.assert_called_once()
    called_sessions = [call.args[0] for call in session_mgr.send_command.call_args_list]
    assert called_sessions == [
        "session:error_analyzer",
        "session:operator_fixer",
        "session:operator_fixer_retry",
    ]


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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        framework_config=framework_config,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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


def test_fix_operator_without_explicit_timeout_uses_finite_default_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
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

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 30000
    log_text = caplog.text
    assert "phase_id=fix_operator" in log_text
    assert "agent_id=operator_fixer" in log_text
    assert "session_id=session:operator_fixer" in log_text
    assert "timeout=30000" in log_text
    assert "prompt_length=" in log_text
    assert "raw_response_length=" in log_text


def test_repair_subphase_uses_configured_session_timeout_repair(tmp_path: Path):
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

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 123


def test_invalid_repair_timeout_config_uses_default_and_logs_warning(
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

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 30000
    assert "Invalid session_timeout_repair" in caplog.text


def test_explicit_subphase_timeout_overrides_repair_default(tmp_path: Path):
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

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 77


def test_analyze_error_uses_configured_repair_timeout(tmp_path: Path):
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

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 123


def test_analyze_error_specific_timeout_overrides_repair_timeout(tmp_path: Path):
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

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 45


def test_analyze_error_without_explicit_timeout_uses_finite_default(tmp_path: Path):
    session_mgr = _run_single_llm_subphase(
        tmp_path,
        {
            "id": "analyze_error",
            "type": "llm",
            "prompt_template": "analyze_prompt",
            "agent": "error_analyzer",
        },
    )

    assert session_mgr.send_command.call_args.kwargs["timeout"] == 600


def test_non_repair_non_analyzer_subphase_timeout_remains_unbounded_without_explicit_timeout(
    tmp_path: Path,
):
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
        json.dumps(
            {
                "repair_role": "code_adapter",
                "category": "pathing",
                "root_cause": "stale Path.relative_to(PROJECT_DIR) failure",
                "suggested_fix": "adjust path handling",
            }
        ),
        json.dumps({"fixed": True}),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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
                # pylint: disable-next=line-too-long; silent
                "Custom-op final evidence gate failed: full_migration_status is FULL_MIGRATION_INCOMPLETE; "
                # pylint: disable-next=line-too-long; silent
                "closed_pass_entries=0; remaining_entries=4; custom_call_count_total=0; zero_call_detected=true"
            ),
        },
        loop_history=[
            {
                "iteration": 1,
                "status": "success",
                "error_category": "pathing",
                "repair_role": "code_adapter",
                # pylint: disable-next=line-too-long; silent
                "agent_diagnostics": "Remaining failure is custom-op/operator evidence incompleteness",
            }
        ],
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(name="plain_pathing", version="1.0", phases=[], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
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


def test_workflow_executor_disable_custom_op_injection_disables_force_routing(tmp_path: Path):
    phase = PhaseDefinition(
        id="analyze_error",
        name="Analyze",
        prompt_template="analyze_prompt",
        output_schema={},
        type="llm",
        agent="error_analyzer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="disabled_force_route",
            version="1.0",
            phases=[],
            terminals=[],
            globals={"disable_custom_op_contract_injection": True},
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
        phase,
        {
            "repair_role": "code_adapter",
            "category": "pathing",
            "root_cause": "custom-op final evidence gate failed",
            "suggested_fix": "fix path handling",
        },
        {
            # pylint: disable-next=line-too-long; silent
            "failure_log": "Custom-op final evidence gate failed: full_migration_status is FULL_MIGRATION_INCOMPLETE",
            "entry_script_contract": json.dumps({"entry_script_kind": "custom_op_full_validation"}),
            "previous_outputs": "",
        },
        {},
    )

    assert normalized["category"] == "pathing"
    assert normalized["repair_role"] == "code_adapter"


def test_phase5_entry_command_does_not_expand_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_script = tmp_path / "expanded_target.py"
    target_script.write_text(
        "from pathlib import Path\nPath('expanded-ran').write_text('yes')\n", encoding="utf-8"
    )
    monkeypatch.setenv("PY_SCRIPT", str(target_script))
    workflow = WorkflowDefinition(
        name="entry-no-shell-expansion",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    status, output = executor._execute_shell_phase(  # pylint: disable=protected-access; silent
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
        # pylint: disable-next=line-too-long; silent
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    status, output = executor._execute_shell_phase(  # pylint: disable=protected-access; silent
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
        # pylint: disable-next=line-too-long; silent
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    status, output = executor._execute_shell_phase(  # pylint: disable=protected-access; silent
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


# pylint: disable-next=unused-argument; silent
def test_phase5_env_prefix_local_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target_script = tmp_path / "env_target.py"
    target_script.write_text(
        # pylint: disable-next=line-too-long; silent
        "import os\nfrom pathlib import Path\nPath('env_ok').write_text(os.environ.get('MPLBACKEND', 'missing'))\n",
        encoding="utf-8",
    )
    workflow = WorkflowDefinition(
        name="entry-env-prefix",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    status, output = executor._execute_shell_phase(  # pylint: disable=protected-access; silent
        phase,
        state={},
        context={},
        loop_vars={"entry_script": "MPLBACKEND=Agg python3 env_target.py"},
        loop_state={},
    )

    assert status == "success"
    assert output["exit_code"] == 0
    assert (tmp_path / "env_ok").read_text(encoding="utf-8") == "Agg"


def test_phase5_env_prefix_multiple_env_vars(tmp_path: Path) -> None:
    target_script = tmp_path / "multi_env.py"
    target_script.write_text(
        # pylint: disable-next=line-too-long; silent
        "import os\nfrom pathlib import Path\nPath('multi_ok').write_text(os.environ.get('FOO', 'x') + os.environ.get('BAR', 'y'))\n",
        encoding="utf-8",
    )
    workflow = WorkflowDefinition(
        name="entry-multi-env",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    status, output = executor._execute_shell_phase(  # pylint: disable=protected-access; silent
        phase,
        state={},
        context={},
        loop_vars={"entry_script": "FOO=hello BAR=world python3 multi_env.py"},
        loop_state={},
    )

    assert status == "success"
    assert output["exit_code"] == 0
    assert (tmp_path / "multi_ok").read_text(encoding="utf-8") == "helloworld"


def test_phase5_entry_script_action_allows_env_prefix_command() -> None:
    state = {
        "phase_3_entry_script": {
            "entry_script_path": "old.py",
            "run_command": "python old.py",
            "phase5_entry_script_revision_allowed": True,
        }
    }
    workflow = WorkflowDefinition(
        name="env-prefix-revision",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir="/tmp/test",
        output_dir="/tmp/test",
    )
    loop_vars = {"entry_script": "python old.py"}
    loop_state: dict[str, object] = {
        "entry_script_revision_count": 0,
        "entry_script_revision_requests": [],
        "max_entry_script_revisions": 2,
    }

    result = executor._maybe_apply_entry_script_action(  # pylint: disable=protected-access; silent
        {
            "entry_script_action": {
                "needed": True,
                "action": "modify",
                "reason": "use env-prefix command",
                "entry_script_path": "new.py",
                "run_command": "MPLBACKEND=Agg python3 new.py",
            }
        },
        loop_vars,
        state,
        {},
        loop_state,
    )

    assert result is not None
    assert result["applied"] is True
    assert state["phase_3_entry_script"]["run_command"] == "MPLBACKEND=Agg python3 new.py"
    assert loop_vars["entry_script"] == "MPLBACKEND=Agg python3 new.py"


def test_subworkflow_llm_exhausted_validation_retries_fail_without_mark_validated(
    tmp_path: Path,
) -> None:
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
    validator.register_validator(
        "always_fail",
        lambda _data: {
            "passed": False,
            "errors": ["invalid repair classification"],
            "warnings": [],
        },
    )
    artifact_store.artifact_dir = str(tmp_path / ".sm-artifacts" / "testrun")
    artifact_store.raw_dir = str(tmp_path / ".sm-artifacts" / "testrun" / "raw")
    session_mgr.get_or_create.return_value = "session:error_analyzer"
    session_mgr.send_command.side_effect = [
        json.dumps({"repair_role": "code_adapter"}),
        json.dumps({"repair_role": "dependency_fixer"}),
        json.dumps({"repair_role": "operator_fixer"}),
    ]
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    # pylint: disable-next=protected-access; silent
    executor._execute_shell_phase = MagicMock(return_value=("success", {"ran": True}))

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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
    assert result["step_outputs"]["analyze_error"]["validation_errors"] == [
        "invalid repair classification"
    ]
    artifact_store.save_phase_output.assert_called_once_with(
        "analyze_error", result["step_outputs"]["analyze_error"]
    )
    artifact_store.mark_validated.assert_not_called()
    executor._execute_shell_phase.assert_not_called()  # pylint: disable=protected-access; silent
    assert session_mgr.send_command.call_count == 3


def test_subworkflow_llm_validation_retry_then_valid_succeeds_and_marks_validated(
    tmp_path: Path,
) -> None:
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
            "errors": []
            if data.get("repair_role") == "code_adapter"
            else ["missing valid repair role"],
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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
    artifact_store.save_phase_output.assert_called_once_with(
        "analyze_error", result["step_outputs"]["analyze_error"]
    )
    artifact_store.mark_validated.assert_called_once_with(
        "analyze_error", result["step_outputs"]["analyze_error"]
    )
    assert session_mgr.send_command.call_count == 2


# pylint: disable-next=too-many-locals; silent
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
        # pylint: disable-next=line-too-long; silent
        '{"repair_role": "dependency_fixer", "category": "dependency", "root_cause": "torch_npu missing", "suggested_fix": "install torch_npu"}',
        '{"fixed": true}',
    ]
    real_loader = PromptLoader(Path(__file__).resolve().parent.parent / "prompts")

    def load_prompt(template: str, ctx: dict[str, str]) -> str:
        if template == "repair_dependency_fixer":
            return real_loader.load_prompt(template, ctx)
        return template

    prompt_loader.load_prompt.side_effect = load_prompt
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path / "dependency project with spaces!"),
        output_dir=str(tmp_path),
        experience_store=MagicMock(),
    )

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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
    # Dependency fixer prompt now includes constraint_summary, No CPU
    # Fallback, and Native Operator Handoff
    assert "No CPU Fallback (CRITICAL)" in fix_prompt
    assert "Native Operator Handoff" in fix_prompt
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
        # pylint: disable-next=protected-access; silent
        assert WorkflowExecutor._is_slim_repair_prompt_phase(phase_id)
    # pylint: disable-next=protected-access; silent
    assert not WorkflowExecutor._is_slim_repair_prompt_phase("fix_code")
    # pylint: disable-next=protected-access; silent
    assert not WorkflowExecutor._is_slim_repair_prompt_phase("imp_fix_code")


# pylint: disable-next=too-many-locals; silent
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
                "runtime_skills": {
                    "include": ["improvement-operator-runtime-skill"],
                    "missing": "ignore",
                },
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    result = executor._run_sub_workflow(  # pylint: disable=protected-access; silent
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


# pylint: disable-next=too-many-locals,too-many-statements; silent
def test_fix_phase_reports_experience_usage_and_updates_counters(tmp_path: Path):
    import sys as _sys  # pylint: disable=import-outside-toplevel; silent

    python = _sys.executable
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=2,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": (
                    f'{python} -c "import pathlib, sys; '
                    f"p=pathlib.Path('{tmp_path / 'flag'}'); sys.exit(0 if p.exists() else 1)\""
                ),
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
    store.upsert_index(
        {
            "id": "code-exp",
            "type": "skill",
            "status": "promoted",
            "title": "CUDA Call Fix",
            "target_roles": ["code_adapter"],
            "target_phases": ["phase_5_validation"],
        }
    )
    store.upsert_index(
        {
            "id": "ignored-exp",
            "type": "skill",
            "status": "promoted",
            "title": "Irrelevant Fix",
            "target_roles": ["code_adapter"],
            "target_phases": ["phase_5_validation"],
        }
    )
    store.upsert_catalog_entry(
        {
            "id": "code-exp",
            "type": "skill",
            "status": "promoted",
            "title": "CUDA Call Fix",
        }
    )
    store.upsert_catalog_entry(
        {
            "id": "ignored-exp",
            "type": "skill",
            "status": "promoted",
            "title": "Irrelevant Fix",
        }
    )
    telemetry_bridge = TelemetryBridge(str(tmp_path / "telemetry"))
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    # pylint: disable-next=unused-argument; silent
    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            # pylint: disable-next=line-too-long; silent
            return '{"repair_role": "code_adapter", "category": "code", "root_cause": "cuda", "suggested_fix": "use npu"}'
        (tmp_path / "flag").write_text("fixed", encoding="utf-8")
        return json.dumps(
            {
                "fixed": True,
                "used_experience_ids": ["code-exp"],
                "experience_actions_taken": {"code-exp": ["replaced cuda call"]},
                "ignored_experience_ids": ["ignored-exp"],
                "ignored_reasons": {"ignored-exp": "not relevant to this CUDA call"},
            }
        )

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
        result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
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
    # pylint: disable-next=protected-access; silent
    event_types = [event["event_type"] for event in telemetry_bridge._events]
    assert "experience_selected" in event_types
    assert "experience_used" in event_types
    assert "experience_ignored" in event_types
    assert "experience_verification" in event_types
    selected_event = next(
        # pylint: disable-next=protected-access; silent
        event for event in telemetry_bridge._events if event["event_type"] == "experience_selected"
    )
    assert selected_event["details"]["action_card_count"] == 2
    assert "CUDA Call Fix" in selected_event["details"]["action_cards"][0]
    ignored_event = next(
        # pylint: disable-next=protected-access; silent
        event for event in telemetry_bridge._events if event["event_type"] == "experience_ignored"
    )
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
                "command": 'python -c "import sys; sys.exit(1)"',
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
    store.upsert_index(
        {"id": "code-exp", "type": "skill", "status": "promoted", "title": "CUDA Call Fix"}
    )
    store.upsert_catalog_entry(
        {"id": "code-exp", "type": "skill", "status": "promoted", "title": "CUDA Call Fix"}
    )
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    # pylint: disable-next=unused-argument; silent
    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            # pylint: disable-next=line-too-long; silent
            return '{"repair_role": "code_adapter", "category": "code", "root_cause": "cuda", "suggested_fix": "use npu"}'
        return json.dumps(
            {
                "fixed": False,
                "used_experience_ids": ["code-exp"],
                "experience_actions_taken": {"code-exp": ["attempted cuda replacement"]},
                "ignored_experience_ids": [],
                "ignored_reasons": {},
            }
        )

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
        result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
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


# pylint: disable-next=too-many-locals,too-many-statements; silent
def test_loop_history_preserves_per_iteration_error_analysis_role(tmp_path: Path):
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=2,
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": 'python -c "import sys; sys.exit(1)"',
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
    analyzer_outputs = iter(
        [
            {
                "repair_role": "dependency_fixer",
                "category": "dependency",
                "root_cause": "missing",
                "suggested_fix": "install",
            },
            {
                "repair_role": "operator_fixer",
                "category": "operator",
                "root_cause": "unsupported",
                "suggested_fix": "replace op",
            },
        ]
    )

    # pylint: disable-next=unused-argument; silent
    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":  # pylint: disable=no-else-return; silent
            return json.dumps(next(analyzer_outputs))
        elif session_id == "session:dependency_fixer":
            return json.dumps( { "fixed": True,
    "summary": "Installed torch_npu; dependency closure verified; no handoff needed",
    "modified_files": ["requirements.txt"],
    "agent_diagnostics": {"verified": True},
     } )
        return json.dumps(
            {
                "fixed": True,
                "summary": "Replaced unsupported op",
                "modified_files": ["model.py"],
                "agent_diagnostics": {"verified": True},
            }
        )

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
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
    # Verify fixer_outputs are propagated into loop_history
    assert "fixer_outputs" in history[0]
    fixer0 = history[0]["fixer_outputs"]
    assert "fix_dependency" in fixer0
    assert (
        fixer0["fix_dependency"]["summary"]
        == "Installed torch_npu; dependency closure verified; no handoff needed"
    )
    assert fixer0["fix_dependency"]["modified_files"] == ["requirements.txt"]
    assert fixer0["fix_dependency"]["agent_diagnostics"] == {"verified": "True"}
    assert "fixer_outputs" in history[1]
    fixer1 = history[1]["fixer_outputs"]
    assert "fix_operator" in fixer1
    assert fixer1["fix_operator"]["summary"] == "Replaced unsupported op"
    assert fixer1["fix_operator"]["modified_files"] == ["model.py"]
    prompt_contexts = {
        call.args[0]: call.args[1]
        for call in prompt_loader.load_prompt.call_args_list  # pylint: disable=no-member; silent
        if call.args[0] in {"fix_dependency_prompt", "fix_operator_prompt"}
    }
    assert "runtime_error_artifact_path" in prompt_contexts["fix_dependency_prompt"]
    assert "runtime_card_artifact_path" in prompt_contexts["fix_dependency_prompt"]
    assert "runtime_error_artifact_path" in prompt_contexts["fix_operator_prompt"]
    assert "runtime_card_artifact_path" in prompt_contexts["fix_operator_prompt"]
    assert "operator_custom_op_guidance" in prompt_contexts["fix_operator_prompt"]
    assert "operator_repair_context_artifact_path" not in prompt_contexts["fix_operator_prompt"]

    # pylint: disable-next=protected-access; silent
    formatted = executor._format_error_analyzer_history(
        history,
        step_outputs={},
        state={"error_analysis": {"category": "operator", "repair_role": "operator_fixer"}},
    )
    assert "| Iter 1 | success |" in formatted
    assert "dependency | dependency_fixer |" in formatted
    assert "| Iter 2 | success |" in formatted and "operator | operator_fixer |" in formatted
    assert "Latest error category: operator (repair role: operator_fixer)" in formatted
    # Verify fixer outputs appear in the formatted table and details section
    assert "Installed torch_npu" in formatted
    assert "Replaced unsupported op" in formatted
    assert "Previous Fixer Outputs" in formatted
    assert "requirements.txt" in formatted
    assert "model.py" in formatted

    # pylint: disable-next=protected-access; silent
    legacy_formatted = executor._format_error_analyzer_history(
        [{"iteration": 1, "status": "success", "duration": 0.1}],
        step_outputs={},
        state={},
    )
    assert "| Iter 1 | success | 0.1 | unknown | (none) | (none) | (none) |" in legacy_formatted


def test_last_iteration_post_repair_canonical_rerun_allows_success(tmp_path: Path):
    """Regression: when a fixer runs on the last loop iteration, the stale
    non-zero script_exit_code from the earlier run_entry_script must be
    refreshed by a canonical re-run so the loop can return success."""
    import sys as _sys  # pylint: disable=import-outside-toplevel; silent

    python = _sys.executable  # use the same interpreter, portable across envs
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": (
                    f'{python} -c "import pathlib, sys; '
                    f"p=pathlib.Path('{tmp_path / 'flag'}'); sys.exit(0 if p.exists() else 1)\""
                ),
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
                "routes": {"dependency_fixer": "fix_dependency"},
            },
            {
                "id": "fix_dependency",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_dependency_prompt",
                "agent": "dependency_fixer",
            },
        ],
    )
    workflow = WorkflowDefinition(
        name="last_iter_rerun",
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
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"

    # On the first pass run_entry_script fails (no flag yet).
    # The fix_dependency LLM creates the flag so the canonical re-run passes.
    # pylint: disable-next=unused-argument; silent
    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            return json.dumps(
                {
                    "repair_role": "dependency_fixer",
                    "category": "dependency",
                    "root_cause": "missing flag",
                    "suggested_fix": "create flag file",
                }
            )
        # dependency_fixer: create the flag file so re-run succeeds
        flag_path = tmp_path / "flag"
        flag_path.write_text("fixed", encoding="utf-8")
        return json.dumps(
            {
                "fixed": True,
                "summary": "Created flag file",
                "modified_files": [str(flag_path)],
            }
        )

    session_mgr.send_command.side_effect = respond
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
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

    # The canonical re-run must produce success
    assert result["status"] == "success", f"Expected success, got {result['status']}"
    assert result["loop_state"]["script_exit_code"] == 0
    assert len(result["loop_history"]) == 1

    # Only error_analyzer + fix_dependency were called (bonus pass
    # succeeds without triggering another analyze_error cycle).
    assert session_mgr.send_command.call_count == 2
    called_sessions = [c.args[0] for c in session_mgr.send_command.call_args_list]
    assert called_sessions == ["session:error_analyzer", "session:dependency_fixer"]


def test_collect_fixer_outputs_extracts_summary_modified_files_and_diagnostics():
    step_outputs = {
        "fix_dependency": {
            "summary": "Installed torch_npu==2.1.0",
            "modified_files": ["requirements.txt", "setup.cfg"],
            "agent_diagnostics": {"verified": True},
        },
        "fix_code": {
            "summary": "Replaced .cuda() calls",
            "modified_files": ["model.py"],
            "agent_diagnostics": "All CUDA APIs migrated",
        },
        "analyze_error": {"category": "dependency"},
        "irrelevant": "not a dict",
    }
    result = WorkflowExecutor._collect_fixer_outputs(  # pylint: disable=protected-access; silent
        WorkflowExecutor.__new__(WorkflowExecutor), step_outputs
    )

    assert result is not None
    assert "fix_dependency" in result  # pylint: disable=unsupported-membership-test; silent
    # pylint: disable-next=unsubscriptable-object; silent
    assert result["fix_dependency"]["summary"] == "Installed torch_npu==2.1.0"
    # pylint: disable-next=unsubscriptable-object; silent
    assert result["fix_dependency"]["modified_files"] == ["requirements.txt", "setup.cfg"]
    # pylint: disable-next=unsubscriptable-object; silent
    assert result["fix_dependency"]["agent_diagnostics"] == {"verified": "True"}
    assert "fix_code" in result  # pylint: disable=unsupported-membership-test; silent
    # pylint: disable-next=unsubscriptable-object; silent
    assert result["fix_code"]["summary"] == "Replaced .cuda() calls"
    # pylint: disable-next=unsubscriptable-object; silent
    assert result["fix_code"]["modified_files"] == ["model.py"]
    # pylint: disable-next=unsubscriptable-object; silent
    assert result["fix_code"]["agent_diagnostics"] == "All CUDA APIs migrated"
    assert "fix_operator" not in result  # pylint: disable=unsupported-membership-test; silent
    # pylint: disable-next=unsupported-membership-test; silent
    assert "imp_fix_dependency" not in result


def test_collect_fixer_outputs_returns_none_when_no_fixers():
    step_outputs = {"analyze_error": {"category": "operator"}, "script_stderr": "error text"}
    result = WorkflowExecutor._collect_fixer_outputs(  # pylint: disable=protected-access; silent
        WorkflowExecutor.__new__(WorkflowExecutor), step_outputs
    )
    assert result is None


def test_format_error_analyzer_history_renders_fixer_outputs():
    history = [
        {
            "iteration": 1,
            "status": "failure",
            "duration": 1.5,
            "error_category": "dependency",
            "repair_role": "dependency_fixer",
            "fixer_outputs": {
                "fix_dependency": {
                    "summary": "Installed torch_npu",
                    "modified_files": ["requirements.txt"],
                    "agent_diagnostics": {"verified": True},
                }
            },
        },
        {
            "iteration": 2,
            "status": "failure",
            "duration": 2.0,
            "error_category": "operator",
            "repair_role": "operator_fixer",
            "fixer_outputs": {
                "fix_operator": {
                    "summary": "Replaced unsupported op with AscendC impl",
                    "modified_files": ["model.py", "ops/custom_ops.cpp"],
                    "agent_diagnostics": {"handoff_needed": False, "verified": True},
                }
            },
        },
    ]

    # pylint: disable-next=redefined-outer-name; silent
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    # pylint: disable-next=protected-access; silent
    formatted = executor._format_error_analyzer_history(history, step_outputs={}, state={})

    assert "| Iter 1 | failure | 1.5 | dependency | dependency_fixer |" in formatted
    assert "| Iter 2 | failure | 2.0 | operator | operator_fixer |" in formatted
    assert "Installed torch_npu" in formatted
    assert "Replaced unsupported op with AscendC impl" in formatted
    assert "## Previous Fixer Outputs" in formatted
    assert "requirements.txt" in formatted
    assert "model.py" in formatted
    assert "ops/custom_ops.cpp" in formatted


def test_format_history_summary_renders_fixer_outputs():
    history = [
        {
            "iteration": 1,
            "status": "failure",
            "duration": 1.5,
            "fixer_outputs": {
                "fix_dependency": {
                    "summary": "Installed torch_npu",
                    "agent_diagnostics": {"verified": True},
                }
            },
        },
        {
            "iteration": 2,
            "status": "success",
            "duration": 2.0,
            "fixer_outputs": {
                "fix_operator": {
                    "summary": "Added AscendC kernel",
                    "agent_diagnostics": "operator fixed",
                }
            },
        },
    ]

    # pylint: disable-next=redefined-outer-name; silent
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    # pylint: disable-next=protected-access; silent
    formatted = executor._format_history_summary(history)

    assert "| Iteration | Status | Duration | Summary | Agent Diagnostics |" in formatted
    assert "| 1 | failure | 1.5 | Installed torch_npu |" in formatted
    assert "| 2 | success | 2.0 | Added AscendC kernel | operator fixed |" in formatted

    empty = executor._format_history_summary([])  # pylint: disable=protected-access; silent
    assert "(No previous repair attempts)" in empty


def _entry_script_revision_workflow(
    max_iterations: int = 3, max_revisions: int = 2
) -> WorkflowDefinition:
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


def _entry_script_revision_executor(
    tmp_path: Path, workflow: WorkflowDefinition
) -> WorkflowExecutor:
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    validator.validate.return_value = MagicMock(passed=True, errors=[])
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
    # pylint: disable-next=redefined-outer-name; silent
    executor = _entry_script_revision_executor(tmp_path, workflow)
    revised_script = tmp_path / "final_evidence_validate.py"
    revised_script.write_text(
        "from pathlib import Path\nPath('entry-ok').write_text('ok')\n", encoding="utf-8"
    )
    revised_command = f"python {revised_script}"
    executor.session_mgr.send_command.return_value = json.dumps(
        {
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
        }
    )
    state = {
        "phase_3_entry_script": {
            "entry_script_path": "old.py",
            "run_command": 'python -c "import sys; sys.exit(1)"',
            "phase5_entry_script_revision_allowed": True,
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
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
    # pylint: disable-next=redefined-outer-name; silent
    executor = _entry_script_revision_executor(tmp_path, workflow)
    first_revision_script = tmp_path / "first_revision.py"
    first_revision_script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    blocked_revision_script = tmp_path / "blocked_revision.py"
    blocked_revision_script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    first_revision = f"python {first_revision_script}"
    blocked_revision = f"python {blocked_revision_script}"

    # Use an iterator because loop_state is not stored on executor.state until the loop returns.
    analyzer_outputs = iter([first_revision, blocked_revision])

    # pylint: disable-next=unused-argument; silent
    def respond_with_iterator(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            command = next(analyzer_outputs)
            return json.dumps(
                {
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
                }
            )
        return json.dumps({"fixed": True})

    executor.session_mgr.send_command.side_effect = respond_with_iterator
    state = {
        "phase_3_entry_script": {
            "entry_script_path": "old.py",
            "run_command": 'python -c "import sys; sys.exit(1)"',
            "phase5_entry_script_revision_allowed": True,
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
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
    assert (
        result["loop_history"][1]["entry_script_action"]["blocked_reason"]
        == "max_revisions_exceeded"
    )
    assert result["loop_history"][1]["repair_role"] == "code_adapter"
    called_sessions = [call.args[0] for call in executor.session_mgr.send_command.call_args_list]
    assert called_sessions == [
        "session:error_analyzer",
        "session:error_analyzer",
        "session:code_adapter",
    ]


def test_entry_script_action_blocks_when_phase3_contract_flag_false(tmp_path: Path):
    # pylint: disable-next=redefined-outer-name; silent
    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    state = {
        "phase_3_entry_script": {"entry_script_path": "old.py", "run_command": "python old.py"}
    }
    loop_vars = {"entry_script": "python old.py"}
    loop_state: dict[str, object] = {
        "entry_script_revision_count": 0,
        "entry_script_revision_requests": [],
        "max_entry_script_revisions": 2,
    }

    result = executor._maybe_apply_entry_script_action(  # pylint: disable=protected-access; silent
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
        "FOO=bar bash -c id",
        "X=1 sh run_validation.sh",
        "CUDA_VISIBLE_DEVICES=0 docker run --rm python3 train.py",
        "MPLBACKEND=Agg bash new.py",
    ],
)
def test_entry_script_action_blocks_unsafe_revised_command(tmp_path: Path, run_command: str):
    # pylint: disable-next=redefined-outer-name; silent
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

    result = executor._maybe_apply_entry_script_action(  # pylint: disable=protected-access; silent
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


def test_workflow_executor_phase3_legacy_output_fails_when_custom_op_context_required(
    tmp_path: Path,
) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="phase3-custom-op-required", version="1.0", phases=[phase], terminals=[]
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "phase_1 says CUDAExtension custom operator is required"},
        {"phase_1_project_analysis": {"notes": "CUDAExtension custom operator"}},
    )

    assert normalized["entry_script_kind"] == "custom_op_full_validation"
    result = validate_entry_script(normalized)
    assert result["passed"] is False
    assert any("required_report_paths" in error for error in result["errors"])


def test_workflow_executor_phase3_legacy_output_passes_without_custom_op_context(
    tmp_path: Path,
) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(name="phase3-legacy", version="1.0", phases=[phase], terminals=[]),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "plain project"},
        {"phase_1_project_analysis": {"notes": "plain training"}},
    )

    assert "entry_script_kind" not in normalized
    result = validate_entry_script(normalized)
    assert result["passed"] is True


def test_workflow_executor_phase3_negative_custom_op_notes_do_not_force_custom_op_context(
    tmp_path: Path,
) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="phase3-negative-custom-op", version="1.0", phases=[phase], terminals=[]
        ),
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
        normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
            phase,
            {"entry_script_path": "train.py", "run_command": "python train.py"},
            {"previous_outputs": notes},
            {"phase_1_project_analysis": {"notes": notes}},
        )

        assert "entry_script_kind" not in normalized
        result = validate_entry_script(normalized)
        assert result["passed"] is True


def test_workflow_executor_phase3_structured_custom_op_surface_controls_custom_op_context(
    tmp_path: Path,
) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="phase3-structured-custom-op", version="1.0", phases=[phase], terminals=[]
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    false_surface = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
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

    true_surface = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
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

    contract_output = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
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


def test_workflow_executor_phase35_injects_custom_op_marker_before_validation(
    tmp_path: Path,
) -> None:
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
    session_mgr.get_or_create.return_value = "session:main"
    session_mgr.send_command.side_effect = [
        json.dumps(
            {
                "validation_passed": True,
                "issues": [],
                "fix_plan": "Legacy static pass shape.",
            }
        ),
        json.dumps(
            {
                "validation_passed": True,
                "issues": [],
                "fix_plan": "Full custom-op static pass shape.",
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
        ),
    ]
    prompt_loader.load_prompt.return_value = "prompt"
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(  # pylint: disable=protected-access; silent
        phase,
        {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation"}},
        {},
    )

    assert status == "success"
    assert output["custom_op_static_required"] is True
    assert output["entry_script_kind"] == "custom_op_full_validation"
    assert output["script_runs_project_api_custom_ops"] is True
    assert session_mgr.send_command.call_count == 2


def test_workflow_executor_phase35_exhausted_validation_retries_fail_without_mark_validated(
    tmp_path: Path,
) -> None:
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    status, output = executor._execute_llm_phase(  # pylint: disable=protected-access; silent
        phase,
        {"phase_3_entry_script": {"entry_script_kind": "custom_op_full_validation"}},
        {},
    )

    assert status == "failure"
    assert output["custom_op_static_required"] is True
    assert any(
        "custom-op static validation missing booleans" in error
        for error in output["validation_errors"]
    )
    artifact_store.save_phase_output.assert_called_once()
    artifact_store.mark_validated.assert_not_called()
    assert session_mgr.send_command.call_count == 3


def test_entry_script_action_needed_false_string_does_not_apply_or_count(tmp_path: Path):
    # pylint: disable-next=redefined-outer-name; silent
    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    state = {"phase_3_entry_script": {"run_command": "python old.py"}}
    loop_vars = {"entry_script": "python old.py"}
    step_outputs: dict[str, object] = {}
    loop_state: dict[str, object] = {
        "entry_script_revision_count": 0,
        "entry_script_revision_requests": [],
        "max_entry_script_revisions": 2,
    }

    result = executor._maybe_apply_entry_script_action(  # pylint: disable=protected-access; silent
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
    # pylint: disable-next=use-implicit-booleaness-not-comparison; silent
    assert loop_state["entry_script_revision_requests"] == []
    assert loop_vars["entry_script"] == "python old.py"
    assert state["phase_3_entry_script"]["run_command"] == "python old.py"
    assert step_outputs == {}  # pylint: disable=use-implicit-booleaness-not-comparison; silent


def test_entry_script_action_needed_string_normalization():
    # pylint: disable-next=protected-access; silent
    normalize = WorkflowExecutor._normalize_entry_script_action

    for value in (True, "true", "1", "yes"):
        assert normalize({"needed": value})["needed"] is True

    for value in (False, "false", "0", "no", "maybe", "", None):
        action = {} if value is None else {"needed": value}
        assert normalize(action)["needed"] is False


def test_analyze_error_prompt_has_entry_script_action_schema_and_contract_context(tmp_path: Path):
    prompt_content = (
        Path(__file__).resolve().parent.parent / "prompts" / "phase_error_recovery.md"
    ).read_text(encoding="utf-8")
    assert "entry_script_contract" in prompt_content
    assert "entry_script_action" in prompt_content
    assert '"needed": false' in prompt_content
    assert '"action": "none"' in prompt_content
    assert '"run_command": ""' in prompt_content
    assert "reason freely" not in prompt_content

    # pylint: disable-next=redefined-outer-name; silent
    executor = _entry_script_revision_executor(tmp_path, _entry_script_revision_workflow())
    input_ctx: dict[str, str] = {}
    executor._inject_sub_workflow_context(  # pylint: disable=protected-access; silent
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
    # pylint: disable-next=redefined-outer-name; silent
    executor = _executor_for_experience_context(tmp_path)
    output = {"fixed": True}

    # pylint: disable-next=protected-access; silent
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
            "baseline_device": "cuda",
            "custom_device": "npu",
            "overall_baseline_seconds": 0.05,
            "overall_custom_seconds": 0.04,
            "overall_speedup_vs_baseline": 1.25,
            "overall_project_api_invoked": True,
            "overall_all_units_replaced": True,
            "overall_baseline_device": "cuda",
            "overall_custom_device": "npu",
            "entries": [
                {
                    "unit_identity": "op_1",
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cuda",
                    "custom_device": "npu",
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
                    "project_local": True,
                    "built": True,
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
                    "baseline_device": "cuda",
                    "custom_device": "npu",
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
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00libascendcl aclrt native-op")
    build_log = project_dir / "migration_reports" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    _ = build_log.write_text("g++ op_kernel.o -lascendcl -o libop_1.so\n", encoding="utf-8")
    _ = (project_dir / "migration_reports" / "migration_manifest.json").write_text(
        json.dumps({"required_units": ["op_1"]}),
        encoding="utf-8",
    )


def _custom_op_gate_workflow(max_iterations: int = 1) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="npu_migration_custom_gate",
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
                        "command": "python -c \"print('ok')\"",
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


def _custom_op_gate_executor(tmp_path: Path) -> WorkflowExecutor:
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    prompt_loader = MagicMock()
    validator = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    session_mgr.send_command.return_value = json.dumps(
        {
            "repair_role": "code_adapter",
            "category": "validation",
            "root_cause": "final evidence gate failed",
            "suggested_fix": "complete custom-op evidence",
        }
    )
    prompt_loader.load_prompt.side_effect = lambda template, ctx: template
    return WorkflowExecutor(
        _custom_op_gate_workflow(),
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )


def test_rule_based_migration_builtin_without_backend_uses_report_only_safe_default(
    tmp_path: Path,
) -> None:
    """Rule-based migration without explicit backend defaults to report_only (safe)."""
    source_file = tmp_path / "model.py"
    original = (
        "import torch\n"
        "device = 'cuda'\n"
        "with torch.cuda.amp.autocast():\n"
        "    tensor = torch.ones(1).cuda()\n"
    )
    source_file.write_text(original, encoding="utf-8")
    workflow = WorkflowDefinition(
        name="rule-builtin", version="1.0", phases=[], terminals=["complete"]
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    # pylint: disable-next=protected-access; silent
    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    migrated = source_file.read_text(encoding="utf-8")
    assert status == "success"
    assert output["operation"] == "rule_based_migration"
    assert output["result"]["summary"]["total_files"] == 1
    assert output["result"]["summary"]["total_replacements"] == 0, (
        "Without explicit backend, report_only safe default must not modify files"
    )
    assert migrated == original, "Report only must not modify source code"
    assert output.get("strategy") == "report_only"


def test_rule_based_migration_builtin_with_backend_ppu(tmp_path: Path) -> None:
    """Rule-based migration with explicit backend=ppu uses PPU (preserve CUDA, report only)."""
    source_file = tmp_path / "model.py"
    original = (
        "import torch\n"
        "device = 'cuda'\n"
        "with torch.cuda.amp.autocast():\n"
        "    tensor = torch.ones(1).cuda()\n"
    )
    source_file.write_text(original, encoding="utf-8")
    workflow = WorkflowDefinition(
        name="rule-builtin", version="1.0", phases=[], terminals=["complete"]
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
    setattr(
        phase, "params", {"operation": "rule_based_migration", "pattern": "*.py", "backend": "ppu"}
    )

    # pylint: disable-next=protected-access; silent
    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    migrated = source_file.read_text(encoding="utf-8")
    assert status == "success"
    assert output["operation"] == "rule_based_migration"
    assert output.get("backend") == "ppu"
    assert output.get("strategy") == "preserve_cuda_report_only"
    assert migrated == original, "PPU backend must preserve CUDA code"
    assert "import torch_npu" not in migrated


def test_rule_based_migration_builtin_with_backend_report_only(tmp_path: Path) -> None:
    """Rule-based migration with explicit backend=report_only does not modify files."""
    source_file = tmp_path / "model.py"
    original = (
        "import torch\n"
        "device = 'cuda'\n"
        "with torch.cuda.amp.autocast():\n"
        "    tensor = torch.ones(1).cuda()\n"
    )
    source_file.write_text(original, encoding="utf-8")
    workflow = WorkflowDefinition(
        name="rule-builtin", version="1.0", phases=[], terminals=["complete"]
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
    setattr(
        phase,
        "params",
        {"operation": "rule_based_migration", "pattern": "*.py", "backend": "report_only"},
    )

    # pylint: disable-next=protected-access; silent
    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    migrated = source_file.read_text(encoding="utf-8")
    assert status == "success"
    assert output["operation"] == "rule_based_migration"
    assert output.get("backend") == "report_only"
    assert output.get("strategy") == "report_only"
    assert migrated == original, "report_only backend must not modify files"


def test_rule_based_migration_top_level_strategy_file_overrides_platform(tmp_path: Path) -> None:
    # pylint: disable-next=import-outside-toplevel; silent
    from core.platform_policy import TargetPlatformConfig

    source_file = tmp_path / "model.py"
    original = "import torch\nprint(torch.cuda.is_available())\n"
    source_file.write_text(original, encoding="utf-8")
    workflow = WorkflowDefinition(
        name="rule-builtin",
        version="1.0",
        phases=[],
        terminals=["complete"],
        target_platform=TargetPlatformConfig(preset="npu_ascend"),
        rule_migration={"strategy_file": "rule_strategies/report_only.yaml"},
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    # pylint: disable-next=protected-access; silent
    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    assert status == "success"
    assert output.get("strategy") == "rule_strategies/report_only.yaml"
    assert source_file.read_text(encoding="utf-8") == original


def test_rule_based_migration_platform_strategy_used_without_workflow_override(
    tmp_path: Path,
) -> None:
    # pylint: disable-next=import-outside-toplevel; silent
    from core.platform_policy import TargetPlatformConfig

    source_file = tmp_path / "model.py"
    source_file.write_text("import torch\nprint(torch.cuda.is_available())\n", encoding="utf-8")
    workflow = WorkflowDefinition(
        name="rule-builtin",
        version="1.0",
        phases=[],
        terminals=["complete"],
        target_platform=TargetPlatformConfig(preset="npu_ascend"),
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    # pylint: disable-next=protected-access; silent
    status, output = executor._execute_builtin_phase(phase, state={}, context={})

    migrated = source_file.read_text(encoding="utf-8")
    assert status == "success"
    assert output.get("strategy") == "cuda_to_npu"
    assert "torch.npu.is_available()" in migrated


def test_builtin_phase_missing_operation_fails(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        name="rule-builtin", version="1.0", phases=[], terminals=["complete"]
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    # pylint: disable-next=protected-access; silent
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
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
    workflow_path = (
        Path(__file__).resolve().parent.parent / "workflows" / "experience_memory_test.yaml"
    )
    workflow = load_workflow(str(workflow_path))

    gate_phase = next(
        phase
        for phase in workflow.sub_workflows["repair_loop"].phases
        if isinstance(phase, dict) and phase.get("id") == "custom_op_final_gate"
    )

    assert isinstance(gate_phase, dict)
    assert gate_phase["type"] == "builtin"
    assert gate_phase["params"] == {"operation": "custom_op_final_gate"}


# pylint: disable-next=unused-argument; silent
def test_experience_memory_custom_op_gate_skips_for_non_custom_contract(tmp_path: Path) -> None:
    workflow_path = (
        Path(__file__).resolve().parent.parent / "workflows" / "experience_memory_test.yaml"
    )
    workflow = load_workflow(str(workflow_path))

    phase_ids = [
        phase.get("id")
        for phase in workflow.sub_workflows["repair_loop"].phases
        if isinstance(phase, dict)
    ]
    assert "custom_op_final_gate" in phase_ids


def test_missing_custom_op_final_gate_blocks_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    executor = _custom_op_gate_executor(tmp_path)  # pylint: disable=redefined-outer-name; silent
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={
                "entry_script": "${state.phase_3_entry_script.run_command}",
                "project_dir": str(tmp_path),
            },
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert "Custom-op final evidence gate failed" in result["loop_state"]["script_stderr"]
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is False
    executor.session_mgr.send_command.assert_called_once()


def test_incomplete_performance_report_blocks_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    payload = _custom_op_gate_payload()
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report["complete"] = False
    (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(payload), encoding="utf-8")
    executor = _custom_op_gate_executor(tmp_path)  # pylint: disable=redefined-outer-name; silent
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={
                "entry_script": "${state.phase_3_entry_script.run_command}",
                "project_dir": str(tmp_path),
            },
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert result["loop_state"]["script_exit_code"] == 1
    assert any(
        "performance_report.complete" in error
        for error in result["loop_state"]["custom_op_final_gate"]["errors"]
    )


def test_custom_op_final_gate_ignores_outside_project_reports_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside_reports"
    outside.mkdir()
    (outside / "custom_op_final_gate.json").write_text(
        json.dumps(_custom_op_gate_payload()), encoding="utf-8"
    )
    executor = _custom_op_gate_executor(tmp_path)  # pylint: disable=redefined-outer-name; silent
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(outside),
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={
                "entry_script": "${state.phase_3_entry_script.run_command}",
                "project_dir": str(tmp_path),
            },
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    gate = result["loop_state"]["custom_op_final_gate"]
    assert gate["passed"] is False
    assert gate["path"] == str(
        (tmp_path / "migration_reports" / "custom_op_final_gate.json").resolve()
    )


def test_custom_op_final_gate_rejects_oversized_report(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _ = (reports_dir / "custom_op_final_gate.json").write_text(
        "{" + " " * (5 * 1024 * 1024), encoding="utf-8"
    )
    executor = _custom_op_gate_executor(tmp_path)  # pylint: disable=redefined-outer-name; silent
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={
                "entry_script": "${state.phase_3_entry_script.run_command}",
                "project_dir": str(tmp_path),
            },
        ),
        state=state,
        context={},
    )

    assert result["status"] == "failure"
    assert any(
        "too large" in error for error in result["loop_state"]["custom_op_final_gate"]["errors"]
    )


def test_valid_custom_op_final_gate_allows_phase5_success(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    (reports_dir / "custom_op_final_gate.json").write_text(
        json.dumps(_custom_op_gate_payload()), encoding="utf-8"
    )
    executor = _custom_op_gate_executor(tmp_path)  # pylint: disable=redefined-outer-name; silent
    state = {
        "phase_3_entry_script": {
            "entry_script_kind": "custom_op_full_validation",
            "run_command": "python validate.py",
            "reports_dir": str(reports_dir),
        }
    }

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={
                "entry_script": "${state.phase_3_entry_script.run_command}",
                "project_dir": str(tmp_path),
            },
        ),
        state=state,
        context={},
    )

    assert result["status"] == "success"
    assert result["loop_state"]["script_exit_code"] == 0
    assert result["loop_state"]["custom_op_final_gate"]["passed"] is True
    executor.session_mgr.send_command.assert_not_called()


def test_non_custom_project_skips_custom_op_final_gate(tmp_path: Path) -> None:
    executor = _custom_op_gate_executor(tmp_path)  # pylint: disable=redefined-outer-name; silent
    state = {"phase_3_entry_script": {"run_command": "python validate.py"}}

    result = executor._execute_loop_phase(  # pylint: disable=protected-access; silent
        PhaseDefinition(
            id="phase_5_validation",
            name="Validation",
            prompt_template="",
            output_schema={},
            type="loop",
            sub_workflow="repair_loop",
            input_mapping={
                "entry_script": "${state.phase_3_entry_script.run_command}",
                "project_dir": str(tmp_path),
            },
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


def test_phase7a_orchestration_uses_artifact_backed_evaluator_and_persists_candidates(
    tmp_path: Path,
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "model.py").write_text("import torch\nprint('npu fix')\n", encoding="utf-8")

    artifact_store = ArtifactStore(str(tmp_path), "run-1")
    Path(artifact_store.validated_dir, "phase_1_project_analysis_canonical.json").write_text(
        json.dumps(
            {
                "project_dir": str(project_root),
                "dependencies": ["torch"],
                "unique_project_marker": "artifact-project-context",
            }
        ),
        encoding="utf-8",
    )
    Path(artifact_store.validated_dir, "phase_5_validation_canonical.json").write_text(
        json.dumps(
            {
                "final_status": "success",
                "unique_validation_marker": "artifact-validation-context",
            }
        ),
        encoding="utf-8",
    )
    Path(artifact_store.raw_dir, "phase_run_entry_script_attempt1.json").write_text(
        json.dumps(
            {
                "stderr": "missing torch_npu before fix",
                "unique_raw_marker": "artifact-raw-context",
            }
        ),
        encoding="utf-8",
    )
    Path(artifact_store.journal_path).write_text(
        json.dumps(
            {"phase_id": "phase_5_validation", "unique_journal_marker": "artifact-journal-context"}
        )
        + "\n",
        encoding="utf-8",
    )

    store = ExperienceStore(str(tmp_path))
    session_mgr = FakePhase7SessionManager(
        {
            "evaluation_summary": "Found dependency pattern",
            "project_source_root": str(project_root),
            "candidates": [
                {
                    "title": "Install torch-npu after CPU torch",
                    "problem_description": "Generic dependency fix",
                    "rough_fix_approach": "Pin CPU torch then install torch-npu",
                    "artifact_evidence": [
                        "validated/phase_5_validation_canonical.json",
                        "raw/phase_run_entry_script_attempt1.json",
                    ],
                    "involved_code_files": [{"path": "model.py", "role": "entry"}],
                    "recommended_type": "skill",
                    "category": "dependency",
                    "subtype": "torch_npu_install",
                    "tags": ["torch-npu", "pip"],
                    "confidence": 0.92,
                }
            ],
        }
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    # pylint: disable-next=protected-access; silent
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
    assert (tmp_path / "memory" / "staging" / "run-1" / "evaluation_summary.md").read_text(
        encoding="utf-8"
    ) == "Found dependency pattern"


def test_phase7b_orchestration_refines_candidates_and_updates_catalog_manifest(tmp_path: Path):
    artifact_store = ArtifactStore(str(tmp_path), "run-1")
    store = ExperienceStore(str(tmp_path))
    store.upsert_index(
        {
            "id": "run-0-exp-existing",
            "type": "skill",
            "status": "staging",
            "category": "dependency",
            "subtype": "torch_npu_install",
            "tags": ["torch-npu", "pip"],
            "title": "Existing torch-npu install fix",
            "confidence": 0.7,
        }
    )
    store.write_candidate(
        "run-1",
        "candidate-001",
        {
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
        },
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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

    # pylint: disable-next=protected-access; silent
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


def test_runtime_skill_repo_root_relative_path_resolves_against_execution_root(
    tmp_path: Path,
) -> None:
    from core.paths import execution_root  # pylint: disable=import-outside-toplevel; silent

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
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
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
            # pylint: disable-next=protected-access; silent
            _ = executor._execute_llm_phase(phase, {}, {})
        finally:
            os.chdir(old_cwd)

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert "### agent-skill" in sent_prompt
        assert "Agent guidance" in sent_prompt
    finally:
        import shutil  # pylint: disable=import-outside-toplevel; silent

        shutil.rmtree(skill_repo_root, ignore_errors=True)


# ── Container preflight / probe during WorkflowExecutor init ───────────


class TestPhase5ContainerEnvPrefix:
    def test_env_prefix_passed_as_env_not_argv(self, tmp_path: Path) -> None:
        cmd = "MPLBACKEND=Agg python3 /workspace/057_example_fwi.py"
        workflow = WorkflowDefinition(
            name="container-env-prefix",
            version="1.0",
            phases=[],
            terminals=["complete"],
        )
        mock_backend = MagicMock(spec=ContainerBackend)
        mock_backend.run.return_value = MagicMock(
            exit_code=0,
            stdout="",
            stderr="",
            duration=0.1,
        )
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
            exec_backend=mock_backend,
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

        executor._execute_shell_phase(  # pylint: disable=protected-access; silent
            phase,
            state={},
            context={},
            loop_vars={"entry_script": cmd},
            loop_state={},
        )

        mock_backend.run.assert_called_once()
        call_kwargs = mock_backend.run.call_args.kwargs
        run_cmd = call_kwargs.get("command") or mock_backend.run.call_args[0][0]
        env = call_kwargs.get("env")

        assert isinstance(run_cmd, list)
        assert run_cmd[0] == "python3"
        assert run_cmd[1] == "/workspace/057_example_fwi.py"
        assert env == {"MPLBACKEND": "Agg"}

    def test_multiple_env_prefix_passed_as_env_not_argv(self, tmp_path: Path) -> None:
        cmd = "CUDA_VISIBLE_DEVICES=0 PYTHONPATH=/workspace/src python3 /workspace/script.py"
        workflow = WorkflowDefinition(
            name="container-multi-env",
            version="1.0",
            phases=[],
            terminals=["complete"],
        )
        mock_backend = MagicMock(spec=ContainerBackend)
        mock_backend.run.return_value = MagicMock(
            exit_code=0,
            stdout="",
            stderr="",
            duration=0.1,
        )
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
            exec_backend=mock_backend,
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

        executor._execute_shell_phase(  # pylint: disable=protected-access; silent
            phase,
            state={},
            context={},
            loop_vars={"entry_script": cmd},
            loop_state={},
        )

        call_kwargs = mock_backend.run.call_args.kwargs
        run_cmd = call_kwargs.get("command") or mock_backend.run.call_args[0][0]
        env = call_kwargs.get("env")

        assert isinstance(run_cmd, list)
        assert run_cmd[0] == "python3"
        assert env["CUDA_VISIBLE_DEVICES"] == "0"
        assert env["PYTHONPATH"] == "/workspace/src"


class TestWorkflowExecutorContainerPreflight:
    @patch("core.execution_backend.ContainerBackend")
    # pylint: disable-next=invalid-name; silent
    def test_container_workflow_calls_preflight_and_probe(self, MockBackend, tmp_path: Path):
        backend = MagicMock()
        MockBackend.return_value = backend
        cfg = ExecutionBackendConfig.from_dict({"mode": "container", "image": "test:latest"})
        workflow = WorkflowDefinition(
            name="test",
            version="1.0",
            phases=[],
            terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )
        backend.set_project_dir.assert_called_once()
        backend.preflight.assert_called_once()
        backend.probe_environment.assert_called_once()
        assert executor.exec_backend is backend
        # pylint: disable-next=protected-access; silent
        assert executor._container_env_probe is backend.probe_environment.return_value

    @patch("core.execution_backend.ContainerBackend")
    # pylint: disable-next=invalid-name; silent
    def test_local_workflow_does_not_call_preflight(self, MockBackend, tmp_path: Path):
        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"])
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )
        MockBackend.assert_not_called()
        assert executor.exec_backend is None
        assert executor._container_env_probe is None  # pylint: disable=protected-access; silent

    @patch("subprocess.run")
    def test_container_backend_preflight_is_called_on_init(self, mock_run, tmp_path: Path):
        mock_run.return_value = MagicMock(returncode=0, stdout="init-cid\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict({"mode": "container", "image": "test:latest"})
        workflow = WorkflowDefinition(
            name="test",
            version="1.0",
            phases=[],
            terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )
        assert isinstance(executor.exec_backend, ContainerBackend)
        # pylint: disable-next=protected-access; silent
        assert executor.exec_backend._container_id == "init-cid"


# ── Container context injection into LLM prompts ──────────────────────


class TestContainerEnvContextInjection:
    def test_inject_container_env_context_skipped_for_local(self, tmp_path: Path):
        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"])
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )
        ctx: dict = {}
        executor._inject_container_env_context(ctx)  # pylint: disable=protected-access; silent
        assert ctx == {}  # pylint: disable=use-implicit-booleaness-not-comparison; silent

    @patch("subprocess.run")
    def test_inject_container_env_context_adds_keys_for_container(self, mock_run, tmp_path: Path):
        mock_run.return_value = MagicMock(
            returncode=0,
            # pylint: disable-next=line-too-long; silent
            stdout='{"status": "ok", "python_version": "3.10.1", "platform": "Linux", "cwd": "/workspace"}\n',
            stderr="",
        )
        cfg = ExecutionBackendConfig.from_dict({"mode": "container", "image": "test:latest"})
        workflow = WorkflowDefinition(
            name="test",
            version="1.0",
            phases=[],
            terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )
        ctx: dict = {}
        executor._inject_container_env_context(ctx)  # pylint: disable=protected-access; silent
        assert "container_env_facts" in ctx
        assert "container_python_version" in ctx
        assert ctx["container_python_version"] == "3.10.1"

    def test_inject_container_env_context_uses_setdefault(self, tmp_path: Path):
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.execution_backend import ContainerBackend

        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"])
        mock_backend = ContainerBackend(
            ExecutionBackendConfig.from_dict({"mode": "container", "image": "x"})
        )
        mock_backend._container_id = "existing-cid"  # pylint: disable=protected-access; silent
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
            exec_backend=mock_backend,
        )
        # pylint: disable-next=protected-access; silent
        executor._container_env_probe = {"container_id": "existing-cid", "status": "ok"}
        ctx: dict = {"container_name_or_id": "pre-set"}
        executor._inject_container_env_context(ctx)  # pylint: disable=protected-access; silent
        assert ctx["container_name_or_id"] == "pre-set"

    def test_review_phase_receives_execution_environment_context(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (
    prompts_dir /
    "review_probe.md").write_text(
        # pylint: disable-next=line-too-long; silent
        "{execution_environment_context}\n{container_probe_command_prefix}\n{actual_execution_command}\n",
        encoding="utf-8",
         )
        workflow = WorkflowDefinition(
            name="review-context",
            version="1.0",
            phases=[],
            terminals=["complete"],
        )
        backend = ContainerBackend(
            ExecutionBackendConfig.from_dict({"mode": "container", "image": "x"})
        )
        backend._container_id = "cid-review"  # pylint: disable=protected-access; silent
        backend.set_project_dir(str(tmp_path))
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "s-review"
        session_mgr.send_command.return_value = '{"verdict": "accept", "reasoning": "ok"}'
        executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
            workflow,
            session_mgr,
            MagicMock(),
            PromptLoader(prompts_dir),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
            exec_backend=backend,
        )
        executor._container_env_probe = {  # pylint: disable=protected-access; silent
            "status": "ok",
            "interpreter_path": "/opt/conda/bin/python3",
            "python_version": "3.10.12",
        }
        phase = PhaseDefinition(
            id="review_gate",
            name="Review",
            prompt_template="review_probe",
            output_schema={},
            type="llm",
            agent="main_engineer",
        )

        result = executor._execute_review_phase(  # pylint: disable=protected-access; silent
            phase,
            state={},
            context={},
            loop_vars={"entry_script": "/opt/conda/bin/python3 /workspace/train.py"},
            loop_state={"script_stdout": "ok", "script_duration": 1.0, "iteration": 2},
            loop_history=[],
            sub_workflow_def=None,
            verdicts_cfg={},
        )

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert result["status"] == "success"
        assert "execution_backend_mode**: container" in sent_prompt
        assert "/opt/conda/bin/python3" in sent_prompt
        assert "docker exec -i" in sent_prompt


class TestExperienceConfigGate:
    def test_experience_injection_gated_when_workflow_disabled(self, tmp_path: Path):
        phase = PhaseDefinition(
            id="test",
            name="T",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
            retrieve_experience=True,
        )
        workflow = WorkflowDefinition(
            name="exp_disabled",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
            experience=ExperienceConfig(enabled=False, phase7_enabled=True),
        )
        mock_store = MagicMock()
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session_1"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "BASE PROMPT"

        # pylint: disable-next=invalid-name; silent
        with patch("core.experience_query.ExperienceQuerier") as MockQuerier:
            executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
                workflow,
                session_mgr,
                MagicMock(),
                prompt_loader,
                MagicMock(),
                project_dir=str(tmp_path),
                output_dir=str(tmp_path),
                experience_store=mock_store,
            )
            # pylint: disable-next=protected-access; silent
            result = executor._append_dynamic_experience_markdown("PROMPT", phase, {}, {}, None)
            MockQuerier.assert_not_called()
            assert result == "PROMPT"

    def test_experience_injection_allowed_when_enabled(self, tmp_path: Path):
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.types import ExperienceConfig

        phase = PhaseDefinition(
            id="test",
            name="T",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
            retrieve_experience=True,
        )
        workflow = WorkflowDefinition(
            name="exp_enabled",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
            experience=ExperienceConfig(enabled=True, phase7_enabled=True),
        )
        mock_store = MagicMock()
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session_1"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "BASE PROMPT"

        query_result = {
            "selected_experiences": [],
            "summary": "",
            "warning": "",
        }

        # pylint: disable-next=invalid-name; silent
        with patch("core.experience_query.ExperienceQuerier") as MockQuerier:
            mock_querier = MagicMock()
            mock_querier.query.return_value = query_result
            MockQuerier.return_value = mock_querier

            executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
                workflow,
                session_mgr,
                MagicMock(),
                prompt_loader,
                MagicMock(),
                project_dir=str(tmp_path),
                output_dir=str(tmp_path),
                experience_store=mock_store,
            )
            # pylint: disable-next=protected-access; silent
            executor._append_dynamic_experience_markdown("PROMPT", phase, {}, {}, None)
            MockQuerier.assert_called_once()


class TestPhase7SkipAndReroute:
    def _executor_with_phases(self, tmp_path: Path, phases: list, experience_cfg=None):
        if experience_cfg is None:
            experience_cfg = ExperienceConfig(enabled=True, phase7_enabled=True)
        workflow = WorkflowDefinition(
            name="phase7_test",
            version="1.0",
            phases=phases,
            terminals=["complete", "failed"],
            agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
            experience=experience_cfg,
        )
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session_1"
        session_mgr.send_command.return_value = '{"ok": true}'
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "PROMPT"
        return WorkflowExecutor(
            workflow,
            session_mgr,
            MagicMock(),
            prompt_loader,
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
            experience_store=None,
        ), session_mgr

    def test_phase7_rerouted_in_transition_definition(self, tmp_path: Path):
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.types import TransitionDefinition

        phase = PhaseDefinition(
            id="phase_6_report",
            name="Report",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
            transition=TransitionDefinition(on_success="phase_7a_evaluate", on_failure="complete"),
        )
        executor, _ = self._executor_with_phases(  # pylint: disable=redefined-outer-name; silent
            tmp_path,
            [phase],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=False),
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == "complete"

    def test_phase7_rerouted_in_transitions_dict(self, tmp_path: Path):
        phase = PhaseDefinition(
            id="phase_6_report",
            name="Report",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
            transitions={"on_success": "phase_7a_evaluate", "on_failure": "complete"},
        )
        executor, _ = self._executor_with_phases(  # pylint: disable=redefined-outer-name; silent
            tmp_path,
            [phase],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=False),
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == "complete"

    def test_phase7b_rerouted(self, tmp_path: Path):
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.types import TransitionDefinition

        phase = PhaseDefinition(
            id="phase_7a_evaluate",
            name="Evaluate",
            prompt_template="x",
            output_schema={},
            type="orchestration",
            handler="experience_evaluator.ExperienceEvaluator.evaluate",
            transition=TransitionDefinition(on_success="phase_7b_refine", on_failure="complete"),
        )
        executor, _ = self._executor_with_phases(  # pylint: disable=redefined-outer-name; silent
            tmp_path,
            [phase],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=False),
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == "complete"

    def test_phase7_not_rerouted_when_enabled(self, tmp_path: Path):
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.types import TransitionDefinition

        phase = PhaseDefinition(
            id="phase_6_report",
            name="Report",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
            transition=TransitionDefinition(on_success="phase_7a_evaluate"),
        )
        executor, _ = self._executor_with_phases(  # pylint: disable=redefined-outer-name; silent
            tmp_path,
            [phase],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=True),
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase, "success", {}, {})
        assert next_id == "phase_7a_evaluate"

    def test_phase7_default_next_reroute(self, tmp_path: Path):
        phase_6 = PhaseDefinition(
            id="phase_6_report",
            name="Report",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
        )
        phase_7a = PhaseDefinition(
            id="phase_7a_evaluate",
            name="Evaluate",
            prompt_template="x",
            output_schema={},
            type="orchestration",
            handler="x.y.z",
        )
        executor, _ = self._executor_with_phases(  # pylint: disable=redefined-outer-name; silent
            tmp_path,
            [phase_6, phase_7a],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=False),
        )
        # pylint: disable-next=protected-access; silent
        next_id = executor._get_next_phase_id(phase_6, "success", {}, {})
        assert next_id == "complete"

    def test_phase7_skipped_in_execute_loop(self, tmp_path: Path):
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.types import TransitionDefinition

        phase_6 = PhaseDefinition(
            id="phase_6_report",
            name="Report",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
            transition=TransitionDefinition(on_success="phase_7a_evaluate"),
        )
        phase_7a = PhaseDefinition(
            id="phase_7a_evaluate",
            name="Evaluate",
            prompt_template="x",
            output_schema={},
            type="orchestration",
            handler="experience_evaluator.ExperienceEvaluator.evaluate",
            transition=TransitionDefinition(on_success="phase_7b_refine"),
        )
        phase_7b = PhaseDefinition(
            id="phase_7b_refine",
            name="Refine",
            prompt_template="x",
            output_schema={},
            type="orchestration",
            handler="experience_dispatcher.ExperienceDispatcher.dispatch_and_refine",
            transition=TransitionDefinition(on_success="complete"),
        )
        # pylint: disable-next=redefined-outer-name,unused-variable; silent
        executor, session_mgr = self._executor_with_phases(
            tmp_path,
            [phase_6, phase_7a, phase_7b],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=False),
        )
        executor.hook_manager = MagicMock()

        result = executor.execute({"PROJECT_DIR": str(tmp_path)})

        assert result["status"] == "complete"
        assert "phase_6_report" in executor.phase_results
        assert executor.phase_results["phase_6_report"]["status"] == "success"
        assert "phase_7a_evaluate" not in executor.phase_results
        assert "phase_7b_refine" not in executor.phase_results

    def test_phase7_direct_start_skipped(self, tmp_path: Path):
        phase_7a = PhaseDefinition(
            id="phase_7a_evaluate",
            name="Evaluate",
            prompt_template="x",
            output_schema={},
            type="orchestration",
            handler="experience_evaluator.ExperienceEvaluator.evaluate",
        )
        phase_end = PhaseDefinition(
            id="phase_end",
            name="End",
            prompt_template="x",
            output_schema={},
            type="llm",
            agent="main_engineer",
        )
        # pylint: disable-next=redefined-outer-name,unused-variable; silent
        executor, session_mgr = self._executor_with_phases(
            tmp_path,
            [phase_7a, phase_end],
            experience_cfg=ExperienceConfig(enabled=True, phase7_enabled=False),
        )
        executor.hook_manager = MagicMock()

        result = executor.execute({"PROJECT_DIR": str(tmp_path)})

        assert result["status"] == "complete"
        assert "phase_7a_evaluate" in executor.phase_results
        assert executor.phase_results["phase_7a_evaluate"]["status"] == "skipped"
        assert executor.phase_results["phase_7a_evaluate"]["reason"] == "phase7_disabled"
        assert "phase_end" in executor.phase_results


# ── Phase-aware previous_outputs filtering ────────────────────────


# pylint: disable-next=redefined-outer-name; silent
def test_we_filter_previous_outputs_empty_for_early_phases(temp_dir):
    """Phase 0/1/2/3 should receive empty previous_outputs."""
    workflow = WorkflowDefinition(name="filter_test", version="1.0", phases=[], terminals=[])
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=temp_dir,
        output_dir=temp_dir,
    )
    state = {
        "phase_0_env_detect": {"platform": "npu"},
        "phase_1_project_analysis": {"entry_script": "train.py"},
        "phase_2_venv_create": {"venv_path": "/.venv"},
    }
    for pid in (
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_2_venv_create",
        "phase_3_entry_script",
    ):
        phase = PhaseDefinition(id=pid, name=pid, prompt_template=pid, output_schema={}, type="llm")
        # pylint: disable-next=protected-access; silent
        assert executor._filter_previous_outputs(phase, state) == {}


# pylint: disable-next=redefined-outer-name; silent
def test_we_filter_previous_outputs_phase35_only_includes_phase3(temp_dir):
    """Phase 3.5 must receive only phase_3_entry_script, not earlier phases."""
    workflow = WorkflowDefinition(name="filter_test", version="1.0", phases=[], terminals=[])
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=temp_dir,
        output_dir=temp_dir,
    )
    state = {
        "phase_0_env_detect": {"platform": "npu"},
        "phase_1_project_analysis": {"entry_script": "train.py"},
        "phase_2_venv_create": {"venv_path": "/.venv"},
        "phase_3_entry_script": {
            "entry_script_path": "/train.py",
            "entry_script_kind": "custom_op_full_validation",
        },
    }
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="3.5",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
    )
    # pylint: disable-next=protected-access; silent
    filtered = executor._filter_previous_outputs(phase, state)
    assert "phase_3_entry_script" in filtered
    assert "phase_0_env_detect" not in filtered
    assert "phase_1_project_analysis" not in filtered
    assert "phase_2_venv_create" not in filtered


# pylint: disable-next=redefined-outer-name; silent
def test_we_filter_previous_outputs_fallback_to_all_for_unlisted(temp_dir):
    """Phases not in whitelist should fall back to all state (backward compat)."""
    workflow = WorkflowDefinition(name="filter_test", version="1.0", phases=[], terminals=[])
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=temp_dir,
        output_dir=temp_dir,
    )
    state = {"phase_1_entry_script": {}, "phase_5_validation": {}}
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="5",
        prompt_template="phase_5_validation",
        output_schema={},
        type="llm",
    )
    # pylint: disable-next=protected-access; silent
    filtered = executor._filter_previous_outputs(phase, state)
    assert filtered == state


# pylint: disable-next=redefined-outer-name; silent
def test_we_inject_llm_baseline_context_phase35_excludes_early_phases(temp_dir):
    """Integration: _inject_llm_baseline_context produces filtered JSON for Phase 3.5."""
    workflow = WorkflowDefinition(name="filter_test", version="1.0", phases=[], terminals=[])
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=temp_dir,
        output_dir=temp_dir,
    )
    state = {
        "phase_0_env_detect": {"platform": "npu", "python_version": "3.10"},
        "phase_1_project_analysis": {"entry_script": "train.py"},
        "phase_2_venv_create": {"venv_path": "/.venv"},
        "phase_3_entry_script": {
            "entry_script_path": "/train.py",
            "run_command": "python train.py",
        },
    }
    phase = PhaseDefinition(
        id="phase_35_static_validate",
        name="3.5",
        prompt_template="phase_35_static_validate",
        output_schema={},
        type="llm",
    )
    ctx: dict = {}
    # pylint: disable-next=protected-access; silent
    executor._inject_llm_baseline_context(ctx, phase, state)
    parsed = json.loads(ctx["previous_outputs"])
    assert "phase_3_entry_script" in parsed
    assert "phase_0_env_detect" not in parsed
    assert "phase_1_project_analysis" not in parsed
    assert "phase_2_venv_create" not in parsed


# pylint: disable-next=redefined-outer-name; silent
def test_we_inject_llm_baseline_context_early_phase_empty(temp_dir):
    """Integration: Phase 0 gets empty previous_outputs."""
    workflow = WorkflowDefinition(name="filter_test", version="1.0", phases=[], terminals=[])
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=temp_dir,
        output_dir=temp_dir,
    )
    state = {"phase_0_env_detect": {"platform": "npu"}}
    phase = PhaseDefinition(
        id="phase_0_env_detect",
        name="0",
        prompt_template="phase_0_env_detect",
        output_schema={},
        type="llm",
    )
    ctx: dict = {}
    # pylint: disable-next=protected-access; silent
    executor._inject_llm_baseline_context(ctx, phase, state)
    assert json.loads(ctx["previous_outputs"]) == {}


# ── disable_custom_op_contract_injection flag regression ──────────────────


def test_disable_custom_op_injection_prevents_auto_injection(tmp_path: Path) -> None:
    """When globals set disable_custom_op_contract_injection=True, custom-op signals
    in Phase 1 output do NOT trigger entry_script_kind injection."""
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="no-custom-injection",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            globals={"disable_custom_op_contract_injection": True},
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "phase_1 says CUDAExtension custom operator is required"},
        {"phase_1_project_analysis": {"notes": "CUDAExtension custom operator"}},
    )

    assert "entry_script_kind" not in normalized
    result = validate_entry_script(normalized)
    assert result["passed"] is True


def test_custom_op_route_disabled_strips_agent_contract_fields(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="normal-entry-route",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            globals={"custom_op_route_enabled": False},
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
        phase,
        {
            "entry_script_path": "train.py",
            "run_command": "python train.py",
            "entry_script_kind": "custom_op_full_validation",
            "reports_dir": str(tmp_path / "migration_reports"),
            "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
            "required_checks": ["same_run_runtime_coverage"],
            "operator_discovery_sources": ["source"],
            "operator_inventory_schema": {"semantic_rows": "one row per operator"},
            "performance_report_schema": {"entries": "per unit"},
            "validation_obligations": ["no_fallback"],
            "phase5_entry_script_revision_allowed": True,
        },
        {"previous_outputs": "custom operators exist"},
        {"phase_1_project_analysis": {"custom_op_surface": {"custom_op_detected": True}}},
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
        "phase5_entry_script_revision_allowed",
    ):
        assert field not in normalized
    assert validate_entry_script(normalized)["passed"] is True


def test_legacy_disable_custom_op_injection_strips_agent_contract_fields(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        WorkflowDefinition(
            name="legacy-normal-entry-route",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            globals={"disable_custom_op_contract_injection": True},
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    normalized = executor._normalize_llm_output(  # pylint: disable=protected-access; silent
        phase,
        {
            "entry_script_path": "train.py",
            "run_command": "python train.py",
            "entry_script_kind": "custom_op_full_validation",
            "reports_dir": str(tmp_path / "migration_reports"),
            "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
        },
        {"previous_outputs": "custom operators exist"},
        {},
    )

    assert "entry_script_kind" not in normalized
    assert "reports_dir" not in normalized
    assert "required_report_paths" not in normalized
    assert validate_entry_script(normalized)["passed"] is True


def test_disable_custom_op_injection_false_signal_injects(tmp_path: Path) -> None:
    """Without the disable flag (or with explicitly False), custom-op signals
    trigger entry_script_kind injection — backward-compatible behaviour."""
    phase = PhaseDefinition(
        id="phase_3_entry_script",
        name="Entry",
        prompt_template="phase_3_entry_script",
        output_schema={},
        type="llm",
        validator="entry_script",
        agent="main_engineer",
    )
    executor_no_globals = WorkflowExecutor(
        WorkflowDefinition(
            name="default-behaviour", version="1.0", phases=[phase], terminals=["complete"]
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    # pylint: disable-next=protected-access; silent
    normalized_no_flag = executor_no_globals._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "CUDAExtension custom operator is required"},
        {"phase_1_project_analysis": {"notes": "CUDAExtension custom operator"}},
    )
    assert normalized_no_flag["entry_script_kind"] == "custom_op_full_validation"

    executor_flag_false = WorkflowExecutor(
        WorkflowDefinition(
            name="explicit-false",
            version="1.0",
            phases=[phase],
            terminals=["complete"],
            globals={"disable_custom_op_contract_injection": False},
        ),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    # pylint: disable-next=protected-access; silent
    normalized_false = executor_flag_false._normalize_llm_output(
        phase,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
        {"previous_outputs": "CUDAExtension custom operator required"},
        {"phase_1_project_analysis": {"notes": "CUDAExtension custom operator"}},
    )
    assert normalized_false["entry_script_kind"] == "custom_op_full_validation"
    result = validate_entry_script(normalized_false)
    assert result["passed"] is False


def test_phase_6_report_session_error_generates_fallback(tmp_path: Path) -> None:
    class Phase6ErrorSessionManager:
        def __init__(self) -> None:
            self.send_calls: list[tuple[str, str, int | None, int | None]] = []

        def get_or_create(self, role: str, lifecycle: str) -> str:
            del role, lifecycle
            return "main-session"

        def send_command(
            self,
            session_id: str,
            command: str,
            timeout: int | None = None,
            retries: int | None = None,
        ) -> str:
            self.send_calls.append((session_id, command, timeout, retries))
            return json.dumps({"ok": False, "error": "Session still running"})

    phase = PhaseDefinition(
        id="phase_6_report",
        name="Phase 6",
        prompt_template="phase_6_report_musa",
        output_schema={},
        type="llm",
        agent="main_engineer",
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="phase6-fallback",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = Phase6ErrorSessionManager()
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        PromptLoader(),
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.state["phase_5_validation"] = {"status": "success", "script_exit_code": 0}

    result = executor.execute({"PROJECT_DIR": str(tmp_path)})

    phase6 = result["state"]["phase_6_report"]
    assert phase6["fallback"] is True
    assert phase6["migration_summary"]["overall_status"] == "partial"
    assert phase6["migration_summary"]["files_migrated"] == 0
    assert phase6["migration_summary"]["files_skipped"] == 0
    assert phase6["migration_summary"]["phase5_status"] == "success"
    assert session_mgr.send_calls[0][2] == 600
    assert session_mgr.send_calls[0][3] == 0
    assert all(Path(path).exists() for path in phase6["report_paths"])

    saved = artifact_store.load_phase_output("phase_6_report")
    assert saved is not None
    assert saved["fallback"] is True


def test_phase_6_report_timeout_exception_generates_fallback(tmp_path: Path) -> None:
    class Phase6TimeoutSessionManager:
        def __init__(self) -> None:
            self.send_calls: list[tuple[str, str, int | None, int | None]] = []

        def get_or_create(self, role: str, lifecycle: str) -> str:
            del role, lifecycle
            return "main-session"

        def send_command(
            self,
            session_id: str,
            command: str,
            timeout: int | None = None,
            retries: int | None = None,
        ) -> str:
            self.send_calls.append((session_id, command, timeout, retries))
            raise TimeoutError("phase 6 timed out")

    phase = PhaseDefinition(
        id="phase_6_report",
        name="Phase 6",
        prompt_template="phase_6_report_musa",
        output_schema={},
        type="llm",
        agent="main_engineer",
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="phase6-timeout-fallback",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )
    artifact_store = ArtifactStore(str(tmp_path), "testrun")
    session_mgr = Phase6TimeoutSessionManager()
    executor = WorkflowExecutor(  # pylint: disable=redefined-outer-name; silent
        workflow,
        session_mgr,
        artifact_store,
        PromptLoader(),
        ValidatorEngine(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    result = executor.execute({"PROJECT_DIR": str(tmp_path)})

    phase6 = result["state"]["phase_6_report"]
    assert phase6["fallback"] is True
    assert phase6["fallback_reason"] == "phase 6 timed out"
    assert session_mgr.send_calls[0][2] == 600
    assert session_mgr.send_calls[0][3] == 0
    assert all(Path(path).exists() for path in phase6["report_paths"])


class TestProductionWorkflowPlatformPolicy:
    """Production PPU workflow loads with performance override policy."""

    def test_ppu_entryfix_workflow_loads_performance_presence_only(self):
        """Load the production PPU entryfix workflow and verify its resolved
        platform policy includes performance_validation = presence_only and
        CPU baseline values."""
        # pylint: disable-next=import-outside-toplevel,redefined-outer-name,reimported; silent
        from core.config import load_workflow
        from core.platform_policy import (  # pylint: disable=import-outside-toplevel; silent
            get_performance_baseline_boolean_fields,
            get_performance_baseline_device_values,
            get_performance_validation_mode,
            resolve_policy,
        )

        wf_path = (
            Path(__file__).resolve().parent.parent
            / "workflows"
            / "ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml"
        )
        wf = load_workflow(str(wf_path))
        assert wf.target_platform is not None, "Workflow must have target_platform"
        assert wf.target_platform.preset == "ppu_cuda_compatible"

        policy = resolve_policy(wf.target_platform, wf.name)
        assert policy.id == "ppu_cuda_compatible"

        mode = get_performance_validation_mode(policy)
        assert mode == "presence_only", f"Expected presence_only, got {mode}"

        baseline_devices = get_performance_baseline_device_values(policy)
        assert "cpu" in baseline_devices, "CPU baseline must be accepted when configured"
        assert "cuda" in baseline_devices, "CUDA baseline must still be accepted"

        baseline_fields = get_performance_baseline_boolean_fields(policy)
        assert "cpu_baseline" in baseline_fields
        assert "cuda_baseline" in baseline_fields

    def test_default_full_mode_has_cuda_baseline_only(self):
        """A workflow without performance overrides defaults to full mode
        with CUDA-only baseline values."""
        from core.platform_policy import (  # pylint: disable=import-outside-toplevel; silent
            BUILTIN_PRESETS,
            get_performance_baseline_device_values,
            get_performance_validation_mode,
        )

        ppu = BUILTIN_PRESETS["ppu_cuda_compatible"]
        mode = get_performance_validation_mode(ppu)
        assert mode == "full"

        devices = get_performance_baseline_device_values(ppu)
        assert "cpu" not in devices, "Default baseline must NOT include CPU"
        assert "cuda" in devices
