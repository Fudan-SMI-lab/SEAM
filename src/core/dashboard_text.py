"""Terminal-cell-width compaction and dynamic-text builders for the dashboard.

All dynamic strings rendered by the Rich and Textual dashboards are produced
here as plain ``str``. Two rendering bugs are fixed at the source:

* Bracketed labels like ``[phase_id]`` or ``[session_id]`` are kept as literal
  text. The renderers receive them already wrapped in literal ``Text``, so they
  are never re-parsed as console markup.
* Compaction is by terminal cell width, not codepoint count. East Asian W/F
  glyphs count as two cells, combining marks as zero, everything else as one,
  so CJK content no longer overflows narrow panel borders.

The module depends only on the standard library plus the intra-package
``core.ui_events.PHASE_DISPLAY`` table; it never imports ``rich`` or
``textual``, so importing it has no optional-dependency cost.
"""

from __future__ import annotations

import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from core.dashboard_tables import (
    PHASE_ACTION_TEXT,
    ROLE_ACTION_TEXT,
    ROLE_TEXT,
    STATUS_TEXT,
)
from core.ui_events import PHASE_DISPLAY


class _DashboardStateLike(Protocol):
    """Structural shape of the dashboard state read by the text builders.

    The real ``core.dashboard_state.DashboardState`` dataclass satisfies this
    protocol; declaring it here keeps the renderer module free to define the
    concrete dataclass without creating an import cycle, and lets the type
    checker verify the attribute access without a string forward reference.
    """

    current_iteration: dict[str, Any]
    sessions: dict[str, dict[str, Any]]
    subphases: dict[str, dict[str, Any]]

MAX_CURRENT_WORK = 140
MAX_ACTIVITY_LINE = 140
MAX_ACTIVITY_LINES = 8
MAX_ERROR_LINE = 180
MAX_ERROR_LINES = 12
MAX_SESSION_LINES = 6
SPINNER_FRAMES = ("|", "/", "-", "\\")
ELLIPSIS = "..."


def _char_cell_width(ch: str) -> int:
    if unicodedata.combining(ch) != 0:
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def cell_width(text: str) -> int:
    return sum(_char_cell_width(ch) for ch in text)


def compact_cells(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if cell_width(text) <= limit:
        return text
    budget = limit - len(ELLIPSIS)
    if budget <= 0:
        return ELLIPSIS[: max(limit, 0)]
    kept: list[str] = []
    used = 0
    for ch in text:
        width = _char_cell_width(ch)
        if used + width > budget:
            break
        kept.append(ch)
        used += width
    return "".join(kept).rstrip() + ELLIPSIS


def beijing_time(timestamp: object) -> str:
    raw = str(timestamp or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    except ValueError:
        return raw[11:19]


def short_phase_description(description: str) -> str:
    return compact_cells(description, 46)


def status_text(status: object) -> str:
    return STATUS_TEXT.get(str(status or "pending"), str(status or "待执行"))


def _details(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details")
    return details if isinstance(details, dict) else {}


def actor_text(agent_role: object, session_id: object, event_type: str) -> str:
    actor = str(agent_role or session_id or event_type)
    return ROLE_TEXT.get(actor, actor)


def phase_action(phase_id: object) -> str:
    phase_key = str(phase_id or "")
    if phase_key in PHASE_ACTION_TEXT:
        return PHASE_ACTION_TEXT[phase_key]
    if phase_key in PHASE_DISPLAY:
        return PHASE_DISPLAY[phase_key].description
    return "执行迁移步骤"


def agent_action(event: dict[str, Any]) -> str:
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
    return phase_action(event.get("phase_id"))


def event_sentence(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    status = str(event.get("status") or "")
    event_details = _details(event)

    if event_type == "phase_started":
        return f"开始：{phase_action(event.get('phase_id'))}"
    if event_type == "phase_finished":
        return f"{status_text(status)}：{phase_action(event.get('phase_id'))}"
    if event_type == "session_ready":
        role = actor_text(event.get("agent_role"), event.get("session_id"), event_type)
        return f"就绪：{role} 已连接"
    if event_type == "agent_command_started":
        return f"开始：{agent_action(event)}"
    if event_type == "agent_command_finished":
        prefix = "失败" if status in {"failed", "failure"} else "完成"
        return f"{prefix}：{agent_action(event)}"
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
    return compact_cells(event.get("message") or event_type, MAX_CURRENT_WORK)


def current_work_text(event: dict[str, Any], sentence: str) -> str:
    event_type = str(event.get("event_type") or "")
    actor = actor_text(event.get("agent_role"), event.get("session_id"), event_type)
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


def iteration_text(state: _DashboardStateLike) -> str:
    if not state.current_iteration:
        return "尚未进入循环迭代。"
    attempt = state.current_iteration.get("attempt")
    max_attempts = state.current_iteration.get("max_attempts")
    status = status_text(state.current_iteration.get("status"))
    counter = f"第 {attempt}/{max_attempts} 次" if attempt and max_attempts else "当前迭代"
    details = [f"{counter}｜{status}"]
    error_category = state.current_iteration.get("error_category")
    repair_role = state.current_iteration.get("repair_role")
    if error_category:
        details.append(f"错误类型：{error_category}")
    if repair_role:
        details.append(f"修复角色：{ROLE_TEXT.get(str(repair_role), repair_role)}")
    return "\n".join(details)


def sessions_text(state: _DashboardStateLike) -> str:
    if not state.sessions:
        return "暂无 subsession。"
    lines: list[str] = []
    for session_id, session in list(state.sessions.items())[-MAX_SESSION_LINES:]:
        role = ROLE_TEXT.get(str(session.get("role") or ""), session.get("role") or "未知角色")
        status = status_text(session.get("status"))
        action = session.get("action") or "等待任务"
        short_id = session_id if len(session_id) <= 20 else session_id[:17] + "..."
        sequence = session.get("command_sequence")
        sequence_text = f" 命令#{sequence}" if sequence else ""
        lines.append(f"{role} [{short_id}]｜{status}{sequence_text}｜{action}")
    return "\n".join(lines)


def subphases_text(state: _DashboardStateLike) -> str:
    if not state.subphases:
        return "暂无子阶段活动。"
    lines: list[str] = []
    for subphase_id, subphase in list(state.subphases.items())[-MAX_SESSION_LINES:]:
        status = status_text(subphase.get("status"))
        phase_type = subphase.get("subphase_type") or "unknown"
        iteration = subphase.get("iteration")
        iteration_line = f"｜第 {iteration} 次迭代" if iteration else ""
        lines.append(f"{subphase_id}｜{phase_type}｜{status}{iteration_line}")
    return "\n".join(lines)


def spinner_frame() -> str:
    return SPINNER_FRAMES[int(time.monotonic() * 4) % len(SPINNER_FRAMES)]