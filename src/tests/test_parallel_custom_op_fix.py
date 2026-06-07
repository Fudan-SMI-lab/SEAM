# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedCallResult=false

"""Unit tests for parallel custom-op fix (_execute_parallel_custom_op_fix).

Covers:
  - Template routing: CUSTOM_OP vs CUSTOM_OP_WITH_VARIANTS
  - Session isolation: each group gets a unique session
  - Scoped progress block: group only sees its assigned units
  - Group agent ID naming: operator_fixer_group1, group2, ...
  - Fallback when no groups are formed
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.routes import CUSTOM_OP, CUSTOM_OP_WITH_VARIANTS
from core.types import PhaseDefinition
from core.workflow_executor import WorkflowExecutor


def _make_workflow():
    wf = MagicMock()
    wf.name = "generic_migration"
    wf.target_platform = None
    return wf


# ── Template routing ────────────────────────────────────────────────────

def test_prompt_template_for_llm_phase_custom_op_no_variants_uses_default() -> None:
    """CUSTOM_OP (no variants) falls through to default_template.
    The default_template is typically 'repair_operator_fixer_npu'."""
    state: dict[str, object] = {
        "phase_1_project_analysis": {"migration_route": CUSTOM_OP},
    }
    result = WorkflowExecutor._prompt_template_for_llm_phase(
        phase_id="fix_operator",
        default_template="repair_operator_fixer_npu",
        state=state,  # type: ignore[arg-type]
    )
    assert result == "repair_operator_fixer_npu"


def test_prompt_template_for_llm_phase_custom_op_with_variants_returns_variant_service() -> None:
    """CUSTOM_OP_WITH_VARIANTS routes to repair_custom_op_variant_service."""
    state: dict[str, object] = {
        "phase_1_project_analysis": {"migration_route": CUSTOM_OP_WITH_VARIANTS},
    }
    result = WorkflowExecutor._prompt_template_for_llm_phase(
        phase_id="fix_operator",
        default_template="repair_operator_fixer_npu",
        state=state,  # type: ignore[arg-type]
    )
    assert result == "repair_custom_op_variant_service"


def test_prompt_template_for_llm_phase_non_fix_operator_returns_default() -> None:
    """Non fix_operator phases always return default_template."""
    state: dict[str, object] = {
        "phase_1_project_analysis": {"migration_route": CUSTOM_OP_WITH_VARIANTS},
    }
    result = WorkflowExecutor._prompt_template_for_llm_phase(
        phase_id="fix_dependency",
        default_template="repair_dependency_fixer_npu",
        state=state,  # type: ignore[arg-type]
    )
    assert result == "repair_dependency_fixer_npu"


# ── Session isolation ───────────────────────────────────────────────────

def test_parallel_fix_creates_unique_sessions_per_group() -> None:
    """Each group gets a unique persistent session for isolation."""
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp) / "test_project"
        project_dir.mkdir()
        (project_dir / "migration_reports").mkdir()

        session_mgr = MagicMock()
        session_calls: list[str] = []
        session_mgr.get_or_create.side_effect = lambda role, lifecycle, **kw: (
            session_calls.append(role) or f"session-{role}-{len(session_calls)}"
        )

        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "## Prompt for group"

        artifact_store = MagicMock()
        validator_engine = MagicMock()

        executor = WorkflowExecutor(
            _make_workflow(), session_mgr, artifact_store, prompt_loader, validator_engine,
            project_dir=str(project_dir), output_dir=tmp,
        )

        executor.platform_policy = MagicMock()
        executor.platform_policy.custom_op_evidence.positive_boolean_fields = ["npu_custom"]
        executor.platform_policy.custom_op_evidence.target_device_values = ["npu", "ascend"]
        executor.platform_policy.custom_op_evidence.performance_baseline_device_values = ["cpu", "torch_cpu"]
        executor.platform_policy.custom_op_evidence.performance_baseline_boolean_fields = ["cpu_baseline"]
        executor.platform_policy.custom_op_evidence.custom_op_evidence_policy = ""
        executor.platform_policy.custom_op_evidence.native_build_log_tokens = ["opp_build.log"]
        executor.platform_policy.custom_op_evidence.native_binary_tokens = [".so"]
        executor.platform_policy.custom_op_evidence.native_source_tokens = [".cpp"]

        # Write a minimal gate so unit ledger returns groups
        gate_path = project_dir / "migration_reports" / "custom_op_final_gate.json"
        gate_path.write_text(json.dumps({"full_migration_status": "INCOMPLETE"}), encoding="utf-8")

        def _fake_ledger(gate_data, *, target_units=None, project_root=None, custom_op_surface=None):
            # Return 2 groups with 2 units each
            return cast(
                dict[str, object],
                {
                    "strict_pass_count": 0,
                    "remaining_count": 4,
                    "parallelization_groups": [
                        {"units": ["unit_a", "unit_b"]},
                        {"units": ["unit_c", "unit_d"]},
                    ],
                },
            )

        # Simulate a complete call ignoring LLM + artifacts
        with (
            patch("core.workflow_executor.custom_op_final_gate_unit_ledger", side_effect=_fake_ledger),
            patch.object(executor, "_write_repair_runtime_artifacts", return_value=("/tmp/re.md", "/tmp/card.md")),
            patch.object(executor, "_write_operator_repair_context_artifact", return_value="/tmp/ctx.md"),
            patch.object(executor, "_custom_op_phase1_phase3_repair_scope", return_value=""),
            patch.object(executor, "_resolve_constraint_summary", return_value=""),  # internal use: _build_group_prompt
            patch.object(executor, "_inject_llm_baseline_context"),  # internal use: _build_group_prompt
            patch.object(executor, "_append_explicit_runtime_skill_markdown", return_value=("prompt", "")),  # internal use
            patch.object(executor, "_send_sub_workflow_llm_command", return_value=json.dumps({
                "status": "success",
                "modified_files": ["a.py"],
                "fix_summary": "fixed",
                "agent_diagnostics": "",
            })),
            patch.object(executor, "_resolve_sub_workflow_llm_timeout", return_value=600),
        ):
            executor._execute_parallel_custom_op_fix(
                phase_id="fix_operator",
                mini=PhaseDefinition(
                    id="fix_operator", name="fix_operator",
                    prompt_template="repair_operator_fixer_npu",
                    output_schema={}, type="llm",
                    agent="operator_fixer",
                ),
                state={
                    "phase_3_entry_script": {
                        "entry_script_path": "python train.py",
                        "entry_script_kind": "custom_op_full_validation",
                        "operator_inventory_schema": {
                            "fine_grained_operator_units": ["unit_a", "unit_b", "unit_c", "unit_d"],
                        },
                    },
                },
                step_outputs={"script_stderr": "some error", "error_analysis": {"category": "operator"}},
                loop_vars={"project_dir": str(project_dir)},
            )

        # Verify session isolation: 2 groups → 2 unique sessions
        assert len(session_calls) == 2
        assert session_calls[0] == "operator_fixer_group1"
        assert session_calls[1] == "operator_fixer_group2"
        assert len(set(session_calls)) == 2  # no duplicate roles


# ── Scoped progress block ───────────────────────────────────────────────

def test_build_group_prompt_scopes_progress_to_assigned_units_only(tmp_path: Path) -> None:
    """Each group's prompt contains ONLY its assigned units, not the global count."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    (project_dir / "migration_reports").mkdir()

    # Write a gate with some pre-existing pass records
    gate_path = project_dir / "migration_reports" / "custom_op_final_gate.json"
    gate_path.write_text(json.dumps({
        "full_migration_status": "INCOMPLETE",
        "rows": [
            {
                "unit_name": "unit_x", "status": "closed",
                "inventory_count": 1, "manifest_entries": 1,
                "closed_pass_entries": 1, "remaining_entries": 0,
            },
            {"unit_name": "unit_y", "status": "open"},
        ],
        "source_inventory": {"entries": {"unit_x": {}, "unit_y": {}}},
        "performance_report": {
            "entries": [
                {"unit": "unit_x", "accuracy": "pass"},
            ],
        },
    }), encoding="utf-8")

    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "## GENERATED PROMPT"

    session_mgr = MagicMock()
    artifact_store = MagicMock()
    validator_engine = MagicMock()

    executor = WorkflowExecutor(
        _make_workflow(), session_mgr, artifact_store, prompt_loader, validator_engine,
        project_dir=str(project_dir), output_dir=str(tmp_path),
    )

    executor.platform_policy = MagicMock()
    executor.platform_policy.custom_op_evidence.positive_boolean_fields = ["npu_custom"]
    executor.platform_policy.custom_op_evidence.target_device_values = ["npu", "ascend"]
    executor.platform_policy.custom_op_evidence.performance_baseline_device_values = ["cpu"]
    executor.platform_policy.custom_op_evidence.performance_baseline_boolean_fields = ["cpu_baseline"]
    executor.platform_policy.custom_op_evidence.custom_op_evidence_policy = ""
    executor.platform_policy.custom_op_evidence.native_build_log_tokens = ["opp_build.log"]
    executor.platform_policy.custom_op_evidence.native_binary_tokens = [".so"]
    executor.platform_policy.custom_op_evidence.native_source_tokens = [".cpp"]

    captured_ctx: list[dict[str, str]] = []
    original_load = prompt_loader.load_prompt

    def capture_load(template, ctx):
        captured_ctx.append(cast(dict[str, str], ctx))
        return original_load(template, ctx)

    prompt_loader.load_prompt = capture_load

    with (
        patch.object(executor, "_write_repair_runtime_artifacts", return_value=("/tmp/re.md", "/tmp/card.md")),
        patch.object(executor, "_write_operator_repair_context_artifact", return_value="/tmp/ctx.md"),
        patch.object(executor, "_custom_op_phase1_phase3_repair_scope", return_value=""),
        patch.object(executor, "_resolve_constraint_summary", return_value=""),  # internal use
        patch.object(executor, "_inject_llm_baseline_context"),  # internal use
        patch.object(executor, "_append_explicit_runtime_skill_markdown", return_value=("prompt", "")),  # internal use
        patch.object(executor, "_send_sub_workflow_llm_command", return_value=json.dumps({
            "status": "success",
            "modified_files": [],
            "fix_summary": "",
            "agent_diagnostics": "",
        })),
        patch.object(executor, "_resolve_sub_workflow_llm_timeout", return_value=600),
    ):
        executor._execute_parallel_custom_op_fix(
            phase_id="fix_operator",
            mini=PhaseDefinition(
                id="fix_operator", name="fix_operator",
                prompt_template="repair_operator_fixer_npu",
                output_schema={}, type="llm",
                agent="operator_fixer",
            ),
            state={
                "phase_3_entry_script": {
                    "entry_script_path": "python train.py",
                    "entry_script_kind": "custom_op_full_validation",
                    "operator_inventory_schema": {
                        "fine_grained_operator_units": ["unit_a", "unit_b"],
                    },
                },
            },
            step_outputs={"script_stderr": "some error", "error_analysis": {"category": "operator"}},
            loop_vars={"project_dir": str(project_dir)},
        )

    # Verify prompt loading happened for the group
    assert len(captured_ctx) >= 1

    ctx = captured_ctx[0]
    # Scoped progress block should reference only assigned units
    progress = ctx.get("operator_repair_progress_block", "")
    assert "YOUR ASSIGNED UNITS ONLY" in progress
    assert "assigned_units=" in progress
    assert "scoped" in progress.lower() or "assigned" in progress.lower()
    # Assigned units from the context
    assert "assigned_units" in ctx
    assert "assigned_unit_count" in ctx


