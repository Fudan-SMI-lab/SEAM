from __future__ import annotations

import pytest

from . import BASE_URL, cleanup_remote_sessions, server_available
from harness.session.manager import SessionManager

pytestmark = pytest.mark.skipif(not server_available(), reason="No OpenCode server")


def test_session_manager_round_trip_and_json_parsing() -> None:
    session_mgr = SessionManager(base_url=BASE_URL, timeout=15.0)
    try:
        session_id = session_mgr.create_session(role="integration-json", lifecycle="ephemeral")

        response = session_mgr.send_json_command(
            session_id,
            'Return exactly this JSON and nothing else: {"status": "ok", "value": 7}',
            timeout=120,
            retries=0,
        )

        assert response["status"] == "ok"
        assert response["value"] == 7
        assert session_mgr.wait_for_idle(session_id, timeout_s=30, interval_s=1.0) is True
        assert session_mgr.get_last_response(session_id)
    finally:
        cleanup_remote_sessions(session_mgr)


def test_session_manager_reuses_persistent_session_per_role() -> None:
    session_mgr = SessionManager(base_url=BASE_URL, timeout=15.0)
    try:
        first_session_id = session_mgr.get_or_create(role="integration-role", lifecycle="persistent")
        second_session_id = session_mgr.get_or_create(role="integration-role", lifecycle="persistent")

        response = session_mgr.send_command(
            first_session_id,
            "Reply with exactly one word: pong",
            timeout=120,
            retries=0,
        )

        assert first_session_id == second_session_id
        assert "pong" in response.lower()
    finally:
        cleanup_remote_sessions(session_mgr)
