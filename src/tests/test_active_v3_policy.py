from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.config import load_workflow
from core.config_loader import load_framework_config
from core.prompt_loader import PromptLoader
from core.repair_loop import JsonDict, RepairLoopEngine
from core.review_policy import (
    ReviewCliOverrides,
    ReviewIterationLimit,
    ReviewPolicyInputs,
    apply_review_policy,
    framework_review_defaults,
    review_outcome_routes,
    resolve_review_policy,
    workflow_review_defaults,
)
from core.run_outcome import ReviewOutcome
from core.types import WorkflowDefinition
from core.validator_engine import ValidatorEngine
from core.workflow_selector import resolve_workflow_from_selector


SRC_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_SELECTOR = SRC_ROOT / "workflows" / "seam_auto_default.yaml"
V2_WORKFLOW = SRC_ROOT / "workflows" / "npu_migration_v2.yaml"


class _SelectorSession:
    def get_or_create(
        self,
        role: str,
        lifecycle: str,
        agent: str = "",
    ) -> str:
        return f"selector-{role}-{lifecycle}-{agent}"

    def send_command(
        self,
        session_id: str,
        command: str,
        timeout: int | float = 120,
        agent: str = "",
    ) -> str:
        del session_id, command, timeout, agent
        return json.dumps({"selected_workflow": "musa_muxi_vllm.yaml"})


class _SelectorPromptLoader:
    def load_prompt(self, template_name: str, context: dict[str, str]) -> str:
        return f"{template_name}:{len(context)}"


class _LegacySession:
    def __init__(self) -> None:
        self._responses: list[str] = [
            json.dumps(
                {
                    "repair_role": "code_adapter",
                    "improvement_area": "review feedback",
                    "suggested_direction": "preserve compatibility",
                }
            ),
            '```json\n{"modified_files": [], "summary": "legacy improvement"}\n```',
        ]

    def get_or_create(self, role: str, lifecycle: str) -> str:
        return f"legacy-{role}-{lifecycle}"

    def send_command(
        self,
        session_id: str,
        command: str,
        timeout: int | None = None,
    ) -> str:
        del session_id, command, timeout
        return self._responses.pop(0)


def _materialize_active_workflow(tmp_path: Path) -> WorkflowDefinition:
    materialized = resolve_workflow_from_selector(
        str(ACTIVE_SELECTOR),
        _SelectorSession(),
        _SelectorPromptLoader(),
        output_dir=tmp_path,
    )
    return load_workflow(str(materialized))


def test_active_v3_selector_materializes_three_strict_reviews(tmp_path: Path) -> None:
    # Given the active selector has selected and materialized a reachable workflow.
    workflow = _materialize_active_workflow(tmp_path)

    # When unset CLI policy is resolved after materialization.
    policy = resolve_review_policy(
        ReviewPolicyInputs(
            cli=ReviewCliOverrides(max_iterations=None, fail_closed=None),
            workflow=workflow_review_defaults(workflow),
            framework=framework_review_defaults(load_framework_config()),
        )
    )
    apply_review_policy(workflow, policy)

    # Then active V3 uses three strict rounds without changing Phase 5's maximum.
    assert policy.max_iterations == 3
    assert policy.fail_closed is True
    assert workflow.globals is not None
    assert workflow.globals["max_review_iterations"] == 3
    assert workflow.globals["review_fail_closed"] is True
    assert workflow.globals["max_repair_iterations"] == 8
    assert workflow.sub_workflows["repair_loop"].max_review_iterations == 3


def test_active_v3_explicit_policy_keeps_task_seven_precedence(tmp_path: Path) -> None:
    # Given a reachable active workflow and explicit compatibility CLI values.
    workflow = _materialize_active_workflow(tmp_path)

    # When the effective policy is resolved through the established precedence.
    policy = resolve_review_policy(
        ReviewPolicyInputs(
            cli=ReviewCliOverrides(
                max_iterations=ReviewIterationLimit(5),
                fail_closed=False,
            ),
            workflow=workflow_review_defaults(workflow),
            framework=framework_review_defaults(load_framework_config()),
        )
    )
    apply_review_policy(workflow, policy)

    # Then explicit values win while the repair maximum remains independent.
    assert policy.max_iterations == 5
    assert policy.fail_closed is False
    assert workflow.globals is not None
    assert workflow.globals["max_review_iterations"] == 5
    assert workflow.globals["review_fail_closed"] is False
    assert workflow.globals["max_repair_iterations"] == 8


