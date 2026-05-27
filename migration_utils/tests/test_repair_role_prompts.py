import sys
from pathlib import Path
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
    "operator_custom_op_guidance": "4. This is a generic operator-incompatibility repair.\n5. 修改后用 /tmp/test_project/.venv/bin/python 和 python train.py 进行验证, 只在最终回答里输出一个 JSON 代码块, 至少包含 modified_files, summary, agent_diagnostics。",
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
        "operator_custom_op_guidance": (
            "4. Read bounded operator context: /tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md; "
            "this context is the only inventory / manifest / final-gate closure source.\n"
            "5. Treat the custom-op contract as hard scope: freeze manifest rows, keep every in-scope operator, public entry, framework alias, and forward/backward/grad/training-only path in scope, and never downgrade rows or accept report-only, MVP-only, fallback, builtin, or zero-call success. If a row is unresolved, split it into smaller slices and continue the remaining rows instead of stopping.\n"
            "6. Every in-scope row must have real Ascend OPP artifacts, adapter/import/link success, direct/reference parity, same-run runtime coverage > 0, and baseline/custom performance evidence. Final success requires inventory_count == manifest_entries == closed_pass_entries, remaining_entries == 0, full_migration_status == FULL_PASS, and passing final evidence validation.\n"
            "7. 修改后用 /tmp/test_project/.venv/bin/python 和 python train.py 进行验证。只在最终回答里输出一个 JSON 代码块, 至少包含 modified_files, summary, agent_diagnostics；modified_files 必须列出实际修改文件，除非 summary 明确写 FAILED/INCOMPLETE 和外部阻塞原因。"
        ),
    }
    prompt = loader.load_prompt("repair_operator_fixer", context)
    assert "bounded operator context" in prompt
    assert "/tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md" in prompt
    assert "inventory / manifest / final-gate" in prompt.lower()
    assert "freeze manifest rows" in prompt
    assert "inventory_count == manifest_entries == closed_pass_entries" in prompt
    assert "remaining_entries == 0" in prompt
    assert "full_migration_status == FULL_PASS" in prompt
    assert "same-run runtime coverage > 0" in prompt
    assert "baseline/custom performance evidence" in prompt
    assert "report-only" in prompt
    assert "MVP-only" in prompt
    assert "zero-call" in prompt
    assert "modified_files 必须列出实际修改文件" in prompt
    assert "FAILED/INCOMPLETE" in prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in prompt
    assert ".skills" not in prompt


def test_generated_custom_op_guidance_rejects_evidence_only_marker_artifacts() -> None:
    guidance = repair_loop._operator_custom_op_guidance(
        "/tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md",
        project_dir="/tmp/test_project",
        entry_script="python validate_custom_ops_full.py",
    )

    assert "Evidence-only marker shims" in guidance
    assert "*_evidence*" in guidance
    assert "stub/dummy/fake placeholder native libraries" in guidance
    assert "synthetic success codes" in guidance
    assert "FAILED/INCOMPLETE" in guidance


