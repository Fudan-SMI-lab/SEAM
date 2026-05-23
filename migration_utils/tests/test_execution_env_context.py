"""Focused tests for execution_environment_context prompt placeholder and base-aware prompt wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.execution_backend import (
    ContainerBackend,
    LocalBackend,
    get_execution_environment_context,
)
from core.types import ExecutionBackendConfig

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
WORKFLOWS_DIR = ROOT / "workflows"

BASEAWARE_PHASE2 = "phase_2_venv_create_ppu_container_baseaware"
BASEAWARE_PHASE3 = "phase_3_entry_script_ppu_container_baseaware"
BASEAWARE_PHASE35 = "phase_35_static_validate_ppu_baseaware"
OLD_PHASE2 = "phase_2_venv_create_ppu"
OLD_PHASE3 = "phase_3_entry_script_ppu"
OLD_PHASE35 = "phase_35_static_validate_ppu"
PLACEHOLDER = "execution_environment_context"


# ── get_execution_environment_context ────────────────────────────────────


class TestGetExecutionEnvironmentContext:
    def test_local_mode_returns_local_context(self):
        result = get_execution_environment_context(None)
        assert "execution_backend_mode" in result
        assert "local" in result
        assert "Phase 5" in result
        assert "host/local" in result or "host" in result.lower()

    def test_local_backend_returns_local_context(self):
        result = get_execution_environment_context(LocalBackend())
        assert "execution_backend_mode" in result
        assert "local" in result

    def test_container_mode_returns_container_context(self):
        cfg = ExecutionBackendConfig(
            mode="container", source="image", image="test:latest",
            container_workdir="/workspace",
        )
        backend = ContainerBackend(cfg)
        backend._host_project_dir = "/home/user/project"
        backend._container_id = "abc123"

        result = get_execution_environment_context(backend, probe_facts=None)
        assert "execution_backend_mode" in result
        assert "container" in result
        assert "Phase 5" in result
        assert "container" in result.lower()

    def test_container_mode_with_probe_facts(self):
        cfg = ExecutionBackendConfig(
            mode="container", source="image", image="test:latest",
            container_workdir="/workspace",
        )
        backend = ContainerBackend(cfg)
        backend._host_project_dir = "/home/user/project"
        backend._container_id = "abc123"

        probe = {
            "status": "ok",
            "python_version": "3.10.12",
            "torch_version": "2.9.0",
            "platform": "Linux",
            "cwd": "/workspace",
        }
        result = get_execution_environment_context(backend, probe_facts=probe)
        assert "python_version" in result
        assert "3.10.12" in result
        assert "torch_version" in result
        assert "2.9.0" in result

    def test_container_mode_with_failed_probe(self):
        cfg = ExecutionBackendConfig(
            mode="container", source="image", image="test:latest",
            container_workdir="/workspace",
        )
        backend = ContainerBackend(cfg)
        backend._host_project_dir = "/home/user/project"
        backend._container_id = "abc123"

        probe = {"status": "probe_failed", "error": "timeout"}
        result = get_execution_environment_context(backend, probe_facts=probe)
        assert "container" in result.lower()
        assert "probe_failed" in result or "probe" in result.lower()

    def test_local_context_mentions_same_environment(self):
        result = get_execution_environment_context(None)
        assert "same" in result.lower() or "local environment" in result.lower()

    def test_container_context_mentions_container_probe(self):
        cfg = ExecutionBackendConfig(
            mode="container", source="image", image="test:latest",
            container_workdir="/workspace",
        )
        backend = ContainerBackend(cfg)
        backend._host_project_dir = "/home/user/project"
        backend._container_id = "abc123"

        probe = {"status": "ok", "python_version": "3.10.12"}
        result = get_execution_environment_context(backend, probe_facts=probe)
        assert "probe" in result.lower()

    def test_local_context_no_container_specific_terms(self):
        result = get_execution_environment_context(None)
        assert "docker exec" not in result.lower()
        assert "container probe" not in result.lower()


# ── Old prompt files unchanged ───────────────────────────────────────────


class TestOldPromptsNoPlaceholder:
    def test_old_phase2_no_exec_env_context(self):
        content = (PROMPTS_DIR / f"{OLD_PHASE2}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER not in content

    def test_old_phase3_no_exec_env_context(self):
        content = (PROMPTS_DIR / f"{OLD_PHASE3}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER not in content

    def test_old_phase35_no_exec_env_context(self):
        content = (PROMPTS_DIR / f"{OLD_PHASE35}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER not in content


# ── New base-aware prompts have placeholder ──────────────────────────────


class TestBaseAwarePromptsHavePlaceholder:
    def test_phase2_has_exec_env_context(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE2}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER in content

    def test_phase3_has_exec_env_context(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE3}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER in content

    def test_phase35_baseaware_has_exec_env_context(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE35}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER in content


# ── Phase 2 wording: target Phase 5 execution environment ────────────────


class TestPhase2TargetExecutionEnvWording:
    def test_mentions_target_phase5_execution_environment(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE2}.md").read_text(encoding="utf-8")
        assert "Phase 5" in content
        assert "execution environment" in content.lower()

    def test_python_path_callable_in_target_env(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE2}.md").read_text(encoding="utf-8")
        lower = content.lower()
        assert "phase 5" in lower or "target" in lower
        assert "python_path" in lower

    def test_container_mode_non_authoritative_host_tools(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE2}.md").read_text(encoding="utf-8")
        lower = content.lower()
        assert "non-authoritative" in lower or "not necessarily" in lower or "host" in lower


# ── Phase 3 wording: generic execution backend ───────────────────────────


class TestPhase3GenericExecutionBackendWording:
    def test_no_unconditional_container_statement(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE3}.md").read_text(encoding="utf-8")
        assert "this workflow runs inside a framework-created container" not in content.lower()

    def test_mentions_execution_backend_generically(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE3}.md").read_text(encoding="utf-8")
        assert "execution backend" in content.lower() or "execution backend" in content

    def test_mentions_target_execution_environment(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE3}.md").read_text(encoding="utf-8")
        assert "target execution environment" in content or "execution environment" in content.lower()

    def test_no_docker_exec_prohibition(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE3}.md").read_text(encoding="utf-8")
        assert "docker exec" in content.lower() or "podman exec" in content.lower()

    def test_python_path_weakened_from_absolute(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE3}.md").read_text(encoding="utf-8")
        assert "preferred" in content.lower()
        assert "source-of-truth" not in content.lower()


# ── Phase 3.5 base-aware variant exists ─────────────────────────────────


class TestPhase35BaseAwareVariant:
    def test_file_exists(self):
        path = PROMPTS_DIR / f"{BASEAWARE_PHASE35}.md"
        assert path.exists(), f"Phase 3.5 base-aware prompt {path} must exist"

    def test_has_placeholder(self):
        content = (PROMPTS_DIR / f"{BASEAWARE_PHASE35}.md").read_text(encoding="utf-8")
        assert PLACEHOLDER in content


# ── Workflow YAML wiring ────────────────────────────────────────────────


class TestBaseAwareWorkflowPromptWiring:
    def _load_yaml(self):
        import yaml
        return yaml.safe_load(
            (WORKFLOWS_DIR / "ppu_migration_v2_auto_vllm018_smoke_baseaware.yaml").read_text()
        )

    def test_workflow_loads(self):
        wf = self._load_yaml()
        assert wf is not None

    def test_phase35_uses_baseaware_prompt(self):
        wf = self._load_yaml()
        phases = {p["id"]: p for p in wf["phases"]}
        phase35 = phases.get("phase_35_static_validate")
        assert phase35 is not None
        assert phase35["prompt_template"] == BASEAWARE_PHASE35

    def test_phase2_uses_baseaware_prompt(self):
        wf = self._load_yaml()
        phases = {p["id"]: p for p in wf["phases"]}
        phase2 = phases.get("phase_2_venv_create")
        assert phase2 is not None
        assert phase2["prompt_template"] == BASEAWARE_PHASE2

    def test_phase3_uses_baseaware_prompt(self):
        wf = self._load_yaml()
        phases = {p["id"]: p for p in wf["phases"]}
        phase3 = phases.get("phase_3_entry_script")
        assert phase3 is not None
        assert phase3["prompt_template"] == BASEAWARE_PHASE3


# ── PhaseRunner always provides execution_environment_context ─────────────


class TestPhaseRunnerAlwaysProvidesContext:
    def test_basaware_prompt_has_local_default_when_no_orchestrator(self):
        from core.prompt_loader import PromptLoader
        from core.phase_runner import PhaseRunner

        prompt_loader = PromptLoader(str(PROMPTS_DIR))
        runner = PhaseRunner(
            MagicMock(), MagicMock(), prompt_loader, MagicMock(),
            workflow=None, framework_config=None,
        )
        prompt_ctx = runner._build_prompt_context(
            runner.phase_specs["phase_2_venv_create"],
            {"previous_outputs": {}},
        )
        assert "execution_environment_context" in prompt_ctx
        assert "local" in prompt_ctx["execution_environment_context"]

    def test_basaware_phase3_prompt_has_local_default(self):
        from core.phase_runner import PhaseRunner
        from core.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(str(PROMPTS_DIR))
        runner = PhaseRunner(
            MagicMock(), MagicMock(), prompt_loader, MagicMock(),
            workflow=None, framework_config=None,
        )
        prompt_ctx = runner._build_prompt_context(
            runner.phase_specs["phase_3_entry_script"],
            {"previous_outputs": {}},
        )
        assert "execution_environment_context" in prompt_ctx
        assert "local" in prompt_ctx["execution_environment_context"]

    def test_basaware_phase35_prompt_has_local_default(self):
        from core.phase_runner import PhaseRunner
        from core.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(str(PROMPTS_DIR))
        runner = PhaseRunner(
            MagicMock(), MagicMock(), prompt_loader, MagicMock(),
            workflow=None, framework_config=None,
        )
        prompt_ctx = runner._build_prompt_context(
            runner.phase_specs["phase_35_static_validate"],
            {"previous_outputs": {}},
        )
        assert "execution_environment_context" in prompt_ctx
        assert "local" in prompt_ctx["execution_environment_context"]


# ── Container context includes python3 callable ─────────────────────────


class TestContainerContextHasPython3Command:
    def test_container_context_reports_probe_interpreter(self):
        """When probe succeeds, context should state that python3 was
        confirmed callable on the container PATH."""
        cfg = ExecutionBackendConfig(
            mode="container", source="image", image="test:latest",
            container_workdir="/workspace",
        )
        backend = ContainerBackend(cfg)
        backend._host_project_dir = "/home/user/project"
        backend._container_id = "abc123"

        probe = {
            "status": "ok",
            "python_version": "3.10.12",
            "torch_version": "2.9.0",
            "platform": "Linux",
            "cwd": "/workspace",
        }
        result = get_execution_environment_context(backend, probe_facts=probe)
        assert "python3" in result
        assert "callable" in result or "PATH" in result or "probe interpreter" in result.lower()


# ── Accelerator context extraction (shared helper) ────────────────────────


class TestAcceleratorContextExtraction:
    """Tests for ``extract_accelerator_context`` — legacy NPU + platform-neutral PPU/XPU/CUDA."""

    # ── Legacy NPU ────────────────────────────────────────────────────

    def test_torch_npu_version_with_equals(self):
        """torch-npu==2.1.0 → torch_npu_version = '2.1.0'."""
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-npu==2.1.0"])
        assert result["torch_npu_version"] == "2.1.0"
        assert "torch_npu" in result["accelerator_packages"]
        assert result["accelerator_package_versions"].get("torch_npu") == "2.1.0"

    def test_torch_npu_version_with_underscore(self):
        """torch_npu==2.1.0 → torch_npu_version = '2.1.0'."""
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch_npu==2.1.0"])
        assert result["torch_npu_version"] == "2.1.0"

    def test_torch_npu_version_ge(self):
        """torch-npu>=2.1.0 → torch_npu_version = '2.1.0' (any comparator works)."""
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-npu>=2.1.0"])
        assert result["torch_npu_version"] == "2.1.0"

    def test_no_torch_npu_returns_none(self):
        """When no torch-npu/torch_npu package, torch_npu_version is None."""
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["numpy==1.24.0", "requests"])
        assert result["torch_npu_version"] is None
        assert "torch_npu" not in result["accelerator_packages"]

    def test_torch_npu_bare_no_version_returns_none(self):
        """Bare 'torch-npu' without version: accelerator_packages includes it,
        but torch_npu_version is None because the legacy code required a version."""
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-npu"])
        assert result["torch_npu_version"] is None
        assert "torch_npu" in result["accelerator_packages"]
        assert "torch_npu" not in result["accelerator_package_versions"]

    # ── PPU packages ───────────────────────────────────────────────────

    def test_ppukernel_versioned(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["ppukernel==1.2.3"])
        assert "ppukernel" in result["accelerator_packages"]
        assert result["accelerator_package_versions"]["ppukernel"] == "1.2.3"

    def test_torch_ppu_versioned(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-ppu==0.1.0"])
        assert "torch_ppu" in result["accelerator_packages"]
        assert result["accelerator_package_versions"]["torch_ppu"] == "0.1.0"

    def test_torch_ppu_underscore_form(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch_ppu==0.2.0"])
        assert "torch_ppu" in result["accelerator_packages"]
        assert result["accelerator_package_versions"]["torch_ppu"] == "0.2.0"

    def test_ppuccl(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["ppuccl==1.0.0"])
        assert "ppuccl" in result["accelerator_packages"]
        assert result["accelerator_package_versions"]["ppuccl"] == "1.0.0"

    def test_bare_ppu_package_name(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["ppu"])
        assert "ppu" in result["accelerator_packages"]
        assert "ppu" not in result["accelerator_package_versions"]

    # ── PPU smoke scenario (vLLM + torch + PPU, no torch-npu) ────────

    def test_ppu_smoke_scenario(self):
        """Simulate a PPU smoke installed_packages: vllm, torch, ppukernel, ppuccl, cuda."""
        from core.accelerator_context import extract_accelerator_context

        pkgs = [
            "vllm==0.18.0",
            "torch==2.9.0",
            "ppukernel==1.0.0",
            "ppuccl==1.0.0",
            "cuda",  # bare package name (PEP 508; also realistic pip freeze output)
        ]
        result = extract_accelerator_context(pkgs)

        assert result["torch_npu_version"] is None
        assert "vllm" in result["accelerator_packages"]
        assert "torch" in result["accelerator_packages"]
        assert "ppukernel" in result["accelerator_packages"]
        assert "ppuccl" in result["accelerator_packages"]
        assert "cuda" in result["accelerator_packages"]

        assert result["accelerator_package_versions"]["vllm"] == "0.18.0"
        assert result["accelerator_package_versions"]["torch"] == "2.9.0"
        assert result["accelerator_package_versions"]["ppukernel"] == "1.0.0"

    # ── Mixed NPU + PPU ───────────────────────────────────────────────

    def test_mixed_npu_and_ppu(self):
        from core.accelerator_context import extract_accelerator_context

        pkgs = [
            "torch-npu==2.1.0",
            "torch-ppu==0.1.0",
            "ppukernel==1.0.0",
            "vllm==0.18.0",
            "torch==2.9.0",
        ]
        result = extract_accelerator_context(pkgs)

        assert result["torch_npu_version"] == "2.1.0"
        assert set(result["accelerator_packages"]) >= {
            "torch_npu", "torch_ppu", "ppukernel", "vllm", "torch",
        }

    # ── Edge cases ─────────────────────────────────────────────────────

    def test_empty_list(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context([])
        assert result["torch_npu_version"] is None
        assert result["accelerator_packages"] == []
        assert result["accelerator_package_versions"] == {}

    def test_none_input(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(None)
        assert result["torch_npu_version"] is None
        assert result["accelerator_packages"] == []
        assert result["accelerator_package_versions"] == {}

    def test_non_string_entries_ignored(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-npu==2.1.0", 42, None, ["ppukernel"]])
        assert result["torch_npu_version"] == "2.1.0"
        assert "torch_npu" in result["accelerator_packages"]
        assert len(result["accelerator_packages"]) == 1

    def test_all_fields_json_serializable(self):
        import json
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-npu==2.1.0", "ppukernel==1.0.0"])
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        assert "torch_npu_version" in serialized
        assert "accelerator_packages" in serialized
        assert "accelerator_package_versions" in serialized

    def test_hyphen_underscore_equivalence(self):
        """torch-npu and torch_npu normalize to the same name."""
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch-npu==2.1.0", "torch_npu==2.2.0"])
        assert result["torch_npu_version"] == "2.1.0"  # first wins
        # Both normalize to "torch_npu", so only one entry in the list
        assert result["accelerator_packages"].count("torch_npu") == 1

    def test_triton_package(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["triton==2.1.0"])
        assert "triton" in result["accelerator_packages"]
        assert result["accelerator_package_versions"]["triton"] == "2.1.0"

    def test_cuda_ecosystem_packages(self):
        from core.accelerator_context import extract_accelerator_context

        pkgs = ["cuda", "cudnn==8.9.0", "nccl==2.18.0"]
        result = extract_accelerator_context(pkgs)
        assert "cuda" in result["accelerator_packages"]
        assert "cudnn" in result["accelerator_packages"]
        assert "nccl" in result["accelerator_packages"]


# ── orchestrator._build_env_context integration ──────────────────────────


class TestOrchestratorBuildEnvContext:
    def test_includes_legacy_torch_npu_version(self):
        from core.orchestrator import Orchestrator

        result = Orchestrator._build_env_context(
            {"os": "Linux"},
            {"installed_packages": ["torch-npu==2.1.0"]},
        )
        assert result["torch_npu_version"] == "2.1.0"
        assert result["os"] == "Linux"

    def test_includes_accelerator_packages(self):
        from core.orchestrator import Orchestrator

        result = Orchestrator._build_env_context(
            {"os": "Linux"},
            {"installed_packages": ["ppukernel==1.0.0", "torch-ppu==0.1.0"]},
        )
        assert result["torch_npu_version"] is None
        assert "ppukernel" in result["accelerator_packages"]
        assert "torch_ppu" in result["accelerator_packages"]

    def test_includes_accelerator_package_versions(self):
        from core.orchestrator import Orchestrator

        result = Orchestrator._build_env_context(
            {"os": "Linux"},
            {"installed_packages": ["ppukernel==1.0.0"]},
        )
        assert result["accelerator_package_versions"]["ppukernel"] == "1.0.0"

    def test_empty_accelerator_packages_when_no_match(self):
        from core.orchestrator import Orchestrator

        result = Orchestrator._build_env_context(
            {"os": "Linux"},
            {"installed_packages": ["numpy==1.24.0", "requests"]},
        )
        assert result["torch_npu_version"] is None
        assert result["accelerator_packages"] == []
        assert result["accelerator_package_versions"] == {}

    def test_ppu_smoke_scenario(self):
        """Full PPU smoke scenario: no torch-npu, has ppukernel, vllm, torch, cuda."""
        from core.orchestrator import Orchestrator

        result = Orchestrator._build_env_context(
            {"os": "Linux"},
            {"installed_packages": [
                "vllm==0.18.0",
                "torch==2.9.0",
                "ppukernel==1.0.0",
                "ppuccl==1.0.0",
                "cuda",
            ]},
        )
        assert result["torch_npu_version"] is None
        assert "vllm" in result["accelerator_packages"]
        assert "ppukernel" in result["accelerator_packages"]
        assert "ppuccl" in result["accelerator_packages"]


# ── workflow_executor._build_env_context integration ─────────────────────


class TestWorkflowExecutorBuildEnvContext:
    def test_includes_legacy_torch_npu_version(self):
        from core.workflow_executor import WorkflowExecutor

        wfe = WorkflowExecutor.__new__(WorkflowExecutor)
        result = wfe._build_env_context({
            "phase_0_env_detect": {"os": "Linux"},
            "phase_2_venv_create": {"installed_packages": ["torch-npu==2.1.0"]},
        })
        assert result["torch_npu_version"] == "2.1.0"
        assert result["os"] == "Linux"

    def test_includes_accelerator_packages_and_versions(self):
        from core.workflow_executor import WorkflowExecutor

        wfe = WorkflowExecutor.__new__(WorkflowExecutor)
        result = wfe._build_env_context({
            "phase_0_env_detect": {},
            "phase_2_venv_create": {
                "installed_packages": [
                    "torch-ppu==0.1.0",
                    "ppukernel==1.0.0",
                    "numpy==1.24.0",
                ],
            },
        })
        assert result["torch_npu_version"] is None
        assert "torch_ppu" in result["accelerator_packages"]
        assert "ppukernel" in result["accelerator_packages"]
        assert result["accelerator_package_versions"]["torch_ppu"] == "0.1.0"
        assert result["accelerator_package_versions"]["ppukernel"] == "1.0.0"

    def test_no_phase2_returns_empty(self):
        from core.workflow_executor import WorkflowExecutor

        wfe = WorkflowExecutor.__new__(WorkflowExecutor)
        result = wfe._build_env_context({
            "phase_0_env_detect": {"os": "Linux"},
        })
        assert result["torch_npu_version"] is None
        assert result["accelerator_packages"] == []
        assert result["accelerator_package_versions"] == {}

    def test_npu_ppu_mixed_behavior_identical_to_orchestrator(self):
        from core.orchestrator import Orchestrator
        from core.workflow_executor import WorkflowExecutor

        packages = ["torch-npu==2.1.0", "torch-ppu==0.1.0", "ppukernel==1.0.0", "vllm==0.18.0"]

        orch_result = Orchestrator._build_env_context(
            {"os": "Linux"},
            {"installed_packages": packages},
        )

        wfe = WorkflowExecutor.__new__(WorkflowExecutor)
        wf_result = wfe._build_env_context({
            "phase_0_env_detect": {"os": "Linux"},
            "phase_2_venv_create": {"installed_packages": packages},
        })

        assert orch_result["torch_npu_version"] == wf_result["torch_npu_version"]
        assert set(orch_result["accelerator_packages"]) == set(wf_result["accelerator_packages"])
        assert orch_result["accelerator_package_versions"] == wf_result["accelerator_package_versions"]

