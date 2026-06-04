import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PROMPTS_DIR = PROJECT_ROOT / "prompts"
EXECUTION_ROOT = PROJECT_ROOT.parent

from validators.validate_entry_static import CUSTOM_OP_BOOLEAN_FIELDS, EXPANDED_VARIANT_BOOLEAN_FIELDS

PHASE_PROMPT_FILES = [
    "phase_0_env_detect_npu.md",
    "phase_1_project_analysis_npu.md",
    "phase_2_venv_create_npu.md",
    "phase_3_entry_script_npu.md",
    "phase_35_static_validate_npu.md",
    "phase_4_rule_migration.md",
    "phase_5_validation.md",
    "phase_6_report_npu.md",
]

OLD_CONSTRAINT = "must be valid JSON only, with no markdown fence"
NEW_CONSTRAINT = "reason freely"


def test_no_prompt_contains_json_only_hard_constraint():
    """All listed phase prompts no longer contain the old JSON-only constraint."""
    for filename in PHASE_PROMPT_FILES:
        content = (PROMPTS_DIR / filename).read_text()
        assert OLD_CONSTRAINT not in content, f"{filename} still has JSON-only hard constraint"


def test_all_prompts_contain_relaxed_constraint():
    """All listed phase prompts contain the new 'reason freely' instruction."""
    for filename in PHASE_PROMPT_FILES:
        content = (PROMPTS_DIR / filename).read_text()
        assert NEW_CONSTRAINT in content, f"{filename} missing relaxed constraint"


def test_phase_35_prompt_mentions_custom_op_contract_static_gate():
    content = (PROMPTS_DIR / "phase_35_static_validate_npu.md").read_text()

    assert "previous_outputs" in content
    assert "phase_3_entry_script" in content
    assert "migration_reports/" in content
    assert "required_report_paths" in content
    assert "required_checks" in content
    assert "migration_manifest.json" in (PROMPTS_DIR / "phase_3_entry_script_npu.md").read_text()
    assert "operator_manifest.json" not in (PROMPTS_DIR / "phase_3_entry_script_npu.md").read_text()
    assert "smoke, MVP, partial" in content
    assert "script_records_native_operator_symbols" in content
    assert "native symbol/kernel inventory" in content
    assert "script_emits_fine_grained_units" in content
    assert "script_maps_public_api_to_units" in content
    assert NEW_CONSTRAINT in content


def test_phase_35_custom_op_example_includes_validator_boolean_contract():
    content = (PROMPTS_DIR / "phase_35_static_validate_npu.md").read_text()

    for field in CUSTOM_OP_BOOLEAN_FIELDS:
        assert f'"{field}": true' in content, f"phase 3.5 custom-op example missing {field}"
    for field in EXPANDED_VARIANT_BOOLEAN_FIELDS:
        assert f'"{field}": true' in content, f"phase 3.5 expanded-variant example missing {field}"


def test_phase3_forbids_hard_coded_and_report_only_custom_op_gates():
    content = (PROMPTS_DIR / "phase_3_entry_script_npu.md").read_text()

    assert "Hard-coded expected unit identity lists are forbidden" in content
    assert "Report-only scripts" in content
    assert "merely inspect existing JSON" in content
    assert "custom_op_with_variants" in content
    assert "discover the expanded variant inventory from source" in content
    assert "prove variant-axis coverage" in content
    assert "one performance entry per expanded variant" in content


def test_phase3_phase35_prompts_are_platform_generic_for_custom_op_contracts():
    phase3 = (PROMPTS_DIR / "phase_3_entry_script_npu.md").read_text()
    phase35 = (PROMPTS_DIR / "phase_35_static_validate_npu.md").read_text()

    assert "Ascend NPU migration workflow" not in phase3
    assert "Ascend NPU migration workflow" not in phase35
    assert "targeting the platform selected by platform policy" in phase3
    assert "platform selected by platform policy" in phase35
    assert "per_entry_target_custom_op_artifact_evidence" in phase3
    assert "per_entry_opp_custom_op_artifact_evidence" not in phase3


def test_custom_op_phase_prompts_use_source_driven_contract_without_external_requirements():
    for filename in ("phase_3_entry_script_npu.md", "phase_35_static_validate_npu.md", "phase_6_report_npu.md"):
        content = (PROMPTS_DIR / filename).read_text()
        assert "cuda_custom_op_skill_test_prompt.md" not in content
        assert "requirements_doc_path" not in content
        assert "source" in content.lower()
        assert "inventory" in content.lower()
    phase3 = (PROMPTS_DIR / "phase_3_entry_script_npu.md").read_text()
    phase35 = (PROMPTS_DIR / "phase_35_static_validate_npu.md").read_text()
    assert "one row per fine-grained source-discovered operator unit" in phase3
    assert "family-only rows are invalid" in phase3
    assert "kernel_launch_sites" in phase3
    assert "public_entry_mapping" in phase3
    assert "group multiple source-discovered units into a family-only row" in phase35