# ── Group agent ID naming ────────────────────────────────────────────────

def test_parallel_fix_includes_group_label_in_output() -> None:
    """Each group result carries _parallel_group label."""
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp) / "test_project"
        project_dir.mkdir()
        (project_dir / "migration_reports").mkdir()

        gate_path = project_dir / "migration_reports" / "custom_op_final_gate.json"
        gate_path.write_text(json.dumps({"full_migration_status": "INCOMPLETE"}), encoding="utf-8")

        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "## Prompt"
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session-1"
        artifact_store = MagicMock()
        validator_engine = MagicMock()

        executor = WorkflowExecutor(
            _make_workflow(), session_mgr, artifact_store, prompt_loader, validator_engine,
            project_dir=str(project_dir), output_dir=tmp,
        )
        executor.platform_policy = MagicMock()

        def _fake_ledger(*args, **kwargs):
            return cast(dict[str, object], {
                "strict_pass_count": 0, "remaining_count": 2,
                "parallelization_groups": [
                    {"units": ["unit_x"]},
                    {"units": ["unit_y"]},
                ],
            })

        with (
            patch("core.workflow_executor.custom_op_final_gate_unit_ledger", side_effect=_fake_ledger),
            patch.object(executor, "_write_repair_runtime_artifacts", return_value=("/tmp/re.md", "/tmp/card.md")),
            patch.object(executor, "_write_operator_repair_context_artifact", return_value="/tmp/ctx.md"),
            patch.object(executor, "_custom_op_phase1_phase3_repair_scope", return_value=""),
            patch.object(executor, "_resolve_constraint_summary", return_value=""),  # internal use
            patch.object(executor, "_inject_llm_baseline_context"),  # internal use
            patch.object(executor, "_append_explicit_runtime_skill_markdown", return_value=("prompt", "")),  # internal use
            patch.object(executor, "_send_sub_workflow_llm_command", return_value=json.dumps({
                "status": "success", "modified_files": ["x.py"], "fix_summary": "ok", "agent_diagnostics": "",
            })),
            patch.object(executor, "_resolve_sub_workflow_llm_timeout", return_value=600),
        ):
            result = executor._execute_parallel_custom_op_fix(
                phase_id="fix_operator",
                mini=PhaseDefinition(
                    id="fix_operator", name="fix_operator",
                    prompt_template="repair_operator_fixer_npu",
                    output_schema={}, type="llm",
                    agent="operator_fixer",
                ),
                state={
                    "phase_3_entry_script": {
                        "entry_script_path": "python train.py",
                        "entry_script_kind": "custom_op_full_validation",
                        "operator_inventory_schema": {"fine_grained_operator_units": ["unit_x", "unit_y"]},
                    },
                },
                step_outputs={"script_stderr": "some error", "error_analysis": {"category": "operator"}},
                loop_vars={"project_dir": str(project_dir)},
            )

        assert result is not None
        assert result["_parallel_group_count"] == 2
        parallel_results = result.get("_parallel_results")
        assert isinstance(parallel_results, list)
        assert len(parallel_results) == 2
        groups = [r.get("_parallel_group") for r in parallel_results if isinstance(r, dict)]
        assert "group-1" in groups
        assert "group-2" in groups


