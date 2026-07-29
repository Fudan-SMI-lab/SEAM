from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple, final

from typing_extensions import TypeAlias


ConditionScalar: TypeAlias = "str | int | float | bool | None"
ConditionValue: TypeAlias = (
    "ConditionScalar | Mapping[str, ConditionScalar] | Sequence[ConditionScalar]"
)


@final
class ConditionEvaluationError(Exception):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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


def _tokenize(expression: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    expression = expression.strip()
    while index < len(expression):
        if expression[index].isspace():
            index += 1
            continue
        if expression[index] in ('"', "'"):
            quote = expression[index]
            end = index + 1
            while end < len(expression) and expression[end] != quote:
                end += 2 if expression[end] == "\\" else 1
            tokens.append(expression[index : end + 1])
            index = end + 1
            continue
        if expression[index : index + 2] in ("==", "!=", ">=", "<="):
            tokens.append(expression[index : index + 2])
            index += 2
            continue
        if expression[index] in ("(", ")", ">", "<"):
            tokens.append(expression[index])
            index += 1
            continue
        end = index
        while end < len(expression) and (
            expression[end].isalnum() or expression[end] in "._-"
        ):
            end += 1
        if end > index:
            tokens.append(expression[index:end])
            index = end
        else:
            index += 1
    return tokens


def _ordered(left: ConditionValue, right: ConditionValue, operator: str) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator == ">=":
            return left >= right
        return left <= right
    if isinstance(left, str) and isinstance(right, str):
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator == ">=":
            return left >= right
        return left <= right
    raise ConditionEvaluationError(
        "ordered comparison requires matching strings or numbers"
    )


def _contains(container: ConditionValue, item: ConditionValue) -> bool:
    if isinstance(container, str) and isinstance(item, str):
        return item in container
    if isinstance(container, Mapping) and isinstance(item, str):
        return item in container
    if isinstance(container, Sequence) and not isinstance(container, str):
        return item in container
    raise ConditionEvaluationError("membership requires a string, mapping, or sequence")


def evaluate_boolean_expression(
    expression: str,
    environment: Mapping[str, ConditionValue],
) -> bool:
    tokens = _tokenize(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def consume(expected: str | None = None) -> str:
        nonlocal position
        token = tokens[position]
        position += 1
        if expected is not None and token != expected:
            raise ConditionEvaluationError(f"expected {expected!r}, got {token!r}")
        return token

    def primary() -> ConditionValue:
        token = peek()
        if token == "(":
            _ = consume("(")
            value = expression_value()
            _ = consume(")")
            return value
        if token is None:
            raise ConditionEvaluationError("unexpected end of expression")
        _ = consume()
        if token == "true":
            return True
        if token == "false":
            return False
        if token in ("null", "none"):
            return None
        try:
            number = float(token)
        except ValueError:
            number = None
        if number is not None:
            return int(number) if number == int(number) else number
        if token[:1] in ('"', "'") and token[-1:] == token[:1]:
            return token[1:-1]
        return environment.get(token, token)

    def comparison() -> ConditionValue:
        left = primary()
        operator = peek()
        if operator not in ("==", "!=", ">", "<", ">=", "<=", "in"):
            return left
        _ = consume()
        right = primary()
        if operator == "==":
            return left == right
        if operator == "!=":
            return left != right
        if operator == "in":
            return _contains(right, left)
        return _ordered(left, right, operator)

    def term() -> ConditionValue:
        if peek() == "not":
            _ = consume()
            return not bool(term())
        return comparison()

    def expression_value() -> ConditionValue:
        value = term()
        while peek() in ("and", "or"):
            operator = consume()
            right = term()
            value = (
                bool(value) and bool(right)
                if operator == "and"
                else (bool(value) or bool(right))
            )
        return value

    return bool(expression_value()) if tokens else False


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
        return ConditionDecision(
            evaluate_boolean_expression(expression, environment), None
        )
    except (ConditionEvaluationError, IndexError, TypeError, ValueError) as exc:
        return ConditionDecision(True, str(exc))
