import sys
from pathlib import Path
from collections.abc import Callable
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import repair_loop
from core.prompt_loader import PromptLoader

PROMPTS_DIR = PROJECT_ROOT / "prompts"

COMMON_CONTEXT = {
    "repair_role": "dependency_fixer",
    "entry_script": "python train.py",
    "project_dir": "/tmp/test_project",
    "iteration": "1",
    "category": "dependency",
    "root_cause": "torch_npu missing",
    "suggested_fix": "pip install torch_npu",
    "error_text": "ModuleNotFoundError: No module named 'torch_npu'",
    "history_summary": "[]",
    "constraint_summary": "Rule 1: No CPU fallback",
    "last_review": "(No review available)",
    "env_context": "{}",
    "runtime_error_artifact_path": "/tmp/test_project/.sm-artifacts/testrun/runtime/runtime_error_test_project.md",
    "runtime_card_artifact_path": "/tmp/test_project/.sm-artifacts/testrun/runtime/runtimeCard_test_project.md",
    "workspace_root": "/workspace",
    "operator_custom_op_guidance": repair_loop._operator_generic_guidance(
        project_dir="/tmp/test_project",
        entry_script="python train.py",
    ),
}


def _load_role_prompt(loader: PromptLoader, role: str) -> str:
    prompt_ids = cast(dict[str, str], getattr(repair_loop, "_REPAIR_PROMPT_IDS"))
    prompt_id = prompt_ids[role]
    context = {**COMMON_CONTEXT, "repair_role": role}
    return loader.load_prompt(prompt_id, context)


