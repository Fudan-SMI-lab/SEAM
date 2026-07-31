from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Final

from core.compat import TypeAlias

JsonScalar: TypeAlias = "str | int | float | bool | None"
JsonValue: TypeAlias = "JsonScalar | Sequence[JsonValue] | Mapping[str, JsonValue]"
_REDACTED: Final = "<REDACTED>"
_SENSITIVE_KEY_PARTS: Final = frozenset(
    {
        "api-key",
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_SENSITIVE_COMPOUND_PARTS: Final = frozenset(
    {
        "accesstoken",
        "apikey",
        "clientsecret",
    }
)
_STRUCTURAL_SUFFIXES: Final = frozenset(
    {
        "complete",
        "count",
        "enabled",
        "id",
        "length",
        "requested",
        "status",
        "type",
    }
)
_NAME_SEPARATOR: Final = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_REDACTION_MARKER: Final = re.compile(r"<REDACTED(?:_[A-Z_]+)?>")
_SECRET_PATTERNS: Final = (
    (re.compile(r"(?i)(Bearer)\s+[A-Za-z0-9._~+/=-]+"), "\\1 <REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<REDACTED_API_KEY>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}"), "<REDACTED_GITHUB_TOKEN>"),
)
_QUOTED_NAMED_VALUE: Final = re.compile(
    r"(?P<name>[\"']?[A-Za-z_][A-Za-z0-9_-]*[\"']?)"
    + r"(?P<separator>\s*[:=]\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_PLAIN_NAMED_VALUE: Final = re.compile(
    r"\b(?P<name>[A-Za-z_][A-Za-z0-9_-]*)"
    + r"(?P<separator>\s*[:=]\s*)(?P<value>[^\s\'\"`,;]+)"
)
_QUOTED_CLI_VALUE: Final = re.compile(
    r"(?i)(?P<option>(?:--|/)[A-Za-z0-9_-]+)(?P<separator>\s*(?:=|:)\s*|\s+)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_PLAIN_CLI_VALUE: Final = re.compile(
    r"(?i)(?P<option>(?:--|/)[A-Za-z0-9_-]+)(?P<separator>\s*(?:=|:)\s*|\s+)(?P<value>[^\s,;]+)"
)


def _name_parts(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    segments = tuple(
        segment for segment in _NAME_SEPARATOR.split(name.lstrip("-/")) if segment
    )
    raw = tuple(segment.lower() for segment in segments)
    expanded = tuple(
        part.lower()
        for segment in segments
        for part in _CAMEL_BOUNDARY.split(segment)
        if part
    )
    return raw, expanded


def _is_sensitive_name(name: str) -> bool:
    raw, expanded = _name_parts(name.strip("\"'"))
    if not raw:
        return False
    suffix = expanded[-1] if expanded else raw[-1]
    if suffix in _STRUCTURAL_SUFFIXES:
        return False
    candidates = frozenset((*raw, *expanded))
    if candidates & _SENSITIVE_KEY_PARTS:
        return True
    if candidates & _SENSITIVE_COMPOUND_PARTS:
        return True
    return any(
        first == "api" and second == "key" for first, second in pairwise(expanded)
    )


def _is_sensitive_option(option: str) -> bool:
    return _is_sensitive_name(option)


def _redact_quoted(match: re.Match[str]) -> str:
    if not _is_sensitive_option(match.group("option")):
        return match.group(0)
    return (
        f"{match.group('option')}{match.group('separator')}"
        f"{match.group('quote')}{_REDACTED}{match.group('quote')}"
    )


def _redact_plain(match: re.Match[str]) -> str:
    if not _is_sensitive_option(match.group("option")):
        return match.group(0)
    return f"{match.group('option')}{match.group('separator')}{_REDACTED}"


def _redact_named_quoted(match: re.Match[str]) -> str:
    if not _is_sensitive_name(match.group("name")):
        return match.group(0)
    return (
        f"{match.group('name')}{match.group('separator')}{match.group('quote')}"
        f"{_REDACTED}{match.group('quote')}"
    )


def _redact_named_plain(match: re.Match[str]) -> str:
    if not _is_sensitive_name(match.group("name")):
        return (
            f"{match.group('name')}{match.group('separator')}"
            f"{redact_sensitive_text(match.group('value'))}"
        )
    return f"{match.group('name')}{match.group('separator')}{_REDACTED}"


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    redacted = _QUOTED_CLI_VALUE.sub(_redact_quoted, redacted)
    redacted = _PLAIN_CLI_VALUE.sub(_redact_plain, redacted)
    redacted = _QUOTED_NAMED_VALUE.sub(_redact_named_quoted, redacted)
    redacted = _PLAIN_NAMED_VALUE.sub(_redact_named_plain, redacted)
    return redacted


def contains_redaction_marker(value: str) -> bool:
    return _REDACTION_MARKER.search(value) is not None


def _redacted_token_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return f"{value[0]}{_REDACTED}{value[-1]}"
    return _REDACTED


def redact_cli_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    sanitized: list[str] = []
    redact_next = False
    for token in arguments:
        if redact_next:
            sanitized.append(_redacted_token_value(token))
            redact_next = False
            continue
        option, separator, value = token.partition("=")
        if not separator and token.startswith("/"):
            option, separator, value = token.partition(":")
        if _is_sensitive_option(option):
            sanitized.append(
                f"{option}{separator}{_redacted_token_value(value)}"
                if separator
                else option
            )
            redact_next = not separator
            continue
        sanitized.append(redact_sensitive_text(token))
    return tuple(sanitized)


def redact_named_value(name: str, value: str) -> str:
    if _is_sensitive_name(name):
        return _REDACTED
    return redact_sensitive_text(value)


def redact_json_value(
    value: JsonValue,
    key: str = "",
    *,
    sensitive_parent: bool = False,
) -> JsonValue:
    sensitive = sensitive_parent or bool(key and _is_sensitive_name(key))
    if isinstance(value, str):
        return _REDACTED if sensitive else redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {
            child_key: redact_json_value(
                child_value,
                child_key,
                sensitive_parent=sensitive,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, Sequence):
        return [redact_json_value(item, sensitive_parent=sensitive) for item in value]
    return value
