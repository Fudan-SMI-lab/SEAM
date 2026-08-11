"""Safe shared JSONC parser: comment/comma stripping and typed value parsing.

Lifts the string-aware comment/trailing-comma logic from
``harness.server.lifecycle`` into a public typed parser with duplicate-key
rejection, prototype-key refusal, and an object-only config-root contract.

Honesty contract — comments are NOT preserved. The parser emits a normalized
(comment/comma stripped) string for caller-consented output only; the source
``text`` is never mutated, on success or failure. Keep the original file as
the owner-only backup. The naive regex in ``scripts.check_opencode_config``
(corrupts ``//``/``/*`` inside strings) is intentionally not reused.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

from core.compat import TypeAlias, assert_never

__all__ = [
    "JsoncError",
    "JsoncErrorKind",
    "JsoncParseError",
    "JsonValue",
    "ParsedJsonc",
    "error_kind_label",
    "parse_config_object",
    "parse_jsonc",
    "strip_jsonc",
]

# Recursive JSON value type. Quoted so basedpyright resolves the self
# reference; covers every value json.loads can produce.
JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)

# Keys that pollute JS object prototypes; rejected at the parse boundary.
_PROTOTYPE_KEYS: frozenset[str] = frozenset(
    {"__proto__", "constructor", "prototype"}
)


class JsoncErrorKind(Enum):
    """Sealed parse/contract failure variants."""

    UNTERMINATED_STRING = auto()
    UNTERMINATED_COMMENT = auto()
    DUPLICATE_KEY = auto()
    PROTOTYPE_KEY = auto()
    NON_OBJECT_ROOT = auto()
    INVALID_JSON = auto()


@dataclass(frozen=True, slots=True)
class JsoncError:
    """Typed parse error carrying the failure variant and a position."""

    kind: JsoncErrorKind
    message: str
    position: int


class JsoncParseError(ValueError):
    """Raised on a JSONC parse/contract violation; inspect ``self.error``."""

    def __init__(self, error: JsoncError) -> None:
        super().__init__(error.message)
        self.error: JsoncError = error


@dataclass(frozen=True, slots=True)
class ParsedJsonc:
    """Immutable parse result; comments dropped, not preserved."""

    value: JsonValue
    normalized: str


def error_kind_label(kind: JsoncErrorKind) -> str:
    """Exhaustive variant -> human label (new variants fail via assert_never)."""
    match kind:
        case JsoncErrorKind.UNTERMINATED_STRING:
            return "unterminated string literal"
        case JsoncErrorKind.UNTERMINATED_COMMENT:
            return "unterminated block comment"
        case JsoncErrorKind.DUPLICATE_KEY:
            return "duplicate object key"
        case JsoncErrorKind.PROTOTYPE_KEY:
            return "prototype-pollution key"
        case JsoncErrorKind.NON_OBJECT_ROOT:
            return "configuration root is not a JSON object"
        case JsoncErrorKind.INVALID_JSON:
            return "invalid JSON"
        case _:
            assert_never(kind)


def _fail(kind: JsoncErrorKind, message: str, position: int) -> JsoncParseError:
    return JsoncParseError(JsoncError(kind=kind, message=message, position=position))


def _strip_comments(text: str) -> str:
    """Remove // line and /* block */ comments, string-aware; fail-closed.

    Lifted from ``harness.server.lifecycle._strip_json_comments``; an
    unterminated string or block comment raises instead of silently
    truncating. ``text`` is never mutated.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    string_start = 0
    idx = 0
    n = len(text)
    while idx < n:
        char = text[idx]
        nxt = text[idx + 1] if idx + 1 < n else ""
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            idx += 1
            continue
        if char == '"':
            in_string = True
            string_start = idx
            out.append(char)
            idx += 1
            continue
        if char == "/" and nxt == "/":
            idx += 2
            while idx < n and text[idx] not in "\r\n":
                idx += 1
            continue
        if char == "/" and nxt == "*":
            start = idx
            idx += 2
            closed = False
            while idx < n:
                if text[idx] == "*" and idx + 1 < n and text[idx + 1] == "/":
                    idx += 2
                    closed = True
                    break
                idx += 1
            if not closed:
                raise _fail(
                    JsoncErrorKind.UNTERMINATED_COMMENT,
                    "unterminated block comment",
                    start,
                )
            continue
        out.append(char)
        idx += 1
    if in_string:
        raise _fail(
            JsoncErrorKind.UNTERMINATED_STRING,
            "unterminated string literal",
            string_start,
        )
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Drop commas whose next non-whitespace char is ``}`` or ``]`` (lifecycle)."""
    out: list[str] = []
    in_string = False
    escaped = False
    idx = 0
    n = len(text)
    while idx < n:
        char = text[idx]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            idx += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            idx += 1
            continue
        if char == ",":
            lookahead = idx + 1
            while lookahead < n and text[lookahead].isspace():
                lookahead += 1
            if lookahead < n and text[lookahead] in "}]":
                idx += 1
                continue
        out.append(char)
        idx += 1
    return "".join(out)


def strip_jsonc(text: str) -> str:
    """Return JSONC text with comments and trailing commas removed.

    Never mutates ``text``. Raises :class:`JsoncParseError` on an
    unterminated string or block comment.
    """
    return _strip_trailing_commas(_strip_comments(text))


def _build_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """object_pairs_hook: reject duplicate and prototype-pollution keys."""
    obj: dict[str, JsonValue] = {}
    for key, val in pairs:
        if key in _PROTOTYPE_KEYS:
            raise _fail(
                JsoncErrorKind.PROTOTYPE_KEY,
                f"prototype-pollution key rejected: {key!r}",
                0,
            )
        if key in obj:
            raise _fail(
                JsoncErrorKind.DUPLICATE_KEY,
                f"duplicate object key: {key!r}",
                0,
            )
        obj[key] = val
    return obj


class _JsonLoader(Protocol):
    # Typed alias for stdlib ``json.loads`` (typed as returning Any in
    # typeshed). Calling ``_JSON_LOADS`` instead of ``json.loads`` lets the
    # result narrow to ``JsonValue`` at the boundary without Any, casts, or
    # ignores. Same technique already used in
    # ``harness.session.opencode_contract_json`` and
    # ``harness.session.opencode_trace_bundle``.
    def __call__(
        self,
        value: str,
        /,
        *,
        object_pairs_hook: Callable[[list[tuple[str, JsonValue]]], dict[str, JsonValue]],
    ) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads


def parse_jsonc(text: str) -> ParsedJsonc:
    """Parse JSONC ``text`` into an immutable :class:`ParsedJsonc`.

    ``text`` is never mutated. Raises :class:`JsoncParseError` on malformed
    JSONC, unterminated strings/comments, duplicate keys, or prototype keys.
    """
    normalized = strip_jsonc(text)
    try:
        value = _JSON_LOADS(normalized, object_pairs_hook=_build_object)
    except JsoncParseError:
        raise
    except json.JSONDecodeError as exc:
        raise _fail(JsoncErrorKind.INVALID_JSON, str(exc), exc.pos) from exc
    return ParsedJsonc(value=value, normalized=normalized)


def parse_config_object(text: str) -> ParsedJsonc:
    """Like :func:`parse_jsonc` but require a JSON object as the root."""
    result = parse_jsonc(text)
    if not isinstance(result.value, dict):
        raise _fail(
            JsoncErrorKind.NON_OBJECT_ROOT,
            "configuration root must be a JSON object",
            0,
        )
    return result
