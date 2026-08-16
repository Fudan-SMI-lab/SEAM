"""Marker verification and probe cleanup-state extraction for message validation.

Parses diagnose ``--json-only`` output, verifies the exact ``SEAM_DIAG_OK``
marker from ``response_text`` (cross-checked against ``contains_marker``), and
extracts cleanup/session-deletion state. Never trusts a caller-supplied boolean
alone; inconsistency between the boolean and the content is reported as such.
"""
from __future__ import annotations

import json
from typing import Final

from seam_init.models import SafeDetail

__all__ = ["SEAM_DIAG_OK_MARKER", "check_marker", "cleanup_state", "extract_probe"]

SEAM_DIAG_OK_MARKER: Final[str] = "SEAM_DIAG_OK"
_EMPTY: Final[SafeDetail] = SafeDetail("")


def extract_probe(text: str) -> dict[str, object] | None:
    """Parse JSON and return the ``message_probe`` dict; None on any failure."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    probe = parsed.get("message_probe")
    return probe if isinstance(probe, dict) else None


def check_marker(probe: dict[str, object] | None) -> tuple[bool, bool]:
    """Return ``(exact_match, inconsistent)`` from response_text + cross-check.

    ``exact_match`` is True only when ``response_text`` normalizes (strip) to
    exactly ``SEAM_DIAG_OK``. ``inconsistent`` is True when ``contains_marker``
    disagrees with the substring presence in ``response_text`` — a sign of
    forged or corrupted structured output.
    """
    if probe is None:
        return False, False
    text = probe.get("response_text")
    bool_val = probe.get("contains_marker")
    exact = isinstance(text, str) and text.strip() == SEAM_DIAG_OK_MARKER
    if not isinstance(bool_val, bool):
        return exact, False
    if isinstance(text, str):
        if bool_val is not (SEAM_DIAG_OK_MARKER in text):
            return exact, True
    elif bool_val:
        return exact, True
    return exact, False


def cleanup_state(probe: dict[str, object] | None) -> tuple[bool, SafeDetail]:
    """Return ``(session_deleted, cleanup_diagnostic)``.

    If no session was created (empty ``session_id``), returns ``(False, "")``
    so the caller does not falsely claim deletion failed. If a session exists
    and cleanup failed, returns a non-empty diagnostic.
    """
    if probe is None:
        return False, _EMPTY
    sid = probe.get("session_id")
    if not (isinstance(sid, str) and sid.strip()):
        return False, _EMPTY
    cleanup = probe.get("cleanup")
    if isinstance(cleanup, dict) and cleanup.get("ok") is True:
        return True, _EMPTY
    return False, SafeDetail("probe session not deleted")
