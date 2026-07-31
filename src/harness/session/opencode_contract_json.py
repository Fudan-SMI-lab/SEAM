from __future__ import annotations

import json
import math
import typing
from collections.abc import Callable
from typing import Final, Protocol

if typing.TYPE_CHECKING:
    from core.compat import TypeAlias


MAX_CAPTURE_CHARS: Final = 64 * 1024 * 1024
MAX_JSON_INTEGER_DIGITS: Final = 309
MAX_JSON_DEPTH: Final = 128
MAX_JSON_NODES: Final = 1_000_000
JsonValue: "TypeAlias" = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)
JsonObject: "TypeAlias" = "dict[str, JsonValue]"
RawCapture: "TypeAlias" = "JsonValue | bytes"


class _JsonLoader(Protocol):
    def __call__(
        self,
        value: str | bytes,
        /,
        *,
        object_pairs_hook: Callable[[list[tuple[str, JsonValue]]], JsonObject],
        parse_constant: Callable[[str], JsonValue],
        parse_float: Callable[[str], JsonValue],
        parse_int: Callable[[str], JsonValue],
    ) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads


class _InvalidJsonError(Exception):
    pass


def _reject_constant(_value: str) -> JsonValue:
    raise _InvalidJsonError


def _finite_float(value: str) -> JsonValue:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _InvalidJsonError
    return parsed


def _bounded_int(value: str) -> JsonValue:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise _InvalidJsonError
    parsed = int(value)
    if parsed.bit_length() > 1024:
        raise _InvalidJsonError
    return parsed


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJsonError
        result[key] = value
    return result


def load_json(value: str | bytes) -> JsonValue:
    return _JSON_LOADS(
        value,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        parse_int=_bounded_int,
    )


def _within_structure_limits(value: JsonValue) -> bool:
    nodes = 0
    pending: list[tuple[JsonValue, int]] = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return False
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
    return True


def decode_capture(payload: str | bytes) -> RawCapture:
    payload_bytes = (
        payload
        if isinstance(payload, bytes)
        else payload.encode("utf-8", errors="surrogatepass")
    )
    if len(payload_bytes) > MAX_CAPTURE_CHARS:
        return None
    try:
        parsed = load_json(payload)
        return parsed if _within_structure_limits(parsed) else None
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        RecursionError,
        ValueError,
        _InvalidJsonError,
    ):
        return payload


def clone_json(value: RawCapture) -> RawCapture:
    if isinstance(value, bytes):
        return value
    return load_json(json.dumps(value, ensure_ascii=False))