# ── Fallback when no groups ─────────────────────────────────────────────

def test_parallel_fix_returns_none_when_no_groups_and_no_target_units() -> None:
    """Returns None (fallback to single fix_operator) when no groups formable."""
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp) / "test_project"
        project_dir.mkdir()
        (project_dir / "migration_reports").mkdir()

        # No gate file → ledger returns no groups, no remaining_units
        prompt_loader = MagicMock()
        session_mgr = MagicMock()
        artifact_store = MagicMock()
        validator_engine = MagicMock()

        executor = WorkflowExecutor(
            _make_workflow(), session_mgr, artifact_store, prompt_loader, validator_engine,
            project_dir=str(project_dir), output_dir=tmp,
        )
        executor.platform_policy = MagicMock()

        def _empty_ledger(*args, **kwargs):
            return cast(dict[str, object], {"strict_pass_count": 0, "remaining_count": 0})

        with (
            patch("core.workflow_executor.custom_op_final_gate_unit_ledger", side_effect=_empty_ledger),
            patch.object(executor, "_resolve_sub_workflow_llm_timeout", return_value=600),
        ):
            result = executor._execute_parallel_custom_op_fix(
                phase_id="fix_operator",
                mini=PhaseDefinition(
                    id="fix_operator", name="fix_operator",
                    prompt_template="repair_operator_fixer_npu",
                    output_schema={}, type="llm",
                    agent="operator_fixer",
                ),
                state={
                    "phase_3_entry_script": {
                        "entry_script_path": "python train.py",
                        "entry_script_kind": "custom_op_full_validation",
                    },
                },
                step_outputs={"script_stderr": "", "error_analysis": {}},
                loop_vars={"project_dir": str(project_dir)},
            )

        assert result is None  # fallback to single fix_operator


