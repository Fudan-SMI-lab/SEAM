from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import NamedTuple

from core.workflow_condition_parser import (
    ConditionEvaluationError as ConditionEvaluationError,
    ConditionEvaluationMode as ConditionEvaluationMode,
    ConditionValue as ConditionValue,
    evaluate_boolean_expression as evaluate_boolean_expression,
)


class ConditionRequest(NamedTuple):
    condition: str
    state: Mapping[str, ConditionValue]
    workflow_globals: Mapping[str, ConditionValue]
    context: Mapping[str, ConditionValue]
    loop_variables: Mapping[str, ConditionValue]
    loop_state: Mapping[str, ConditionValue]
    step_outputs: Mapping[str, ConditionValue]


class ConditionDecision(NamedTuple):
    matched: bool
    evaluation_error: str | None


def select_condition_evaluation_mode(
    workflow_globals: Mapping[str, ConditionValue],
) -> ConditionEvaluationMode:
    return (
        ConditionEvaluationMode.ACTIVE_V3
        if isinstance(workflow_globals.get("review_fail_closed"), bool)
        else ConditionEvaluationMode.LEGACY
    )


def evaluate_condition(
    request: ConditionRequest,
    resolve_template: Callable[[str], ConditionValue],
    resolve_expression: Callable[[str], ConditionValue],
) -> ConditionDecision:
    pattern = re.compile(r"\$\{([^{}]+)\}")
    if pattern.fullmatch(request.condition):
        resolved = resolve_template(request.condition)
    elif "${" in request.condition:
        resolved = pattern.sub(
            lambda match: json.dumps(
                resolve_expression(match.group(1).strip()),
                ensure_ascii=False,
                default=str,
            ),
            request.condition,
        )
    else:
        resolved = resolve_template(request.condition)
    if not isinstance(resolved, str):
        return ConditionDecision(bool(resolved), None)

    def replace_field(match: re.Match[str]) -> str:
        name = match.group(1)
        for source in (
            request.step_outputs,
            request.workflow_globals,
            request.context,
            request.loop_state,
        ):
            if name in source:
                value = source[name]
                return value if isinstance(value, str) else json.dumps(value)
        return repr(name)

    expression = re.sub(r"\$\.(\w+)", replace_field, resolved)
    lowered = expression.lower()
    if lowered in ("true", "1"):
        return ConditionDecision(True, None)
    if lowered in ("false", "0", ""):
        return ConditionDecision(False, None)
    environment = {
        **request.state,
        **request.workflow_globals,
        **request.context,
        **request.loop_state,
        **request.loop_variables,
        **request.step_outputs,
    }
    try:
        mode = select_condition_evaluation_mode(request.workflow_globals)
        return ConditionDecision(
            evaluate_boolean_expression(expression, environment, mode), None
        )
    except (ConditionEvaluationError, IndexError, TypeError, ValueError) as exc:
        return ConditionDecision(True, str(exc))
