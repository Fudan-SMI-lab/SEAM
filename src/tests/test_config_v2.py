"""Tests for v2 YAML config parsing."""
import pytest
import tempfile
import os
from pathlib import Path

from core.config import load_workflow
from core.types import RuntimeSkillsConfig, ExperienceConfig

# Get the package root for path resolution
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def test_load_v1_yaml_still_works():
    """V1 YAMLs should still parse correctly."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v1.yaml"))
    assert wf.name == "npu_migration"
    assert len(wf.phases) > 0
    assert isinstance(wf.terminals, list)


def test_load_v2_yaml():
    """V2 YAML with all new fields should parse."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    assert wf.name == "npu_migration"
    assert wf.version == "2.0"
    assert len(wf.phases) >= 8


def test_agents_registry():
    """"agents" section should be parsed."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    assert "main_engineer" in wf.agents
    assert "error_analyzer" in wf.agents
    assert len(wf.agents) == 5


def test_sub_workflows_parsed():
    """"sub_workflows" section should be parsed."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    assert "repair_loop" in wf.sub_workflows
    swf = wf.sub_workflows["repair_loop"]
    assert swf.type == "loop"
    assert len(swf.stop_conditions) > 0
    assert len(swf.phases) > 0
    assert "improvement_block" in swf.blocks


def test_hooks_parsed():
    """Top-level hooks should be parsed."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    assert "workflow_start" in wf.hooks
    assert "workflow_end" in wf.hooks
    start_hooks = wf.hooks["workflow_start"]
    assert len(start_hooks) >= 1
    assert start_hooks[0].operation == "snapshot_project"


def test_phase_type_llm():
    """LLM phase type should be parsed."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    llm_phases = [p for p in wf.phases if p.type == "llm"]
    assert len(llm_phases) > 0
    p0 = wf.phases[0]
    assert p0.type == "llm"
    assert p0.agent == "main_engineer"


def test_canonical_v2_yaml_has_no_phase_timeouts():
    """Canonical v2 YAML should not define phase wall-clock timeouts."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    globals_cfg = wf.globals or {}
    assert all(p.timeout is None for p in wf.phases)
    assert "entry_script_timeout" not in globals_cfg
    assert "session_timeout_phase" not in globals_cfg
    assert "session_timeout_repair" not in globals_cfg
    repair_loop = wf.sub_workflows["repair_loop"]
    assert all(not isinstance(p, dict) or "timeout" not in p for p in repair_loop.phases)
    improvement_block = repair_loop.blocks["improvement_block"]
    assert all("timeout" not in p for p in improvement_block["phases"])


def test_sm_adapt_workflow_yamls_have_no_phase_or_session_timeouts():
    timeout_tokens = ("timeout", "timeout_per_phase", "entry_script_timeout")
    for workflow_path in (PACKAGE_ROOT / "workflows").glob("*.yaml"):
        text = workflow_path.read_text(encoding="utf-8")
        for token in timeout_tokens:
            assert token not in text, f"{workflow_path.name} still contains {token}"
        assert "session_timeout" not in text, f"{workflow_path.name} still contains session_timeout"


def test_phase_validator_null():
    """Phase validator can be null."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    phases_15 = [p for p in wf.phases if p.id == "phase_1_5_constraint_summary"]
    assert len(phases_15) == 1
    assert phases_15[0].validator is None


def test_phase_condition():
    """Phase condition should be parsed."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    phases_15 = [p for p in wf.phases if p.id == "phase_1_5_constraint_summary"]
    assert phases_15[0].condition is not None


def test_phase_input_mapping():
    """Phase input_mapping should be parsed as dict."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    for p in wf.phases:
        if p.input_mapping:
            assert isinstance(p.input_mapping, dict)


def test_builtin_phase_type():
    """builtin phase type should be recognized."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    builtin_phases = [p for p in wf.phases if p.type == "builtin"]
    assert len(builtin_phases) >= 1


def test_canonical_builtin_phase_params_and_failure_transition():
    """Canonical v2 builtin and failure routing fields should be normalized."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))

    phase_35 = next(p for p in wf.phases if p.id == "phase_35_static_validate")
    phase_4 = next(p for p in wf.phases if p.id == "phase_4_rule_migration")

    assert phase_35.transitions["on_failure"] == "phase_3_entry_script"
    assert phase_4.params["operation"] == "rule_based_migration"
    assert phase_4.params["pattern"] == "*.py"


def test_builtin_phase_without_operation_still_loads(tmp_path: Path):
    """Builtin phases without operations should still load for compatibility."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: invalid_builtin
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    type: builtin
    prompt_template: x
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))

    assert wf.phases[0].type == "builtin"
    assert wf.phases[0].params == {}


def test_loop_phase_type():
    """loop phase type should be recognized."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    loop_phases = [p for p in wf.phases if p.type == "loop"]
    assert len(loop_phases) >= 1


