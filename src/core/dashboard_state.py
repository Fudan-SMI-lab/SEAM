"""Dashboard state machine: event application and visible-phase selection.

Holds the live dashboard state (``DashboardState``), the row view model
(``PhaseRow``), the JSONL event tail reader seam (``_load_events``), the event
application state machine (``_apply_event``), and the two-row phase window
selector (``visible_phase_rows``). This module depends only on the standard
library plus the intra-package lookup tables and text builders; it never
imports ``rich`` or ``textual``, so importing it has no optional-dependency
cost.

Extracted from ``core.dashboard`` so the event state machine and the two
renderer entry points can evolve independently without any single module
exceeding the reviewer working-memory budget. Behavior is unchanged: every
line is a verbatim move of the prior cohesive unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.dashboard_tables import PHASE_NUMBER_TEXT
from core.dashboard_text import (
    MAX_ACTIVITY_LINE,
    MAX_ACTIVITY_LINES,
    MAX_ERROR_LINE,
    MAX_ERROR_LINES,
    actor_text,
    agent_action,
    beijing_time,
    compact_cells,
    current_work_text,
    event_sentence,
    short_phase_description,
    status_text,
)
from core.ui_event_reader import load_ui_events
from core.ui_events import PHASE_DISPLAY


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


def _record_error(state: DashboardState, event: dict[str, Any], sentence: str) -> None:
    status = str(event.get("status") or "")
    raw_details = event.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    error = details.get("error")
    if not error and status not in {"failed", "failure"}:
        return
    timestamp = beijing_time(event.get("timestamp"))
    phase = event.get("subphase_id") or event.get("phase_id") or "unknown"
    message = error or event.get("message") or sentence
    line = compact_cells(f"{timestamp} [{phase}] {message}".strip(), MAX_ERROR_LINE)
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
                status=status_text(status),
                description=short_phase_description(copy.description),
            )
        )
    return rows


def _load_events(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read complete JSONL records from ``path`` starting at byte ``offset``.

    Delegates to :func:`core.ui_event_reader.load_ui_events`, which withholds
    incomplete trailing bytes for the next poll, skips malformed
    newline-terminated records once, and recovers from truncation. Kept as the
    seam used by the Rich and Textual polling loops.
    """
    return load_ui_events(path, offset)


def _apply_event(state: DashboardState, event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    phase_id = event.get("phase_id")
    status = str(event.get("status") or "")
    sentence = event_sentence(event)
    timestamp = beijing_time(event.get("timestamp"))
    agent_role = event.get("agent_role")
    session_id = event.get("session_id")
    raw_details = event.get("details")
    event_details = raw_details if isinstance(raw_details, dict) else {}

    if isinstance(phase_id, str) and phase_id:
        display = PHASE_DISPLAY.get(phase_id)
        phase = state.phases.setdefault(
            phase_id,
            {
                "title": display.title if display is not None else phase_id,
                "description": display.description if display is not None else "",
                "status": "pending",
            },
        )
        if event_type == "phase_started":
            phase["status"] = "running"
            state.current_work = current_work_text(event, sentence)
        elif event_type == "phase_finished":
            phase["status"] = status or "finished"
            state.current_work = current_work_text(event, sentence)

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
        actor = actor_text(agent_role, session_id, event_type)
        line = compact_cells(
            f"{timestamp} {actor} {sentence}".strip(),
            MAX_ACTIVITY_LINE,
        )
        state.activity.append(line)
        state.activity = state.activity[-MAX_ACTIVITY_LINES:]
        state.current_work = current_work_text(event, sentence)

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
        session["action"] = agent_action(event)
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
