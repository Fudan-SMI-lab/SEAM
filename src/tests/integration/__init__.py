# pyright: reportAny=false, reportUnknownVariableType=false

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

BASE_URL = "http://127.0.0.1:4096"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / "workflows" / "npu_migration_v1.yaml"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness.session.manager import SessionManager, extract_json_response


@lru_cache(maxsize=1)
def server_available(base_url: str = BASE_URL, timeout: float = 1.0) -> bool:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/agent", headers={"Accept": "application/json"})
    session_mgr: SessionManager | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0))
            if not 200 <= status < 300:
                return False

        session_mgr = SessionManager(base_url=base_url, timeout=max(timeout, 5.0))
        session_id = session_mgr.create_session(role="integration-healthcheck", lifecycle="ephemeral")
        response_text = session_mgr.send_command(
            session_id,
            'Return exactly this JSON and nothing else: {"ok": true}',
            timeout=30,
            retries=0,
        )
        parsed = extract_json_response(response_text)
        return parsed.get("ok") is True
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError):
        return False
    finally:
        if session_mgr is not None:
            cleanup_remote_sessions(session_mgr)


def cleanup_remote_sessions(session_mgr: object) -> None:
    list_sessions = getattr(session_mgr, "list_sessions", None)
    abort_session = getattr(session_mgr, "abort_session", None)
    http_call = getattr(session_mgr, "_http", None)
    sessions: list[object] = []
    if callable(list_sessions):
        sessions = list(cast_session_iterable(list_sessions))

    for record in sessions:
        session_id = getattr(record, "session_id", "")
        if not session_id:
            continue
        if callable(abort_session):
            _ = cast_abort(abort_session)(session_id)
        if callable(http_call):
            _ = cast_http(http_call)("DELETE", f"/session/{session_id}")
        getattr(session_mgr, "_sessions", {}).pop(session_id, None)


def cast_session_iterable(callable_obj: Callable[[], object]) -> Iterable[object]:
    sessions = callable_obj()
    if isinstance(sessions, list):
        return sessions
    return []


def cast_abort(callable_obj: Callable[[str], object]) -> Callable[[str], object]:
    return callable_obj


def cast_http(callable_obj: Callable[[str, str], object]) -> Callable[[str, str], object]:
    return callable_obj