def test_different_roles_receive_different_prompts() -> None:
    """Each repair role loads a distinct prompt file with unique content."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompts = {}
    for role in ("dependency_fixer", "code_adapter", "operator_fixer"):
        prompts[role] = _load_role_prompt(loader, role)

    assert prompts["dependency_fixer"] != prompts["code_adapter"]
    assert prompts["code_adapter"] != prompts["operator_fixer"]
    assert prompts["dependency_fixer"] != prompts["operator_fixer"]


def test_operator_fixer_prompt_is_generic_without_custom_op_guidance() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompt = _load_role_prompt(loader, "operator_fixer")
    assert "This is a generic operator-incompatibility repair" in prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in prompt
    assert "第1、2、3、5、6、7点要求" not in prompt
    assert ".skills" not in prompt
    assert "/tmp/test_project/.sm-artifacts/testrun/runtime/runtime_error_test_project.md" in prompt
    assert "/tmp/test_project/.sm-artifacts/testrun/runtime/runtimeCard_test_project.md" in prompt
    assert "bounded operator context" not in prompt
    assert "inventory / manifest / final-gate" not in prompt.lower()
    assert "source_inventory" not in prompt
    assert "migration_manifest" not in prompt
    assert "custom_op_final_gate" not in prompt
    assert "repair_role" not in prompt
    assert "category" not in prompt
    assert "root_cause" not in prompt
    assert "suggested_fix" not in prompt
    assert "constraint_summary" not in prompt
    assert "env_context" not in prompt
    assert "last_review" not in prompt
    assert "operator_fixer" not in prompt
    assert "torch_npu missing" not in prompt
    assert "pip install torch_npu" not in prompt
    assert "ModuleNotFoundError" not in prompt
    assert "Rule 1: No CPU fallback" not in prompt
    assert "(No review available)" not in prompt
    assert "/tmp/test_project" in prompt
    assert "python train.py" in prompt
    assert "Ascend NPU 原生修复" in prompt
    assert "No active custom-op contract is present" in prompt
    assert "不要生成 OPP/custom-op 产物" in prompt
    assert "active custom-op contract" in prompt
    assert "严格 Ascend C/CANN OPP custom operator" not in prompt
    assert "op_host" not in prompt
    assert "op_kernel" not in prompt
    assert "NpuExtension" not in prompt
    assert "ATen-only npu_ops.cpp" not in prompt
    assert "adapter evidence" not in prompt
    assert "CPU fallback" in prompt
    assert "不要启动后台检索/后台 agents 后提前返回" in prompt
    assert "modified_files: []" in prompt
    assert "modified_files" in prompt
    assert "summary" in prompt
    assert "agent_diagnostics" in prompt
    assert "## Analyzer-Selected Experience Action Cards" not in prompt
    assert "Experience Card" not in prompt


def test_operator_fixer_prompt_can_receive_custom_op_guidance() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    context = {
        **COMMON_CONTEXT,
        "repair_role": "operator_fixer",
        "operator_custom_op_guidance": repair_loop._operator_custom_op_guidance(
            "/tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md",
            project_dir="/tmp/test_project",
            entry_script="python train.py",
        ),
    }
    prompt = loader.load_prompt("repair_operator_fixer", context)
    assert "bounded operator context" in prompt
    assert "Active custom-op contract is present" in prompt
    assert "/tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md" in prompt
    assert "inventory / manifest / final-gate" in prompt.lower()
    assert "freeze manifest rows" in prompt
    assert "inventory_count == manifest_entries == closed_pass_entries" in prompt
    assert "remaining_entries == 0" in prompt
    assert "full_migration_status == FULL_PASS" in prompt
    assert "same-run runtime coverage > 0" in prompt
    assert "CPU baseline runtime against Ascend OPP/custom-op runtime" in prompt
    assert "strict Ascend C/CANN OPP custom operator producer evidence" in prompt
    assert "op_host source path" in prompt
    assert "op_kernel/AscendC source path" in prompt
    assert "CMakeLists.txt/build.sh" in prompt
    assert "CANN/OPP build-install logs" in prompt
    assert "generated header/op_info/kernel_meta/producer/package artifacts" in prompt
    assert "NpuExtension" in prompt
    assert "CppExtension" in prompt
    assert "ATen-only npu_ops.cpp" in prompt
    assert "not opp_custom_op_artifact_evidence" in prompt
    assert "adapter evidence" in prompt
    assert "report-only" in prompt
    assert "MVP-only" in prompt
    assert "zero-call" in prompt
    assert "modified_files 必须列出实际修改文件" in prompt
    assert "FAILED/INCOMPLETE" in prompt
    assert "self-baseline" in prompt
    assert "speedup_vs_baseline" in prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in prompt
    assert ".skills" not in prompt


def test_generated_custom_op_guidance_rejects_evidence_only_marker_artifacts() -> None:
    guidance_factory = cast(Callable[..., str], repair_loop.__dict__["_operator_custom_op_guidance"])
    guidance = guidance_factory(
        "/tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md",
        project_dir="/tmp/test_project",
        entry_script="python validate_custom_ops_full.py",
    )

    assert "Evidence-only marker shims" in guidance
    assert "*_evidence*" in guidance
    assert "stub/dummy/fake placeholder native libraries" in guidance
    assert "synthetic success codes" in guidance
    assert "strict Ascend C/CANN OPP custom operator producer evidence" in guidance
    assert "op_host source path" in guidance
    assert "op_kernel/AscendC source path" in guidance
    assert "NpuExtension" in guidance
    assert "ATen-only npu_ops.cpp" in guidance
    assert "not opp_custom_op_artifact_evidence" in guidance
    assert "FAILED/INCOMPLETE" in guidance
    assert "CPU `baseline_seconds / custom_seconds`" in guidance
    assert "same-NPU" in guidance


def test_phase_prompts_require_strict_opp_artifacts_and_final_chinese_table() -> None:
    phase3 = (PROMPTS_DIR / "phase_3_entry_script.md").read_text(encoding="utf-8")
    phase6 = (PROMPTS_DIR / "phase_6_report.md").read_text(encoding="utf-8")

    for prompt in (phase3, phase6):
        assert "strict Ascend C/CANN OPP" in prompt
        assert "op_host" in prompt
        assert "op_kernel" in prompt
        assert "CMakeLists.txt/build.sh" in prompt
        assert "CANN/OPP build-install" in prompt
        assert "generated header/op_info/kernel_meta/producer/package artifacts" in prompt
        assert "NpuExtension" in prompt
        assert "ATen-only" in prompt

    assert "CPU baseline" in phase3
    assert "Ascend OPP/custom-op" in phase3
    assert "final_chinese_per_row_table_parity" in phase3
    assert "final Chinese summary" in phase6
    assert "same-NPU/self-baseline placeholder" in phase6
    assert "| row | semantic operator | public entries / aliases | route evidence type | route evidence summary | OPP artifact | adapter callable | coverage key/count | parity | integration/e2e | CPU baseline vs Ascend OPP/custom-op performance | status | next action |" in phase6


def test_dependency_fixer_prompt_is_three_line_artifact_pointer() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompt = _load_role_prompt(loader, "dependency_fixer")
    lines = prompt.splitlines()
    assert len(lines) == 3
    assert "dependency_fixer" in prompt
    assert "环境、包、导入、版本、安装和运行依赖问题" in prompt
    assert "算子、custom-op实现或CUDA/NPU代码改写问题" in prompt
    assert "{workspace_root}" not in prompt
    assert "/workspace/cuda_custom_op_skill_test_prompt.md" in prompt
    assert "第5点要求" in prompt
    assert "只有 active custom-op contract" in prompt
    assert "普通 CUDA 项目不要生成 OPP/custom-op 产物" in prompt
    assert "可以参考的文档：历史运行报错：/tmp/test_project/.sm-artifacts/testrun/runtime/runtime_error_test_project.md,运行经验文档：/tmp/test_project/.sm-artifacts/testrun/runtime/runtimeCard_test_project.md" in prompt
    assert "ModuleNotFoundError: No module named 'torch_npu'" not in prompt
    assert "Rule 1: No CPU fallback" not in prompt
    assert "Execution Failure" not in prompt
    assert "Error Classification" not in prompt
    assert "agent_diagnostics" not in prompt
    assert "runtime_error_artifact_path" not in prompt
    assert "runtime_card_artifact_path" not in prompt


def test_code_adapter_prompt_contains_code_modification_content() -> None:
    """code_adapter prompt references code changes, API replacement, torch.npu."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompt = _load_role_prompt(loader, "code_adapter")
    for keyword in ("torch.npu", "CUDA", "device"):
        assert keyword.lower() in prompt.lower(), (
            f"code_adapter prompt missing '{keyword}'"
        )


