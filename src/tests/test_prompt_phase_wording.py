"""Verify that early-phase prompts do not contain forward-phase or framework-specific wording."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PROMPTS_DIR = PROJECT_ROOT / "prompts"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"

EARLY_PHASE_PATTERNS = (
    "phase_0_",
    "phase_1_",
    "phase_2_",
    "phase_3_",
    "phase_3_entry_",
    "phase_35_",
)

LATER_PHASE_PATTERNS = (
    "phase_5_",
    "phase_6_",
    "phase_4_",
    "repair_",
    "experience_",
    "phase_error_",
    "phase_review_",
    "container_image_",
)


def _prompt_files():
    return sorted(f for f in PROMPTS_DIR.iterdir() if f.suffix == ".md")


def _is_early_phase(filename: str) -> bool:
    name = filename.lower()
    if any(name.startswith(p) for p in LATER_PHASE_PATTERNS):
        return False
    return any(p in name for p in EARLY_PHASE_PATTERNS)


@pytest.mark.parametrize("prompt_file", _prompt_files())
def test_no_forward_phase_5_wording_in_early_prompts(prompt_file: Path) -> None:
    if not _is_early_phase(prompt_file.name):
        pytest.skip(f"{prompt_file.name} is not an early-phase prompt")
    text = prompt_file.read_text(encoding="utf-8")
    assert "Phase 5" not in text, (
        f"{prompt_file.name} contains forward 'Phase 5' wording. "
        f"Replace with 'target runtime' or equivalent neutral language."
    )


@pytest.mark.parametrize("prompt_file", _prompt_files())
def test_no_opencode_wording_in_early_prompts(prompt_file: Path) -> None:
    if not _is_early_phase(prompt_file.name):
        pytest.skip(f"{prompt_file.name} is not an early-phase prompt")
    text = prompt_file.read_text(encoding="utf-8")
    assert "OpenCode" not in text, (
        f"{prompt_file.name} contains explicit 'OpenCode' wording. "
        f"Replace with 'file tools' or equivalent neutral language."
    )


# ── Regression: specific early prompts changed for neutral target-runtime wording ─


EXPERIENCE_PHASE1 = PROMPTS_DIR / "experience_query_phase1.md"
CONSTRAINT_SUMMARY = PROMPTS_DIR / "phase_1_5_constraint_summary.md"
CONSTRAINT_SUMMARY_PPU = PROMPTS_DIR / "phase_1_5_constraint_summary_ppu.md"


def test_experience_query_phase1_no_phase5() -> None:
    # pylint: disable-next=line-too-long; silent
    """experience_query_phase1.md was changed from 'Unlike Phase 5' to 'Unlike later repair phases'."""
    text = EXPERIENCE_PHASE1.read_text(encoding="utf-8")
    assert "Unlike later repair phases" in text, (
        "expected neutral 'Unlike later repair phases' wording"
    )
    assert "Unlike Phase 5" not in text, (
        "experience_query_phase1.md must not contain explicit 'Unlike Phase 5'; "
        "use neutral 'Unlike later repair phases' instead."
    )


def test_constraint_summary_no_phase_enumeration() -> None:
    """phase_1_5_constraint_summary.md was changed to say 'ALL subsequent phases and agents'."""
    text = CONSTRAINT_SUMMARY.read_text(encoding="utf-8")
    assert "ALL subsequent phases and agents" in text, (
        "expected neutral 'ALL subsequent phases and agents' wording"
    )
    assert "Phase 2, 3, 4, 5 repair agents" not in text, (
        "phase_1_5_constraint_summary.md must not enumerate individual phase numbers; "
        "use 'ALL subsequent phases and agents' instead."
    )


def test_constraint_summary_ppu_already_neutral() -> None:
    """phase_1_5_constraint_summary_ppu.md was already neutral — verify it still is."""
    text = CONSTRAINT_SUMMARY_PPU.read_text(encoding="utf-8")
    assert "ALL subsequent phases" in text, "expected neutral 'ALL subsequent phases' wording"


# ── Phase 3 MUSA/MUXI entryfix prompt contract tests ─────────────────────────

MUSA_ENTRYFIX_PROMPT = PROMPTS_DIR / "phase_3_entry_script_musa_container_baseaware_entryfix.md"
MUSA_NORMAL_PROMPT = (
    PROMPTS_DIR / "phase_3_entry_script_musa_container_baseaware_entryfix_normal.md"
)


def _read_prompt(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.name} does not exist")
    return path.read_text(encoding="utf-8")


# ── MUSA custom-op prompt (entryfix) assertions ──────────────────────────────


def test_musa_custom_op_prompt_contains_priority_zero_user_constraints() -> None:
    """MUSA custom-op prompt must include Priority #0 user constraints section."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Priority #0" in text, "Missing Priority #0 user constraints"
    assert "explicit user-mandated entry scripts" in text, "Missing binding priority text"
    assert "{user_constraints}" in text, "Missing user_constraints placeholder"


