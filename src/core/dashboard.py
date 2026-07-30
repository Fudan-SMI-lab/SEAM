from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.ui_events import PHASE_DISPLAY

MAX_CURRENT_WORK = 140
MAX_ACTIVITY_LINE = 140
MAX_ACTIVITY_LINES = 8
MAX_ERROR_LINE = 180
MAX_ERROR_LINES = 12
MAX_SESSION_LINES = 6
SPINNER_FRAMES = ("|", "/", "-", "\\")
STATUS_TEXT = {
    "pending": "待执行",
    "running": "运行中",
    "success": "已完成",
    "passed": "已完成",
    "skipped": "已跳过",
    "failed": "失败",
    "failure": "失败",
    "complete": "已结束",
    "dispatched": "已路由",
}
ROLE_TEXT = {
    "workflow_selector": "工作流选择器",
    "main_engineer": "主迁移智能体",
    "dependency_fixer": "依赖修复智能体",
    "code_adapter": "代码适配智能体",
    "operator_fixer": "算子修复智能体",
    "runtime_analyzer": "运行错误分析器",
    "repair_router": "修复路由器",
}
ROLE_ACTION_TEXT = {
    "workflow_selector": "选择迁移工作流",
    "dependency_fixer": "修复依赖和环境问题",
    "code_adapter": "修改项目代码以适配目标平台",
    "operator_fixer": "修复自定义算子或编译问题",
    "runtime_analyzer": "分析运行失败原因",
    "repair_router": "选择下一步修复角色",
}
PHASE_ACTION_TEXT = {
    "phase_0_env_detect": "检测运行环境和平台能力",
    "phase_1_project_analysis": "分析项目结构、依赖和 CUDA 使用点",
    "phase_1_5_constraint_summary": "整理用户约束和迁移要求",
    "phase_2_venv_create": "准备迁移环境和依赖",
    "phase_3_entry_script": "生成迁移验证入口命令",
    "phase_35_static_validate": "检查入口命令是否能有效验证迁移",
    "phase_4_rule_migration": "执行规则化代码迁移",
    "phase_5_validation": "运行验证并自动修复失败",
    "phase_6_report": "生成报告和使用说明",
    "phase_7a_evaluate": "评估可复用迁移经验",
    "phase_7b_refine": "沉淀可复用迁移经验",
}
PHASE_NUMBER_TEXT = {
    "phase_0_env_detect": "0",
    "phase_1_project_analysis": "1",
    "phase_1_5_constraint_summary": "1.5",
    "phase_2_venv_create": "2",
    "phase_3_entry_script": "3",
    "phase_35_static_validate": "3.5",
    "phase_4_rule_migration": "4",
    "phase_5_validation": "5",
    "phase_6_report": "6",
    "phase_7a_evaluate": "7a",
    "phase_7b_refine": "7b",
}


@dataclass
class DashboardState:
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_work: str = "Waiting for workflow events..."
    activity: list[str] = field(default_factory=list)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    subphases: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_iteration: dict[str, Any] = field(default_factory=dict)
    error_history: list[str] = field(default_factory=list)
    status: str = "running"


@dataclass(frozen=True)
class PhaseRow:
    number: str
    phase_id: str
    title: str
    status: str
    description: str


