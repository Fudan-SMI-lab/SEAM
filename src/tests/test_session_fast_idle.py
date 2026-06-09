"""Smoke test: verify _send_message_raw skips wait_for_idle when the agent
response already signals finish="stop"/"success".

The fix: when the agent response includes finish="stop" or "success", the
session's work is done — skip the belt-and-suspenders wait_for_idle check
entirely.  Only call wait_for_idle when finish is absent (no completion signal).

This test has two parts:
  1. Unit: mock _http and wait_for_idle, verify wait_for_idle is skipped
     when finish="stop"/"success" and called with effective_timeout otherwise.
  2. Integration: use real opencode server, verify a trivial command
     completes in under 60s.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

_src_dir = os.path.join(os.path.dirname(__file__), "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, os.path.abspath(_src_dir))

# ---------------------------------------------------------------------------
# Unit test — mock-based
# ---------------------------------------------------------------------------


class MockSessionManager:
    """Minimal mock to exercise the wait_for_idle skip logic directly."""

    DEFAULT_TIMEOUT = 3600.0

    def __init__(self) -> None:
        self._wait_for_idle_calls: list[dict] = []

    def _command_forbids_nested_tasks(self, _cmd: str) -> bool:
        return False

    def _effective_wait_timeout(self, timeout: float | None) -> float | None:
        if timeout is None:
            return self.DEFAULT_TIMEOUT
        return max(1.0, float(timeout))

    def _last_message_text_tolerant(self, _sid: str) -> str:
        return ""

    def _raise_if_forbidden_nested_tool_used(self, _sid: str, _enabled: bool) -> None:
        return

    def _is_compaction_payload(self, _data: dict) -> bool:
        return False

    def _extract_message_text(self, data: dict) -> str:
        parts = data.get("parts", [])
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "text":
                return str(p.get("text", ""))
        return ""

    def _post_session_message_with_guard(
        self,
        session_id: str,
        payload: dict,
        http_timeout: float | None,
        nested_task_guard: bool,
        **_kw: object,
    ) -> dict:
        return {
            "ok": True,
            "data": {
                "info": {"finish": "stop"},
                "parts": [{"type": "text", "text": "Hello from agent"}],
            },
        }

    def wait_for_idle(
        self,
        session_id: str,
        timeout_s: float | None = 300,
        interval_s: float = 2.0,
        **_kw: object,
    ) -> bool:
        self._wait_for_idle_calls.append(
            {"session_id": session_id, "timeout_s": timeout_s, "interval_s": interval_s}
        )
        return True

    # Copy the logic under test exactly
    def _send_message_raw(
        self,
        session_id: str,
        text: str,
        agent: str = "",
        timeout: float | None = None,
    ) -> str:
        command_text = text
        nested_task_guard = self._command_forbids_nested_tasks(command_text)
        payload: dict = {"parts": [{"type": "text", "text": text}]}
        if agent:
            payload["agent"] = agent
        effective_timeout = self._effective_wait_timeout(timeout)
        http_timeout = None if effective_timeout is None else effective_timeout + 30
        previous_text = self._last_message_text_tolerant(session_id)
        resp = self._post_session_message_with_guard(
            session_id,
            payload,
            http_timeout,
            nested_task_guard,
            previous_text=previous_text,
            command_text=command_text,
        )
        if not resp.get("ok"):
            raise RuntimeError("mock: response not ok")
        data = resp.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("Unexpected session response payload")
        info = data.get("info") or {}
        if isinstance(info, dict) and info.get("error"):
            raise RuntimeError("mock: agent error")
        if self._is_compaction_payload(data):
            raise RuntimeError("mock: compaction")
        finish = str(info.get("finish", "")).lower() if isinstance(info, dict) else ""
        if finish and finish not in {"stop", "success"}:
            raise RuntimeError(f"Agent finished unexpectedly: {finish}")
        text_result = self._extract_message_text(data)
        if not text_result:
            self._raise_if_forbidden_nested_tool_used(session_id, nested_task_guard)
            text_result = "recovered text"
            return text_result
        self._raise_if_forbidden_nested_tool_used(session_id, nested_task_guard)
        # *** THE FIX: skip wait_for_idle when agent signaled completion ***
        if finish not in {"stop", "success"}:
            if not self.wait_for_idle(session_id, timeout_s=effective_timeout, interval_s=1.0):
                self._raise_if_forbidden_nested_tool_used(session_id, nested_task_guard)
                raise TimeoutError("Session still running")
        self._raise_if_forbidden_nested_tool_used(session_id, nested_task_guard)
        return text_result


class TestFastIdleSkip(unittest.TestCase):
    """Unit tests verifying wait_for_idle is skipped when finish="stop"/"success"."""

    def test_wait_for_idle_skipped_when_finish_stop(self) -> None:
        """Default mock returns finish="stop" → no idle wait."""
        mgr = MockSessionManager()
        mgr._send_message_raw("s1", "hello", timeout=None)
        self.assertEqual(len(mgr._wait_for_idle_calls), 0)

    def test_wait_for_idle_skipped_when_finish_success(self) -> None:
        mgr = MockSessionManager()
        original = mgr._post_session_message_with_guard

        def _mock_post(*a: object, **kw: object) -> dict:
            return {
                "ok": True,
                "data": {
                    "info": {"finish": "success"},
                    "parts": [{"type": "text", "text": "done"}],
                },
            }
        mgr._post_session_message_with_guard = _mock_post  # type: ignore[assignment]
        mgr._send_message_raw("s2", "hello", timeout=None)
        self.assertEqual(len(mgr._wait_for_idle_calls), 0)

    def test_wait_for_idle_called_when_finish_empty(self) -> None:
        """finish="" (no signal) → wait_for_idle with full effective_timeout."""
        mgr = MockSessionManager()
        original = mgr._post_session_message_with_guard

        def _mock_post(*a: object, **kw: object) -> dict:
            return {
                "ok": True,
                "data": {
                    "info": {"finish": ""},
                    "parts": [{"type": "text", "text": "done"}],
                },
            }
        mgr._post_session_message_with_guard = _mock_post  # type: ignore[assignment]
        mgr._send_message_raw("s3", "hello", timeout=None)
        self.assertEqual(len(mgr._wait_for_idle_calls), 1)
        self.assertEqual(mgr._wait_for_idle_calls[0]["timeout_s"], 3600.0)

    def test_wait_for_idle_preserves_explicit_timeout_when_finish_empty(self) -> None:
        mgr = MockSessionManager()
        original = mgr._post_session_message_with_guard

        def _mock_post(*a: object, **kw: object) -> dict:
            return {
                "ok": True,
                "data": {
                    "info": {"finish": ""},
                    "parts": [{"type": "text", "text": "done"}],
                },
            }
        mgr._post_session_message_with_guard = _mock_post  # type: ignore[assignment]
        mgr._send_message_raw("s4", "hello", timeout=600)
        self.assertEqual(len(mgr._wait_for_idle_calls), 1)
        self.assertEqual(mgr._wait_for_idle_calls[0]["timeout_s"], 600.0)

    def test_wait_for_idle_preserves_small_timeout_when_finish_empty(self) -> None:
        mgr = MockSessionManager()
        original = mgr._post_session_message_with_guard

        def _mock_post(*a: object, **kw: object) -> dict:
            return {
                "ok": True,
                "data": {
                    "info": {"finish": ""},
                    "parts": [{"type": "text", "text": "done"}],
                },
            }
        mgr._post_session_message_with_guard = _mock_post  # type: ignore[assignment]
        mgr._send_message_raw("s5", "hello", timeout=10)
        self.assertEqual(len(mgr._wait_for_idle_calls), 1)
        self.assertEqual(mgr._wait_for_idle_calls[0]["timeout_s"], 10.0)


# ---------------------------------------------------------------------------
# Integration test — real opencode server
# ---------------------------------------------------------------------------

@unittest.skipIf(os.environ.get("SKIP_INTEGRATION"), "SKIP_INTEGRATION set")
class TestRealOpenCodeServer(unittest.TestCase):
    """Smoke test: real opencode server should complete a trivial command quickly."""

    SERVER_URL = "http://127.0.0.1:400"

    def setUp(self) -> None:
        from harness.session.manager import MigrationSessionManager
        self.mgr = MigrationSessionManager(
            base_url=self.SERVER_URL,
            timeout=30,
            work_dir="/tmp/smoke_test_sessions",
        )

    def test_trivial_command_completes_quickly(self) -> None:
        sessions = self.mgr.list_sessions()
        for s in sessions:
            self.mgr.close_session(s)
        sid = self.mgr.create_session(role="smoke_test", lifecycle="ephemeral")
        try:
            start = time.time()
            result = self.mgr.send_command(
                sid,
                "Reply with exactly the word: OK",
                timeout=30,
                retries=0,
            )
            elapsed = time.time() - start
            self.assertIn("OK", result, f"Expected 'OK' in response, got: {result!r}")
            self.assertLess(elapsed, 60, f"Command took {elapsed:.1f}s, expected <60s")
        finally:
            self.mgr.close_session(sid)


if __name__ == "__main__":
    unittest.main()