def test_musa_custom_op_prompt_contains_phase2_interpreter_choice() -> None:
    """MUSA custom-op prompt must include Phase 2 Interpreter Choice section."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Phase 2 Interpreter Choice" in text, "Missing Phase 2 Interpreter Choice section"
    assert "python_path" in text, "Missing python_path reference"
    assert "preferred interpreter" in text.lower(), "Missing preferred interpreter wording"


def test_musa_custom_op_prompt_contains_decision_priority() -> None:
    """MUSA custom-op prompt must include Decision Priority with correct levels."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Decision Priority" in text, "Missing Decision Priority section"
    assert "Priority #0" in text, "Missing Priority #0 in Decision Priority"
    assert "documented non-interactive full validation command" in text.lower(), (
        "Missing documented command priority"
    )
    assert "full validation script when no documented full runner exists" in text, (
        "Missing custom-op validation script priority"
    )
    assert "smoke_test.py" in text, "Missing smoke fallback priority"


def test_musa_custom_op_prompt_contains_headless_execution_compliance() -> None:
    """MUSA custom-op prompt must include Headless Execution Compliance."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Headless Execution Compliance" in text, "Missing Headless Execution Compliance section"
    assert "input()" in text, "Missing interactive-blocking example"
    assert "write it under" in text.lower(), "Missing file creation requirement"


def test_musa_custom_op_prompt_says_do_not_execute_build_adapt_repair() -> None:
    """MUSA custom-op prompt must say Phase 3 does not execute/build/adapt/repair."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Do NOT execute the full migration workload during Phase 3" in text, (
        "Missing full migration workload prohibition"
    )
    assert "not running validation" in text.lower(), "Missing 'not running validation' boundary"
    assert "build, adapt, repair, or migrate" in text, (
        "Missing explicit 'build, adapt, repair, or migrate' prohibition"
    )


def test_musa_custom_op_prompt_contains_execution_backend_prohibition() -> None:
    """MUSA custom-op prompt must include detailed Execution Backend Prohibition."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Execution Backend Prohibition" in text, "Missing Execution Backend Prohibition section"
    assert "docker exec" in text, "Missing docker exec prohibition"
    assert "podman exec" in text, "Missing podman exec prohibition"
    assert "container names/IDs" in text, "Missing container names/IDs prohibition"
    assert "pre-existing or shared containers" in text, "Missing shared containers prohibition"
    assert "Example good:" in text, "Missing good example"
    assert "Example bad:" in text, "Missing bad example"


def test_musa_custom_op_prompt_contains_field_semantics_normal() -> None:
    """MUSA custom-op prompt must include Field Semantics for normal fields."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Field Semantics" in text, "Missing Field Semantics section"
    assert "entry_script_path" in text, "Missing entry_script_path semantics"
    assert "host-visible absolute path" in text, "Missing host-visible path requirement"
    assert "container-internal-only path" in text, "Missing container-internal prohibition"
    assert "reports_dir" in text, "Missing reports_dir semantics"
    assert "run_command" in text, "Missing run_command semantics"