def test_dependency_fixer_prompt_contains_constraint_summary_and_handoff() -> None:
    """dependency_fixer prompt receives constraint_summary, no-CPU-fallback, and native-op handoff guidance."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompt = _load_role_prompt(loader, "dependency_fixer")
    assert "dependency_fixer" in prompt
    assert "环境、包、导入、版本、安装和运行依赖问题" in prompt
    assert "算子、custom-op实现或CUDA/NPU代码改写问题" in prompt
    assert "{workspace_root}" not in prompt
    assert "/workspace/cuda_custom_op_skill_test_prompt.md" in prompt
    assert "第5点要求" in prompt
    assert "可以参考的文档：历史运行报错：/tmp/test_project/.sm-artifacts/testrun/runtime/runtime_error_test_project.md,运行经验文档：/tmp/test_project/.sm-artifacts/testrun/runtime/runtimeCard_test_project.md" in prompt
    assert "Rule 1: No CPU fallback" in prompt
    assert "Migration Constraints (from Phase 1.5)" in prompt
    assert "No CPU Fallback (CRITICAL)" in prompt
    assert "Native Operator Handoff" in prompt
    assert "ModuleNotFoundError: No module named 'torch_npu'" not in prompt
    assert "Execution Failure" not in prompt
    assert "Error Classification" not in prompt
    assert "handoff rationale in your `summary`" in prompt
    assert "dependency closure" in prompt.lower()
    assert "runtime_error_artifact_path" not in prompt
    assert "runtime_card_artifact_path" not in prompt


def test_dependency_fixer_prompt_contains_self_verified_closure_guidance() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompt = _load_role_prompt(loader, "dependency_fixer")
    assert "Self-Verified Dependency Closure (CRITICAL)" in prompt
    assert "hints only" in prompt
    assert "do not constitute verified facts" not in prompt  # that's in error_analyzer
    assert "Batch In-Scope Dependency/Env Closure" in prompt
    assert "actual execution command" in prompt.lower()
    assert "Native/Custom-Op Handoff via Summary" in prompt
    assert "summary" in prompt
    assert "agent_diagnostics.handoff_recommended" not in prompt
    assert "agent_diagnostics.dependency_closure_validated" not in prompt


def test_muxi_container_dependency_prompt_uses_summary_for_closure_and_handoff() -> None:
    content = (PROMPTS_DIR / "repair_dependency_fixer_container_musa.md").read_text(encoding="utf-8")
    assert "Self-Verified Dependency Closure (CRITICAL)" in content
    assert "hints only" in content
    assert "Report the closure validation in `summary`" in content
    assert "handoff need in your `summary`" in content
    assert "agent_diagnostics.handoff_recommended" not in content
    assert "agent_diagnostics.dependency_closure_validated" not in content


def test_error_analyzer_prompt_contains_prior_outputs_hints_only() -> None:
    """error_analyzer prompt warns that prior outputs are hints only."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    context = {
        **COMMON_CONTEXT,
        "phase_name": "error_analyzer",
        "repair_role": "error_analyzer",
        "failed_phase": "phase_5_validation",
        "entry_script_contract": "{}",
        "failure_log": "some error",
        "previous_outputs": "(no history)",
        "artifact_base_path": "/tmp/artifacts",
        "raw_attempt_files": "(none)",
    }
    prompt_id = "phase_error_recovery"
    prompt = loader.load_prompt(prompt_id, context)
    assert "hints only" in prompt
    assert "do NOT constitute verified facts" in prompt
    assert "MUST independently verify" in prompt


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
    for role in ("code_adapter", "dependency_fixer"):
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


# ── normal-entry prompt / constraint wording regression ──────────────────

_NORMAL_ENTRY_BANNED_PHRASES = (
    "zero custom operators",
    "ZERO custom",
    "custom_op_detected MUST be false",
    "Do NOT search for custom",
)

_NORMAL_ENTRY_PROMPT_FILES = (
    "phase_1_project_analysis_ppu_normal_entry_057.md",
    "phase_3_entry_script_ppu_normal_entry_057.md",
    "phase_35_static_validate_ppu_normal_entry_057.md",
)


def _read_normal_entry_file(filename: str) -> str:
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_normal_entry_prompts_never_claim_zero_custom_operators() -> None:
    """Every normal-entry 057 prompt file must NOT contain banned phrases
    that falsely assert the project has zero custom operators."""
    for filename in _NORMAL_ENTRY_PROMPT_FILES:
        content = _read_normal_entry_file(filename)
        for phrase in _NORMAL_ENTRY_BANNED_PHRASES:
            assert phrase not in content, (
                f"{filename} contains banned phrase: {phrase!r}"
            )


def test_normal_entry_constraints_never_claim_zero_custom_operators() -> None:
    """The normal-entry constraints file must NOT contain banned zero-custom phrasing."""
    constraints_dir = PROJECT_ROOT / "constraints_normal_entry_057.md"
    if not constraints_dir.exists():
        return  # skip if not present
    content = constraints_dir.read_text(encoding="utf-8")
    for phrase in _NORMAL_ENTRY_BANNED_PHRASES:
        assert phrase not in content, (
            f"constraints_normal_entry_057.md contains banned phrase: {phrase!r}"
        )


def test_operator_custom_op_guidance_is_free_of_report_schema() -> None:
    guidance = repair_loop._operator_custom_op_guidance(
        "/tmp/test_project/.sm-artifacts/testrun/runtime/operatorRepairContext_test_project.md",
        project_dir="/tmp/test_project",
        entry_script="python validate_custom_ops_full.py",
    )

    assert "source_inventory structure" not in guidance
    assert "performance_report structure" not in guidance
    assert "Rejection rules" not in guidance
    assert "fine-grained unit identity fields" not in guidance
    assert "Python shim" not in guidance
    assert "coarse/collapsed" not in guidance


def test_normal_entry_prompts_exist() -> None:
    """Sanity: all expected normal-entry prompt files exist."""
    for filename in _NORMAL_ENTRY_PROMPT_FILES:
        assert (PROMPTS_DIR / filename).exists(), f"{filename} is missing"


# ── final_gate_report_fixer prompt tests ────────────────────────────────

