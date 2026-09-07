from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest

from core.config import load_workflow
from core.review_policy import (
    ReviewCliOverrides,
    ReviewDefaults,
    ReviewIterationLimit,
    ReviewPolicy,
    ReviewPolicyInputs,
    apply_review_policy,
    framework_review_defaults,
    resolve_review_policy,
    workflow_review_defaults,
)


def test_explicit_review_policy_wins_over_workflow_and_framework() -> None:
    # Given every precedence layer has a different policy.
    inputs = ReviewPolicyInputs(
        cli=ReviewCliOverrides(
            max_iterations=ReviewIterationLimit(5),
            fail_closed=False,
        ),
        workflow=ReviewDefaults(
            max_iterations=ReviewIterationLimit(4),
            fail_closed=True,
        ),
        framework=ReviewDefaults(
            max_iterations=ReviewIterationLimit(6),
            fail_closed=True,
        ),
    )

    # When the post-materialization resolver runs.
    policy = resolve_review_policy(inputs)

    # Then explicit CLI values win.
    assert policy.max_iterations == 5
    assert policy.fail_closed is False


def test_review_policy_uses_materialized_workflow_before_framework() -> None:
    # Given CLI values are unset and workflow policy differs from framework.
    inputs = ReviewPolicyInputs(
        cli=ReviewCliOverrides(max_iterations=None, fail_closed=None),
        workflow=ReviewDefaults(
            max_iterations=ReviewIterationLimit(4),
            fail_closed=False,
        ),
        framework=ReviewDefaults(
            max_iterations=ReviewIterationLimit(6),
            fail_closed=True,
        ),
    )

    # When the post-materialization resolver runs.
    policy = resolve_review_policy(inputs)

    # Then selected workflow values win without being masked.
    assert policy.max_iterations == 4
    assert policy.fail_closed is False


def test_review_policy_uses_framework_when_workflow_is_unset() -> None:
    # Given CLI and workflow values are unset.
    inputs = ReviewPolicyInputs(
        cli=ReviewCliOverrides(max_iterations=None, fail_closed=None),
        workflow=ReviewDefaults(max_iterations=None, fail_closed=None),
        framework=ReviewDefaults(
            max_iterations=ReviewIterationLimit(6),
            fail_closed=False,
        ),
    )

    # When the post-materialization resolver runs.
    policy = resolve_review_policy(inputs)

    # Then unwrapped framework values are used.
    assert policy.max_iterations == 6
    assert policy.fail_closed is False


def test_review_policy_falls_back_to_strict_literals() -> None:
    # Given every configurable policy layer is unset.
    inputs = ReviewPolicyInputs(
        cli=ReviewCliOverrides(max_iterations=None, fail_closed=None),
        workflow=ReviewDefaults(max_iterations=None, fail_closed=None),
        framework=ReviewDefaults(max_iterations=None, fail_closed=None),
    )

    # When the post-materialization resolver runs.
    policy = resolve_review_policy(inputs)

    # Then the public behavior is three strict rounds.
    assert policy.max_iterations == 3
    assert policy.fail_closed is True


def test_framework_review_defaults_are_unwrapped() -> None:
    # Given load_framework_config's nested framework envelope.
    config = {
        "framework": {
            "review": {"max_review_iterations": 7, "review_fail_closed": False}
        }
    }

    # When the review defaults boundary parses it.
    defaults = framework_review_defaults(config)

    # Then nested values are exposed to precedence resolution.
    assert defaults.max_iterations == 7
    assert defaults.fail_closed is False


def _write_workflow(
    path: Path,
    *,
    include_global_review: bool,
    include_subworkflow_review: bool = True,
) -> None:
    global_review = (
        "  max_review_iterations: 4\n  review_fail_closed: false\n"
        if include_global_review
        else ""
    )
    subworkflow_review = (
        "    max_review_iterations: 6\n" if include_subworkflow_review else ""
    )
    _ = path.write_text(
        f"""
name: review-policy
version: "1"
globals:
  max_repair_iterations: 8
{global_review}terminals: [complete]
phases:
  - id: phase_5_iterative_fix
    type: loop
    sub_workflow: repair_loop
sub_workflows:
  repair_loop:
    type: loop
    max_iterations: 9
{subworkflow_review}
""".lstrip(),
        encoding="utf-8",
    )


def test_workflow_defaults_use_selected_subworkflow_when_global_is_unset(
    tmp_path: Path,
) -> None:
    # Given a materialized workflow without a global review override.
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(workflow_path, include_global_review=False)
    workflow = load_workflow(str(workflow_path))

    # When review defaults are read after materialization.
    defaults = workflow_review_defaults(workflow)

    # Then the selected repair sub-workflow supplies the maximum.
    assert defaults.max_iterations == 6
    assert defaults.fail_closed is None


