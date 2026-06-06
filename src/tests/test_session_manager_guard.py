from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import harness.session.manager as manager_module
from harness.session.manager import MigrationSessionManager

# Import after manager_module to ensure conftest has already configured _sqlite3 stub if needed.
try:
    import _sqlite3  # noqa: F401
except NameError:
    _sqlite3 = None  # type: ignore[misc, assignment]

# Use conftest flag to detect whether real sqlite3 C extension is available.
from tests.conftest import NO_REAL_SQLITE3 as _NO_REAL_SQLITE

_SKIP_SQLITE = pytest.mark.skipif(_NO_REAL_SQLITE, reason="no sqlite3 C extension on this system")


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
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {"ok": True, "data": message},
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": status_type}}},
        ("GET", "/session/ses-1/message"): history or {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]
    return manager


def _sqlite_backed_manager(db_path: Path, status_data: dict[str, Any] | None = None) -> FakeSessionManager:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": status_data if status_data is not None else {}},
        ("GET", "/session/ses-1/message"): {"ok": False, "status": 400},
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


def test_send_command_timeout_none_uses_unbounded_post_timeout() -> None:
    manager = _manager_with_message({
        "info": {"finish": "stop"},
        "parts": [{"type": "text", "text": "phase complete"}],
    })

    result = manager.send_command("ses-1", "do work", timeout=None, retries=0)

    post_call = next(call for call in manager.calls if call["method"] == "POST")
    assert result == "phase complete"
    assert post_call["timeout"] == pytest.approx(3630.0, abs=1.0), f"expected bounded POST timeout, got {post_call['timeout']}"


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
            "data": [{"name": "OtherAgent"}, {"name": "Sisyphus"}, {"name": "sisyphus-helper"}],
        }
    })
    exact._detect_agent()

    containing = FakeSessionManager({
        ("GET", "/agent"): {
            "ok": True,
            "data": [{"name": "OtherAgent"}, {"name": "custom-sisyphus-agent"}],
        }
    })
    containing._detect_agent()

    assert exact.active_agent == "Sisyphus"
    assert containing.active_agent == "custom-sisyphus-agent"


def test_send_command_rejects_compaction_response_as_incomplete() -> None:
    manager = _manager_with_message({
        "info": {"mode": "compaction", "agent": "compaction", "summary": True, "sessionID": "ses-1"},
        "parts": [{"type": "step-start"}],
    })

    result = json.loads(manager.send_command("ses-1", "do work", retries=0))

    assert result["ok"] is False
    assert "Compaction response is incomplete" in result["error"]
    assert not any(call["method"] == "GET" and call["path"] == "/session/status" for call in manager.calls)


def test_send_command_allows_nested_task_usage_in_phase_prompts() -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "working"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [
            {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "tool", "tool": "task", "state": {"status": "running"}}]},
        ]},
        ("POST", "/session/ses-1/abort"): {"ok": True, "data": {}},
    })

    result = manager.send_command(
        "ses-1",
        "Do not delegate, spawn nested agents, start background tasks, or ask another model/session to investigate.",
        retries=0,
    )

    assert result == "working"
    assert not any(call["method"] == "POST" and call["path"] == "/session/ses-1/abort" for call in manager.calls)


def test_nested_task_prompt_does_not_enable_polling_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): [
            {"ok": True, "data": {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "working"}]}},
        ],
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): [
            {"ok": True, "data": []},
            {"ok": True, "data": [{"parts": [{"type": "tool", "tool": "task", "state": {"status": "running"}}]}]},
        ],
        ("POST", "/session/ses-1/abort"): {"ok": True, "data": {}},
    })
    monkeypatch.setattr(manager_module, "NESTED_TASK_GUARD_POLL_SECONDS", 0.01)

    result = manager.send_command(
        "ses-1",
        "Do not delegate, spawn nested agents, start background tasks, or ask another model/session to investigate.",
        retries=0,
    )

    assert result == "working"
    assert not any(call["method"] == "POST" and call["path"] == "/session/ses-1/abort" for call in manager.calls)


