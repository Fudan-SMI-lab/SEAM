from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import harness.session.manager as manager_module
from harness.session.manager import MigrationSessionManager


Response = dict[str, Any]
RouteValue = Response | list[Response] | Callable[[dict[str, Any]], Response]


class FakeSessionManager(MigrationSessionManager):
    def __init__(self, routes: dict[tuple[str, str], RouteValue]) -> None:
        super().__init__(base_url="http://opencode.test", auto_detect_agent=False)
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def _http(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: Any = None,
    ) -> dict[str, Any]:
        call = {"method": method, "path": path, "query": query, "body": body, "timeout": timeout}
        self.calls.append(call)
        route = self.routes.get((method, path))
        if callable(route):
            return route(call)
        if isinstance(route, list):
            if len(route) > 1:
                return route.pop(0)
            if route:
                return route[0]
        if isinstance(route, dict):
            return route
        raise AssertionError(f"Unexpected HTTP call: {method} {path}")


def _manager_with_message(message: Response, history: RouteValue | None = None, status_type: str = "idle") -> FakeSessionManager:
    return FakeSessionManager({
        ("POST", "/session/ses-1/message"): {"ok": True, "data": message},
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": status_type}}},
        ("GET", "/session/ses-1/message"): history or {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })


def _sqlite_backed_manager(db_path: Path, status_data: dict[str, Any] | None = None) -> FakeSessionManager:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": status_data if status_data is not None else {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"parts": [{"type": "text", "text": "No structured todo list."}]}]},
    })
    manager._candidate_sqlite_paths = lambda: [db_path]  # type: ignore[method-assign]
    return manager


def test_send_command_returns_normal_text_when_idle_and_todos_complete() -> None:
    manager = _manager_with_message({
        "info": {"finish": "stop"},
        "parts": [{"type": "text", "text": "phase complete"}],
    })

    result = manager.send_command("ses-1", "do work", retries=0)

    assert result == "phase complete"


def test_send_command_timeout_none_uses_finite_post_timeout() -> None:
    manager = _manager_with_message({
        "info": {"finish": "stop"},
        "parts": [{"type": "text", "text": "phase complete"}],
    })

    result = manager.send_command("ses-1", "do work", timeout=None, retries=0)

    post_call = next(call for call in manager.calls if call["method"] == "POST")
    assert result == "phase complete"
    assert post_call["timeout"] == manager_module.DEFAULT_POST_RESPONSE_TIMEOUT


def test_send_command_short_timeout_uses_short_post_timeout() -> None:
    manager = _manager_with_message({
        "info": {"finish": "stop"},
        "parts": [{"type": "text", "text": "phase complete"}],
    })

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    post_call = next(call for call in manager.calls if call["method"] == "POST")
    assert result == "phase complete"
    assert post_call["timeout"] == 35.0


def test_send_command_rejects_non_finite_timeout_without_posting() -> None:
    manager = _manager_with_message({
        "info": {"finish": "stop"},
        "parts": [{"type": "text", "text": "phase complete"}],
    })

    result = json.loads(manager.send_command("ses-1", "do work", timeout=float("inf"), retries=0))

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result == {"ok": False, "error": "Session timeout must be finite"}
    assert post_calls == []


def test_active_agent_defaults_to_sisyphus() -> None:
    manager = FakeSessionManager({})

    assert manager.active_agent == "Sisyphus"


def test_detect_agent_prefers_exact_sisyphus_then_contains_sisyphus() -> None:
    exact = FakeSessionManager({
        ("GET", "/agent"): {
            "ok": True,
            "data": [{"name": "OtherAgent"}, {"name": "Atlas"}, {"name": "Sisyphus"}, {"name": "sisyphus-helper"}],
        }
    })
    exact._detect_agent()

    containing = FakeSessionManager({
        ("GET", "/agent"): {
            "ok": True,
            "data": [{"name": "OtherAgent"}, {"name": "Atlas"}, {"name": "custom-sisyphus-agent"}],
        }
    })
    containing._detect_agent()

    assert exact.active_agent == "Sisyphus"
    assert containing.active_agent == "custom-sisyphus-agent"


def test_detect_agent_falls_back_to_sisyphus_when_server_only_lists_atlas() -> None:
    manager = FakeSessionManager({
        ("GET", "/agent"): {
            "ok": True,
            "data": [{"name": "Atlas"}, {"name": "OtherAgent"}],
        }
    })
    manager._detect_agent()

    assert manager.active_agent == "Sisyphus"