def test_review_policy_updates_review_without_changing_phase_five(
    tmp_path: Path,
) -> None:
    # Given a materialized workflow with independent repair and review limits.
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(workflow_path, include_global_review=True)
    workflow = load_workflow(str(workflow_path))
    policy = ReviewPolicy(
        max_iterations=ReviewIterationLimit(5),
        fail_closed=False,
    )

    # When the effective policy is applied to V3 workflow wiring.
    apply_review_policy(workflow, policy)

    # Then only review settings change and Phase 5 remains eight.
    globals_config = workflow.globals
    assert globals_config is not None
    assert globals_config["max_repair_iterations"] == 8
    assert globals_config["max_review_iterations"] == 5
    assert globals_config["review_fail_closed"] is False
    assert workflow.sub_workflows["repair_loop"].max_review_iterations == 5


def test_workflow_defaults_follow_each_materialized_workflow(
    tmp_path: Path,
) -> None:
    # Given two independently materialized workflow files.
    first_path = tmp_path / "first.yaml"
    second_path = tmp_path / "second.yaml"
    _write_workflow(first_path, include_global_review=True)
    _write_workflow(second_path, include_global_review=False)
    first = load_workflow(str(first_path))
    second = load_workflow(str(second_path))
    first.sub_workflows["repair_loop"].max_review_iterations = 4

    # When each loaded materialization is resolved.
    first_defaults = workflow_review_defaults(first)
    second_defaults = workflow_review_defaults(second)

    # Then no prior materialized value leaks into the next workflow.
    assert first_defaults.max_iterations == 4
    assert second_defaults.max_iterations == 6


def test_selected_subworkflow_maximum_wins_materialized_global(
    tmp_path: Path,
) -> None:
    # Given a materialized global of four and selected sub-workflow of six.
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(workflow_path, include_global_review=True)
    workflow = load_workflow(str(workflow_path))

    # When workflow review defaults are resolved.
    defaults = workflow_review_defaults(workflow)

    # Then the executor-selected sub-workflow value wins.
    assert defaults.max_iterations == 6


def test_omitted_subworkflow_maximum_reaches_framework_fallback(
    tmp_path: Path,
) -> None:
    # Given loaded YAML omits the selected sub-workflow review maximum.
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(
        workflow_path,
        include_global_review=False,
        include_subworkflow_review=False,
    )
    workflow = load_workflow(str(workflow_path))

    # When defaults preserve omission and policy resolution sees framework seven.
    workflow_defaults = workflow_review_defaults(workflow)
    policy = resolve_review_policy(
        ReviewPolicyInputs(
            cli=ReviewCliOverrides(max_iterations=None, fail_closed=None),
            workflow=workflow_defaults,
            framework=framework_review_defaults(
                {"framework": {"review": {"max_review_iterations": 7}}}
            ),
        )
    )

    # Then framework configuration is not masked by a loader-injected three.
    assert workflow_defaults.max_iterations is None
    assert policy.max_iterations == 7


def test_params_selected_subworkflow_takes_executor_precedence(
    tmp_path: Path,
) -> None:
    # Given the executor selects repair_loop through phase params.
    workflow_path = tmp_path / "workflow.yaml"
    _ = workflow_path.write_text(
        """
name: params-review-policy
version: "1"
globals:
  max_repair_iterations: 8
terminals: [complete]
phases:
  - id: phase_5_iterative_fix
    type: loop
    sub_workflow: declared_loop
    params:
      sub_workflow: repair_loop
sub_workflows:
  declared_loop:
    type: loop
    max_review_iterations: 9
  repair_loop:
    type: loop
    max_review_iterations: 2
""".lstrip(),
        encoding="utf-8",
    )
    workflow = load_workflow(str(workflow_path))

    # When explicit CLI policy five is applied.
    apply_review_policy(
        workflow,
        ReviewPolicy(max_iterations=ReviewIterationLimit(5), fail_closed=True),
    )

    # Then only the runtime-selected sub-workflow receives five.
    assert workflow.sub_workflows["repair_loop"].max_review_iterations == 5
    assert workflow.sub_workflows["declared_loop"].max_review_iterations == 9


@pytest.mark.parametrize("invalid", [True, 1.0])
def test_framework_maximum_rejects_coerced_nonintegers(invalid: bool | float) -> None:
    # Given a boolean or float in framework review configuration.
    config = {"framework": {"review": {"max_review_iterations": invalid}}}

    # When the typed framework boundary parses the value.
    with pytest.raises(ValidationError):
        _ = framework_review_defaults(config)


def test_framework_validation_error_hides_rejected_input() -> None:
    # Given an invalid value containing a secret-like sentinel.
    sentinel = "SECRET_REVIEW_LIMIT"
    config = {"framework": {"review": {"max_review_iterations": sentinel}}}

    # When framework validation fails.
    with pytest.raises(ValidationError) as raised:
        _ = framework_review_defaults(config)

    # Then persisted exception text cannot expose the rejected value.
    assert sentinel not in str(raised.value)