_REPORT_FIXER_CONTEXT = {
    **COMMON_CONTEXT,
    "repair_role": "final_gate_report_fixer",
    "execution_backend_mode": "container",
    "actual_execution_command": "docker exec test-container python validate.py",
    "container_name_or_id": "test-container",
    "container_workdir": "/workspace",
    "host_project_dir": "/tmp/test_project",
    "container_project_dir": "/workspace",
    "container_probe_command_prefix": "docker exec test-container",
}

_REPORT_FIXER_BANNED_PHRASES = (
    "directly edit",
    "directly patch",
    "hand-edit",
    "JSON patching",
    "json patching",
)


def test_report_fixer_prompt_exists() -> None:
    path = PROMPTS_DIR / "repair_final_gate_report_fixer_container_musa.md"
    assert path.exists(), f"{path.name} is missing"
    content = path.read_text(encoding="utf-8")
    assert len(content) > 100


def test_report_fixer_prompt_forbids_direct_json_patching() -> None:
    content = (PROMPTS_DIR / "repair_final_gate_report_fixer_container_musa.md").read_text(encoding="utf-8")
    lower = content.lower()
    assert "do not directly patch" in lower or "do not directly edit" in lower
    assert "entry script" in lower or "report aggregation" in lower
    assert "fix the entry script" in lower or "fix the report" in lower


def test_report_fixer_prompt_requires_validate_custom_op_final_gate_self_check() -> None:
    content = (PROMPTS_DIR / "repair_final_gate_report_fixer_container_musa.md").read_text(encoding="utf-8")
    assert "{final_gate_validator_command}" in content
    assert "self-check" in content.lower() or "self check" in content.lower()
    assert "do NOT guess" in content or "do not guess" in content.lower()
    assert "Copy its exact output" in content or "copy exact output" in content.lower()


def test_report_fixer_prompt_does_not_contain_operator_guidance() -> None:
    content = (PROMPTS_DIR / "repair_final_gate_report_fixer_container_musa.md").read_text(encoding="utf-8")
    assert "operator_custom_op_guidance" not in content
    # "opp_custom_op_artifact_evidence" appears only in the "do NOT touch evidence-level content" warning,
    # which is acceptable. Verify it's not present as an instruction to generate evidence.
    evidence_block_count = content.lower().count("opp_custom_op_artifact_evidence")
    assert evidence_block_count <= 1, "opp_custom_op_artifact_evidence should appear at most once (only in 'do NOT touch' warning)"


def test_report_fixer_prompt_contains_required_actions() -> None:
    content = (PROMPTS_DIR / "repair_final_gate_report_fixer_container_musa.md").read_text(encoding="utf-8")
    assert "inventory_count == manifest_entries == closed_pass_entries" in content
    assert "remaining_entries == 0" in content
    assert "full_migration_status" in content
    assert "source_inventory" in content
    assert "performance_report" in content


def test_generic_report_fixer_prompt_exists() -> None:
    path = PROMPTS_DIR / "repair_final_gate_report_fixer.md"
    assert path.exists(), f"{path.name} is missing"
    content = path.read_text(encoding="utf-8")
    assert "{final_gate_validator_command}" in content
    assert "{final_gate_validator_contract_summary}" in content
    lower = content.lower()
    assert "do not directly" in lower


# ── normal-way isolation: no report fixer in normal workflow ────────────


def test_normal_repair_prompt_ids_exclude_report_fixer() -> None:
    prompt_ids = cast(dict[str, str], getattr(repair_loop, "_REPAIR_PROMPT_IDS"))
    assert "final_gate_report_fixer" not in prompt_ids.get("operator_fixer", "")
    for role, pid in prompt_ids.items():
        if role == "final_gate_report_fixer":
            continue
        content = (PROMPTS_DIR / f"{pid}.md").read_text(encoding="utf-8") if (PROMPTS_DIR / f"{pid}.md").exists() else ""
        if content:
            assert "final_gate_report_fixer" not in content, f"{pid}.md contains final_gate_report_fixer"
            assert "fix_report" not in content.lower(), f"{pid}.md contains fix_report"


def test_operator_fixer_prompt_has_no_report_schema_guidance() -> None:
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    prompt = _load_role_prompt(loader, "operator_fixer")
    assert "validate_custom_op_final_gate" not in prompt
    assert "report schema" not in prompt.lower()
    assert "final_gate_report_fixer" not in prompt