def test_non_operator_repair_prompts_contain_apply_fix_directly() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    for role in ("code_adapter",):
        prompt = _load_role_prompt(loader, role)
        assert "Apply the fix directly" in prompt, (
            f"{role} prompt missing 'Apply the fix directly'"
        )
        assert "minimal" not in prompt.lower() or "minimal code" not in prompt.lower(), (
            f"{role} prompt should not have 'minimal editing' language"
        )


def test_non_operator_repair_prompts_contain_assigned_role_value() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    for role in ("code_adapter",):
        prompt = _load_role_prompt(loader, role)
        assert role in prompt, (
            f"Loaded prompt for {role} should contain the role name in output"
        )


def test_non_operator_repair_prompts_contain_constraint_summary() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    for role in ("code_adapter",):
        prompt = _load_role_prompt(loader, role)
        assert "Rule 1: No CPU fallback" in prompt, f"{role} prompt missing constraint_summary"


def test_operator_fixer_prompt_omits_empty_constraint_summary_clause() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    context = {
        **COMMON_CONTEXT,
        "repair_role": "operator_fixer",
        "constraint_summary": "",
    }
    prompt = loader.load_prompt("repair_operator_fixer", context)
    assert "{constraint_summary}" not in prompt
    assert "Rule 1: No CPU fallback" not in prompt
    assert ", ," not in prompt


def test_non_operator_repair_prompts_contain_last_review() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    for role in ("code_adapter",):
        prompt = _load_role_prompt(loader, role)
        assert "(No review available)" in prompt, f"{role} prompt missing last_review"