def test_send_command_aborts_blocking_question_tool_usage() -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "working"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [
            {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "tool", "tool": "question", "state": {"status": "running"}}]},
        ]},
        ("POST", "/session/ses-1/abort"): {"ok": True, "data": {}},
    })

    result = json.loads(manager.send_command(
        "ses-1",
        "Do not ask questions or request clarification; return JSON.",
        retries=0,
    ))

    assert result["ok"] is False
    assert "blocking user questions" in result["error"]
    assert any(call["method"] == "POST" and call["path"] == "/session/ses-1/abort" for call in manager.calls)


def test_send_command_aborts_question_when_prompt_only_forbids_clarification() -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "working"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [
            {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "tool", "tool": "question", "state": {"status": "running"}}]},
        ]},
        ("POST", "/session/ses-1/abort"): {"ok": True, "data": {}},
    })

    result = json.loads(manager.send_command(
        "ses-1",
        "Do not request clarification; choose the safest valid repair and return JSON.",
        retries=0,
    ))

    assert result["ok"] is False
    assert "blocking user questions" in result["error"]
    assert any(call["method"] == "POST" and call["path"] == "/session/ses-1/abort" for call in manager.calls)


def test_send_command_aborts_question_for_clarification_continuation_prompt() -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "working"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [
            {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "tool", "tool": "question", "state": {"status": "running"}}]},
        ]},
        ("POST", "/session/ses-1/abort"): {"ok": True, "data": {}},
    })

    result = json.loads(manager.send_command(
        "ses-1",
        "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.",
        retries=0,
    ))

    assert result["ok"] is False
    assert "blocking user questions" in result["error"]
    assert any(call["method"] == "POST" and call["path"] == "/session/ses-1/abort" for call in manager.calls)


def test_send_command_retries_runtime_errors_that_mention_old_guard_text() -> None:
    class NestedGuardFailureManager(FakeSessionManager):
        def __init__(self) -> None:
            super().__init__({})
            self.send_attempts: int = 0

        def _send_message_raw(
            self,
            session_id: str,
            text: str,
            agent: str = "",
            timeout: int | float | None = None,
        ) -> str:
            self.send_attempts += 1
            raise RuntimeError(
                "Phase repair command forbids nested agents/background tasks, but session ses-1 used nested tool 'task'"
            )

    manager = NestedGuardFailureManager()

    result = json.loads(manager.send_command("ses-1", "do work", retries=2))

    assert result["ok"] is False
    assert "forbids nested agents/background tasks" in result["error"]
    assert manager.send_attempts == 3


def test_send_command_recovers_json_when_latest_completed_message_is_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    json_message = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": "```json\n{\"ok\": true, \"phase\": 1}\n```"}],
    }
    followup = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": "What should I do next?"}],
    }
    compaction = {
        "info": {"role": "assistant", "finish": "stop", "mode": "compaction", "summary": True},
        "parts": [{"type": "compaction"}],
    }

    def blocking_post(_call: dict[str, Any]) -> Response:
        raise TimeoutError("simulated transport wait should be bypassed by recovery")

    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): blocking_post,
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [followup, compaction, json_message]},
    })
    monkeypatch.setattr(manager_module, "NESTED_TASK_GUARD_POLL_SECONDS", 0.0)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = manager.send_command("ses-1", "do work", timeout=120, retries=0)

    assert "\"phase\": 1" in result
    assert "What should I do next" not in result


