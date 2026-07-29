from __future__ import annotations

import re
from typing import Final

from typing_extensions import TypeAlias

JsonScalar: TypeAlias = "str | int | float | bool | None"
JsonValue: TypeAlias = (
    "JsonScalar | list[JsonValue] | tuple[JsonValue, ...] | dict[str, JsonValue]"
)
_REDACTED: Final = "<REDACTED>"
_SENSITIVE_KEY_FRAGMENTS: Final = (
    "auth",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)
_SENSITIVE_OPTIONS: Final = frozenset({"api-key", "apikey"})
_SECRET_PATTERNS: Final = (
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<REDACTED_API_KEY>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}"), "<REDACTED_GITHUB_TOKEN>"),
    (
        re.compile(
            r"(?i)([\"']?(?:HF_TOKEN|HUGGINGFACE_TOKEN|OPENAI_API_KEY|API_KEY|TOKEN|PASSWORD|PASSWD|SECRET)[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])"
        ),
        r"\1<REDACTED>\3",
    ),
    (
        re.compile(
            r"(?i)\b(HF_TOKEN|HUGGINGFACE_TOKEN|OPENAI_API_KEY|API_KEY|TOKEN|PASSWORD|PASSWD|SECRET)\s*([:=])\s*([^\s\'\"`,;]+)"
        ),
        r"\1\2<REDACTED>",
    ),
)
_QUOTED_CLI_VALUE: Final = re.compile(
    r"(?i)(?P<option>(?:--|/)[A-Za-z0-9_-]+)(?P<separator>\s*(?:=|:)\s*|\s+)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_PLAIN_CLI_VALUE: Final = re.compile(
    r"(?i)(?P<option>(?:--|/)[A-Za-z0-9_-]+)(?P<separator>\s*(?:=|:)\s*|\s+)(?P<value>[^\s,;]+)"
)


def _is_sensitive_option(option: str) -> bool:
    normalized = option.lstrip("-/").lower().replace("_", "-")
    parts = frozenset(normalized.split("-"))
    return normalized in _SENSITIVE_OPTIONS or any(
        fragment in parts or fragment in normalized
        for fragment in _SENSITIVE_KEY_FRAGMENTS
    )


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


def redact_sensitive_text(text: str) -> str:
    redacted = _QUOTED_CLI_VALUE.sub(_redact_quoted, text)
    redacted = _PLAIN_CLI_VALUE.sub(_redact_plain, redacted)
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


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
    if any(fragment in name.lower() for fragment in _SENSITIVE_KEY_FRAGMENTS):
        return _REDACTED
    return redact_sensitive_text(value)


def redact_json_value(value: JsonValue, key: str = "") -> JsonValue:
    if key and any(fragment in key.lower() for fragment in _SENSITIVE_KEY_FRAGMENTS):
        return _REDACTED
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {
            child_key: redact_json_value(child_value, child_key)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_json_value(item) for item in value]
    return value
