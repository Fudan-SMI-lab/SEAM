from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.types import TransitionDefinition
from core.workflow_condition_policy import (
    ConditionDecision,
    ConditionRequest,
    ConditionValue,
    evaluate_boolean_expression,
    evaluate_condition,
)
from core.workflow_transition_policy import TransitionRequest, plan_next_phase


def _evaluate_request(
    expression: str,
    environment: Mapping[str, ConditionValue],
    workflow_globals: Mapping[str, ConditionValue],
) -> ConditionDecision:
    request = ConditionRequest(
        expression,
        environment,
        workflow_globals,
        {},
        {},
        {},
        {},
    )
    return evaluate_condition(request, lambda value: value, lambda value: value)


@pytest.mark.parametrize(
    ("expression", "workflow_globals", "expected", "error_fragment"),
    [
        ("true or false and false", {}, False, None),
        (
            "true or false and false",
            {"review_fail_closed": True},
            True,
            None,
        ),
        ("false trailing", {}, False, None),
        (
            "false trailing",
            {"review_fail_closed": True},
            True,
            "unexpected token",
        ),
        ("false @", {}, False, None),
        (
            "false @",
            {"review_fail_closed": True},
            True,
            "unexpected character",
        ),
        ("true and", {}, True, "unexpected end"),
        (
            "true and",
            {"review_fail_closed": True},
            True,
            "unexpected end",
        ),
    ],
)
def test_condition_semantics_follow_active_v3_boundary(
    expression: str,
    workflow_globals: Mapping[str, ConditionValue],
    expected: bool,
    error_fragment: str | None,
) -> None:
    # Given
    environment: Mapping[str, ConditionValue] = {}

    # When
    decision = _evaluate_request(expression, environment, workflow_globals)

    # Then
    assert decision.matched is expected
    if error_fragment is None:
        assert decision.evaluation_error is None
    else:
        assert decision.evaluation_error is not None
        assert error_fragment in decision.evaluation_error


def test_shared_boolean_evaluator_defaults_to_legacy_precedence() -> None:
    # Given
    environment: Mapping[str, ConditionValue] = {}

    # When
    matched = evaluate_boolean_expression("true or false and false", environment)

    # Then
    assert matched is False


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("3 > 2 > 4", False),
        ("true or false and false", True),
        ("not false and true", True),
        ("(true or false) and false", False),
        ("ready not in members", False),
    ],
)
def test_active_v3_keeps_enhanced_condition_semantics(
    expression: str,
    expected: bool,
) -> None:
    # Given
    environment: Mapping[str, ConditionValue] = {"members": {"ready", "running"}}

    # When
    decision = _evaluate_request(
        expression,
        environment,
        {"review_fail_closed": True},
    )

    # Then
    assert decision == ConditionDecision(expected, None)


@pytest.mark.parametrize(
    ("expression", "environment", "expected"),
    [
        ("ready in members", {"members": {"ready", "running"}}, True),
        ("missing in members", {"members": ["ready", "running"]}, False),
        ("run in label", {"label": "runner"}, True),
        ("ready in members", {"members": {"ready": 1}}, True),
    ],
)
@pytest.mark.parametrize(
    "workflow_globals",
    [{}, {"review_fail_closed": True}],
    ids=["legacy", "active-v3"],
)
def test_membership_matches_container_semantics_on_both_paths(
    expression: str,
    environment: Mapping[str, ConditionValue],
    expected: bool,
    workflow_globals: Mapping[str, ConditionValue],
) -> None:
    # When
    decision = _evaluate_request(expression, environment, workflow_globals)

    # Then
    assert decision == ConditionDecision(expected, None)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("3 > 2", True),
        ("2 >= 3", False),
        ("name == 'seam'", True),
        ("name != 'other'", True),
    ],
)
@pytest.mark.parametrize(
    "workflow_globals",
    [{}, {"review_fail_closed": True}],
    ids=["legacy", "active-v3"],
)
def test_valid_comparisons_match_on_both_paths(
    expression: str,
    expected: bool,
    workflow_globals: Mapping[str, ConditionValue],
) -> None:
    # Given
    environment: Mapping[str, ConditionValue] = {"name": "seam"}

    # When
    decision = _evaluate_request(expression, environment, workflow_globals)

    # Then
    assert decision == ConditionDecision(expected, None)


@pytest.mark.parametrize(
    ("status", "transition", "transitions", "phase7_enabled", "expected"),
    [
        ("failure", TransitionDefinition(on_failure="recover"), {}, True, "recover"),
        ("skipped", None, {"on_skip": "skip_target"}, True, "skip_target"),
        ("success", None, {}, True, "phase_b"),
        ("failure", None, {}, True, None),
        (
            "success",
            TransitionDefinition(on_success="phase_7a_evaluate"),
            {},
            False,
            "complete",
        ),
    ],
)
def test_transition_policy_preserves_legacy_routing(
    status: str,
    transition: TransitionDefinition | None,
    transitions: Mapping[str, str],
    phase7_enabled: bool,
    expected: str | None,
) -> None:
    # Given
    request = TransitionRequest(
        current_phase_id="phase_a",
        status=status,
        transition=transition,
        transitions=transitions,
        phase_ids=("phase_a", "phase_b"),
        phase_index={"phase_a": 0, "phase_b": 1},
        phase7_enabled=phase7_enabled,
    )

    # When
    target = plan_next_phase(request)

    # Then
    assert target == expected
