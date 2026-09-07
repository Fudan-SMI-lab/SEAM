"""Textual renderer for the live dashboard.

Constructs the Textual ``App`` instance that polls ``ui_events.jsonl``,
applies events through the shared state machine, and renders a header, a
stack of ``Static`` panels, and a footer. Dynamic content is wrapped in
literal ``rich.text.Text`` so bracketed labels like ``[phase_id]`` survive.

``textual`` is imported lazily inside ``_build_textual_dashboard``: merely
importing ``core.dashboard_textual`` never imports ``textual``. The ``App``
subclass is built inside the factory so the optional dependency is only
touched when a renderer is actually requested, and exposing the instance
builder (rather than only ``.run()``) lets the QA pilot drive the real app
through ``run_test()``.

Extracted from ``core.dashboard`` so the Textual render path lives in a
focused module. Behavior is unchanged.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from core.dashboard_state import (
    DashboardState,
    _apply_event,
    _load_events,
    visible_phase_rows,
)
from core.dashboard_text import (
    MAX_CURRENT_WORK,
    compact_cells,
    iteration_text,
    sessions_text,
    spinner_frame,
    subphases_text,
)


def _build_textual_dashboard(
    events_path: Path, stop_event: threading.Event
) -> Any:
    """Construct the Textual dashboard app instance.

    The ``App`` subclass is built lazily inside this factory so ``textual``
    remains an optional import: merely importing ``core.dashboard`` never
    imports it. Exposing the instance builder (rather than only ``.run()``)
    lets the QA pilot drive the real app through ``run_test()``.
    """
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.containers import Container
    from textual.widgets import Footer, Header, Static

    class _TextualDashboard(App[None]):
        CSS = """
        Screen { layout: vertical; }
        #timeline { height: 28%; overflow-y: auto; }
        #current { height: 12%; }
        #iteration { height: 10%; }
        #subphases { height: 12%; overflow-y: auto; }
        #sessions { height: 15%; overflow-y: auto; }
        #activity { height: 1fr; overflow-y: auto; }
        #errors { height: 18%; overflow-y: auto; }
        """
        BINDINGS = [
            ("q", "quit", "Quit dashboard"),
            ("l", "focus_activity", "Logs"),
            ("s", "focus_activity", "Sessions"),
            ("?", "help", "Help"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.state = DashboardState()
            self.offset = 0

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Container():
                yield Static("", id="timeline")
                yield Static("", id="current")
                yield Static("", id="iteration")
                yield Static("", id="subphases")
                yield Static("", id="sessions")
                yield Static("", id="activity")
                yield Static("", id="errors")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(0.25, self.refresh_events)

        def refresh_events(self) -> None:
            if stop_event.is_set():
                self.exit()
                return
            events, self.offset = _load_events(events_path, self.offset)
            for event in events:
                _apply_event(self.state, event)
            running_mark = spinner_frame() if self.state.status == "running" else ""
            self.query_one("#timeline", Static).update(
                Text(self._timeline_text())
            )
            self.query_one("#current", Static).update(
                Text(
                    f"当前工作 {running_mark}\n\n"
                    f"{compact_cells(self.state.current_work, MAX_CURRENT_WORK)}"
                )
            )
            self.query_one("#iteration", Static).update(
                Text("当前迭代\n\n" + iteration_text(self.state))
            )
            self.query_one("#subphases", Static).update(
                Text("子阶段\n\n" + subphases_text(self.state))
            )
            self.query_one("#sessions", Static).update(
                Text("Subsessions\n\n" + sessions_text(self.state))
            )
            self.query_one("#activity", Static).update(
                Text(
                    "智能体活动\n\n"
                    + ("\n".join(self.state.activity) or "暂无智能体活动。")
                )
            )
            self.query_one("#errors", Static).update(
                Text(
                    "历史错误\n\n"
                    + ("\n".join(self.state.error_history) or "暂无历史错误。")
                )
            )

        def _timeline_text(self) -> str:
            lines = ["当前阶段"]
            for row in visible_phase_rows(self.state):
                lines.append(
                    f"{row.number}. {row.title}｜{row.status}\n"
                    f"   {row.description}"
                )
            return "\n".join(lines)

        def action_focus_activity(self) -> None:
            self.query_one("#activity", Static).focus()

        def action_help(self) -> None:
            self.query_one("#current", Static).update(
                Text(
                    "快捷键\n\nq: 退出仪表盘视图\n"
                    "l/s: 聚焦智能体活动面板\n?: 显示帮助\n"
                    "退出仪表盘后，迁移任务仍会继续运行。"
                )
            )

    return _TextualDashboard()
