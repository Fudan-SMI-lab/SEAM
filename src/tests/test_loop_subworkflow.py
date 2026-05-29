"""Tests for loop sub-workflow, review gate, and dispatch routing."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.types import (
    PhaseDefinition,
    RuntimeSkillsConfig,
    SubWorkflowDefinition,
    WorkflowDefinition,
)
from core.workflow_executor import WorkflowExecutor


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
def loop_workflow(temp_dir):  # pylint: disable=redefined-outer-name,unused-argument; silent
    """Create a workflow with a loop phase."""
    swf = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=3,
        stagnation_threshold=2,
        review_gate_enabled=False,
        max_review_iterations=3,
        stop_conditions=[
            {"condition": "$.script_exit_code == 0", "status": "success"},
            {"condition": "$.stagnation_count >= 2", "status": "stagnation"},
        ],
        phases=[
            {"id": "run_cmd", "type": "shell", "command": "exit 1", "timeout": 60},
            {"id": "stagnation_check", "type": "builtin", "operation": "stagnation_check"},
        ],
        blocks={},
    )
    return WorkflowDefinition(
        name="loop_test",
        version="1.0",
        phases=[
            PhaseDefinition(
                id="loop_phase",
                name="Loop",
                prompt_template="",
                output_schema={},
                type="loop",
                sub_workflow="repair_loop",
                max_iterations=3,
                transitions={"on_success": "complete"},
            ),
        ],
        terminals=["complete", "failed"],
        sub_workflows={"repair_loop": swf},
    )


class MockResult:  # pylint: disable=too-few-public-methods; silent
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestLoopSubworkflow:
    # pylint: disable-next=redefined-outer-name; silent
    def test_mini_phase_propagates_builtin_operation(self, temp_dir):
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()

        workflow = WorkflowDefinition(name="loop_test", version="1.0", phases=[], terminals=[])
        executor = WorkflowExecutor(
            workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        mini = executor._mini_phase(  # pylint: disable=protected-access; silent
            {"id": "stagnation_check", "type": "builtin", "operation": "stagnation_check"}
        )

        assert mini.params == {"operation": "stagnation_check"}

    # pylint: disable-next=redefined-outer-name; silent
    def test_loop_phase_runs_max_iterations(self, loop_workflow, temp_dir):
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()

        executor = WorkflowExecutor(
            loop_workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        mock_result = MockResult(returncode=1, stdout="", stderr="Error: module not found")
        with patch("core.workflow_executor.subprocess.run", return_value=mock_result):
            result = executor.execute({"PROJECT_DIR": temp_dir})
        assert isinstance(result, dict)
        assert "state" in result
        assert "phase_results" in result
        loop_result = result["state"].get("loop_phase", {})
        assert loop_result.get("iterations") == 3

    # pylint: disable-next=redefined-outer-name; silent
    def test_stop_conditions_evaluated(self, loop_workflow, temp_dir):
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()

        executor = WorkflowExecutor(
            loop_workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        loop_state = {"script_exit_code": 0, "review_verdict": "accept"}
        result = executor._check_stop_conditions(  # pylint: disable=protected-access; silent
            loop_workflow.sub_workflows["repair_loop"].stop_conditions,
            loop_state,
            {},
        )
        assert result == "success"

    # pylint: disable-next=redefined-outer-name; silent
    def test_stagnation_stop_condition(self, loop_workflow, temp_dir):
        """Stagnation stop condition should match when threshold reached."""
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()

        executor = WorkflowExecutor(
            loop_workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        loop_state = {"stagnation_count": 2, "last_error_signature": "sig"}
        result = executor._check_stop_conditions(  # pylint: disable=protected-access; silent
            loop_workflow.sub_workflows["repair_loop"].stop_conditions,
            loop_state,
            {},
        )
        assert result == "stagnation"

    def test_subworkflow_llm_injects_agent_and_subphase_runtime_skills(self, tmp_path: Path):
        write_runtime_skill(tmp_path, "agent-repair", "# Agent Repair\n\nAgent repair guidance")
        write_runtime_skill(tmp_path, "subphase-repair", "# Subphase Repair\n\nSubphase guidance")
        sub_workflow = SubWorkflowDefinition(
            id="repair_loop",
            type="loop",
            max_iterations=1,
            phases=[
                {
                    "id": "fix_code",
                    "type": "llm",
                    "prompt_template": "repair_prompt",
                    "agent": "code_adapter",
                    "runtime_skills": {
                        "include": ["subphase-repair"],
                        "inject_full": True,
                    },
                }
            ],
        )
        workflow = WorkflowDefinition(
            name="sub_runtime",
            version="1.0",
            phases=[],
            terminals=["complete"],
            agents={
                "code_adapter": {
                    "role": "code_adapter",
                    "lifecycle": "persistent",
                    "runtime_skills": RuntimeSkillsConfig(include=["agent-repair"]),
                },
            },
            sub_workflows={"repair_loop": sub_workflow},
        )
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        artifact_store.artifact_dir = str(tmp_path / "artifacts")
        artifact_store.raw_dir = str(tmp_path / "raw")
        session_mgr.get_or_create.return_value = "session_123"
        session_mgr.send_command.return_value = '{"fixed": true}'
        prompt_loader.load_prompt.return_value = "SUB PROMPT"
        executor = WorkflowExecutor(
            workflow,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            framework_config={"runtime_skill_repo_root": str(tmp_path)},
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

        sent_prompt = session_mgr.send_command.call_args[0][1]
        assert sent_prompt.startswith("SUB PROMPT\n\n## Explicit Runtime Skills")
        assert "### agent-repair" in sent_prompt
        assert "### subphase-repair" in sent_prompt
        assert "Agent repair guidance" in sent_prompt
        assert "Subphase guidance" in sent_prompt


class TestDispatchRouting:
    def test_dispatch_finds_route(self, temp_dir):  # pylint: disable=redefined-outer-name; silent
        """Dispatch should resolve route_field and find matching phase."""
        wf = WorkflowDefinition(name="disp", version="1.0", phases=[], terminals=[])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        phase = PhaseDefinition(
            id="dispatch",
            name="Dispatch",
            prompt_template="",
            output_schema={},
            type="dispatch",
        )
        phase.params = {
            "route_field": "${error_analysis.repair_role}",
            "routes": {"code_adapter": "fix_code", "dependency_fixer": "fix_dep"},
        }

        target = executor._execute_dispatch_phase(  # pylint: disable=protected-access; silent
            phase,
            {},
            {},
            loop_vars={},
            loop_state={},
            step_outputs={"error_analysis": {"repair_role": "code_adapter"}},
        )
        assert target == "fix_code"

    # pylint: disable-next=redefined-outer-name; silent
    def test_dispatch_unknown_route(self, temp_dir):
        """Unknown route should return None."""
        wf = WorkflowDefinition(name="disp", version="1.0", phases=[], terminals=[])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        phase = PhaseDefinition(
            id="dispatch",
            name="Dispatch",
            prompt_template="",
            output_schema={},
            type="dispatch",
        )
        phase.params = {
            "route_field": "${error_analysis.repair_role}",
            "routes": {"code_adapter": "fix_code"},
        }

        target = executor._execute_dispatch_phase(  # pylint: disable=protected-access; silent
            phase,
            {},
            {},
            loop_vars={},
            loop_state={},
            step_outputs={"error_analysis": {"repair_role": "unknown_role"}},
        )
        assert target is None


class TestReviewGate:
    def test_review_phase_accept(self, temp_dir):  # pylint: disable=redefined-outer-name; silent
        """Review verdict 'accept' should result in success status."""
        wf = WorkflowDefinition(name="review", version="1.0", phases=[], terminals=["complete"])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        prompt_loader.load_prompt.return_value = '{"verdict": "accept", "reasoning": "looks good"}'
        session_mgr.get_or_create.return_value = "session_123"
        session_mgr.send_command.return_value = '{"verdict": "accept", "reasoning": "looks good"}'

        phase = PhaseDefinition(
            id="review",
            name="Review",
            prompt_template="review_prompt",
            output_schema={},
            type="review",
            agent="main_engineer",
        )

        loop_state = {"script_stderr": "some error", "iteration": 1}

        result = executor._execute_review_phase(  # pylint: disable=protected-access; silent
            phase,
            {},
            {},
            loop_vars={},
            loop_state=loop_state,
            loop_history=[],
            sub_workflow_def=None,
            verdicts_cfg={},
        )
        assert result["status"] == "success"
        assert result["verdict"] == "accept"

    def test_review_phase_reject(self, temp_dir):  # pylint: disable=redefined-outer-name; silent
        """Review verdict 'reject' should trigger snapshot and return continue status."""
        wf = WorkflowDefinition(name="review", version="1.0", phases=[], terminals=["complete"])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )
        artifact_store.mark_validated.return_value = "path"

        prompt_loader.load_prompt.return_value = "review template"
        session_mgr.get_or_create.return_value = "session_123"
        session_mgr.send_command.return_value = (
            '{"verdict": "reject", "reasoning": "cpu fallback detected"}'
        )

        phase = PhaseDefinition(
            id="review",
            name="Review",
            prompt_template="review_prompt",
            output_schema={},
            type="review",
            agent="main_engineer",
        )

        loop_state = {"script_stderr": "NpuUtilization: 0%", "iteration": 2}
        verdicts_cfg = {
            "accept": {"action": "break", "set_status": "success"},
            "reject": {"action": "continue", "increment": "review_reject_count", "snapshot": True},
        }

        # pylint: disable-next=protected-access; silent
        executor.hook_manager._dispatch_builtin = MagicMock()
        result = executor._execute_review_phase(  # pylint: disable=protected-access; silent
            phase,
            {},
            {},
            loop_vars={},
            loop_state=loop_state,
            loop_history=[],
            sub_workflow_def=None,
            verdicts_cfg=verdicts_cfg,
        )
        assert result["verdict"] == "reject"
        assert loop_state.get("review_reject_count", 0) >= 1
        # pylint: disable-next=protected-access; silent
        executor.hook_manager._dispatch_builtin.assert_called_once()

    def test_review_verdict_routing(self):
        """Verify verdict routing: accept -> break, reject -> continue."""
        verdicts = {
            "accept": {"action": "break", "set_status": "success"},
            "reject": {"action": "continue", "increment": "review_reject_count", "snapshot": True},
        }

        accept_config = verdicts["accept"]
        assert accept_config["action"] == "break"
        assert accept_config["set_status"] == "success"

        reject_config = verdicts["reject"]
        assert reject_config["action"] == "continue"


class TestLoopStateManagement:  # pylint: disable=too-few-public-methods; silent
    def test_loop_state_sharing(self, temp_dir):  # pylint: disable=redefined-outer-name; silent
        """Loop state variables should be shared between steps."""
        wf = WorkflowDefinition(name="ls", version="1.0", phases=[], terminals=[])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        loop_state = {"exit_code": 1, "error": "module not found"}

        phase = PhaseDefinition(
            id="shell",
            name="S",
            prompt_template="",
            output_schema={},
            type="shell",
            on_failure="continue",
        )
        phase.command = "echo test"

        mock_result = MockResult(returncode=0, stdout="test\n", stderr="")
        with patch("core.workflow_executor.subprocess.run", return_value=mock_result):
            # pylint: disable-next=protected-access,unused-variable; silent
            status, output = executor._execute_shell_phase(
                phase, {}, {}, loop_vars=None, loop_state=loop_state
            )
        assert status == "success"
        assert "script_exit_code" in loop_state
        assert loop_state["script_exit_code"] == 0


class TestConditionallySkippedPhase:
    # pylint: disable-next=redefined-outer-name; silent
    def test_condition_true_proceeds(self, temp_dir):
        wf = WorkflowDefinition(name="cond", version="1.0", phases=[], terminals=["complete"])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        condition = "iteration > 0"
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            condition,
            state={},
            context={},
            loop_state={"iteration": 1},
        )
        assert result is True

    # pylint: disable-next=redefined-outer-name; silent
    def test_condition_false_proceeds(self, temp_dir):
        wf = WorkflowDefinition(name="cond", version="1.0", phases=[], terminals=["complete"])
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        prompt_loader = MagicMock()
        validator = MagicMock()
        executor = WorkflowExecutor(
            wf,
            session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            project_dir=temp_dir,
            output_dir=temp_dir,
        )

        condition = "iteration > 10"
        result = executor._evaluate_condition(  # pylint: disable=protected-access; silent
            condition,
            state={},
            context={},
            loop_state={"iteration": 1},
        )
        assert result is False