def test_send_command_rejects_compaction_response_as_incomplete() -> None:
    manager = _manager_with_message({
        "info": {"mode": "compaction", "agent": "compaction", "summary": True, "sessionID": "ses-1"},
        "parts": [{"type": "step-start"}],
    })

    result = json.loads(manager.send_command("ses-1", "do work", retries=0))

    assert result["ok"] is False
    assert "Compaction response is incomplete" in result["error"]
    assert not any(call["method"] == "GET" and call["path"] == "/session/status" for call in manager.calls)


def test_send_command_recovers_empty_post_response_from_latest_history() -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": []},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "recovered phase complete"}]}]},
        ],
    )

    result = manager.send_command("ses-1", "do work", retries=0)

    assert result == "recovered phase complete"
    assert any(call["method"] == "GET" and call["path"] == "/session/status" for call in manager.calls)


def test_send_command_rejects_stale_history_after_empty_post_response() -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": []},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
        ],
    )

    result = json.loads(manager.send_command("ses-1", "do work", retries=0))

    assert result == {"ok": False, "error": "Empty session response"}


def test_send_command_rejects_user_prompt_echo_after_empty_post_response() -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": []},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "do work"}]}]},
        ],
    )

    result = json.loads(manager.send_command("ses-1", "do work", retries=0))

    assert result == {"ok": False, "error": "Empty session response"}


def test_send_command_preserves_structured_error_when_empty_history_stays_empty() -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": []},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
            {"ok": True, "data": [{"parts": []}]},
        ],
    )

    result = json.loads(manager.send_command("ses-1", "do work", retries=0))

    assert result == {"ok": False, "error": "Empty session response"}


def test_send_command_waits_for_incomplete_todos_until_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "in_progress", "content": "rerun validator"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed", "content": "rerun validator"}]}]},
        ],
    )

    result = manager.send_command("ses-1", "do work", retries=0)

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    status_calls = [call for call in manager.calls if call["method"] == "GET" and call["path"] == "/session/status"]
    assert result == "phase complete"
    assert len(post_calls) == 1
    assert len(status_calls) == 2


def test_send_command_times_out_for_incomplete_todos_without_reposting(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "partial repair result"}]},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "in_progress", "content": "rerun validator"}]}]},
        ],
    )
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=2))

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result["ok"] is False
    assert "Session still running" in result["error"]
    assert len(post_calls) == 1


def test_send_command_recovers_same_session_after_post_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {"ok": False, "error": "timed out", "timeout": True},
        ("GET", "/session/status"): [
            {"ok": True, "data": {"ses-1": {"type": "running"}}},
            {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ],
        ("GET", "/session/ses-1/message"): [
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "in_progress", "content": "fix OPP"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed", "content": "fix OPP"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "recovered phase complete"}]}]},
        ],
    })

    result = manager.send_command("ses-1", "do work", timeout=5, retries=2)

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result == "recovered phase complete"
    assert len(post_calls) == 1


def test_send_command_uses_command_timeout_for_post_timeout_recovery_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("GET", "/session/ses-1/message"): {
            "ok": True,
            "data": [
                {
                    "info": {"role": "assistant", "finish": "stop"},
                    "parts": [{"type": "text", "text": "old assistant text"}],
                }
            ],
        },
    })
    manager._post_message_with_wall_timeout = lambda **_kwargs: {"ok": False, "error": "timed out", "timeout": True}  # type: ignore[method-assign]
    waited: list[tuple[str, float | int | None, float]] = []

    def wait_for_idle(session_id: str, timeout_s: int | float | None = 300, interval_s: float = 2.0) -> bool:
        waited.append((session_id, timeout_s, interval_s))
        return False

    manager.wait_for_idle = wait_for_idle  # type: ignore[method-assign]

    result = json.loads(manager.send_command("ses-1", "do work", timeout=30000, retries=0))

    assert result["ok"] is False
    assert result["error"] == "Session still running or has incomplete todos"
    assert waited == [("ses-1", 30000.0, 1.0)]


