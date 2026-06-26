from __future__ import annotations

import json
import sqlite3
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
    assert post_call["timeout"] == manager_module.DEFAULT_SESSION_WAIT_TIMEOUT + 30


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

    assert manager.active_agent == "sisyphus"


def test_detect_agent_prefers_exact_sisyphus_then_contains_sisyphus() -> None:
    exact = FakeSessionManager({
        ("GET", "/agent"): {
            "ok": True,
            "data": [
                {"name": "OtherAgent"},
                {"name": "sisyphus"},
                {"name": "sisyphus-helper"},
            ],
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

    assert exact.active_agent == "sisyphus"
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
        "agent": "Atlas - Plan Executor",
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


def test_wait_for_idle_returns_idle_when_status_empty_and_no_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: when /session/status returns {} after a completed response,
    wait_for_idle must NOT spin until timeout."""
    manager = FakeSessionManager({
        ("POST", "/session/ses-1/message"): {
            "ok": True,
            "data": {"info": {"finish": "stop"}, "parts": [{"type": "text", "text": "phase complete"}]},
        },
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    assert manager.wait_for_idle("ses-1", timeout_s=1, interval_s=0) is True


def test_wait_for_idle_tolerant_empty_status_no_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same scenario via _wait_after_hard_error: empty status + no todo signal → return."""
    manager = FakeSessionManager({
        ("GET", "/session/status"): {"ok": True, "data": {}},
        ("GET", "/session/ses-1/message"): {"ok": True, "data": [{"todos": [{"status": "completed"}]}]},
    })
    monkeypatch.setattr(manager_module.time, "sleep", lambda _interval: None)
    manager._candidate_sqlite_paths = lambda: []  # type: ignore[method-assign]

    manager._wait_after_hard_error("ses-1", timeout=1, interval_s=0)


# ── Agent name resolution tests ───────────────────────────────────────


class TestFetchAgentList:
    def test_returns_sorted_names_from_agent_endpoint(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [
                    {"name": "Atlas - Plan Executor"},
                    {"name": "OpenCode-Builder"},
                    {"name": "build"},
                ],
            }
        })
        names = manager._fetch_agent_list()
        assert names == ["Atlas - Plan Executor", "OpenCode-Builder", "build"]

    def test_returns_empty_list_on_non_ok(self) -> None:
        manager = FakeSessionManager({("GET", "/agent"): {"ok": False}})
        assert manager._fetch_agent_list() == []

    def test_returns_empty_list_on_non_list_data(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {"ok": True, "data": "not_a_list"}
        })
        assert manager._fetch_agent_list() == []

    def test_skips_non_dict_entries(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "Atlas"}, "not_a_dict", {"name": ""}],
            }
        })
        assert manager._fetch_agent_list() == ["Atlas"]


class TestResolveAgentName:
    def test_exact_match(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "Atlas - Plan Executor"}, {"name": "build"}],
            }
        })
        assert manager.resolve_agent_name("Atlas - Plan Executor") == "Atlas - Plan Executor"

    def test_case_insensitive_exact_match(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "Atlas - Plan Executor"}, {"name": "build"}],
            }
        })
        assert manager.resolve_agent_name("atlas - plan executor") == "Atlas - Plan Executor"

    def test_partial_substring_match(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "Atlas - Plan Executor"}, {"name": "OpenCode-Builder"}],
            }
        })
        assert manager.resolve_agent_name("Atlas") == "Atlas - Plan Executor"

    def test_prefers_exact_word_when_ambiguous_partials(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [
                    {"name": "Atlas - Plan Executor"},
                    {"name": "Atlas Helper"},
                    {"name": "Atlas"},
                ],
            }
        })
        assert manager.resolve_agent_name("Atlas") == "Atlas"

    def test_raises_on_ambiguous_partial(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [
                    {"name": "Atlas - Plan Executor"},
                    {"name": "Atlas Helper"},
                ],
            }
        })
        with pytest.raises(ValueError, match="Ambiguous agent name"):
            manager.resolve_agent_name("Atlas")

    def test_raises_on_not_found(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "build"}],
            }
        })
        with pytest.raises(ValueError, match="not found"):
            manager.resolve_agent_name("Atlas")

    def test_raises_on_no_agents_available(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {"ok": False}
        })
        with pytest.raises(ValueError, match="no agents available"):
            manager.resolve_agent_name("anything")

    def test_caches_agent_list(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "Atlas"}],
            }
        })
        first = manager.resolve_agent_name("Atlas")
        assert first == "Atlas"
        assert len(manager.calls) == 1  # Only one HTTP call for /agent


class TestOverrideAgent:
    def test_resolves_and_sets_agent(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "Atlas - Plan Executor"}],
            }
        })
        canonical = manager.override_agent("Atlas")
        assert canonical == "Atlas - Plan Executor"
        assert manager.active_agent == "Atlas - Plan Executor"

    def test_raises_for_invalid_name(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {"ok": True, "data": [{"name": "build"}]}
        })
        with pytest.raises(ValueError, match="not found"):
            manager.override_agent("Atlas")


class TestAvailableAgentsProperty:
    def test_caches_on_first_access(self) -> None:
        manager = FakeSessionManager({
            ("GET", "/agent"): {
                "ok": True,
                "data": [{"name": "A"}, {"name": "B"}],
            }
        })
        assert manager.available_agents == ["A", "B"]
        assert len(manager.calls) == 1
        # Second access uses cache
        _ = manager.available_agents
        assert len(manager.calls) == 1