def _compact_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _beijing_time(timestamp: object) -> str:
    raw = str(timestamp or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    except ValueError:
        return raw[11:19]


def _short_phase_description(description: str) -> str:
    return _compact_text(description, 46)


def _status_text(status: object) -> str:
    return STATUS_TEXT.get(str(status or "pending"), str(status or "待执行"))


def _details(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details")
    return details if isinstance(details, dict) else {}


def _actor_text(agent_role: object, session_id: object, event_type: str) -> str:
    actor = str(agent_role or session_id or event_type)
    return ROLE_TEXT.get(actor, actor)


def _phase_action(phase_id: object) -> str:
    phase_key = str(phase_id or "")
    if phase_key in PHASE_ACTION_TEXT:
        return PHASE_ACTION_TEXT[phase_key]
    if phase_key in PHASE_DISPLAY:
        return PHASE_DISPLAY[phase_key].description
    return "执行迁移步骤"


def _agent_action(event: dict[str, Any]) -> str:
    role = str(event.get("agent_role") or "")
    if role in ROLE_ACTION_TEXT:
        return ROLE_ACTION_TEXT[role]

    event_details = _details(event)
    searchable = " ".join(
        str(value or "")
        for value in (
            event.get("message"),
            event_details.get("command_preview"),
            event_details.get("response_preview"),
        )
    ).lower()
    if "workflow selection" in searchable or "workflow_selector" in searchable:
        return "选择迁移工作流"
    if "dependency_fixer" in searchable or "依赖" in searchable:
        return "修复依赖和环境问题"
    if "operator_fixer" in searchable or "custom-op" in searchable or "算子" in searchable:
        return "修复自定义算子或编译问题"
    if "code_adapter" in searchable:
        return "修改项目代码以适配目标平台"
    if "repair_role" in searchable or "route" in searchable:
        return "选择下一步修复角色"
    return _phase_action(event.get("phase_id"))


def _event_sentence(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    status = str(event.get("status") or "")
    event_details = _details(event)

    if event_type == "phase_started":
        return f"开始：{_phase_action(event.get('phase_id'))}"
    if event_type == "phase_finished":
        return f"{_status_text(status)}：{_phase_action(event.get('phase_id'))}"
    if event_type == "session_ready":
        role = _actor_text(event.get("agent_role"), event.get("session_id"), event_type)
        return f"就绪：{role} 已连接"
    if event_type == "agent_command_started":
        return f"开始：{_agent_action(event)}"
    if event_type == "agent_command_finished":
        prefix = "失败" if status in {"failed", "failure"} else "完成"
        return f"{prefix}：{_agent_action(event)}"
    if event_type == "shell_command_started":
        cwd = event_details.get("cwd")
        suffix = f"；目录 {cwd}" if cwd else ""
        action = "运行迁移验证命令" if event.get("phase_id") == "phase_5_validation" else "运行命令"
        return f"开始：{action}{suffix}"
    if event_type == "shell_command_finished":
        exit_code = event_details.get("exit_code")
        prefix = "失败" if status in {"failed", "failure"} else "完成"
        suffix = f"；退出码 {exit_code}" if exit_code is not None else ""
        action = "迁移验证命令" if event.get("phase_id") == "phase_5_validation" else "命令"
        return f"{prefix}：{action}{suffix}"
    if event_type == "repair_iteration_started":
        attempt = event_details.get("attempt")
        max_attempts = event_details.get("max_attempts")
        counter = f"第 {attempt}/{max_attempts} 轮" if attempt and max_attempts else "新一轮"
        return f"开始：{counter}运行验证与自动修复"
    if event_type == "repair_iteration_finished":
        attempt = event_details.get("attempt")
        parts = [f"第 {attempt} 轮修复" if attempt else "本轮修复"]
        if event_details.get("error_category"):
            parts.append(f"错误类型 {event_details['error_category']}")
        if event_details.get("repair_role"):
            parts.append(f"修复角色 {event_details['repair_role']}")
        prefix = "失败" if status in {"failed", "failure"} else "完成"
        return f"{prefix}：" + "；".join(parts)
    if event_type == "subphase_started":
        subphase_id = event.get("subphase_id") or "unnamed"
        phase_type = event_details.get("subphase_type")
        suffix = f"（{phase_type}）" if phase_type else ""
        return f"开始：子阶段 {subphase_id}{suffix}"
    if event_type == "subphase_finished":
        subphase_id = event.get("subphase_id") or "unnamed"
        prefix = "失败" if status in {"failed", "failure"} else "完成"
        return f"{prefix}：子阶段 {subphase_id}"
    if event_type == "opencode_tool_started":
        tool = event_details.get("tool") or event.get("message") or "工具"
        return f"开始：调用工具 {tool}"
    if event_type == "opencode_phase_complete":
        return "完成：OpenCode 子阶段"
    return _compact_text(event.get("message") or event_type, MAX_CURRENT_WORK)


def _current_work_text(event: dict[str, Any], sentence: str) -> str:
    event_type = str(event.get("event_type") or "")
    actor = _actor_text(event.get("agent_role"), event.get("session_id"), event_type)
    phase_id = event.get("phase_id")
    phase_title = (
        PHASE_DISPLAY[phase_id].title
        if isinstance(phase_id, str) and phase_id in PHASE_DISPLAY
        else ""
    )
    parts = [sentence]
    if actor and event_type not in {"phase_started", "phase_finished", "workflow_finished"}:
        parts.append(f"执行者：{actor}")
    if phase_title:
        parts.append(f"阶段：{phase_title}")
    return "\n".join(parts)


def _spinner_frame() -> str:
    return SPINNER_FRAMES[int(time.monotonic() * 4) % len(SPINNER_FRAMES)]


def _iteration_text(state: DashboardState) -> str:
    if not state.current_iteration:
        return "尚未进入循环迭代。"
    attempt = state.current_iteration.get("attempt")
    max_attempts = state.current_iteration.get("max_attempts")
    status = _status_text(state.current_iteration.get("status"))
    counter = f"第 {attempt}/{max_attempts} 次" if attempt and max_attempts else "当前迭代"
    details = [f"{counter}｜{status}"]
    error_category = state.current_iteration.get("error_category")
    repair_role = state.current_iteration.get("repair_role")
    if error_category:
        details.append(f"错误类型：{error_category}")
    if repair_role:
        details.append(f"修复角色：{ROLE_TEXT.get(str(repair_role), repair_role)}")
    return "\n".join(details)


def _sessions_text(state: DashboardState) -> str:
    if not state.sessions:
        return "暂无 subsession。"
    lines: list[str] = []
    for session_id, session in list(state.sessions.items())[-MAX_SESSION_LINES:]:
        role = ROLE_TEXT.get(str(session.get("role") or ""), session.get("role") or "未知角色")
        status = _status_text(session.get("status"))
        action = session.get("action") or "等待任务"
        short_id = session_id if len(session_id) <= 20 else session_id[:17] + "..."
        sequence = session.get("command_sequence")
        sequence_text = f" 命令#{sequence}" if sequence else ""
        lines.append(f"{role} [{short_id}]｜{status}{sequence_text}｜{action}")
    return "\n".join(lines)


def _subphases_text(state: DashboardState) -> str:
    if not state.subphases:
        return "暂无子阶段活动。"
    lines: list[str] = []
    for subphase_id, subphase in list(state.subphases.items())[-MAX_SESSION_LINES:]:
        status = _status_text(subphase.get("status"))
        phase_type = subphase.get("subphase_type") or "unknown"
        iteration = subphase.get("iteration")
        iteration_text = f"｜第 {iteration} 次迭代" if iteration else ""
        lines.append(f"{subphase_id}｜{phase_type}｜{status}{iteration_text}")
    return "\n".join(lines)


def _record_error(state: DashboardState, event: dict[str, Any], sentence: str) -> None:
    status = str(event.get("status") or "")
    details = _details(event)
    error = details.get("error")
    if not error and status not in {"failed", "failure"}:
        return
    timestamp = _beijing_time(event.get("timestamp"))
    phase = event.get("subphase_id") or event.get("phase_id") or "unknown"
    message = error or event.get("message") or sentence
    line = _compact_text(f"{timestamp} [{phase}] {message}".strip(), MAX_ERROR_LINE)
    if not state.error_history or state.error_history[-1] != line:
        state.error_history.append(line)
        state.error_history = state.error_history[-MAX_ERROR_LINES:]


def visible_phase_rows(state: DashboardState) -> list[PhaseRow]:
    phase_ids = list(PHASE_DISPLAY)
    current_index = 0
    for index, phase_id in enumerate(phase_ids):
        status = str(state.phases.get(phase_id, {}).get("status", "pending"))
        if status == "running":
            current_index = index
            break
    else:
        for index, phase_id in enumerate(phase_ids):
            status = str(state.phases.get(phase_id, {}).get("status", "pending"))
            if status == "pending":
                current_index = index
                break
        else:
            current_index = max(len(phase_ids) - 1, 0)

    selected_ids = phase_ids[current_index : current_index + 2]
    rows: list[PhaseRow] = []
    for phase_id in selected_ids:
        copy = PHASE_DISPLAY[phase_id]
        status = state.phases.get(phase_id, {}).get("status", "pending")
        rows.append(
            PhaseRow(
                number=PHASE_NUMBER_TEXT.get(phase_id, str(phase_ids.index(phase_id) + 1)),
                phase_id=phase_id,
                title=copy.title,
                status=_status_text(status),
                description=_short_phase_description(copy.description),
            )
        )
    return rows


def _load_events(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], offset
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        new_offset = handle.tell()
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            events.append(raw)
    return events, new_offset


def _apply_event(state: DashboardState, event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    phase_id = event.get("phase_id")
    status = str(event.get("status") or "")
    sentence = _event_sentence(event)
    timestamp = _beijing_time(event.get("timestamp"))
    agent_role = event.get("agent_role")
    session_id = event.get("session_id")
    event_details = _details(event)

    if isinstance(phase_id, str) and phase_id:
        phase = state.phases.setdefault(
            phase_id,
            {
                "title": PHASE_DISPLAY.get(phase_id, None).title
                if phase_id in PHASE_DISPLAY
                else phase_id,
                "description": PHASE_DISPLAY.get(phase_id, None).description
                if phase_id in PHASE_DISPLAY
                else "",
                "status": "pending",
            },
        )
        if event_type == "phase_started":
            phase["status"] = "running"
            state.current_work = _current_work_text(event, sentence)
        elif event_type == "phase_finished":
            phase["status"] = status or "finished"
            state.current_work = _current_work_text(event, sentence)

    if event_type in {
        "agent_command_started",
        "agent_command_finished",
        "shell_command_started",
        "shell_command_finished",
        "session_ready",
        "opencode_tool_started",
        "opencode_phase_complete",
        "repair_iteration_started",
        "repair_iteration_finished",
        "subphase_started",
        "subphase_finished",
    }:
        actor = _actor_text(agent_role, session_id, event_type)
        line = _compact_text(
            f"{timestamp} {actor} {sentence}".strip(),
            MAX_ACTIVITY_LINE,
        )
        state.activity.append(line)
        state.activity = state.activity[-MAX_ACTIVITY_LINES:]
        state.current_work = _current_work_text(event, sentence)

    if event_type == "session_ready" and session_id:
        state.sessions[str(session_id)] = {
            "role": agent_role,
            "status": "ready",
            "action": "等待任务",
        }
    elif event_type in {"agent_command_started", "agent_command_finished"} and session_id:
        session = state.sessions.setdefault(
            str(session_id),
            {"role": agent_role, "status": "ready", "action": "等待任务"},
        )
        session["role"] = agent_role or session.get("role")
        session["status"] = "running" if event_type == "agent_command_started" else status
        session["action"] = _agent_action(event)
        session["command_sequence"] = event_details.get("command_sequence")

    if event_type == "repair_iteration_started":
        state.current_iteration = {
            "attempt": event_details.get("attempt"),
            "max_attempts": event_details.get("max_attempts"),
            "status": "running",
        }
    elif event_type == "repair_iteration_finished":
        state.current_iteration.update(
            {
                "attempt": event_details.get("attempt"),
                "status": status,
                "error_category": event_details.get("error_category"),
                "repair_role": event_details.get("repair_role"),
            }
        )

    if event_type in {"subphase_started", "subphase_finished"}:
        subphase_id = str(event.get("subphase_id") or "unnamed")
        subphase = state.subphases.setdefault(subphase_id, {})
        subphase.update(
            {
                "status": "running" if event_type == "subphase_started" else status,
                "subphase_type": event_details.get("subphase_type"),
                "iteration": event_details.get("iteration"),
            }
        )

    _record_error(state, event, sentence)

    if event_type == "workflow_finished":
        state.status = status or "complete"


def run_dashboard(events_path: str | Path, stop_event: threading.Event) -> None:
    """Render a best-effort real-time dashboard from ``ui_events.jsonl``."""
    try:
        from rich.console import Group
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except Exception:
        return

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
            keyboard_fd = sys.stdin.fileno()
            keyboard_settings = termios.tcgetattr(keyboard_fd)
            keyboard_select = select
            keyboard_termios = termios
            tty.setcbreak(keyboard_fd)
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
            table.add_row(str(row.number), row.title, row.status, row.description)
        activity = "\n".join(state.activity) or "暂无智能体活动。"
        errors = "\n".join(state.error_history) or "暂无历史错误。"
        running_mark = _spinner_frame() if state.status == "running" else ""
        return Group(
            Panel(
                Text(f"SEAM 迁移仪表盘  状态={_status_text(state.status)} {running_mark}"),
                title="运行",
            ),
            Panel(table, title="当前阶段"),
            Panel(_compact_text(state.current_work, MAX_CURRENT_WORK), title="当前工作"),
            Panel(_iteration_text(state), title="当前迭代"),
            Panel(_subphases_text(state), title="子阶段"),
            Panel(_sessions_text(state), title="Subsessions"),
            Panel(activity, title="智能体活动"),
            Panel(errors, title="历史错误"),
            Panel("q: 退出仪表盘视图；迁移和日志继续运行", title="快捷键"),
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


class SeamDashboardApp:
    """Small wrapper used by the runner to launch the live dashboard."""

    def __init__(self, events_path: str | Path, stop_event: threading.Event) -> None:
        self.events_path = Path(events_path)
        self.stop_event = stop_event

    def run(self) -> None:
        try:
            self._run_textual()
        except Exception:
            run_dashboard(self.events_path, self.stop_event)

    def _run_textual(self) -> None:
        from textual.app import App, ComposeResult
        from textual.containers import Container
        from textual.widgets import Footer, Header, Static

        events_path = self.events_path
        stop_event = self.stop_event

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
                running_mark = _spinner_frame() if self.state.status == "running" else ""
                self.query_one("#timeline", Static).update(self._timeline_text())
                self.query_one("#current", Static).update(
                    f"当前工作 {running_mark}\n\n"
                    f"{_compact_text(self.state.current_work, MAX_CURRENT_WORK)}"
                )
                self.query_one("#iteration", Static).update(
                    "当前迭代\n\n" + _iteration_text(self.state)
                )
                self.query_one("#subphases", Static).update(
                    "子阶段\n\n" + _subphases_text(self.state)
                )
                self.query_one("#sessions", Static).update(
                    "Subsessions\n\n" + _sessions_text(self.state)
                )
                self.query_one("#activity", Static).update(
                    "智能体活动\n\n"
                    + ("\n".join(self.state.activity) or "暂无智能体活动。")
                )
                self.query_one("#errors", Static).update(
                    "历史错误\n\n"
                    + ("\n".join(self.state.error_history) or "暂无历史错误。")
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
                    "快捷键\n\nq: 退出仪表盘视图\n"
                    "l/s: 聚焦智能体活动面板\n?: 显示帮助\n"
                    "退出仪表盘后，迁移任务仍会继续运行。"
                )

        _TextualDashboard().run()