def test_parallel_fix_returns_none_when_no_groups_and_no_gate(tmp_path: Path) -> None:
    """Returns None when both gate-derived and fallback groups are empty."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    (project_dir / "migration_reports").mkdir()

    prompt_loader = MagicMock()
    session_mgr = MagicMock()
    artifact_store = MagicMock()
    validator_engine = MagicMock()

    executor = WorkflowExecutor(
        _make_workflow(), session_mgr, artifact_store, prompt_loader, validator_engine,
        project_dir=str(project_dir), output_dir=str(tmp_path),
    )
    executor.platform_policy = MagicMock()

    def _ledger_no_groups_no_remaining(*args, **kwargs):
        return cast(dict[str, object], {"strict_pass_count": 0, "remaining_count": 0})

    with (
        patch("core.workflow_executor.custom_op_final_gate_unit_ledger", side_effect=_ledger_no_groups_no_remaining),
        patch.object(executor, "_resolve_sub_workflow_llm_timeout", return_value=600),
    ):
        result = executor._execute_parallel_custom_op_fix(
            phase_id="fix_operator",
            mini=PhaseDefinition(
                id="fix_operator", name="fix_operator",
                prompt_template="repair_operator_fixer_npu",
                output_schema={}, type="llm",
                agent="operator_fixer",
            ),
            state={},  # no phase_3 contract at all
            step_outputs={},
            loop_vars={"project_dir": str(project_dir)},
        )

    assert result is None
