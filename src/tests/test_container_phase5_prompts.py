"""Tests for container-specific Phase 5 execution-context visibility."""

from __future__ import annotations
import shlex
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.config import load_workflow
from core.execution_backend import (
    ContainerBackend,
    ExecResult,
    LocalBackend,
    get_execution_context as _get_exec_ctx,
)
from core.types import ExecutionBackendConfig

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / "workflows"
PROMPTS_DIR = ROOT / "prompts"

PHASE5_CONTAINER_PROMPTS = frozenset({
    "phase_error_recovery_container",
    "repair_dependency_fixer_container",
    "repair_code_adapter_container",
    "repair_operator_fixer_container",
    "phase_5_review_container",
    "phase_review_improvement_container",
})

PHASE5_ORIGINAL_PROMPTS = frozenset({
    "phase_error_recovery",
    "repair_dependency_fixer",
    "repair_code_adapter",
    "repair_operator_fixer",
    "phase_5_review",
    "phase_review_improvement",
})

CONTAINER_ANALYZER_PROMPTS = frozenset({
    "phase_error_recovery_container",
    "phase_error_recovery_container_musa",
    "phase_error_recovery_container_ppu",
})

REPAIR_SELF_VERIFICATION_PROMPTS = frozenset({
    "repair_code_adapter_container",
    "repair_code_adapter_container_musa",
    "repair_code_adapter_container_ppu",
    "repair_dependency_fixer_container",
    "repair_dependency_fixer_container_musa",
    "repair_dependency_fixer_container_ppu",
    "repair_final_gate_report_fixer_container",
    "repair_final_gate_report_fixer_container_musa",
    "repair_operator_fixer_container",
    "repair_operator_fixer_container_musa",
    "repair_operator_fixer_container_ppu",
})

REPAIR_SCOPE_MARKERS = {
    "repair_code_adapter_container": "Python-level source",
    "repair_code_adapter_container_musa": "Python-level source",
    "repair_code_adapter_container_ppu": "Python-level source",
    "repair_dependency_fixer_container": "dependency/environment/runtime-library",
    "repair_dependency_fixer_container_musa": "dependency/environment/runtime-library",
    "repair_dependency_fixer_container_ppu": "dependency/environment/runtime-library",
    "repair_final_gate_report_fixer_container": "report schema/aggregation",
    "repair_final_gate_report_fixer_container_musa": "report schema/aggregation",
    "repair_operator_fixer_container": "native/custom-op",
    "repair_operator_fixer_container_musa": "native/custom-op",
    "repair_operator_fixer_container_ppu": "native/custom-op",
}

# ── Original prompt files unchanged ─────────────────────────────────────


class TestOriginalPromptsUnchanged:
    """Verify original prompt files were not modified (no tracked diff)."""

    def test_original_prompt_files_exist(self):
        for name in PHASE5_ORIGINAL_PROMPTS:
            p = PROMPTS_DIR / f"{name}.md"
            assert p.exists(), f"Original prompt {p} should still exist"

    def test_original_prompts_no_container_placeholders(self):
        for name in PHASE5_ORIGINAL_PROMPTS:
            content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
            assert "execution_backend_mode" not in content, (
                f"Original prompt {name}.md must not contain execution_backend_mode"
            )
            assert "actual_execution_command" not in content, (
                f"Original prompt {name}.md must not contain actual_execution_command"
            )