def test_send_command_explicitly_caps_post_timeout_recovery_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("GET", "/session/ses-1/message"): {
            "ok": True,
            "data": [
                {
                    "info": {"role": "assistant", "finish": "stop"},
                    "parts": [{"type": "text", "text": "old assistant text"}],
                }
            ],
        },
    })
    manager._post_message_with_wall_timeout = lambda **_kwargs: {"ok": False, "error": "timed out", "timeout": True}  # type: ignore[method-assign]
    waited: list[tuple[str, float | int | None, float]] = []

    def wait_for_idle(session_id: str, timeout_s: int | float | None = 300, interval_s: float = 2.0) -> bool:
        waited.append((session_id, timeout_s, interval_s))
        return False

    manager.wait_for_idle = wait_for_idle  # type: ignore[method-assign]

    result = json.loads(
        manager.send_command(
            "ses-1",
            "do work",
            timeout=30000,
            retries=0,
            recovery_wait_timeout=manager_module.DEFAULT_HARD_ERROR_WAIT_TIMEOUT,
        )
    )

    assert result["ok"] is False
    assert result["error"] == "Session still running or has incomplete todos"
    assert waited == [("ses-1", manager_module.DEFAULT_HARD_ERROR_WAIT_TIMEOUT, 1.0)]


def test_send_command_wall_timeout_recovers_same_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("GET", "/session/status"): [
            {"ok": True, "data": {"ses-1": {"type": "running"}}},
            {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ],
        ("GET", "/session/ses-1/message"): [
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed", "content": "phase 0"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "harvested json"}]}]},
        ],
    })
    manager._post_message_with_wall_timeout = lambda **_kwargs: {"ok": False, "error": "timed out after 120 seconds", "timeout": True}  # type: ignore[method-assign]

    result = manager.send_command("ses-1", "do work", timeout=5, retries=2)

    assert result == "harvested json"


def test_send_command_harvests_finished_json_before_newer_empty_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "running"}}},
        ("GET", "/session/ses-1/message"): [
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {
                "ok": True,
                "data": [
                    {
                        "info": {"role": "assistant", "finish": "stop", "time": {"completed": 20}},
                        "parts": [{"type": "text", "text": "finished phase json"}],
                    },
                    {
                        "info": {"role": "assistant", "time": {"created": 30}},
                        "parts": [{"type": "step-start"}, {"type": "reasoning"}],
                    },
                    {
                        "info": {"role": "user", "time": {"created": 25}},
                        "parts": [{"type": "text", "text": "Incomplete tasks remain in your todo list"}],
                    },
                ],
            },
        ],
    })
    manager._post_message_with_wall_timeout = lambda **_kwargs: {"ok": False, "error": "timed out after 120 seconds", "timeout": True}  # type: ignore[method-assign]

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    assert result == "finished phase json"
    status_calls = [call for call in manager.calls if call["method"] == "GET" and call["path"] == "/session/status"]
    assert status_calls == []


def test_send_command_recovers_finished_text_after_upstream_stream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {
                "type": "error",
                "error": {
                    "type": "upstream_error",
                    "code": "stream_read_error",
                    "message": "stream_read_error",
                },
            },
        },
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): [
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {
                "ok": True,
                "data": [
                    {
                        "info": {"role": "assistant", "finish": "stop", "time": {"completed": 20}},
                        "parts": [{"type": "text", "text": '{"project_dir":"/tmp/project","dependencies":[]}'}],
                    }
                ],
            },
        ],
    })

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result == '{"project_dir":"/tmp/project","dependencies":[]}'
    assert len(post_calls) == 1


def test_send_command_skips_newer_upstream_error_when_recovering_finished_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {
                "type": "error",
                "error": {
                    "type": "upstream_error",
                    "code": "missing_terminal_event",
                    "message": "stream closed before response.completed",
                },
            },
        },
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): [
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {
                "ok": True,
                "data": [
                    {
                        "info": {"role": "assistant", "finish": "stop", "time": {"completed": 30}},
                        "parts": [
                            {
                                "type": "text",
                                "text": '{"type":"error","sequence_number":0,"error":{"type":"upstream_error","code":"missing_terminal_event","message":"stream closed before response.completed"}}',
                            }
                        ],
                    },
                    {
                        "info": {"role": "assistant", "finish": "stop", "time": {"completed": 20}},
                        "parts": [{"type": "text", "text": '{"project_dir":"/tmp/project","dependencies":["torch"]}'}],
                    },
                ],
            },
        ],
    })

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    assert result == '{"project_dir":"/tmp/project","dependencies":["torch"]}'


