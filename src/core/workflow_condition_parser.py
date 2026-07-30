from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from enum import Enum, unique
from typing import final

from typing_extensions import TypeAlias


ConditionScalar: TypeAlias = "str | int | float | bool | None"
ConditionValue: TypeAlias = "ConditionScalar | Mapping[str, ConditionScalar] | Sequence[ConditionScalar] | Set[ConditionScalar]"


@unique
class ConditionEvaluationMode(Enum):
    LEGACY = "legacy"
    ACTIVE_V3 = "active_v3"


@final
class ConditionEvaluationError(Exception):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _tokenize(expression: str, *, strict: bool) -> list[str]:
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
            if strict and end >= len(expression):
                raise ConditionEvaluationError("unterminated string literal")
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
        elif strict:
            raise ConditionEvaluationError(
                f"unexpected character {expression[index]!r}"
            )
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
    if isinstance(container, Set):
        return item in container
    raise ConditionEvaluationError(
        "membership requires a string, mapping, sequence, or set"
    )


def _compare(
    left: ConditionValue,
    right: ConditionValue,
    operator: str,
) -> bool:
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == "in":
        return _contains(right, left)
    if operator == "not in":
        return not _contains(right, left)
    return _ordered(left, right, operator)


def evaluate_boolean_expression(
    expression: str,
    environment: Mapping[str, ConditionValue],
    mode: ConditionEvaluationMode = ConditionEvaluationMode.LEGACY,
) -> bool:
    enhanced = mode is ConditionEvaluationMode.ACTIVE_V3
    tokens = _tokenize(expression, strict=enhanced)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def consume(expected: str | None = None) -> str:
        nonlocal position
        if position >= len(tokens):
            raise ConditionEvaluationError("unexpected end of expression")
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
        if not enhanced:
            operator = peek()
            if operator not in ("==", "!=", ">", "<", ">=", "<=", "in"):
                return left
            _ = consume()
            return _compare(left, primary(), operator)

        compared = False
        result = True
        while True:
            operator = peek()
            if operator == "not" and position + 1 < len(tokens):
                operator = "not in" if tokens[position + 1] == "in" else operator
            if operator not in ("==", "!=", ">", "<", ">=", "<=", "in", "not in"):
                return result if compared else left
            _ = consume()
            if operator == "not in":
                _ = consume("in")
            right = primary()
            result = result and _compare(left, right, operator)
            compared = True
            left = right

    def term() -> ConditionValue:
        if peek() == "not":
            _ = consume()
            return not bool(term())
        return comparison()

    def conjunction() -> ConditionValue:
        value = term()
        while peek() == "and":
            _ = consume("and")
            right = term()
            value = bool(value) and bool(right)
        return value

    def expression_value() -> ConditionValue:
        if not enhanced:
            value = term()
            while peek() in ("and", "or"):
                operator = consume()
                right = term()
                value = (
                    bool(value) and bool(right)
                    if operator == "and"
                    else bool(value) or bool(right)
                )
            return value

        value = conjunction()
        while peek() == "or":
            _ = consume("or")
            right = conjunction()
            value = bool(value) or bool(right)
        return value

    if not tokens:
        return False
    result = bool(expression_value())
    if enhanced and peek() is not None:
        raise ConditionEvaluationError(f"unexpected token {peek()!r}")
    return result