class TestContainerPromptsExist:
    """Verify new container prompt files were created with required placeholders."""

    def test_container_prompt_files_exist(self):
        for name in PHASE5_CONTAINER_PROMPTS:
            p = PROMPTS_DIR / f"{name}.md"
            assert p.exists(), f"Container prompt {p} must exist"

    @pytest.mark.parametrize("name", sorted(PHASE5_CONTAINER_PROMPTS))
    def test_container_prompt_has_execution_context_section(self, name):
        content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert "execution_backend_mode" in content
        assert "actual_execution_command" in content
        assert "container_name_or_id" in content
        assert "container_workdir" in content
        assert "host_project_dir" in content
        assert "container_project_dir" in content

    _ENTRY_SCRIPT_PROMPTS = frozenset({
        "phase_error_recovery_container",
        "repair_dependency_fixer_container",
        "repair_code_adapter_container",
        "repair_operator_fixer_container",
    })

    @pytest.mark.parametrize("name", sorted(PHASE5_CONTAINER_PROMPTS))
    def test_entry_script_placeholder_presence(self, name):
        content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        if name in self._ENTRY_SCRIPT_PROMPTS:
            assert "{entry_script}" in content, f"{name} should have {{entry_script}} placeholder"
        # Review/improvement prompts don't need {entry_script} — context injected from review gate


    def test_musa_error_recovery_prompt_exposes_complete_shell_artifacts(self):
        content = (PROMPTS_DIR / "phase_error_recovery_container_musa.md").read_text(encoding="utf-8")
        assert "latest_complete_stdout_artifact_path" in content
        assert "latest_complete_stderr_artifact_path" in content
        assert "latest_complete_meta_artifact_path" in content
        assert "inspect the complete stdout/stderr artifacts when present" in content
        assert "complete execution evidence is unavailable" in content

    @pytest.mark.parametrize("name", sorted(CONTAINER_ANALYZER_PROMPTS))
    def test_container_analyzer_prompts_include_environment_action_schema(self, name):
        content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert "environment_action" in content
        assert "recreate_execution_environment" in content
        assert "needed" in content
        assert "scope" in content

    def test_container_prompts_instruct_container_validation(self):
        for name in PHASE5_CONTAINER_PROMPTS:
            content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
            assert "actual_execution_command" in content
            lower = content.lower()
            assert any(w in lower for w in ["container", "actual_execution_command"]), (
                f"Container prompt {name} should reference container execution"
            )

    @pytest.mark.parametrize("name", sorted(REPAIR_SELF_VERIFICATION_PROMPTS))
    def test_repair_prompts_have_concise_actual_command_loop(self, name):
        content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert "latest_complete_stdout_artifact_path" in content
        assert "latest_complete_stderr_artifact_path" in content
        assert "latest_complete_meta_artifact_path" in content
        assert "complete stdout/stderr" in content
        assert "run `actual_execution_command`" in content
        assert "next complete artifacts" in content
        assert "out-of-scope" in content
        assert "handoff role and reason" in content
        assert REPAIR_SCOPE_MARKERS[name] in content


# ── Workflow YAML prompt references ─────────────────────────────────────