def test_phase_on_skip_transition():
    """on_skip transition key should be parsed."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    phases_15 = [p for p in wf.phases if p.id == "phase_1_5_constraint_summary"]
    assert "on_skip" in phases_15[0].transitions


def test_missing_yaml_file():
    """Missing file should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_workflow("/nonexistent/path/file.yaml")


def test_phase_output_schema_raw():
    """output_schema $ref should be stored as dict."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v1.yaml"))
    p0 = wf.phases[0]
    assert isinstance(p0.output_schema, dict)


def test_duplicate_phase_id_rejected():
    """Duplicate phase ids should raise ValueError."""
    yaml_content = """
name: dup_test
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
  - id: phase_a
    prompt_template: y
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        with pytest.raises(ValueError):
            load_workflow(f.name)
    os.unlink(f.name)


def test_phase_agent_assignment():
    """Phase agent field should be correctly assigned."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    phase_0 = [p for p in wf.phases if p.id == "phase_0_env_detect"]
    assert len(phase_0) == 1
    assert phase_0[0].agent == "main_engineer"


def test_v2_terminals_dict():
    """V2 YAML uses dict terminals."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    assert "complete" in wf.terminals
    assert "failed" in wf.terminals
    assert "complete_partial" in wf.terminals


def test_phase_on_failure_default():
    """Phase on_failure should default to 'continue'."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    for p in wf.phases:
        assert isinstance(p.on_failure, str)


def test_runtime_skills_list_form_parsed_for_agent_and_phase(tmp_path: Path):
    """runtime_skills list shorthand should normalize to RuntimeSkillsConfig."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: runtime_skills_test
version: "1.0"
terminals: [complete]
agents:
  main_engineer:
    role: main_engineer
    lifecycle: persistent
    runtime_skills: [agent-skill]
phases:
  - id: phase_a
    prompt_template: x
    agent: main_engineer
    runtime_skills: [phase-skill]
    transitions:
      on_success: complete
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))

    agent_runtime_skills = wf.agents["main_engineer"]["runtime_skills"]
    assert isinstance(agent_runtime_skills, RuntimeSkillsConfig)
    assert agent_runtime_skills.include == ["agent-skill"]
    assert agent_runtime_skills.inject_full is False
    phase_runtime_skills = wf.phases[0].runtime_skills
    assert isinstance(phase_runtime_skills, RuntimeSkillsConfig)
    assert phase_runtime_skills.include == ["phase-skill"]
    assert phase_runtime_skills.inject_full is False


def test_runtime_skills_mapping_form_parsed(tmp_path: Path):
    """runtime_skills mapping form should preserve all supported options."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: runtime_skills_test
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
    runtime_skills:
      include: [skill-a, skill-b]
      exclude: [skill-a]
      merge: replace
      missing: error
      inject_full: false
      exclude_dynamic_duplicates: false
    transitions:
      on_success: complete
""",
        encoding="utf-8",
    )

    runtime_skills = load_workflow(str(workflow_path)).phases[0].runtime_skills

    assert runtime_skills == RuntimeSkillsConfig(
        include=["skill-a", "skill-b"],
        exclude=["skill-a"],
        merge="replace",
        missing="error",
        inject_full=False,
        exclude_dynamic_duplicates=False,
    )


def test_runtime_skills_mapping_form_can_enable_inject_full(tmp_path: Path):
    """runtime_skills mapping form should honor explicit inject_full: true."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: runtime_skills_test
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
    runtime_skills:
      include: [skill-a]
      inject_full: true
    transitions:
      on_success: complete
""",
        encoding="utf-8",
    )

    runtime_skills = load_workflow(str(workflow_path)).phases[0].runtime_skills

    assert isinstance(runtime_skills, RuntimeSkillsConfig)
    assert runtime_skills.inject_full is True


def test_runtime_skills_invalid_merge_rejected(tmp_path: Path):
    """Unsupported merge values should fail during YAML parsing."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: runtime_skills_test
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
    runtime_skills:
      include: [skill-a]
      merge: prepend
    transitions:
      on_success: complete
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime_skills.*merge"):
        load_workflow(str(workflow_path))


def test_transition_on_stagnation_only(tmp_path: Path):
    """transition: block with only on_stagnation should produce a TransitionDefinition."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: stagnation_test
version: "1.0"
terminals: [complete, error_recovery]
phases:
  - id: phase_a
    prompt_template: x
    transition:
      on_stagnation: error_recovery
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    phase = wf.phases[0]

    assert phase.transition is not None
    assert phase.transition.on_stagnation == "error_recovery"
    assert phase.transition.on_success is None
    assert phase.transition.on_failure is None