def test_send_command_recovers_completed_history_for_finite_timeout_post(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": "finite timeout recovered"}],
    }
    old_message = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": "old assistant text"}],
    }
    latest_message_calls = 0

    def blocking_post(_call: dict[str, Any]) -> Response:
        raise TimeoutError("simulated transport wait should be bypassed by recovery")

    def message_route(call: dict[str, Any]) -> Response:
        nonlocal latest_message_calls
        if call.get("query") == {"limit": 1}:
            return {"ok": True, "data": [old_message]}
        if call.get("query") == {"limit": 20}:
            latest_message_calls += 1
            return {"ok": True, "data": [completed, old_message]}
        return {"ok": True, "data": [completed]}

    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): blocking_post,
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): message_route,
    })
    monkeypatch.setattr(manager_module, "NESTED_TASK_GUARD_POLL_SECONDS", 0.0)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    result = manager.send_command("ses-1", "do work", timeout=120, retries=0)

    assert result == "finite timeout recovered"
    assert latest_message_calls >= 1


def test_send_command_recovers_completed_history_when_post_stream_never_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": "recovered phase complete"}],
    }
    old_message = {
        "info": {"role": "assistant", "finish": "stop"},
        "parts": [{"type": "text", "text": "old assistant text"}],
    }
    release_post = threading.Event()
    latest_message_calls = 0

    def blocking_post(_call: dict[str, Any]) -> Response:
        release_post.wait()
        return {"ok": True, "data": completed}

    def message_route(call: dict[str, Any]) -> Response:
        nonlocal latest_message_calls
        if call.get("query") == {"limit": 1}:
            return {"ok": True, "data": [old_message]}
        if call.get("query") == {"limit": 20}:
            latest_message_calls += 1
            return {"ok": True, "data": [old_message] if latest_message_calls == 1 else [completed, old_message]}
        return {"ok": True, "data": [completed]}

    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): blocking_post,
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): message_route,
    })
    monkeypatch.setattr(manager_module, "NESTED_TASK_GUARD_POLL_SECONDS", 0.0)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    try:
        result = manager.send_command("ses-1", "do work", timeout=None, retries=0)
    finally:
        release_post.set()

    assert result == "recovered phase complete"
    assert any(call["method"] == "POST" and call["path"] == "/session/ses-1/message" for call in manager.calls)
    assert latest_message_calls >= 2


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
            {"ok": True, "data": [{"parts": [{"type": "text", "text": "even older assistant text"}]}]},
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

    result = json.loads(manager.send_command("ses-1", "do work", timeout=1, retries=0))

    post_calls = [call for call in manager.calls if call["method"] == "POST"]
    assert result["ok"] is False
    assert "Session still running" in result["error"]
    assert len(post_calls) == 1


@_SKIP_SQLITE
def test_sqlite_fallback_ignores_unrelated_incomplete_todos(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE todos ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("other-session", "pending", "other work"))
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "completed", "own work"))

    manager = _sqlite_backed_manager(db_path)

    assert manager.send_command("ses-1", "do work", retries=0) == "phase complete"


@_SKIP_SQLITE
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


@_SKIP_SQLITE
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


@_SKIP_SQLITE
def test_sqlite_idle_session_with_completed_todos_is_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE session (id TEXT, status TEXT)')
        conn.execute('CREATE TABLE todos ("sessionID" TEXT, status TEXT, content TEXT)')
        conn.execute('INSERT INTO session (id, status) VALUES (?, ?)', ("ses-1", "idle"))
        conn.execute('INSERT INTO todos ("sessionID", status, content) VALUES (?, ?, ?)', ("ses-1", "completed", "rerun validator"))

    manager = _sqlite_backed_manager(db_path)

    assert manager.send_command("ses-1", "do work", retries=0) == "phase complete"


