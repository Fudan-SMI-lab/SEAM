"""Typed loader for non-interactive initializer answers.

The answers file is a JSON object consumed in ``--non-interactive`` mode.
Secrets are referenced by environment-variable NAME (e.g.
``{"api_key_env": "SEAM_INIT_KEY"}``); inline secret values are rejected with a
typed error. The :class:`Answers` dataclass never stores a secret value — only
the env-var name that later stages resolve at use-site via ``os.environ``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.secret_redaction import redact_sensitive_text
from seam_init.models import SafeDetail

__all__ = ["Answers", "AnswersLoadError", "load_answers"]

# Keys whose presence in the answers file implies an inline secret VALUE was
# supplied. The answers file must reference env-var NAMES via ``*_env`` instead.
_INLINE_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "secret",
        "token",
        "password",
        "passwd",
        "credential",
    }
)


class AnswersLoadError(Exception):
    """Typed error for answers-file loading failures (carries no secret value)."""

    reason: SafeDetail

    def __init__(self, *, reason: SafeDetail) -> None:
        super().__init__(str(reason))
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Answers:
    """Non-interactive answers; secret values are never stored, only env-var names.

    All fields are optional: a minimal answers file (``{}``) is valid for Task 3.
    Later tasks consume the populated fields when driving the full workflow.
    """

    provider_id: str | None = None
    model_id: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    environment: str | None = None
    venv_path: str | None = None
    reasoning: str | None = None
    billable_consent: bool = False


def _safe_detail(raw: str) -> SafeDetail:
    """Wrap a diagnostic string in SafeDetail after running it through redaction."""
    return SafeDetail(redact_sensitive_text(raw))


def _opt_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnswersLoadError(
            reason=_safe_detail(f"'{key}' must be a string when present"),
        )
    return value


def _reject_inline_secrets(data: dict[str, object]) -> None:
    for key in data:
        normalized = key.lower().replace("-", "_")
        if normalized in _INLINE_SECRET_KEYS:
            raise AnswersLoadError(
                reason=_safe_detail(
                    f"inline secret value for '{key}' is not allowed; "
                    f"use a '{normalized}_env' key to reference an environment "
                    "variable name instead",
                ),
            )


def load_answers(path: Path) -> Answers:
    """Load and validate a non-interactive answers file into :class:`Answers`.

    Raises :class:`AnswersLoadError` for: missing file, unreadable file,
    invalid JSON, non-object root, wrong-typed fields, or inline secret values.
    """
    if not path.is_file():
        raise AnswersLoadError(
            reason=_safe_detail(f"answers file not found: {path}"),
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AnswersLoadError(
            reason=_safe_detail(f"cannot read answers file {path}: {exc}"),
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnswersLoadError(
            reason=_safe_detail(f"invalid JSON in answers file {path}: {exc.msg}"),
        ) from exc
    if not isinstance(data, dict):
        raise AnswersLoadError(
            reason=_safe_detail("answers file root must be a JSON object"),
        )
    _reject_inline_secrets(data)
    consent = data.get("billable_consent")
    return Answers(
        provider_id=_opt_str(data, "provider_id"),
        model_id=_opt_str(data, "model_id"),
        base_url=_opt_str(data, "base_url"),
        api_key_env=_opt_str(data, "api_key_env"),
        environment=_opt_str(data, "environment"),
        venv_path=_opt_str(data, "venv_path"),
        reasoning=_opt_str(data, "reasoning"),
        # Strict boolean: any non-boolean value (string "false", 1, "yes", ...)
        # is malformed and treated as declined - never as paid-call consent.
        billable_consent=consent if isinstance(consent, bool) else False,
    )