def test_production_custom_op_prompts_do_not_use_project_specific_examples():
    forbidden_terms = (
        "Deepwave",
        "deepwave",
        "libdeepwave",
        "scalar_",
        "scalar_forward",
        "scalar_backward",
        "scalar_iso",
        "1D",
        "2D",
        "3D",
    )

    for filename in ("phase_1_project_analysis_npu.md", "phase_1_5_constraint_summary_npu.md"):
        content = (PROMPTS_DIR / filename).read_text()
        for term in forbidden_terms:
            assert term not in content, f"{filename} contains project-specific prompt example term {term!r}"


def test_phase3_and_phase5_prompts_require_complete_performance_report_closure():
    phase3 = (PROMPTS_DIR / "phase_3_entry_script_npu.md").read_text()
    phase5 = (PROMPTS_DIR / "phase_5_validation.md").read_text()

    assert "enumerate every source-discovered inventory unit" in phase3
    assert "execute coverage and performance checks for every unit" in phase3
    assert "complete_performance_report" in phase3
    assert "complete_speedup_report" in phase3
    assert "overall_speedup_report" in phase3
    assert "overall_baseline_seconds" in phase3
    assert "overall_all_units_replaced" in phase3
    assert "overall/end-to-end speedup" in phase5
    assert "migration_reports/performance.json" in phase5
    assert "covers every manifest/source-inventory unit" in phase5
    assert "overall_baseline_seconds" in phase5
    assert "overall_all_units_replaced" in phase5


def test_repair_prompts_use_portable_skill_prompt_references_without_full_inline_rules():
    expectations = {
        "phase_error_recovery_npu.md": ("{workspace_root}/docs/cuda_custom_op_skill_test_prompt.md", "第2、3、5、6点要求"),
        "repair_dependency_fixer_npu.md": ("{workspace_root}/docs/cuda_custom_op_skill_test_prompt.md", "第5点要求"),
    }

    for filename, required_phrases in expectations.items():
        content = (PROMPTS_DIR / filename).read_text()
        for phrase in required_phrases:
            assert phrase in content, f"{filename} missing portable citation {phrase!r}"
        assert "全部8个要求" not in content
        assert "/inspire/sj-ssd" not in content

    operator_prompt = (PROMPTS_DIR / "repair_operator_fixer_npu.md").read_text()
    assert "cuda_custom_op_skill_test_prompt.md" not in operator_prompt
    assert ".skills" not in operator_prompt

    for filename in ("phase_0_env_detect_npu.md", "phase_4_rule_migration.md"):
        content = (PROMPTS_DIR / filename).read_text()
        assert "cuda_custom_op_skill_test_prompt.md" not in content


def test_root_custom_op_skill_prompt_is_owned_by_execution_root():
    prompt_path = EXECUTION_ROOT / "docs" / "cuda_custom_op_skill_test_prompt.md"
    content = prompt_path.read_text(encoding="utf-8")

    assert prompt_path.is_file()
    assert "## 1. Manifest 和 scope 锁定" in content
    assert "/inspire/sj-ssd" not in content
    assert "ascend_env_adapter/.skills" not in content


def test_error_recovery_prompt_not_modified():
    """phase_error_recovery.md should NOT contain the relaxed JSON constraint."""
    content = (PROMPTS_DIR / "phase_error_recovery_npu.md").read_text()
    # This prompt is text-only (not JSON), so it should NOT have the new constraint
    assert NEW_CONSTRAINT not in content, (
        "phase_error_recovery.md should not contain JSON-related relaxations"
    )


def test_extract_json_response_handles_natural_language_plus_json():
    """extract_json_response should extract JSON from natural language + trailing JSON."""
    from harness.session.manager import extract_json_response

    # Test: natural language + trailing JSON
    mixed_response = """
    Based on my analysis, I detected the following environment:
    The platform appears to be NPU-based since torch_npu is installed.
    Here is my structured response:
    {"platform": "npu", "npu_detected": true, "python_version": "3.10.12"}
    """
    result = extract_json_response(mixed_response)
    assert result["platform"] == "npu"
    assert result["npu_detected"] is True
    assert result["python_version"] == "3.10.12"


def test_extract_json_response_uses_last_valid_fenced_json():
    from harness.session.manager import extract_json_response

    response = """
    I first considered this shape:
    ```json
    {
      "env_type": "base_env",
      "installed_packages": [
        ...
      ],
      ...
    }
    ```

    The final answer is:
    ```json
    {
      "env_type": "base_env",
      "venv_path": "/opt/conda",
      "python_path": "/opt/conda/bin/python3.10",
      "installed_packages": ["torch==2.8.0+metax3.5.3.9"],
      "vendor_stack": {"api_mode": "cuda_compatible"}
    }
    ```
    """

    result = extract_json_response(response)

    assert result["env_type"] == "base_env"
    assert result["venv_path"] == "/opt/conda"
    assert result["python_path"] == "/opt/conda/bin/python3.10"
    assert result["installed_packages"] == ["torch==2.8.0+metax3.5.3.9"]
    assert result["vendor_stack"]["api_mode"] == "cuda_compatible"