def test_musa_custom_op_prompt_contains_field_semantics_custom_op() -> None:
    """MUSA custom-op prompt must include Field Semantics for custom-op fields."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "custom-op fields" in text.lower() or "custom_op_full_validation" in text, (
        "Missing custom-op field semantics"
    )
    assert "entry_script_kind" in text, "Missing entry_script_kind semantics"
    assert "operator_discovery_sources" in text, "Missing operator_discovery_sources semantics"
    assert "operator_inventory_schema" in text, "Missing operator_inventory_schema semantics"
    assert "performance_report_schema" in text, "Missing performance_report_schema semantics"
    assert "required_report_paths" in text, "Missing required_report_paths semantics"
    assert "required_checks" in text, "Missing required_checks semantics"
    assert "validation_obligations" in text, "Missing validation_obligations semantics"


def test_musa_custom_op_prompt_contains_musa_specific_context() -> None:
    """MUSA custom-op prompt must preserve MUSA/MUXI-specific semantics."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "CUDA-to-MUSA/MUXI" in text, "Missing CUDA-to-MUSA/MUXI context"
    assert "torch_musa" in text, "Missing torch_musa reference"
    assert "torch.musa" in text or "MACA/MetaX" in text, (
        "Missing torch.musa or MACA/MetaX reference"
    )
    assert "MUSA SDK" in text, "Missing MUSA SDK reference"
    assert "presence_only" in text.lower(), "Missing presence_only speedup optionality"
    assert "compile, load, and run" in text.lower() or "compile/load/run" in text.lower(), (
        "Missing compile/load/run MUSA wording"
    )


def test_musa_custom_op_prompt_contains_musa_custom_op_schema() -> None:
    """MUSA custom-op prompt must preserve the MUSA custom-op output schema."""
    text = _read_prompt(MUSA_ENTRYFIX_PROMPT)
    assert "Custom-Op Output Format" in text, "Missing Custom-Op Output Format section"
    assert "native_operator_symbols" in text, "Missing native_operator_symbols field"
    assert "MUSA/exported symbols" in text, "Missing MUSA/exported symbols value"
    assert "native kernel functions" in text, "Missing MUSA kernel functions value"
    assert "positive MUSA/custom timing" in text, "Missing MUSA/custom timing value"


# ── MUSA normal prompt assertions ────────────────────────────────────────────


def _extract_output_format_json(text: str) -> str:
    """Extract the JSON block from the Output Format section."""
    lines = text.splitlines()
    in_output_section = False
    in_json_block = False
    json_lines = []
    for line in lines:
        if line.strip().startswith("## Output Format"):
            in_output_section = True
            continue
        if in_output_section and line.strip().startswith("```json"):
            in_json_block = True
            continue
        if in_json_block and line.strip() == "```":
            break
        if in_json_block:
            json_lines.append(line)
    return "\n".join(json_lines)


CUSTOM_OP_CONTRACT_FIELDS = [
    "custom_op_full_validation",
    "operator_inventory_schema",
    "performance_report_schema",
    "required_report_paths",
    "required_checks",
    "validation_obligations",
]

CUSTOM_OP_SECTION_HEADINGS = [
    "## Custom-Op Output Format",
    "## Custom-Op Mandatory Rules",
    "## Custom-Op Rules",
]


@pytest.mark.parametrize("field", CUSTOM_OP_CONTRACT_FIELDS)
def test_musa_normal_prompt_output_format_omits_custom_op_fields(field: str) -> None:
    """MUSA normal prompt Output Format JSON must NOT contain custom-op contract fields."""
    text = _read_prompt(MUSA_NORMAL_PROMPT)
    output_json = _extract_output_format_json(text)
    assert field not in output_json, (
        f"MUSA normal prompt Output Format JSON contains custom-op field '{field}'. "
        f"It must be omitted by route policy."
    )


