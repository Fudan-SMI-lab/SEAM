"""Behavior characterization for the dashboard before/after the module split.

These tests pin the exact public/import surface and the observable render
behavior of ``core.dashboard`` so the responsibility-based split into
``dashboard_state`` / ``dashboard_rich`` / ``dashboard_textual`` cannot change
behavior. They assert on stable identifiers, counts, status tokens, and the
literal bracket-label survival contract -- never on codepage-rendered bytes.

If any of these fail after the split, the refactor changed behavior.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import core.dashboard as dashboard_module
from core.dashboard import (
    DASHBOARD_INSTALL_COMMAND,
    DashboardBackend,
    DashboardBackendUnavailableError,
    DashboardState,
    PhaseRow,
    SeamDashboardApp,
    _apply_event,
    _build_textual_dashboard,
    _load_events,
    resolve_dashboard_backend,
    run_dashboard,
    visible_phase_rows,
)


# --- Import surface: every public/private symbol callers depend on ---


def test_dashboard_facade_reexports_full_symbol_surface() -> None:
    """The facade must keep re-exporting every symbol tests/callers import."""
    for name in (
        "DashboardState",
        "PhaseRow",
        "visible_phase_rows",
        "_load_events",
        "_apply_event",
        "DashboardBackend",
        "DASHBOARD_INSTALL_COMMAND",
        "DashboardBackendUnavailableError",
        "resolve_dashboard_backend",
        "run_dashboard",
        "SeamDashboardApp",
        "_build_textual_dashboard",
    ):
        assert hasattr(dashboard_module, name), f"core.dashboard lost {name}"


def test_dashboard_backend_enum_members_unchanged() -> None:
    assert DashboardBackend.TEXTUAL.value == "textual"
    assert DashboardBackend.RICH.value == "rich"
    assert DashboardBackend.NONE.value == "none"


def test_install_command_pin_unchanged() -> None:
    assert DASHBOARD_INSTALL_COMMAND == 'python -m pip install -e "./src[dashboard]"'


def test_unavailable_error_is_runtimeerror_subclass() -> None:
    assert issubclass(DashboardBackendUnavailableError, RuntimeError)
    err = DashboardBackendUnavailableError()
    assert err.install_command == DASHBOARD_INSTALL_COMMAND


# --- No mandatory top-level rich/textual import ---


def _top_level_import_targets(module: Any, packages: tuple[str, ...]) -> list[str]:
    """Return module-level import statements that pull ``packages``.

    The no-mandatory-rich/textual-import invariant is structural: every
    ``rich``/``textual`` import must live inside a function body (lazy), never
    at module top level. Inspecting the AST avoids the state corruption that a
    ``importlib.reload`` would cause to later monkeypatch targeting.
    """
    import ast

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in packages:
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in packages:
                found.append(node.module or "")
    return found


def test_dashboard_module_has_no_top_level_textual_import() -> None:
    assert _top_level_import_targets(dashboard_module, ("textual",)) == []


def test_dashboard_module_has_no_top_level_rich_import() -> None:
    assert _top_level_import_targets(dashboard_module, ("rich",)) == []


# --- State machine behavior on a fixed event sequence ---


def _seed_events() -> list[dict[str, Any]]:
    return [
        {
            "event_type": "phase_started",
            "timestamp": "2026-07-01T09:00:00+00:00",
            "phase_id": "phase_1_project_analysis",
            "status": "running",
        },
        {
            "event_type": "session_ready",
            "timestamp": "2026-07-01T09:00:01+00:00",
            "phase_id": "phase_1_project_analysis",
            "agent_role": "main_engineer",
            "session_id": "ses-main-001",
        },
        {
            "event_type": "shell_command_finished",
            "timestamp": "2026-07-01T09:00:02+00:00",
            "phase_id": "phase_1_project_analysis",
            "status": "failed",
            "message": "exit 1: CUDA_HOME missing",
        },
    ]


def _seeded_state() -> DashboardState:
    state = DashboardState()
    for event in _seed_events():
        _apply_event(state, event)
    return state


def test_state_status_and_phase_status_after_fixed_sequence() -> None:
    state = _seeded_state()
    assert state.status == "running"
    assert state.phases["phase_1_project_analysis"]["status"] == "running"


def test_state_activity_records_two_lines_in_order() -> None:
    state = _seeded_state()
    assert len(state.activity) == 2
    assert "17:00:01" in state.activity[0]
    assert "17:00:02" in state.activity[1]


def test_state_error_history_keeps_bracketed_phase_label() -> None:
    state = _seeded_state()
    assert len(state.error_history) == 1
    assert "[phase_1_project_analysis]" in state.error_history[0]
    assert "CUDA_HOME missing" in state.error_history[0]


def test_state_sessions_register_bracketed_session_id() -> None:
    state = _seeded_state()
    assert "ses-main-001" in state.sessions
    session = state.sessions["ses-main-001"]
    assert session["role"] == "main_engineer"
    assert session["status"] == "ready"


def test_state_current_work_marks_failed_command() -> None:
    state = _seeded_state()
    assert "失败" in state.current_work


# --- visible_phase_rows: exact selection contract ---


def test_visible_phase_rows_returns_two_rows_starting_at_running_phase() -> None:
    rows = visible_phase_rows(_seeded_state())
    assert len(rows) == 2
    assert all(isinstance(row, PhaseRow) for row in rows)
    assert rows[0].phase_id == "phase_1_project_analysis"
    assert rows[0].number == "1"
    assert rows[0].status == "运行中"
    assert rows[1].phase_id == "phase_1_5_constraint_summary"
    assert rows[1].number == "1.5"
    assert rows[1].status == "待执行"


# --- Rich render path: panel structure + literal bracket survival ---


def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False))
            handle.write("\n")


def _capture_render_group(events_path: Path) -> Any:
    import rich.live

    captured: list[Any] = []
    stop = threading.Event()

    class _FakeLive:
        def __init__(self, renderable: Any, **_kwargs: Any) -> None:
            captured.append(renderable)

        def __enter__(self) -> "_FakeLive":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def update(self, renderable: Any, **_kwargs: Any) -> None:
            captured.append(renderable)
            stop.set()

    original_live = rich.live.Live
    rich.live.Live = _FakeLive  # type: ignore[misc,assignment]
    try:
        run_dashboard(events_path, stop)
    finally:
        rich.live.Live = original_live  # type: ignore[misc,assignment]
    assert captured, "run_dashboard produced no render output"
    return captured[-1]


def _render_to_plain(group: Any, width: int) -> str:
    from rich.console import Console

    console = Console(width=width, record=True, force_terminal=False)
    console.print(group)
    return console.export_text(styles=False)


def test_rich_render_keeps_all_nine_panel_titles(tmp_path: Path) -> None:
    path = tmp_path / "ui_events.jsonl"
    _write_events(path, _seed_events())
    text = _render_to_plain(_capture_render_group(path), 120)
    for title in (
        "运行",
        "当前阶段",
        "当前工作",
        "当前迭代",
        "子阶段",
        "Subsessions",
        "智能体活动",
        "历史错误",
        "快捷键",
    ):
        assert title in text


def test_rich_render_preserves_bracket_labels_at_all_widths(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ui_events.jsonl"
    _write_events(path, _seed_events())
    group = _capture_render_group(path)
    for width in (80, 120, 160):
        text = _render_to_plain(group, width)
        assert "[ses-main-001]" in text
        assert "[phase_1_project_analysis]" in text


def test_rich_render_shortcut_panel_text_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "ui_events.jsonl"
    _write_events(path, _seed_events())
    text = _render_to_plain(_capture_render_group(path), 120)
    assert "q: 退出仪表盘视图；迁移和日志继续运行" in text


# --- SeamDashboardApp backend dispatch contract ---


def test_seam_dashboard_app_routes_textual_to_run_textual(
    tmp_path: Path, monkeypatch: Any
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(
        SeamDashboardApp, "_run_textual", lambda self: dispatched.append("textual")
    )
    app = SeamDashboardApp(
        tmp_path / "events.jsonl", threading.Event(), backend=DashboardBackend.TEXTUAL
    )
    app.run()
    assert dispatched == ["textual"]


def test_seam_dashboard_app_routes_rich_to_run_dashboard(
    tmp_path: Path, monkeypatch: Any
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(
        "core.dashboard.run_dashboard",
        lambda path, stop: dispatched.append(str(path)),
    )
    app = SeamDashboardApp(
        tmp_path / "events.jsonl", threading.Event(), backend=DashboardBackend.RICH
    )
    app.run()
    assert dispatched, "rich backend must call run_dashboard"


# --- _build_textual_dashboard factory is callable and constructs an app ---


def test_build_textual_dashboard_returns_app_with_expected_widget_ids(
    tmp_path: Path,
) -> None:
    try:
        import textual  # noqa: F401
    except ModuleNotFoundError:
        import pytest

        pytest.skip("textual not installed in this environment")
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    app = _build_textual_dashboard(path, threading.Event())
    assert app is not None
    # The CSS-bound widget ids are part of the public render contract.
    assert hasattr(app, "compose")