def test_latest_completed_assistant_response_overrides_stale_incomplete_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": []},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {
                "ok": True,
                "data": [
                    {
                        "info": {"role": "assistant", "finish": "stop"},
                        "parts": [{"type": "text", "text": "fresh phase json"}],
                    },
                    {
                        "info": {"role": "user"},
                        "parts": [{"type": "text", "text": "Incomplete tasks remain in your todo list"}],
                    },
                ],
            },
            {
                "ok": True,
                "data": [
                    {
                        "info": {"role": "assistant", "finish": "stop"},
                        "parts": [{"type": "text", "text": "fresh phase json"}],
                    },
                    {
                        "info": {"role": "user"},
                        "parts": [{"type": "text", "text": "Incomplete tasks remain in your todo list"}],
                    },
                ],
            },
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "fresh phase json"}]}]},
        ],
    )

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    assert result == "fresh phase json"


def test_last_message_text_uses_latest_non_empty_completed_assistant() -> None:
    manager = FakeSessionManager({
        ("GET", "/session/ses-1/message"): {
            "ok": True,
            "data": [
                {
                    "info": {
                        "role": "assistant",
                        "finish": "stop",
                        "time": {"created": 10, "completed": 20},
                    },
                    "parts": [{"type": "text", "text": "phase json result"}],
                },
                {
                    "info": {
                        "role": "assistant",
                        "finish": "stop",
                        "time": {"created": 21, "completed": 22},
                    },
                    "parts": [],
                },
            ],
        }
    })

    assert manager._last_message_text_tolerant("ses-1") == "phase json result"


def test_last_message_text_skips_status_only_todo_completion() -> None:
    manager = FakeSessionManager({
        ("GET", "/session/ses-1/message"): {
            "ok": True,
            "data": [
                {
                    "info": {"role": "assistant", "finish": "stop", "time": {"completed": 20}},
                    "parts": [{"type": "text", "text": '{"project_dir":"/tmp/project","dependencies":[]}'}],
                },
                {
                    "info": {"role": "assistant", "finish": "stop", "time": {"completed": 30}},
                    "parts": [{"type": "text", "text": "All Phase 1 todo items have been completed."}],
                },
            ],
        }
    })

    assert manager._last_finished_assistant_text_tolerant("ses-1") == '{"project_dir":"/tmp/project","dependencies":[]}'


def test_completed_assistant_signal_skips_newer_empty_stop_message() -> None:
    manager = FakeSessionManager({})
    payload = [
        {
            "info": {"role": "assistant", "finish": "stop", "time": {"completed": 30}},
            "parts": [],
        },
        {
            "info": {"role": "assistant", "finish": "stop", "time": {"completed": 20}},
            "parts": [{"type": "text", "text": "phase json result"}],
        },
        {"info": {"role": "user", "time": {"created": 10}}, "todos": [{"status": "pending"}]},
    ]

    assert manager._latest_completed_assistant_signal(payload) is False


def test_send_command_waits_for_unfinished_tool_calls_until_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager = _manager_with_message(
        {"info": {"finish": "tool-calls"}, "parts": [{"type": "tool-call", "id": "call-1"}]},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "tool-call", "id": "call-1", "state": "running"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "tool-result", "id": "call-1", "result": "ok"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "operator repair done"}]}]},
        ],
    )

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result == "operator repair done"
    assert len(post_calls) == 1


def test_completed_tool_calls_do_not_block_or_become_response_text() -> None:
    manager = _manager_with_message(
        {
            "info": {"finish": "tool-calls"},
            "parts": [
                {"type": "step-start"},
                {"type": "tool", "state": {"status": "completed", "output": "directory listing"}},
                {"type": "step-finish", "reason": "tool-calls"},
            ],
        },
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"todos": [{"status": "completed", "content": "inspect"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "phase json result"}]}]},
        ],
    )

    result = manager.send_command("ses-1", "do work", timeout=5, retries=0)

    assert result == "phase json result"


