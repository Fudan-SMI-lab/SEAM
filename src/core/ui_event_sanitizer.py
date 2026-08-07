"""UI event field sanitization: recursive redaction, bounds, and safe cuts.

Every function here processes untrusted event content before it reaches
``core.ui_events.UIEventSink``. Text is redacted with the shared
``core.secret_redaction`` engine and capped; oversized inputs are cut in
input coordinates so no strict proper prefix of a concrete token can
survive into the bounded head.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from itertools import islice
from typing import Final

from core.secret_redaction import JsonValue, redact_json_value, redact_sensitive_text

_TEXT_LIMIT: Final = 500
_COLLECTION_LIMIT: Final = 50
_DEPTH_LIMIT: Final = 6
_PREVIEW_LIMIT: Final = 2_000
_REDACT_MARGIN: Final = 64
_QUOTE_CHARS: Final = ("'", '"')
_BOUNDED_WALK: Final = 24
_SECRET_PREFIX_GUARD: Final = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{1,15}|gh[pousr]_[A-Za-z0-9_]{1,19})$"
)


def summarize_text(text: object, limit: int = 160) -> str:
    raw = "" if text is None else str(text)
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = redact_sensitive_text(_input_safe_window(raw, limit))
    else:
        raw = redact_sensitive_text(raw)
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


def normalize_optional_text(value: str | None) -> str | None:
    return None if value is None else redact_head(value)


def normalize_details(details: Mapping[str, JsonValue] | None) -> JsonValue:
    """Return recursively redacted, bounded details for one UI event.

    Structure is bounded first, ``core.secret_redaction.redact_json_value``
    redacts every string leaf once, and only then are redacted leaves
    truncated to the text limit so no partial secret can survive.
    """
    bounded = _normalize_detail_value(dict(details or {}), 0)
    redacted = redact_json_value(bounded)
    return _truncate_detail_strings(redacted)


def details_preview(details: JsonValue) -> str:
    rendered = json.dumps(details, ensure_ascii=False)
    return redact_head(rendered, _PREVIEW_LIMIT)


def _bounded_text(text: str, limit: int = _TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit]


def _dangling_quote_cut(text: str) -> int:
    """Return the cut that drops a trailing unpaired quoted span, if any.

    With left-to-right same-quote pairing, an odd quote count means the
    last quote of that type opens a span whose closer lies outside the
    window; content behind it could be an unterminated quoted secret.
    """
    cut = len(text)
    for quote in _QUOTE_CHARS:
        if text.count(quote) % 2 == 1:
            cut = min(cut, text.rfind(quote))
    return cut


def _input_safe_window(value: str, limit: int) -> str:
    """Return the raw input prefix that is safe to redact for a ``limit`` head.

    Bounded walk of at most ``_BOUNDED_WALK`` steps, each examining at most
    ``limit + _REDACT_MARGIN`` characters, so the work is O(1) in the input
    length. Each step drops either a trailing below-minimum concrete-token
    prefix (``sk-`` with 1-15 body chars or a GitHub token with 1-19 — a
    boundary fragment at or above the pattern minimum already matches the
    redaction patterns and is replaced instead) or a dangling quoted span.
    The walk stops when the window ends on a clean boundary: any token
    fully present is redacted afterwards, any below-minimum prefix at the
    boundary is excluded here, and the later bounded cut of the redacted
    window cannot shift a secret fragment back into the head.
    """
    cut = min(len(value), limit + _REDACT_MARGIN)
    for _ in range(_BOUNDED_WALK):
        window = value[:cut]
        quote_cut = _dangling_quote_cut(window)
        if quote_cut < cut:
            cut = quote_cut
            continue
        guard = _SECRET_PREFIX_GUARD.search(window)
        if guard is not None:
            cut = guard.start()
            continue
        return window
    return value[:cut]


def redact_head(value: str, limit: int = _TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return redact_sensitive_text(value)
    return _bounded_text(redact_sensitive_text(_input_safe_window(value, limit)), limit)


def _normalize_detail_value(value: JsonValue, depth: int) -> JsonValue:
    """Bound one detail value structurally (entries, depth, JSON safety)."""
    if isinstance(value, str):
        if len(value) > _TEXT_LIMIT:
            return _input_safe_window(value, _TEXT_LIMIT)
        return value
    if isinstance(value, Mapping):
        if depth >= _DEPTH_LIMIT:
            return repr(value)
        return {
            redact_head(str(key)): _normalize_detail_value(child, depth + 1)
            for key, child in islice(value.items(), _COLLECTION_LIMIT)
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if depth >= _DEPTH_LIMIT:
            return repr(value)
        return [
            _normalize_detail_value(item, depth + 1)
            for item in islice(value, _COLLECTION_LIMIT)
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return repr(value)


def _truncate_detail_strings(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, Mapping):
        return {key: _truncate_detail_strings(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_truncate_detail_strings(item) for item in value]
    return value