def test_reject_exhaustion_route_has_typed_outcome_and_legacy_raw_value(
    tmp_path: Path,
) -> None:
    # Given an existing workflow transition using the legacy YAML key.
    workflow_path = tmp_path / "workflow.yaml"
    _ = workflow_path.write_text(
        """
name: typed-rejection-route
version: "1"
terminals: [complete, review_cleanup]
phases:
  - id: phase_5_validation
    prompt_template: phase_5
    transition:
      on_reject_exhausted: review_cleanup
""".lstrip(),
        encoding="utf-8",
    )

    # When the standard configuration boundary parses the workflow.
    transition = load_workflow(str(workflow_path)).phases[0].transition

    # Then the typed Task 8 outcome and legacy raw destination coexist.
    assert transition is not None
    assert review_outcome_routes(transition) == {
        ReviewOutcome.REJECT_EXHAUSTED: "review_cleanup"
    }
    assert transition.on_reject_exhausted == "review_cleanup"


def test_v2_keeps_legacy_defaults_flags_transitions_and_bytes() -> None:
    # Given the canonical V2 harness and workflow bytes.
    harness_path = SRC_ROOT / "tests" / "e2e" / "e2e_test_v2.py"
    harness_source = harness_path.read_text(encoding="utf-8")
    workflow_bytes = V2_WORKFLOW.read_bytes()

    # When V2 configuration is loaded without active V3 policy materialization.
    workflow = load_workflow(str(V2_WORKFLOW))
    phase_five = next(
        phase for phase in workflow.phases if phase.id == "phase_5_validation"
    )

    # Then V2 keeps its defaults, surface, transitions, and source bytes.
    assert "DEFAULT_MAX_PHASE5_ITER = 5" in harness_source
    assert 'parser.add_argument("--review-gate"' in harness_source
    for active_flag in (
        "--max-review-iter",
        "--review-fail-closed",
        "--no-review-fail-closed",
    ):
        assert active_flag not in harness_source
    assert workflow.globals is not None
    assert workflow.globals["max_repair_iterations"] == 5
    assert workflow.globals["review_gate_enabled"] is False
    assert "review_fail_closed" not in workflow.globals
    assert phase_five.transitions == {
        "on_success": "phase_6_report",
        "on_failure": "complete",
    }
    assert phase_five.transition is None
    assert V2_WORKFLOW.read_bytes() == workflow_bytes


def test_legacy_repair_loop_keeps_passed_with_reviews(tmp_path: Path) -> None:
    # Given the legacy engine defaults and one successful run rejected by review.
    signature = inspect.signature(RepairLoopEngine.run)
    script_path = tmp_path / "success.py"
    _ = script_path.write_text("raise SystemExit(0)\n", encoding="utf-8")
    engine = RepairLoopEngine(
        _LegacySession(),
        ArtifactStore(str(tmp_path), "legacy-policy"),
        PromptLoader(),
        ValidatorEngine(),
    )

    def reject_review(_context: JsonDict) -> JsonDict:
        return {"verdict": "reject", "reasoning": "legacy rejection"}

    # When the unchanged engine exhausts its one-round review budget.
    result = engine.run(
        entry_script=f'"{sys.executable}" "{script_path}"',
        project_dir=str(tmp_path),
        enable_review_gate=True,
        max_review_iterations=1,
        review_callable=reject_review,
    )

    # Then its signature and passed_with_reviews result remain legacy-compatible.
    assert "review_fail_closed" not in signature.parameters
    assert "on_reject_exhausted" not in signature.parameters
    assert result["success"] is True
    assert result["status"] == "passed_with_reviews"
    assert result["iteration_count"] == 1
    assert result["final_exit_code"] == 0
    review_summary = result["review_gate_summary"]
    assert isinstance(review_summary, dict)
    assert review_summary["review_rejections"] == 1
    assert review_summary["improvement_iterations"] == 1