class TestContainerYamlPrompts:
    """Verify container YAML Phase 5 references only _container prompt IDs."""

    @staticmethod
    def _find_phase(repair_loop_def, phase_id: str) -> dict | None:
        for p in repair_loop_def.phases:
            if p.get("id") == phase_id:
                return p
        return None

    @staticmethod
    def _find_block_phase(block: dict, phase_id: str) -> dict | None:
        for p in block.get("phases", []):
            if p.get("id") == phase_id:
                return p
        return None

    def test_container_yaml_uses_container_prompts(self):
        wf_path = str(WORKFLOWS_DIR / "npu_migration_v2_container.yaml")
        wf = load_workflow(wf_path)
        assert wf.sub_workflows is not None
        repair_loop_def = wf.sub_workflows.get("repair_loop")
        assert repair_loop_def is not None

        phase_ids_to_check = {
            "analyze_error": "phase_error_recovery_container",
            "fix_dependency": "repair_dependency_fixer_container",
            "fix_code": "repair_code_adapter_container",
            "fix_operator": "repair_operator_fixer_container",
            "review_gate": "phase_5_review_container",
        }

        for pid, expected_template in phase_ids_to_check.items():
            phase_def = self._find_phase(repair_loop_def, pid)
            assert phase_def is not None, f"Phase {pid} should exist in repair_loop"
            assert phase_def.get("prompt_template") == expected_template, (
                f"Phase {pid} prompt_template should be {expected_template}, "
                f"got {phase_def.get('prompt_template')}"
            )

        blocks = repair_loop_def.blocks or {}
        improvement = blocks.get("improvement_block")
        assert improvement is not None
        for pid, expected_template in {
            "improvement_plan": "phase_review_improvement_container",
            "imp_fix_dependency": "repair_dependency_fixer_container",
            "imp_fix_code": "repair_code_adapter_container",
            "imp_fix_operator": "repair_operator_fixer_container",
        }.items():
            phase_def = self._find_block_phase(improvement, pid)
            assert phase_def is not None, f"Phase {pid} should exist in improvement_block"
            assert phase_def.get("prompt_template") == expected_template, (
                f"Phase {pid} prompt_template should be {expected_template}, "
                f"got {phase_def.get('prompt_template')}"
            )

    def test_container_yaml_no_original_phase5_prompts(self):
        wf_path = str(WORKFLOWS_DIR / "npu_migration_v2_container.yaml")
        with open(wf_path, encoding="utf-8") as f:
            content = f.read()
        for orig in PHASE5_ORIGINAL_PROMPTS:
            assert f'"{orig}"' not in content and f"'{orig}'" not in content, (
                f"Container YAML must not reference original prompt {orig}"
            )

    def test_original_yaml_unchanged_prompts(self):
        wf_path = str(WORKFLOWS_DIR / "npu_migration_v2.yaml")
        wf = load_workflow(wf_path)
        assert wf.sub_workflows is not None
        repair_loop_def = wf.sub_workflows.get("repair_loop")
        assert repair_loop_def is not None

        for pid, expected_template in {
            "analyze_error": "phase_error_recovery",
            "fix_dependency": "repair_dependency_fixer",
            "fix_code": "repair_code_adapter",
            "fix_operator": "repair_operator_fixer",
            "review_gate": "phase_5_review",
        }.items():
            phase_def = self._find_phase(repair_loop_def, pid)
            assert phase_def is not None
            assert phase_def.get("prompt_template") == expected_template, (
                f"Original YAML: {pid} should still use {expected_template}"
            )

        blocks = repair_loop_def.blocks or {}
        improvement = blocks.get("improvement_block")
        assert improvement is not None
        for pid, expected_template in {
            "improvement_plan": "phase_review_improvement",
            "imp_fix_dependency": "repair_dependency_fixer",
            "imp_fix_code": "repair_code_adapter",
            "imp_fix_operator": "repair_operator_fixer",
        }.items():
            phase_def = self._find_block_phase(improvement, pid)
            assert phase_def is not None
            assert phase_def.get("prompt_template") == expected_template


# ── get_execution_context helper ────────────────────────────────────────