@pytest.mark.parametrize("heading", CUSTOM_OP_SECTION_HEADINGS)
def test_musa_normal_prompt_has_no_custom_op_section_heading(heading: str) -> None:
    """MUSA normal prompt must NOT have dedicated Custom-Op section headings."""
    text = _read_prompt(MUSA_NORMAL_PROMPT)
    lines = text.splitlines()
    for line in lines:
        assert line.strip() != heading, (
            f"MUSA normal prompt has custom-op section heading '{heading}'. "
            f"It must be omitted by route policy."
        )


def test_musa_normal_prompt_declares_normal_route() -> None:
    """MUSA normal prompt must declare it uses the normal-entry route."""
    text = _read_prompt(MUSA_NORMAL_PROMPT)
    assert "normal-entry route" in text or "normal" in text.lower(), (
        "Missing normal-entry route declaration"
    )
    assert "omitted by route policy" in text, "Missing route policy omission statement"
    assert "auto-skips" in text, "Missing auto-skips statement for custom_op_final_gate"


def test_musa_normal_prompt_has_must_not_include_section() -> None:
    """MUSA normal prompt must have a MUST NOT INCLUDE section."""
    text = _read_prompt(MUSA_NORMAL_PROMPT)
    assert "MUST NOT INCLUDE" in text, "Missing MUST NOT INCLUDE section"
    assert "Do NOT output" in text, "Missing explicit output prohibition"


def test_musa_normal_prompt_contains_base_aware_sections() -> None:
    """MUSA normal prompt must retain base-aware Phase 3 semantics."""
    text = _read_prompt(MUSA_NORMAL_PROMPT)
    assert "Phase 2 Interpreter Choice" in text, "Missing Phase 2 Interpreter Choice"
    assert "Decision Priority" in text, "Missing Decision Priority"
    assert "Headless Execution Compliance" in text, "Missing Headless Execution Compliance"
    assert "Execution Backend Prohibition" in text, "Missing Execution Backend Prohibition"
    assert "User Constraints" in text, "Missing User Constraints"
    assert "{constraint_summary}" in text, "Missing constraint_summary placeholder"


# ── Workflow reference assertions ────────────────────────────────────────────

MUSA_NORMAL_WORKFLOW = (
    WORKFLOWS_DIR / "musa_muxi_migration_v2_container_baseaware_entryfix_normal.yaml"
)


def test_musa_normal_workflow_references_normal_prompt() -> None:
    """MUSA normal workflow must reference the new normal prompt."""
    if not MUSA_NORMAL_WORKFLOW.exists():
        pytest.skip(f"{MUSA_NORMAL_WORKFLOW.name} does not exist")
    text = MUSA_NORMAL_WORKFLOW.read_text(encoding="utf-8")
    assert "phase_3_entry_script_musa_container_baseaware_entryfix_normal" in text, (
        "MUSA normal workflow does not reference the normal prompt template. "
        "Expected: phase_3_entry_script_musa_container_baseaware_entryfix_normal"
    )
    # The custom-op prompt should NOT be referenced by the normal workflow
    assert 'phase_3_entry_script_musa_container_baseaware_entryfix"' not in text, (
        "MUSA normal workflow still references the custom-op prompt template. "
        "It must use only the normal prompt template."
    )


def test_musa_normal_workflow_has_custom_op_route_disabled() -> None:
    """MUSA normal workflow must disable custom_op_route_enabled."""
    if not MUSA_NORMAL_WORKFLOW.exists():
        pytest.skip(f"{MUSA_NORMAL_WORKFLOW.name} does not exist")
    text = MUSA_NORMAL_WORKFLOW.read_text(encoding="utf-8")
    assert "custom_op_route_enabled: false" in text or "custom_op_route_enabled: False" in text, (
        "MUSA normal workflow must set custom_op_route_enabled to false"
    )