def test_report_fixer_container_prompt_mapping_loads_existing_file() -> None:
    prompt_ids = cast(dict[str, str], getattr(repair_loop, "_REPAIR_PROMPT_IDS_CONTAINER"))
    prompt_id = prompt_ids["final_gate_report_fixer"]
    assert prompt_id == "repair_final_gate_report_fixer_container"

    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    context = {
        **COMMON_CONTEXT,
        "repair_role": "final_gate_report_fixer",
        "execution_backend_mode": "container",
        "actual_execution_command": "docker exec test-container python validate.py",
        "container_name_or_id": "test-container",
        "container_workdir": "/workspace",
        "host_project_dir": "/tmp/test_project",
        "container_project_dir": "/workspace",
        "container_probe_command_prefix": "docker exec test-container",
        "final_gate_validator_command": "echo 'VALIDATOR_COMMAND'",
        "final_gate_validator_contract_summary": "Validator contract summary here.",
    }
    prompt = loader.load_prompt(prompt_id, context)
    assert len(prompt) > 50
    assert "report schema" in prompt.lower() or "report structure" in prompt.lower()
    assert "container" in prompt.lower()
    assert "VALIDATOR_COMMAND" in prompt


# ── final gate validator command injection tests ────────────────────────


def test_generic_report_fixer_prompt_renders_command_not_placeholder() -> None:
    """Generic report fixer prompt renders command/contract, no unresolved {final_gate_validator_command}."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    cmd = "cd /some/path && python3 << 'PYEOF'\nprint('hello')\nPYEOF"
    summary = "Strict report/schema requirements on custom_op_final_gate.json."
    context = {
        **COMMON_CONTEXT,
        "repair_role": "final_gate_report_fixer",
        "final_gate_validator_command": cmd,
        "final_gate_validator_contract_summary": summary,
    }
    prompt = loader.load_prompt("repair_final_gate_report_fixer", context)
    assert "{final_gate_validator_command}" not in prompt
    assert "{final_gate_validator_contract_summary}" not in prompt
    assert cmd in prompt
    assert summary in prompt
    assert "do NOT guess" in prompt.lower() or "do not guess" in prompt.lower()
    assert "copy its exact output" in prompt.lower()


def test_container_report_fixer_prompt_renders_command_and_contract() -> None:
    """Container report fixer prompt renders command/contract with container context intact."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    cmd = "cd /some/path && python3 << 'PYEOF'\nprint('hello')\nPYEOF"
    summary = "Container-specific contract summary."
    context = {
        **COMMON_CONTEXT,
        "repair_role": "final_gate_report_fixer",
        "execution_backend_mode": "container",
        "actual_execution_command": "docker exec test-container python validate.py",
        "container_name_or_id": "test-container",
        "container_workdir": "/workspace",
        "host_project_dir": "/tmp/test_project",
        "container_project_dir": "/workspace",
        "container_probe_command_prefix": "docker exec test-container",
        "final_gate_validator_command": cmd,
        "final_gate_validator_contract_summary": summary,
    }
    prompt = loader.load_prompt("repair_final_gate_report_fixer_container", context)
    assert "{final_gate_validator_command}" not in prompt
    assert "{final_gate_validator_contract_summary}" not in prompt
    assert cmd in prompt
    assert summary in prompt
    assert "Container Execution Context" in prompt


def test_musa_report_fixer_prompt_renders_command_with_anti_simulation() -> None:
    """MUSA report fixer prompt renders command with anti-simulation language and self-check."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    cmd = "cd /some/path && python3 << 'PYEOF'\nprint('hello')\nPYEOF"
    summary = "MUSA-specific contract summary."
    context = {
        **COMMON_CONTEXT,
        "repair_role": "final_gate_report_fixer",
        "execution_backend_mode": "container",
        "actual_execution_command": "docker exec test-container python validate.py",
        "container_name_or_id": "test-container",
        "container_workdir": "/workspace",
        "host_project_dir": "/tmp/test_project",
        "container_project_dir": "/workspace",
        "container_probe_command_prefix": "docker exec test-container",
        "execution_environment_context": "",
        "final_gate_validator_command": cmd,
        "final_gate_validator_contract_summary": summary,
    }
    prompt = loader.load_prompt("repair_final_gate_report_fixer_container_musa", context)
    assert "{final_gate_validator_command}" not in prompt
    assert "{final_gate_validator_contract_summary}" not in prompt
    assert cmd in prompt
    assert summary in prompt
    assert "do NOT guess" in prompt
    assert "self-check" in prompt.lower()
    assert "validator_command_output" in prompt


def test_non_report_fixer_prompts_exclude_validator_command() -> None:
    """Non-report fixer prompts (operator, dependency, code) do NOT contain final_gate_validator_command."""
    loader = PromptLoader(prompts_dir=str(PROMPTS_DIR))
    for role in ("operator_fixer", "dependency_fixer", "code_adapter"):
        prompt = _load_role_prompt(loader, role)
        assert "final_gate_validator_command" not in prompt, (
            f"{role} prompt should not contain final_gate_validator_command"
        )
        assert "final_gate_validator_contract_summary" not in prompt, (
            f"{role} prompt should not contain final_gate_validator_contract_summary"
        )
