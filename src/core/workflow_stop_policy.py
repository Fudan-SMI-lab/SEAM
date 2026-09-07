from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from core.compat import TypeAlias

from core.workflow_condition_policy import (
    ConditionEvaluationError,
    evaluate_boolean_expression,
)


ConditionScalar: TypeAlias = "str | int | float | bool | None"


class StopCondition(NamedTuple):
    condition: str
    status: str


class StopDecision(NamedTuple):
    status: str | None
    evaluation_errors: tuple[str, ...]


def select_stop_status(
    conditions: Sequence[StopCondition],
    loop_state: Mapping[str, ConditionScalar],
    workflow_globals: Mapping[str, ConditionScalar],
) -> StopDecision:
    errors: list[str] = []
    environment = {**workflow_globals, **loop_state}
    for condition in conditions:

        def replace_field(match: re.Match[str]) -> str:
            name = match.group(1)
            for source in (loop_state, workflow_globals):
                if name in source:
                    value = source[name]
                    return value if isinstance(value, str) else json.dumps(value)
            return repr(name)

        expression = re.sub(r"\$\.(\w+)", replace_field, condition.condition)
        if expression.lower() == "true":
            return StopDecision(condition.status, tuple(errors))
        if expression.lower() == "false":
            continue
        try:
            if evaluate_boolean_expression(expression, environment):
                return StopDecision(condition.status, tuple(errors))
        except (ConditionEvaluationError, IndexError, TypeError, ValueError) as exc:
            errors.append(f"{condition.condition}: {exc}")
    return StopDecision(None, tuple(errors))