def test_tool_errors_are_terminal_not_running() -> None:
    manager = FakeSessionManager({})
    payload = [
        {
            "info": {"role": "assistant", "time": {"completed": 30}},
            "parts": [
                {"type": "step-start"},
                {"type": "tool", "state": {"status": "error"}},
                {"type": "step-finish", "reason": "tool-calls"},
            ],
        },
        {
            "info": {"role": "assistant", "finish": "stop", "time": {"completed": 20}},
            "parts": [{"type": "text", "text": "finished json"}],
        },
    ]

    assert manager._latest_completed_assistant_signal(payload) is False


def test_completed_assistant_text_without_finish_is_terminal() -> None:
    manager = FakeSessionManager({})
    payload = [
        {
            "info": {"role": "assistant", "time": {"created": 30, "completed": 40}},
            "parts": [
                {"type": "step-start"},
                {"type": "text", "text": '{"entry_script_path":"validate_custom_ops_full.py"}'},
            ],
        }
    ]

    assert manager._latest_completed_assistant_signal(payload) is False
    assert manager._extract_latest_finished_assistant_text(payload) == '{"entry_script_path":"validate_custom_ops_full.py"}'


def test_send_command_times_out_for_unfinished_tool_calls_without_reposting(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_message(
        {"info": {"finish": "tool-calls"}, "parts": [{"type": "tool-call", "id": "call-1"}]},
        history=[
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "old assistant text"}]}]},
            {"ok": True, "data": [{"parts": [{"type": "tool-call", "id": "call-1", "state": "running"}]}]},
        ],
    )
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=2))

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result["ok"] is False
    assert "Session still running" in result["error"]
    assert len(post_calls) == 1


def test_sqlite_fallback_ignores_unrelated_incomplete_todos(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE todos ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("other-session", "pending", "other work"))
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "completed", "own work"))

    manager = _sqlite_backed_manager(db_path)

    assert manager.send_command("ses-1", "do work", retries=0) == "phase complete"


def test_sqlite_fallback_blocks_camelcase_session_pending_todo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE tasks ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO tasks ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "pending", "rerun validator"))
        conn.execute('INSERT INTO tasks ("sessionID", status, content) VALUES (?, ?, ?)', ("other-session", "completed", "other work"))

    manager = _sqlite_backed_manager(db_path)
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=0))

    assert result["ok"] is False
    assert "incomplete todos" in result["error"] or "Session still running" in result["error"]


def test_sqlite_idle_session_with_pending_todo_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE session (id TEXT, status TEXT)')
        conn.execute('CREATE TABLE todos ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO session (id, status) VALUES (?, ?)', ("ses-1", "idle"))
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "pending", "rerun validator"))

    manager = _sqlite_backed_manager(db_path)
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=0))

    assert result["ok"] is False
    assert "incomplete todos" in result["error"]


def test_sqlite_idle_session_with_completed_todos_is_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE session (id TEXT, status TEXT)')
        conn.execute('CREATE TABLE todos ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO session (id, status) VALUES (?, ?)', ("ses-1", "idle"))
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "completed", "rerun validator"))

    manager = _sqlite_backed_manager(db_path)

    assert manager.send_command("ses-1", "do work", retries=0) == "phase complete"


def test_send_command_timeout_none_uses_sqlite_assistant_completion_without_todos(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    assistant_data = {
        "role": "assistant",
        "agent": "Sisyphus",
        "finish": "stop",
        "time": {"completed": 1710000000},
        "parts": [{"type": "text", "text": '{"platform":"npu","npu_detected":true}'}],
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE session (id TEXT, title TEXT, time_compacting INTEGER)')
        conn.execute('CREATE TABLE message ("sessionID" TEXT, role TEXT, data TEXT, timeCreated INTEGER)')
        conn.execute('INSERT INTO session (id, title, time_compacting) VALUES (?, ?, ?)', ("ses-1", "migration-main_engineer", None))
        conn.execute(
            'INSERT INTO message ("sessionID", role, data, timeCreated) VALUES (?, ?, ?, ?)',
            ("ses-1", "assistant", json.dumps(assistant_data), 2),
        )

    manager = _sqlite_backed_manager(db_path, status_data={})

    assert manager.send_command("ses-1", "do work", timeout=None, retries=0) == "phase complete"


def test_sqlite_assistant_completion_still_blocks_same_session_pending_todo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "opencode.db"
    assistant_data = {
        "role": "assistant",
        "finish": "success",
        "time": {"completed": 1710000000},
        "parts": [{"type": "text", "text": '{"platform":"npu","npu_detected":true}'}],
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE session (id TEXT, title TEXT, time_compacting INTEGER)')
        conn.execute('CREATE TABLE message ("sessionID" TEXT, role TEXT, data TEXT, timeCreated INTEGER)')
        conn.execute('CREATE TABLE todos ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO session (id, title, time_compacting) VALUES (?, ?, ?)', ("ses-1", "migration-main_engineer", None))
        conn.execute(
            'INSERT INTO message ("sessionID", role, data, timeCreated) VALUES (?, ?, ?, ?)',
            ("ses-1", "assistant", json.dumps(assistant_data), 2),
        )
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "pending", "rerun validator"))

    manager = _sqlite_backed_manager(db_path, status_data={})
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=0))

    assert result["ok"] is False
    assert "incomplete todos" in result["error"] or "Session still running" in result["error"]