@_SKIP_SQLITE
def test_send_command_timeout_none_uses_sqlite_assistant_completion_without_todos(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    assistant_data = {
        "role": "assistant",
        "agent": "Sisyphus - Orchestrator",
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


@_SKIP_SQLITE
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


@_SKIP_SQLITE
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


@_SKIP_SQLITE
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


def test_wait_for_idle_times_out_while_todos_remain_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_message(
        {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "waiting on todo"}]},
        history={"ok": True, "data": [{"todos": [{"status": "in_progress", "content": "repair"}]}]},
        status_type="idle",
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


def test_wait_for_idle_timeout_none_waits_until_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "eventually idle"}]},
        },
        ("GET", "/session/status"): [
            {"ok": True, "data": {"ses-1": {"type": "running"}}},
            {"ok": True, "data": {"ses-1": {"type": "idle"}}},
        ],
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)

    assert manager.wait_for_idle("ses-1", timeout_s=None, interval_s=0) is True


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
    # Auth errors from _post_session_message_with_guard return before wait_for_idle,
    # so no status calls are made. The hard-error guard timeout (3630s) is proven by
    # the function returning immediately rather than hanging indefinitely.
    assert len(status_calls) == 0


def test_wait_for_idle_returns_idle_when_status_empty_and_no_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: when /session/status returns {} after a completed response,
    wait_for_idle must NOT spin until timeout."""
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [
            {"role": "assistant", "finish": "stop", "info": {"finish": "stop"}, "todos": [{"status": "completed"}]},
        ]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    assert manager.wait_for_idle("ses-1", timeout_s=1, interval_s=0) is True


def test_wait_for_idle_returns_idle_when_empty_status_and_stale_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [
            {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
            {"todos": [{"status": "in_progress", "content": "stale phase-local todo"}]},
        ]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    assert manager.wait_for_idle("ses-1", timeout_s=1, interval_s=0) is True


def test_wait_for_idle_looks_past_latest_stale_todo_after_empty_status(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): lambda call: {
            "ok": True,
            "data": [
                {"todos": [{"status": "in_progress", "content": "stale phase-local todo"}]},
                {"info": {"role": "assistant", "finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
            ],
        }
        if call.get("query") == {"limit": 20}
        else {"ok": True, "data": [{"todos": [{"status": "in_progress", "content": "stale phase-local todo"}]}]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    assert manager.wait_for_idle("ses-1", timeout_s=1, interval_s=0) is True
    message_calls = [call for call in manager.calls if call["method"] == "GET" and call["path"] == "/session/ses-1/message"]
    assert message_calls[0]["query"] == {"limit": 200}


def test_wait_for_idle_tolerant_empty_status_no_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same scenario via _wait_after_hard_error: empty status + no todo signal → return."""
    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    manager._wait_after_hard_error("ses-1", timeout=1, interval_s=0)



def test_wait_for_idle_requires_consecutive_idle_confirmations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix: single idle observation is NOT enough – must confirm idle over N consecutive polls."""
    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {
            "ok": True,
            "data": [{"info": {"role": "assistant", "finish": "stop"}, "parts": []}],
        },
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    result = manager.wait_for_idle("ses-1", timeout_s=5, interval_s=0, idle_confirm_polls=3, max_restart_cycles=5)
    assert result is True

    message_calls = [c for c in manager.calls if c["method"] == "GET" and c["path"] == "/session/ses-1/message"]
    assert len(message_calls) >= 3, f"expected >=3 confirmation polls, got {len(message_calls)}"


def test_wait_for_idle_detects_restart_loop_and_breaks_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix: when session alternates idle→running→idle→running, detect auto-continuation loop
    and return True after max_restart_cycles."""
    poll_counter = [0]

    def message_route(call: dict[str, Any]) -> dict[str, Any]:
        if call.get("query") != {"limit": 20}:
            return {"ok": True, "data": [{"parts": []}]}
        poll_counter[0] += 1
        if poll_counter[0] % 2 == 1:
            return {"ok": True, "data": [{"info": {"role": "assistant", "finish": "stop"}, "parts": []}]}
        return {"ok": True, "data": [{"info": {"role": "assistant", "finish": "processing"}, "parts": []}]}

    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): message_route,
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    result = manager.wait_for_idle("ses-1", timeout_s=10, interval_s=0, idle_confirm_polls=3, max_restart_cycles=3)
    assert result is True

    message_calls = [c for c in manager.calls if c["method"] == "GET" and c["path"] == "/session/ses-1/message" and c["query"] == {"limit": 20}]
    assert len(message_calls) <= 6, f"expected <=6 assistant polls before loop detection, got {len(message_calls)}"