def test_transition_on_reject_exhausted_only(tmp_path: Path):
    """transition: block with only on_reject_exhausted should produce a TransitionDefinition."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: reject_exhausted_test
version: "1.0"
terminals: [complete, review_cleanup]
phases:
  - id: phase_a
    prompt_template: x
    transition:
      on_reject_exhausted: review_cleanup
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    phase = wf.phases[0]

    assert phase.transition is not None
    assert phase.transition.on_reject_exhausted == "review_cleanup"
    assert phase.transition.on_success is None


def test_transition_all_keys_together(tmp_path: Path):
    """transition: block with all keys including on_stagnation and on_reject_exhausted."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: full_transition_test
version: "1.0"
terminals: [complete, error_recovery, skip_target, stagnation_cleanup, exhausted_cleanup]
phases:
  - id: phase_a
    prompt_template: x
    transition:
      on_success: complete
      on_failure: error_recovery
      on_skip: skip_target
      on_stagnation: stagnation_cleanup
      on_reject_exhausted: exhausted_cleanup
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    phase = wf.phases[0]
    td = phase.transition

    assert td is not None
    assert td.on_success == "complete"
    assert td.on_failure == "error_recovery"
    assert td.on_skip == "skip_target"
    assert td.on_stagnation == "stagnation_cleanup"
    assert td.on_reject_exhausted == "exhausted_cleanup"


def test_empty_transition_dict_returns_none(tmp_path: Path):
    """Empty transition: {} should not produce a TransitionDefinition."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: empty_transition_test
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
    transition: {}
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    assert wf.phases[0].transition is None


def test_experience_defaults_to_enabled(tmp_path: Path):
    """Workflow without experience: section should default to enabled/true."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: no_experience
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    assert wf.experience.enabled is True
    assert wf.experience.phase7_enabled is True


def test_experience_explicit_disabled(tmp_path: Path):
    """experience: block with both flags set to false."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: disabled_experience
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
experience:
  enabled: false
  phase7_enabled: false
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    assert wf.experience.enabled is False
    assert wf.experience.phase7_enabled is False


def test_experience_partial_disable(tmp_path: Path):
    """Only phase7_enabled: false should leave enabled as true."""
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
name: partial_experience
version: "1.0"
terminals: [complete]
phases:
  - id: phase_a
    prompt_template: x
experience:
  phase7_enabled: false
""",
        encoding="utf-8",
    )

    wf = load_workflow(str(workflow_path))
    assert wf.experience.enabled is True
    assert wf.experience.phase7_enabled is False


def test_smoke_workflow_has_experience_disabled():
    """The auto smoke workflow should have experience disabled."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "ppu_migration_v2_auto_vllm018_smoke.yaml"))
    assert wf.experience.enabled is False
    assert wf.experience.phase7_enabled is False


def test_v2_workflow_has_experience_default():
    """The canonical v2 workflow should have default experience config."""
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2.yaml"))
    assert wf.experience.enabled is True
    assert wf.experience.phase7_enabled is True


def test_npu_workflows_are_explicitly_yaml_strategy_driven():
    for workflow_name in ("npu_migration_v2.yaml", "npu_migration_v2_container.yaml"):
        wf = load_workflow(str(PACKAGE_ROOT / "workflows" / workflow_name))
        assert wf.target_platform is not None
        assert wf.target_platform.preset == "npu_ascend"
        assert wf.rule_migration == {"strategy_file": "rule_strategies/cuda_to_npu.yaml"}
        phase_4 = next(p for p in wf.phases if p.id == "phase_4_rule_migration")
        assert phase_4.params["operation"] == "rule_based_migration"
        assert "backend" not in phase_4.params


def test_npu_workflows_reference_npu_prompt_templates():
    expected_main_prompts = {
        "phase_0_env_detect_npu",
        "phase_1_project_analysis_npu",
        "phase_1_5_constraint_summary_npu",
        "phase_2_venv_create_npu",
        "phase_3_entry_script_npu",
        "phase_35_static_validate_npu",
        "phase_6_report_npu",
    }
    for workflow_name in ("npu_migration_v2.yaml", "npu_migration_v2_container.yaml"):
        wf = load_workflow(str(PACKAGE_ROOT / "workflows" / workflow_name))
        prompt_templates = {p.prompt_template for p in wf.phases if p.prompt_template}
        assert expected_main_prompts.issubset(prompt_templates)


def test_npu_container_workflow_has_no_placeholder_image():
    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "npu_migration_v2_container.yaml"))
    assert wf.execution_backend is not None
    assert wf.execution_backend.image != "YOUR_IMAGE_HERE"
    assert wf.execution_backend.image