def test_sqlite_assistant_completion_still_blocks_active_compaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "opencode.db"
    assistant_data = {
        "role": "assistant",
        "finish": "stop",
        "time": {"completed": 1710000000},
        "parts": [{"type": "text", "text": '{"platform":"npu","npu_detected":true}'}],
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE session (id TEXT, title TEXT, time_compacting INTEGER)')
        conn.execute('CREATE TABLE message ("sessionID" TEXT, role TEXT, data TEXT, timeCreated INTEGER)')
        conn.execute('INSERT INTO session (id, title, time_compacting) VALUES (?, ?, ?)', ("ses-1", "migration-main_engineer", 1))
        conn.execute(
            'INSERT INTO message ("sessionID", role, data, timeCreated) VALUES (?, ?, ?, ?)',
            ("ses-1", "assistant", json.dumps(assistant_data), 2),
        )

    manager = _sqlite_backed_manager(db_path, status_data={})
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=0))

    assert result["ok"] is False
    assert "Session still running" in result["error"]


def test_sqlite_fallback_skips_todo_tables_without_session_column(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE todos (status TEXT, content TEXT)')
        conn.execute('INSERT INTO todos (status, content) VALUES (?, ?)', ("pending", "unscoped work"))

    manager = _sqlite_backed_manager(db_path)

    assert manager._session_completion_from_sqlite("ses-1") is None


def test_pending_word_in_normal_text_does_not_mark_todos_incomplete() -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "The pending import issue was resolved."}]},
        history={"ok": True, "data": [{"parts": [{"type": "text", "text": "No structured todos. Pending issue resolved."}]}]},
    )

    assert manager.send_command("ses-1", "do work", retries=0) == "The pending import issue was resolved."


def test_wait_for_idle_times_out_while_session_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "still running"}]},
        status_type="running",
    )
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 2.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    assert manager.wait_for_idle("ses-1", timeout_s=1, interval_s=0) is False


def test_send_command_surfaces_auth_error_after_observed_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {"ok": False, "status": 401, "error": "invalid API key"},
        ("GET", "/session/status"): [
            {"ok": True, "data": {"ses-1": {"type": "running"}}},
            {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ],
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", retries=2))

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    status_calls = [call for call in manager.calls if call["method"] == "GET" and call["path"] == "/session/status"]

    assert result["ok"] is False
    assert "unauthorized" in result["error"]
    assert "invalid API key" in result["error"]
    assert len(post_calls) == 1
    assert len(status_calls) == 2


def test_wait_for_idle_timeout_none_uses_finite_default(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "still running"}]},
        status_type="running",
    )
    times = iter([0.0, 0.0, 30001.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 30001.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    assert manager.wait_for_idle("ses-1", timeout_s=None, interval_s=0) is False


def test_hard_error_wait_timeout_none_uses_finite_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {"ok": False, "status": 401, "error": "invalid API key"},
        ("GET", "/session/status"): [
            {"ok": True, "data": {"ses-1": {"type": "running"}}},
            {"ok": True, "data": {"ses-1": {"type": "running"}}},
            {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ],
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    times = iter([0.0, 0.0, 301.0])
    monkeypatch.setattr(manager_module.time, "time", lambda: next(times, 301.0))
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = json.loads(manager.send_command("ses-1", "do work", timeout=None, retries=0))

    status_calls = [call for call in manager.calls if call["method"] == "GET" and call["path"] == "/session/status"]
    assert result["ok"] is False
    assert "invalid API key" in result["error"]
    assert len(status_calls) == 1
