# pyright: reportUnusedCallResult=false

"""Tests for PPU migration support: prompts, workflow YAML, rule-based migrator, validator compatibility, and executor."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import load_workflow
from core.prompt_loader import PromptLoader
from core.workflow_executor import WorkflowExecutor
from core.types import PhaseDefinition, WorkflowDefinition

ROOT = PROJECT_ROOT
WORKFLOWS_DIR = ROOT / "workflows"
PROMPTS_DIR = ROOT / "prompts"

PPU_PROMPT_FILES = frozenset({
    "phase_0_env_detect_ppu",
    "phase_1_project_analysis_ppu",
    "phase_1_5_constraint_summary_ppu",
    "phase_2_venv_create_ppu",
    "phase_3_entry_script_ppu",
    "phase_35_static_validate_ppu",
    "phase_error_recovery_container_ppu",
    "repair_dependency_fixer_container_ppu",
    "repair_code_adapter_container_ppu",
    "repair_operator_fixer_container_ppu",
    "phase_5_review_container_ppu",
    "phase_review_improvement_container_ppu",
    "phase_6_report_ppu",
})

PPU_CONTAINER_PHASE5_PROMPTS = frozenset({
    "phase_error_recovery_container_ppu",
    "repair_dependency_fixer_container_ppu",
    "repair_code_adapter_container_ppu",
    "repair_operator_fixer_container_ppu",
    "phase_5_review_container_ppu",
    "phase_review_improvement_container_ppu",
})

FORBIDDEN_PPU_PHRASES = (
    "AscendC",
    "AscendC kernel development",
)

PYPI_DANGER_PACKAGES = (
    "torch", "vllm", "sglang", "sgl-kernel", "flash_attn",
    "flashinfer-python", "deep_gemm", "deep_ep", "flash_mla",
    "triton", "xgrammar", "torchao",
)


class TestPPUPromptFilesExist:

    @pytest.mark.parametrize("name", sorted(PPU_PROMPT_FILES))
    def test_ppu_prompt_file_exists(self, name):
        p = PROMPTS_DIR / f"{name}.md"
        assert p.exists(), f"PPU prompt {p} must exist"


class TestPPUPromptContent:

    @pytest.mark.parametrize("name", sorted(PPU_PROMPT_FILES))
    def test_ppu_prompts_no_affirmative_npu_targets(self, name):
        content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        for line in content.split("\n"):
            lower = line.lower()
            for term in ("torch_npu", "torch.npu"):
                if term.lower() in lower:
                    assert any(neg in lower for neg in ("do not", "not ", "not install", "do not install", "not use")), (
                        f"{name}: '{term}' found outside negation context: {line.strip()}"
                    )

    @pytest.mark.parametrize("name", sorted(PPU_PROMPT_FILES))
    def test_ppu_prompts_no_ascendc_targets(self, name):
        content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        for line in content.split("\n"):
            lower = line.lower()
            if "ascendc" in lower:
                stripped = line.strip()
                ascendc_field = "ascendc_available" in stripped and not ("ascendc" in stripped.replace("ascendc_available", ""))
                if ascendc_field:
                    continue  # field name "ascendc_available" is OK as compat key
                assert any(neg in lower for neg in ("do not", "not ", "must not", "not require", "does not use")), (
                    f"{name}: 'AscendC' found outside negation context: {stripped}"
                )

    def test_env_detect_ppu_states_cuda_compatible(self):
        content = (PROMPTS_DIR / "phase_0_env_detect_ppu.md").read_text(encoding="utf-8")
        assert "torch.cuda" in content
        assert "ppu" in content.lower()

    def test_env_detect_ppu_positive_ppu_evidence(self):
        content = (PROMPTS_DIR / "phase_0_env_detect_ppu.md").read_text(encoding="utf-8")
        assert "PPU-ZW810" in content

    def test_env_detect_ppu_outputs_compat_fields(self):
        content = (PROMPTS_DIR / "phase_0_env_detect_ppu.md").read_text(encoding="utf-8")
        assert "npu_detected" in content
        assert "cann_version" in content
        assert "ascendc_available" in content
        assert "driver_version" in content

    def test_env_detect_ppu_no_cpu_platform_value(self):
        content = (PROMPTS_DIR / "phase_0_env_detect_ppu.md").read_text(encoding="utf-8")
        assert "cpu otherwise" not in content.lower(), (
            "PPU env_detect must not suggest platform=cpu; validator only accepts ppu/cuda/npu"
        )
        assert "platform" not in content.lower().split("cpu")[0].lower() or '"cpu"' not in content.lower(), (
            "PPU env_detect must not list cpu as a valid platform value"
        )

    def test_venv_create_ppu_warnings_against_public_pypi(self):
        content = (PROMPTS_DIR / "phase_2_venv_create_ppu.md").read_text(encoding="utf-8")
        for pkg in ("vllm", "sglang", "flash_attn", "triton", "xgrammar", "torch_npu"):
            assert pkg in content, f"PPU venv prompt should warn about {pkg}"

    def test_dependency_fixer_container_ppu_has_pypi_protection(self):
        content = (PROMPTS_DIR / "repair_dependency_fixer_container_ppu.md").read_text(encoding="utf-8")
        assert "dry-run" in content.lower() or "dry_run" in content.lower() or "dry run" in content.lower()
        for pkg in ("vllm", "sglang", "flash_attn", "triton", "torch"):
            assert pkg in content, f"Dependency fixer must mention {pkg} risk"

    def test_code_adapter_ppu_preserves_cuda(self):
        content = (PROMPTS_DIR / "repair_code_adapter_container_ppu.md").read_text(encoding="utf-8")
        assert "Do NOT change `torch.cuda` to `torch.npu`" in content
        assert "AscendC" not in content, "code_adapter PPU must not contain AscendC"
        for placeholder in ("{execution_backend_mode}", "{actual_execution_command}",
                            "{container_name_or_id}", "{container_workdir}",
                            "{host_project_dir}", "{container_project_dir}"):
            assert placeholder in content, f"Container PPU prompt missing {placeholder}"

    def test_phase_3_ppu_entry_script_forbids_container_runtime(self):
        content = (PROMPTS_DIR / "phase_3_entry_script_ppu.md").read_text(encoding="utf-8")
        assert "docker exec" in content.lower()
        assert "podman exec" in content.lower()
        assert "in-container" in content.lower() or "inside" in content.lower()

    def test_operator_fixer_ppu_wraps_custom_op_guidance(self):
        content = (PROMPTS_DIR / "repair_operator_fixer_container_ppu.md").read_text(encoding="utf-8")
        assert "PPU" in content
        assert "AscendC" not in content or "NOT follow AscendC" in content, (
            "operator_fixer PPU must not affirmatively follow AscendC guidance"
        )

    def test_container_ppu_prompts_have_execution_context(self):
        for name in PPU_CONTAINER_PHASE5_PROMPTS:
            content = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
            assert "execution_backend_mode" in content
            assert "actual_execution_command" in content
            assert "container_name_or_id" in content


class TestPPUWorkflowYAML:

    def test_ppu_workflow_loads(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container.yaml")
        wf = load_workflow(wf_path)
        assert wf.name == "ppu_migration_container"

    def test_ppu_workflow_prompts_are_ppu(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container.yaml")
        wf = load_workflow(wf_path)
        assert wf.sub_workflows is not None
        repair_loop_def = wf.sub_workflows.get("repair_loop")
        assert repair_loop_def is not None

        for pid in repair_loop_def.phases:
            if pid.get("type") == "llm" and "prompt_template" in pid:
                template = pid.get("prompt_template", "")
                assert template.endswith("_ppu"), (
                    f"PPU workflow phase {pid['id']} must use a _ppu prompt, got {template}"
                )

        blocks = repair_loop_def.blocks or {}
        improvement = blocks.get("improvement_block")
        if improvement:
            for phase in improvement.get("phases", []):
                if "prompt_template" in phase:
                    template = phase["prompt_template"]
                    assert template.endswith("_ppu"), (
                        f"PPU workflow improvement_block phase {phase['id']} must use _ppu prompt"
                    )

    def test_ppu_workflow_phase4_uses_ppu_operation(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container.yaml")
        wf = load_workflow(wf_path)
        phase_4 = None
        for phase in wf.phases:
            if phase.id == "phase_4_rule_migration":
                phase_4 = phase
                break
        assert phase_4 is not None
        params = phase_4.params or {}
        operation = params.get("operation", "")
        assert operation == "ppu_rule_based_migration"

    def test_ppu_workflow_no_npu_runtime_skills(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container.yaml")
        with open(wf_path, encoding="utf-8") as f:
            content = f.read()
        assert "cuda-custom-op-to-npu-custom-op" not in content, (
            "PPU workflow must not reference NPU custom-op skill"
        )

    def test_ppu_workflow_no_npu_prompts(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container.yaml")
        with open(wf_path, encoding="utf-8") as f:
            content = f.read()
        for orig in ("phase_error_recovery", "repair_dependency_fixer",
                      "repair_code_adapter", "repair_operator_fixer",
                      "phase_5_review"):
            assert f'"{orig}"' not in content and f"'{orig}'" not in content, (
                f"PPU workflow must not reference original NPU prompt {orig}"
            )

    def test_ppu_container_workflow_uses_source_image(self):
        """PPU workflows MUST create new containers from images.
        Must not contain even commented-out 'existing_container' references."""
        for wf_name in ("ppu_migration_v2_container.yaml", "ppu_migration_v2_container_vllm018_smoke.yaml"):
            content = (WORKFLOWS_DIR / wf_name).read_text(encoding="utf-8")
            assert "source: image" in content, f"{wf_name} must have 'source: image' as active config"
            assert "existing_container" not in content, (
                f"{wf_name}: PPU workflows must not contain 'existing_container' "
                "anywhere (even in comments) — copy-paste risk"
            )

    def test_npu_workflow_unchanged(self):
        wf_path = str(WORKFLOWS_DIR / "npu_migration_v2.yaml")
        wf = load_workflow(wf_path)
        phase_4 = None
        for phase in wf.phases:
            if phase.id == "phase_4_rule_migration":
                phase_4 = phase
                break
        assert phase_4 is not None
        params = phase_4.params or {}
        operation = params.get("operation", "")
        assert operation == "rule_based_migration"


class TestValidatorCompatibility:

    @pytest.fixture
    def validator(self):
        from validators.validate_env_detect import validate
        return validate

    def test_npu_schema_still_passes(self, validator):
        result = validator({
            "platform": "npu",
            "npu_detected": True,
            "python_version": "3.10.12",
            "cann_version": "8.0.RC1",
            "ascendc_available": False,
            "driver_version": "25.0.rc1.1",
        })
        assert result["passed"] is True
        assert not result["errors"]

    def test_cuda_schema_still_passes(self, validator):
        result = validator({
            "platform": "cuda",
            "cuda_detected": True,
            "python_version": "3.10.12",
        })
        assert result["passed"] is True

    def test_ppu_schema_passes(self, validator):
        result = validator({
            "platform": "ppu",
            "ppu_detected": True,
            "cuda_api_available": True,
            "python_version": "3.10.12",
            "device_name": "PPU-ZW810",
            "npu_detected": False,
            "cann_version": "n/a",
            "ascendc_available": False,
            "driver_version": "not_applicable",
        })
        assert result["passed"] is True, f"PPU schema failed: {result['errors']}"

    def test_invalid_platform_fails(self, validator):
        for bad_platform in ("gpu", "xpu", "tpu", "ppu-npu"):
            result = validator({
                "platform": bad_platform,
                "npu_detected": False,
                "ppu_detected": True,
                "cuda_api_available": True,
                "python_version": "3.10.12",
                "cann_version": "n/a",
                "ascendc_available": False,
                "driver_version": "n/a",
            })
            assert result["passed"] is False, f"platform={bad_platform} should fail"

    def test_ppu_missing_required_fields_fails(self, validator):
        result = validator({
            "platform": "ppu",
            "npu_detected": False,
        })
        assert result["passed"] is False


class TestPPURuleBasedMigrator:

    @pytest.fixture
    def migrator(self):
        from migrator.rule_based_ppu import PPURuleBasedMigrator
        return PPURuleBasedMigrator()

    def test_does_not_convert_torch_cuda(self, migrator):
        code = "import torch\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert result == code, "PPU migrator must not change torch.cuda code"

    def test_does_not_convert_cuda_methods(self, migrator):
        code = "model.cuda()\ntensor.cuda(device=0)"
        result, report = migrator.migrate(code)
        assert result == code, "PPU migrator must not change .cuda() calls"

    def test_does_not_inject_torch_npu(self, migrator):
        code = "import torch\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert "import torch_npu" not in result
        assert "inject_torch_npu" not in report["rules"]

    def test_no_invented_commands(self, migrator):
        code = 'subprocess.run(["nvidia-smi"])'
        result, report = migrator.migrate(code)
        assert "ppu-device-query" not in result
        assert result == code

    def test_reports_nvidia_smi_references(self, migrator):
        code = 'subprocess.run(["nvidia-smi"])\nx = torch.cuda.is_available()'
        result, report = migrator.migrate(code)
        assert result == code
        assert report["total_replacements"] == 0
        assert report["rules"].get("nvidia_smi_references", 0) >= 1

    def test_report_shape_compatible(self, migrator):
        code = 'subprocess.run(["nvidia-smi"])\nx = torch.cuda.is_available()'
        result, report = migrator.migrate(code)
        assert "rules" in report
        assert "total_replacements" in report
        assert "nvidia_smi_references" in report["rules"]

    def test_directory_migration_does_not_modify_files(self, migrator, tmp_path):
        f1 = tmp_path / "original.py"
        original_content = 'import torch\nsubprocess.run(["nvidia-smi"])'
        f1.write_text(original_content)
        report = migrator.migrate_directory(str(tmp_path))
        assert report["summary"]["total_files"] == 1
        assert report["summary"]["total_replacements"] == 0
        assert f1.read_text() == original_content

    def test_no_nccl_to_hccl_replacement(self, migrator):
        code = 'dist.init_process_group("nccl")'
        result, report = migrator.migrate(code)
        assert '"nccl"' in result
        assert "hccl" not in result


class TestWorkflowExecutorPPUSelection:

    @pytest.fixture
    def temp_dir(self, tmp_path):
        return str(tmp_path)

    @pytest.fixture
    def executor(self, temp_dir):
        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=[])
        return WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=temp_dir, output_dir=temp_dir,
        )

    def test_ppu_rule_based_migration_operation(self, executor, temp_dir):
        code = "import torch\nx = torch.cuda.is_available()\nsubprocess.run(['nvidia-smi'])"
        (Path(temp_dir) / "sample.py").write_text(code)
        phase = PhaseDefinition(
            id="test", name="test", prompt_template="", output_schema={},
            type="builtin", params={"operation": "ppu_rule_based_migration", "pattern": "*.py"},
        )
        status, result = executor._execute_builtin_phase(phase, {}, {})
        assert status == "success"
        assert result["operation"] == "ppu_rule_based_migration"
        assert result.get("backend") == "ppu"
        content = (Path(temp_dir) / "sample.py").read_text()
        assert content == code, "PPU migrator must not modify source"
        assert "torch.cuda.is_available()" in content

    def test_rule_based_migration_with_backend_ppu(self, executor, temp_dir):
        code = "import torch\nx = torch.cuda.is_available()\nsubprocess.run(['nvidia-smi'])"
        (Path(temp_dir) / "sample.py").write_text(code)
        phase = PhaseDefinition(
            id="test", name="test", prompt_template="", output_schema={},
            type="builtin", params={
                "operation": "rule_based_migration",
                "backend": "ppu",
                "pattern": "*.py",
            },
        )
        status, result = executor._execute_builtin_phase(phase, {}, {})
        assert status == "success"
        assert result.get("backend") == "ppu"
        content = (Path(temp_dir) / "sample.py").read_text()
        assert content == code

    def test_rule_based_migration_without_backend_uses_report_only_safe_default(self, executor, temp_dir):
        original = "import torch\nx = torch.cuda.is_available()\n"
        (Path(temp_dir) / "sample.py").write_text(original)
        phase = PhaseDefinition(
            id="test", name="test", prompt_template="", output_schema={},
            type="builtin", params={
                "operation": "rule_based_migration",
                "pattern": "*.py",
            },
        )
        status, result = executor._execute_builtin_phase(phase, {}, {})
        assert status == "success"
        assert result["operation"] == "rule_based_migration"
        content = (Path(temp_dir) / "sample.py").read_text()
        assert content == original, "Without explicit backend, report_only safe default must not modify files"
        assert result.get("strategy") == "report_only"


VLLM018_IMAGE = "egslingjun-registry.cn-wulanchabu.cr.aliyuncs.com/egslingjun/inference-xpu-pytorch:26.04-v2.1.0-vllm0.18.0-torch2.9-cu130-20260508"


class TestPPUSmokeWorkflow:

    def test_smoke_workflow_loads(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container_vllm018_smoke.yaml")
        wf = load_workflow(wf_path)
        assert wf.name == "ppu_migration_vllm018_smoke"

    def test_smoke_workflow_uses_vllm018_image(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container_vllm018_smoke.yaml")
        wf = load_workflow(wf_path)
        backend = wf.execution_backend
        assert backend is not None
        assert backend.image == VLLM018_IMAGE, (
            f"Expected vLLM 0.18 image, got {backend.image}"
        )

    def test_smoke_workflow_prompts_are_ppu(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container_vllm018_smoke.yaml")
        wf = load_workflow(wf_path)
        assert wf.sub_workflows is not None
        repair_loop_def = wf.sub_workflows["repair_loop"]
        for pid in repair_loop_def.phases:
            if pid.get("type") == "llm" and "prompt_template" in pid:
                template = pid["prompt_template"]
                assert template.endswith("_ppu"), (
                    f"Smoke workflow phase {pid['id']} must use _ppu prompt, got {template}"
                )

    def test_smoke_workflow_phase4_uses_ppu_operation(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container_vllm018_smoke.yaml")
        wf = load_workflow(wf_path)
        phase_4 = next((p for p in wf.phases if p.id == "phase_4_rule_migration"), None)
        assert phase_4 is not None
        params = phase_4.params or {}
        assert params.get("operation") == "ppu_rule_based_migration"

    def test_smoke_workflow_no_npu_prompts_in_content(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container_vllm018_smoke.yaml")
        with open(wf_path, encoding="utf-8") as f:
            content = f.read()
        for orig in ("phase_error_recovery\"", "repair_dependency_fixer\"",
                      "repair_code_adapter\"", "repair_operator_fixer\"",
                      "phase_5_review\""):
            assert orig not in content, (
                f"Smoke workflow must not reference NPU prompt {orig}"
            )
        assert "cuda-custom-op-to-npu-custom-op" not in content

    def test_smoke_workflow_main_phases_use_ppu_prompts(self):
        wf_path = str(WORKFLOWS_DIR / "ppu_migration_v2_container_vllm018_smoke.yaml")
        wf = load_workflow(wf_path)
        for phase in wf.phases:
            if phase.type == "builtin":
                continue
            if phase.prompt_template:
                assert phase.prompt_template.endswith("_ppu"), (
                    f"Phase {phase.id} must use _ppu prompt, got {phase.prompt_template}"
                )

    def test_smoke_project_files_exist(self):
        project_root = Path(__file__).resolve().parent.parent.parent.parent / "application_migration_cases" / "SEAM_PPU_SMOKE"
        if not project_root.is_dir():
            pytest.skip("External corpus application_migration_cases/SEAM_PPU_SMOKE is absent — skip smoke project file check")
        for fname in ("README.md", "requirements.txt", "smoke_validate.py", "ppu_target.py"):
            fpath = project_root / fname
            assert fpath.exists(), f"Smoke project file missing: {fpath}"


class TestPPUPromptBaseEnvFirst:
    """PPU prompts must prefer container base environment over forced .venv."""

    def test_phase_2_venv_create_mentions_base_environment(self):
        content = (PROMPTS_DIR / "phase_2_venv_create_ppu.md").read_text(encoding="utf-8")
        lower = content.lower()
        assert "base environment" in lower or "base python" in lower or "base env" in lower, (
            "Phase 2 PPU prompt must mention base environment as the preferred option"
        )

    def test_phase_2_venv_create_venv_not_mandatory(self):
        content = (PROMPTS_DIR / "phase_2_venv_create_ppu.md").read_text(encoding="utf-8")
        assert "Create or reuse a virtual environment" not in content, (
            "Phase 2 PPU prompt must not mandate 'Create or reuse a virtual environment' as a strict instruction"
        )

    def test_repair_dependency_base_env_first(self):
        content = (PROMPTS_DIR / "repair_dependency_fixer_container_ppu.md").read_text(encoding="utf-8")
        lower = content.lower()
        assert "base" in lower and ("environment" in lower or "env" in lower), (
            "Dependency fixer must mention base environment"
        )
        assert "install into `.venv` only" not in content, (
            "Dependency fixer must not say 'install into .venv only'"
        )

    def test_repair_code_adapter_uses_actual_execution_command(self):
        content = (PROMPTS_DIR / "repair_code_adapter_container_ppu.md").read_text(encoding="utf-8")
        assert "actual_execution_command" in content, (
            "Code adapter must reference actual_execution_command"
        )
        assert ".venv/bin/python" not in content, (
            "Code adapter must not hardcode .venv/bin/python"
        )

    def test_phase3_entry_script_base_env(self):
        content = (PROMPTS_DIR / "phase_3_entry_script_ppu.md").read_text(encoding="utf-8")
        lower = content.lower()
        assert "base python" in lower or "base env" in lower or "container base" in lower, (
            "Phase 3 PPU prompt must reference base Python/env as default"
        )
        assert ".venv" not in content.lower().split("explicitly")[0].lower() or "only if" in lower, (
            "Phase 3 should not mandate .venv; it is optional"
        )
