from __future__ import annotations

import shlex
import unicodedata
from typing import Final

from core.agent_io_logger import redact_sensitive_text
from core.phase5_attempt_receipt import BackendKind, Phase5AttemptReceipt

_REDACTED: Final = "<REDACTED>"
_SENSITIVE_OPTIONS: Final = frozenset(
    {
        "api-key",
        "apikey",
        "auth",
        "authorization",
        "password",
        "passwd",
        "secret",
        "token",
    }
)
_SENSITIVE_OPTION_PARTS: Final = frozenset(
    {"auth", "authorization", "passwd", "password", "secret", "token"}
)


def _public_text(value: str) -> str:
    escaped_parts: list[str] = []
    for character in value:
        codepoint = ord(character)
        if not unicodedata.category(character).startswith("C"):
            escaped_parts.append(character)
        elif codepoint <= 0xFF:
            escaped_parts.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            escaped_parts.append(f"\\u{codepoint:04x}")
        else:
            escaped_parts.append(f"\\U{codepoint:08x}")
    escaped = "".join(escaped_parts)
    return redact_sensitive_text(escaped)


def public_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    sanitized: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            sanitized.append(_REDACTED)
            redact_next = False
            continue
        option, separator, _value = token.partition("=")
        normalized = option.lstrip("-/").lower().replace("_", "-")
        option_parts = frozenset(normalized.split("-"))
        if normalized in _SENSITIVE_OPTIONS or option_parts & _SENSITIVE_OPTION_PARTS:
            sanitized.append(f"{option}={_REDACTED}" if separator else option)
            redact_next = not separator
            continue
        sanitized.append(_public_text(token))
    return tuple(sanitized)


def public_validation_command(receipt: Phase5AttemptReceipt) -> str:
    return shlex.join(public_argv(receipt.invocation.argv))


def public_replay_command(receipt: Phase5AttemptReceipt) -> str:
    argv = public_argv(receipt.invocation.argv)
    environment = tuple(
        f"{_public_text(variable.name)}={_REDACTED}"
        for variable in receipt.invocation.environment_delta
    )
    cwd = _public_text(receipt.backend.backend_cwd)
    match receipt.backend.kind:  # noqa  # noqa: MATCH_OK - statically exhaustive
        case BackendKind.LOCAL:
            invocation = ("env", *environment, *argv)
            return f"cd -- {shlex.quote(cwd)} && {shlex.join(invocation)}"
        case BackendKind.CONTAINER:
            runtime = receipt.backend.runtime
            container_id = receipt.backend.container_id
            if runtime is None or container_id is None:
                return ""
            command = [_public_text(runtime), "exec", "-i", "-w", cwd]
            for variable in receipt.invocation.environment_delta:
                command.extend(["-e", f"{_public_text(variable.name)}={_REDACTED}"])
            command.extend([_public_text(container_id), *argv])
            return shlex.join(command)


def public_cwd(receipt: Phase5AttemptReceipt) -> str:
    return _public_text(receipt.backend.backend_cwd)
