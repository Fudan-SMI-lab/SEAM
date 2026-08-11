"""Rich renderer for the live dashboard.

The Rich ``run_dashboard`` entry point polls ``ui_events.jsonl``, applies events
through the shared state machine, and renders a ``rich.console.Group`` of
panels/tables via ``rich.live.Live``. Dynamic content is wrapped in literal
``rich.text.Text`` so bracketed labels like ``[phase_id]`` survive as plain
text instead of being parsed as console markup.

Extracted from ``core.dashboard`` so the Rich render loop lives in a focused
module. ``rich`` is imported lazily inside ``run_dashboard``: merely importing
``core.dashboard_rich`` never imports ``rich``. Behavior is unchanged.
"""

from __future__ import annotations

import sys
import threading
import time
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
    status_text,
    subphases_text,
)


def run_dashboard(events_path: str | Path, stop_event: threading.Event) -> None:
    """Render a best-effort real-time dashboard from ``ui_events.jsonl``.

    Dynamic content is wrapped in literal ``rich.text.Text`` so bracketed
    labels like ``[phase_id]`` survive as plain text instead of being parsed
    as console markup.
    """
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    path = Path(events_path)
    state = DashboardState()
    offset = 0
    keyboard_fd: int | None = None
    keyboard_settings: list[Any] | None = None
    keyboard_select: Any = None
    keyboard_termios: Any = None

    try:
        import select
        import termios
        import tty

        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
            settings = termios.tcgetattr(fd)
            keyboard_select = select
            keyboard_termios = termios
            tty.setcbreak(fd)
            keyboard_fd = fd
            keyboard_settings = settings
    except (ImportError, OSError, ValueError):
        keyboard_fd = None
        keyboard_settings = None

    def render() -> Group:
        table = Table(expand=True)
        table.add_column("编号", ratio=1)
        table.add_column("阶段", ratio=2)
        table.add_column("状态", ratio=1)
        table.add_column("正在做什么", ratio=3)
        for row in visible_phase_rows(state):
            table.add_row(
                Text(str(row.number)),
                Text(row.title),
                Text(row.status),
                Text(row.description),
            )
        activity = "\n".join(state.activity) or "暂无智能体活动。"
        errors = "\n".join(state.error_history) or "暂无历史错误。"
        running_mark = spinner_frame() if state.status == "running" else ""
        return Group(
            Panel(
                Text(f"SEAM 迁移仪表盘  状态={status_text(state.status)} {running_mark}"),
                title="运行",
            ),
            Panel(table, title="当前阶段"),
            Panel(Text(compact_cells(state.current_work, MAX_CURRENT_WORK)), title="当前工作"),
            Panel(Text(iteration_text(state)), title="当前迭代"),
            Panel(Text(subphases_text(state)), title="子阶段"),
            Panel(Text(sessions_text(state)), title="Subsessions"),
            Panel(Text(activity), title="智能体活动"),
            Panel(Text(errors), title="历史错误"),
            Panel(Text("q: 退出仪表盘视图；迁移和日志继续运行"), title="快捷键"),
        )

    try:
        with Live(render(), refresh_per_second=4, screen=True) as live:
            while not stop_event.is_set():
                if keyboard_fd is not None and keyboard_select is not None:
                    readable, _, _ = keyboard_select.select([keyboard_fd], [], [], 0)
                    if readable and sys.stdin.read(1).lower() == "q":
                        break
                events, offset = _load_events(path, offset)
                for event in events:
                    _apply_event(state, event)
                live.update(render())
                time.sleep(0.25)
    finally:
        if (
            keyboard_fd is not None
            and keyboard_settings is not None
            and keyboard_termios is not None
        ):
            keyboard_termios.tcsetattr(
                keyboard_fd,
                keyboard_termios.TCSADRAIN,
                keyboard_settings,
            )

    print(
        "\nDashboard closed (q). Migration continues in background.\n"
        f"Report dir: {path.parent}\n"
        f"Events log: {path}\n"
        "Waiting for migration to finish...",
        file=sys.stderr,
        flush=True,
    )