class TestGetExecutionContext:
    """Test the standalone get_execution_context helper."""

    def test_none_backend_returns_local_context(self):
        ctx = _get_exec_ctx(None, command="echo hi")
        assert ctx["execution_backend_mode"] == "local"
        assert "local execution" in ctx["actual_execution_command"]

    def test_local_backend_returns_local_context(self):
        ctx = _get_exec_ctx(LocalBackend(), command="echo hi")
        assert ctx["execution_backend_mode"] == "local"
        assert "local execution" in ctx["actual_execution_command"]

    def test_container_backend_docker_describes_command(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "abc123"
        backend._initialized = True

        ctx = backend.get_execution_context(command=".venv/bin/python run.py")
        assert ctx["execution_backend_mode"] == "container"
        assert "docker" in ctx["actual_execution_command"]
        assert "exec" in ctx["actual_execution_command"]
        assert "bash" in ctx["actual_execution_command"]
        assert "abc123" in ctx["actual_execution_command"]

    def test_container_backend_podman_describes_command(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "podman"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "pod-42"
        backend._initialized = True

        ctx = backend.get_execution_context(command=".venv/bin/python run.py")
        assert "podman" in ctx["actual_execution_command"]
        assert "pod-42" in ctx["actual_execution_command"]

    def test_container_backend_without_id_shows_placeholder(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        # No _container_id set yet (not created)

        ctx = backend.get_execution_context(command="echo test")
        assert "will be created on first execution" in ctx["container_name_or_id"]
        assert "will be created on first execution" in ctx["actual_execution_command"]

    def test_container_backend_existing_container_shows_name(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict({
            "mode": "container",
            "source": "existing_container",
            "container_name": "my-dev-01",
        })
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))

        ctx = backend.get_execution_context(command="echo test")
        assert ctx["container_name_or_id"] == "my-dev-01"
        assert "my-dev-01" in ctx["actual_execution_command"]

    def test_container_backend_describe_command_no_cwd(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker",
             "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"
        backend._initialized = True

        desc = backend.describe_command("python main.py")
        assert "docker exec" in desc
        assert "-w" in desc
        assert "/workspace" in desc
        assert "c1" in desc

    def test_container_backend_describe_command_with_env(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True

        desc = backend.describe_command(
            "python main.py", env={"FOO": "bar", "BAZ": "qux"}
        )
        assert "-e" in desc
        assert "FOO=bar" in desc
        assert "BAZ=qux" in desc

    def test_container_backend_describe_command_with_cwd_mapping(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"
        backend._initialized = True

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        desc = backend.describe_command("python main.py", cwd=str(subdir))
        assert "/workspace/subdir" in desc

    def test_container_backend_no_command_placeholder(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"

        ctx = backend.get_execution_context()
        assert ctx["container_name_or_id"] == "c1"
        assert ctx["container_workdir"] == "/workspace"
        assert ctx["host_project_dir"] == str(tmp_path)


# ── PromptLoader rendering with container context ───────────────────────


class TestContainerPromptRendering:
    """Verify container prompt placeholders are populated correctly."""

    def _fake_loader(self) -> MagicMock:
        from core.prompt_loader import PromptLoader
        loader = MagicMock(spec=PromptLoader)

        def _load(prompt_id: str, context: dict) -> str:
            tmpl_path = PROMPTS_DIR / f"{prompt_id}.md"
            if tmpl_path.exists():
                tmpl = tmpl_path.read_text(encoding="utf-8")
            else:
                tmpl = f"PROMPT:{prompt_id}\n"
            result = tmpl
            for k, v in sorted(context.items(), key=lambda x: -len(x[0])):
                placeholder = "{" + k + "}"
                result = result.replace(placeholder, str(v))
            return result

        loader.load_prompt = _load
        return loader

    def test_container_prompt_renders_docker_command(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "ascendhub:24.03", "runtime": "docker"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "docker-ctx-99"
        backend._initialized = True

        loader = self._fake_loader()
        entry_script = ".venv/bin/python run_test.py"
        exec_ctx = backend.get_execution_context(command=entry_script)
        ctx = {
            **exec_ctx,
            "phase_name": "phase_5_validation",
            "project_dir": str(tmp_path),
            "failed_phase": "phase_5_validation",
            "entry_script": entry_script,
            "iteration": "1",
            "previous_outputs": "(none)",
            "failure_log": "test failure",
            "entry_script_contract": "{}",
            "constraint_summary": "",
            "last_review": "(none)",
            "env_context": "{}",
            "artifact_base_path": str(tmp_path),
            "raw_attempt_files": "(none)",
            "workspace_root": str(ROOT.parent),
        }
        rendered = loader.load_prompt("phase_error_recovery_container", ctx)
        assert "docker" in rendered
        assert "exec" in rendered
        assert "docker-ctx-99" in rendered

    def test_container_prompt_renders_entry_script(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"

        loader = self._fake_loader()
        entry = "python app.py"
        exec_ctx = backend.get_execution_context(command=entry)
        ctx = {
            **exec_ctx,
            "repair_role": "dependency_fixer",
            "project_dir": str(tmp_path),
            "iteration": "1",
            "category": "dependency",
            "root_cause": "missing pkg",
            "suggested_fix": "install",
            "error_text": "import error",
            "history_summary": "(none)",
            "constraint_summary": "",
            "last_review": "(none)",
            "env_context": "{}",
            "artifact_base_path": str(tmp_path),
            "raw_attempt_files": "(none)",
            "workspace_root": str(ROOT.parent),
        }
        rendered = loader.load_prompt("repair_dependency_fixer_container", ctx)
        assert entry in rendered

    def test_local_context_does_not_leak_into_container_prompts(self, tmp_path):
        ctx = _get_exec_ctx(None, command="echo test")
        assert ctx["execution_backend_mode"] == "local"
        assert "docker" not in ctx["actual_execution_command"]
        assert "podman" not in ctx["actual_execution_command"]


# ── Command description accuracy (argv vs bash -c) ──────────────────────


class TestCommandDescriptionAccuracy:
    """Verify describe_command produces argv-style for lists, bash -c for strings."""

    def test_list_command_no_bash_c(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker",
             "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"

        desc = backend.describe_command(["python", "train.py", "--epochs", "10"])
        assert "bash" not in desc
        assert "-c" not in desc
        assert "python" in desc
        assert "train.py" in desc
        assert "--epochs" in desc

    def test_list_command_with_cwd(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker",
             "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        subdir = tmp_path / "experiments"
        subdir.mkdir()
        backend._container_id = "c1"

        desc = backend.describe_command(["python", "train.py"], cwd=str(subdir))
        assert "bash" not in desc
        assert "/workspace/experiments" in desc

    def test_list_command_with_env(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"

        desc = backend.describe_command(
            ["python", "train.py"], env={"CUDA_VISIBLE_DEVICES": "0"}
        )
        assert "bash" not in desc
        assert "-e" in desc
        assert "CUDA_VISIBLE_DEVICES=0" in desc

    def test_string_command_shows_bash_c(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"

        desc = backend.describe_command("python train.py && echo done")
        assert "bash" in desc
        assert "-c" in desc

    def test_get_execution_context_uses_argv_for_list(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker",
             "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"

        ctx = backend.get_execution_context(command=["python", "train.py"])
        assert "bash" not in ctx["actual_execution_command"]
        assert "python" in ctx["actual_execution_command"]

    def test_get_execution_context_uses_bash_c_for_string(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "docker",
             "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "c1"

        ctx = backend.get_execution_context(command="python train.py && echo done")
        assert "bash" in ctx["actual_execution_command"]
        assert "-c" in ctx["actual_execution_command"]

    def test_rendered_context_uses_argv_style(self, tmp_path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "ascendhub:24.03", "runtime": "docker",
             "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir(str(tmp_path))
        backend._container_id = "ctx-llm-1"

        loader = TestContainerPromptRendering()._fake_loader()
        entry_script = ".venv/bin/python run_test.py"
        exec_cmd = shlex.split(entry_script)
        exec_ctx = backend.get_execution_context(command=exec_cmd)
        ctx = {
            **exec_ctx,
            "phase_name": "phase_5_validation",
            "project_dir": str(tmp_path),
            "failed_phase": "phase_5_validation",
            "entry_script": entry_script,
            "iteration": "1",
            "previous_outputs": "(none)",
            "failure_log": "test failure",
            "entry_script_contract": "{}",
            "constraint_summary": "",
            "last_review": "(none)",
            "env_context": "{}",
            "artifact_base_path": str(tmp_path),
            "raw_attempt_files": "(none)",
            "workspace_root": str(ROOT.parent),
        }
        rendered = loader.load_prompt("phase_error_recovery_container", ctx)
        assert "bash -c" not in rendered
        assert "exec -i" in rendered or "docker exec" in rendered
        assert "actual_execution_command" in rendered
        assert "run_test.py" in rendered