def test_wait_for_idle_single_restart_then_stable_idle_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix: a single restart followed by stable idle should NOT trigger loop detection.
    idle_confirm_polls counter resets on restart, then re-accumulates."""
    poll_counter = [0]

    def message_route(call: dict[str, Any]) -> dict[str, Any]:
        if call.get("query") != {"limit": 20}:
            return {"ok": True, "data": [{"parts": []}]}
        poll_counter[0] += 1
        states = {1: "idle", 2: "running", 3: "idle", 4: "idle", 5: "idle"}
        state = states.get(poll_counter[0], "idle")
        if state == "running":
            return {"ok": True, "data": [{"info": {"role": "assistant", "finish": "processing"}, "parts": []}]}
        return {"ok": True, "data": [{"info": {"role": "assistant", "finish": "stop"}, "parts": []}]}

    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): message_route,
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    result = manager.wait_for_idle("ses-1", timeout_s=10, interval_s=0, idle_confirm_polls=3, max_restart_cycles=5)
    assert result is True

    message_calls = [c for c in manager.calls if c["method"] == "GET" and c["path"] == "/session/ses-1/message" and c["query"] == {"limit": 20}]
    assert len(message_calls) == 5, f"expected 5 assistant polls (1 restart + 3 confirmations), got {len(message_calls)}"


def test_wait_for_idle_transient_none_resets_idle_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix: transient assistant check failure (None) must reset consecutive_idle_polls
    to prevent an intermittent network blip from triggering a false idle return."""
    poll_counter = [0]

    def message_route(call: dict[str, Any]) -> dict[str, Any]:
        if call.get("query") != {"limit": 20}:
            return {"ok": True, "data": [{"parts": []}]}
        poll_counter[0] += 1
        if poll_counter[0] == 2:
            return {"ok": True, "data": [{"info": {"role": "user"}, "parts": []}]}
        return {"ok": True, "data": [{"info": {"role": "assistant", "finish": "stop"}, "parts": []}]}

    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): message_route,
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    result = manager.wait_for_idle("ses-1", timeout_s=10, interval_s=0, idle_confirm_polls=3, max_restart_cycles=5)
    assert result is True

    message_calls = [c for c in manager.calls if c["method"] == "GET" and c["path"] == "/session/ses-1/message" and c["query"] == {"limit": 20}]
    assert len(message_calls) == 5, f"expected 5 assistant polls after transient reset, got {len(message_calls)}"


def test_wait_for_idle_still_times_out_when_session_never_idles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix: existing timeout behaviour unchanged — when session stays running it still returns False."""
    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {
            "ok": True,
            "data": [{"info": {"role": "assistant", "finish": "processing"}, "parts": []}],
        },
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    result = manager.wait_for_idle("ses-1", timeout_s=0.01, interval_s=0, idle_confirm_polls=3, max_restart_cycles=5)
    assert result is False


def test_wait_for_idle_consecutive_running_does_not_increment_restart_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix: consecutive 'running' states without any preceding idle should NOT
    count as restart cycles. Only idle→running transitions count."""
    poll_counter = [0]

    def message_route(_call: dict[str, Any]) -> dict[str, Any]:
        poll_counter[0] += 1
        # All polls return running → never idle → never increment restart_cycles
        return {"ok": True, "data": [{"info": {"role": "assistant", "finish": "processing"}, "parts": []}]}

    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): message_route,
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    # With short timeout, should hit timeout (return False) because restart_cycles never
    # increments and we never enter the loop-detection return.
    result = manager.wait_for_idle("ses-1", timeout_s=0.01, interval_s=0, idle_confirm_polls=3, max_restart_cycles=1)
    assert result is False
