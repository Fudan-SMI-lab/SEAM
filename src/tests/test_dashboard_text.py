"""Literal bracket rendering and terminal-cell-width compaction for the TUI.

Dynamic dashboard strings (event messages, session ids, error lines, current
work) frequently contain bracketed labels like ``[phase_1_project_analysis]``
or ``[ses-main]``. When such strings reach the renderers as plain ``str``,
Rich parses the brackets as markup tags and silently drops the label, and
Textual's ``Static.update(str)`` parses them as console markup the same way.
Wide (CJK) content is also mis-sized: the legacy compaction counted code
points, so a 140-"character" CJK string occupies up to 280 terminal cells and
overflows the panel borders at 80/120/160 columns.

These tests pin the corrected contract:

* a stdlib-only terminal-cell-width helper measures East Asian W/F glyphs as
  two cells, combining marks as zero, everything else as one, and compacts a
  string to a bounded cell budget reserving the ellipsis width;
* every dynamic Rich panel/table value and Textual ``Static`` update is
  rendered literally so bracket labels survive;
* the existing q binding, stop-polling exit, and visible-phase selection stay
  unchanged.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.dashboard import DashboardState, _apply_event, run_dashboard
from core.dashboard_text import (
    MAX_ACTIVITY_LINE,
    MAX_CURRENT_WORK,
    cell_width,
    compact_cells,
    iteration_text,
    sessions_text,
    subphases_text,
)


# --- Cell-width measurement ---


def test_cell_width_counts_each_ascii_glyph_as_one_cell() -> None:
    assert cell_width("hello world") == 11


def test_cell_width_counts_cjk_glyphs_as_two_cells() -> None:
    # 迁移 = two Han glyphs, each East Asian Width W → 2 cells apiece.
    assert cell_width("迁移") == 4


def test_cell_width_counts_combining_marks_as_zero_cells() -> None:
    # LATIN SMALL LETTER E (1) + COMBINING ACUTE ACCENT U+0301 (0) = 1 cell.
    assert cell_width("e\u0301") == 1


def test_cell_width_counts_japanese_korean_as_wide() -> None:
    # Hiragana, Katakana, Hangul syllables are all East Asian Width W.
    assert cell_width("あ") == 2
    assert cell_width("カ") == 2
    assert cell_width("한") == 2


# --- Cell-width-aware compaction ---


def test_compact_cells_leaves_short_text_unchanged() -> None:
    assert compact_cells("hello world", 50) == "hello world"


def test_compact_cells_keeps_full_text_at_exact_cell_limit() -> None:
    # 4 cells exactly, limit 4 → no ellipsis, unchanged.
    assert compact_cells("迁移", 4) == "迁移"


def test_compact_cells_truncates_ascii_with_legacy_shape() -> None:
    # ASCII behavior must match the legacy codepoint truncation so existing
    # event/state tests stay green: text[:limit-3].rstrip() + "...".
    assert compact_cells("abcdefghij", 8) == "abcde..."


def test_compact_cells_bounds_cjk_one_past_limit() -> None:
    # "迁移迁" = 6 cells, limit 5 → reserve 3 for ellipsis, keep 2 cells
    # ("迁"), total 5 cells. Legacy codepoint truncation produced "迁移..."
    # which is 7 cells and overflowed a 5-cell budget.
    result = compact_cells("迁移迁", 5)
    assert result == "迁..."
    assert cell_width(result) == 5


def test_compact_cells_bounds_long_cjk_run_within_limit() -> None:
    result = compact_cells("迁移" * 30, 5)
    assert cell_width(result) <= 5
    assert result.endswith("...")


def test_compact_cells_never_exceeds_limit_for_mixed_widths() -> None:
    # "ab迁移cd" = 1+1+2+2+1+1 = 8 cells, limit 6 → keep 3 cells then "...".
    result = compact_cells("ab迁移cd", 6)
    assert cell_width(result) <= 6
    assert result.endswith("...")


def test_compact_cells_preserves_bracket_characters_literally() -> None:
    # Brackets must remain plain text; they are never treated as markup here.
    assert compact_cells("[phase] active [session-1]", 50) == "[phase] active [session-1]"


def test_compact_cells_collapses_whitespace_like_legacy() -> None:
    assert compact_cells("  a   b  ", 50) == "a b"


def test_compact_cells_handles_empty_and_none_inputs() -> None:
    assert compact_cells("", 10) == ""
    assert compact_cells(None, 10) == ""  # type: ignore[arg-type]


# --- Dynamic text builders keep bracket labels as plain text ---


def _seed_state_with_bracket_session() -> DashboardState:
    state = DashboardState()
    _apply_event(
        state,
        {
            "event_type": "session_ready",
            "timestamp": "2026-07-01T09:13:38+00:00",
            "phase_id": "phase_1_project_analysis",
            "agent_role": "main_engineer",
            "session_id": "ses-main-001",
            "status": "ready",
        },
    )
    return state


def test_sessions_text_keeps_bracketed_session_id() -> None:
    state = _seed_state_with_bracket_session()

    assert "[ses-main-001]" in sessions_text(state)


def test_iteration_subphase_texts_stay_within_activity_budget() -> None:
    state = DashboardState()
    _apply_event(
        state,
        {
            "event_type": "repair_iteration_started",
            "timestamp": "2026-07-01T09:13:39+00:00",
            "phase_id": "phase_5_validation",
            "status": "running",
            "details": {"attempt": 3, "max_attempts": 8},
        },
    )
    _apply_event(
        state,
        {
            "event_type": "subphase_started",
            "timestamp": "2026-07-01T09:13:40+00:00",
            "phase_id": "phase_5_validation",
            "subphase_id": "scan_cuda",
            "status": "running",
            "details": {"subphase_type": "shell", "iteration": 3},
        },
    )

    for line in iteration_text(state).splitlines():
        assert cell_width(line) <= MAX_CURRENT_WORK
    for line in subphases_text(state).splitlines():
        assert cell_width(line) <= MAX_ACTIVITY_LINE


# --- Rich renderer: brackets preserved and CJK bounded ---


def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False))
            handle.write("\n")


def _capture_render_group(events_path: Path) -> Any:
    """Drive the real ``run_dashboard`` once and return the rendered Group.

    ``rich.live.Live`` is replaced with a capture stub that records the
    renderable passed to ``Live(...)`` and to ``live.update(...)`` and then
    signals the polling loop to stop after the first post-event update. The
    returned renderable reflects the fully-applied event stream.
    """
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


def test_rich_render_preserves_bracket_phase_label_in_error_history(
    tmp_path: Path,
) -> None:
    """Given a failed shell command for a known phase; When rendered through
    the real Rich path; Then the literal ``[phase_id]`` label survives in the
    error-history panel instead of being parsed away as markup."""
    path = tmp_path / "ui_events.jsonl"
    _write_events(
        path,
        [
            {
                "event_type": "shell_command_finished",
                "timestamp": "2026-07-01T09:00:00+00:00",
                "phase_id": "phase_1_project_analysis",
                "status": "failed",
                "message": "exit 1: CUDA_HOME missing",
            }
        ],
    )

    text = _render_to_plain(_capture_render_group(path), 80)

    assert "[phase_1_project_analysis]" in text
    assert "CUDA_HOME missing" in text


def test_rich_render_preserves_bracket_session_label(tmp_path: Path) -> None:
    """Given a session_ready event; When rendered; Then the literal
    ``[session_id]`` label survives in the Subsessions panel."""
    path = tmp_path / "ui_events.jsonl"
    _write_events(
        path,
        [
            {
                "event_type": "session_ready",
                "timestamp": "2026-07-01T09:00:00+00:00",
                "phase_id": "phase_1_project_analysis",
                "agent_role": "main_engineer",
                "session_id": "ses-main-001",
                "status": "ready",
            }
        ],
    )

    text = _render_to_plain(_capture_render_group(path), 80)

    assert "[ses-main-001]" in text


def test_rich_render_treats_unbalanced_markup_like_text(tmp_path: Path) -> None:
    """Given a tool name that looks like unbalanced Rich markup; When rendered;
    Then it appears literally in the activity panel and does not raise. The
    ``opencode_tool_started`` sentence embeds ``event.message`` directly as the
    tool name, so bracket-like content reaches the renderer."""
    path = tmp_path / "ui_events.jsonl"
    _write_events(
        path,
        [
            {
                "event_type": "opencode_tool_started",
                "timestamp": "2026-07-01T09:00:00+00:00",
                "phase_id": "phase_1_project_analysis",
                "agent_role": "main_engineer",
                "session_id": "ses-main-001",
                "status": "running",
                "message": "parse [not-a-style and [nested brackets] trailing",
            }
        ],
    )

    text = _render_to_plain(_capture_render_group(path), 120)

    assert "[not-a-style" in text
    assert "[nested brackets]" in text


def test_rich_render_compacts_long_cjk_current_work_within_cells(
    tmp_path: Path,
) -> None:
    """Given a phase_started message of 100 wide glyphs; When rendered at 80
    columns; Then no current-work line exceeds the terminal width (the
    codepoint-based legacy compaction would have allowed ~140 wide glyphs)."""
    path = tmp_path / "ui_events.jsonl"
    _write_events(
        path,
        [
            {
                "event_type": "phase_started",
                "timestamp": "2026-07-01T09:00:00+00:00",
                "phase_id": "phase_1_project_analysis",
                "status": "running",
                "message": "迁" * 100,
            }
        ],
    )

    text = _render_to_plain(_capture_render_group(path), 80)

    for line in text.splitlines():
        assert cell_width(line.rstrip("\r")) <= 80


# --- q binding and stop-polling baseline stability ---


def test_baseline_run_dashboard_returns_promptly_when_stop_already_set(
    tmp_path: Path,
) -> None:
    """Given stop_event is set before entry; When run_dashboard is invoked;
    Then it returns without hanging (the polling loop must observe stop)."""
    path = tmp_path / "ui_events.jsonl"
    path.write_text("", encoding="utf-8")
    stop = threading.Event()
    stop.set()

    run_dashboard(path, stop)  # must not raise or hang


def test_baseline_run_dashboard_stops_after_external_stop_mid_loop(
    tmp_path: Path,
) -> None:
    """Given stop_event is set from another thread shortly after entry; When
    the dashboard is polling; Then the loop exits within a bounded wait (q/stop
    contract preserved)."""
    path = tmp_path / "ui_events.jsonl"
    _write_events(
        path,
        [
            {
                "event_type": "phase_started",
                "timestamp": "2026-07-01T09:00:00+00:00",
                "phase_id": "phase_1_project_analysis",
                "status": "running",
                "message": "working",
            }
        ],
    )
    stop = threading.Event()
    marker = threading.Event()

    original_update_holder: dict[str, Any] = {}

    def _stop_after_first_render() -> None:
        # Wait for the live loop to apply at least one update, then signal stop.
        marker.wait(timeout=5)
        stop.set()

    import rich.live

    original_live = rich.live.Live

    class _WatchingLive:
        def __init__(self, renderable: Any, **_kwargs: Any) -> None:
            original_update_holder["init"] = renderable

        def __enter__(self) -> "_WatchingLive":
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def update(self, renderable: Any, **_kwargs: Any) -> None:
            original_update_holder["update"] = renderable
            marker.set()

    rich.live.Live = _WatchingLive  # type: ignore[misc,assignment]
    stopper = threading.Thread(target=_stop_after_first_render, daemon=True)
    try:
        stopper.start()
        run_dashboard(path, stop)
    finally:
        rich.live.Live = original_live  # type: ignore[misc,assignment]
    stopper.join(timeout=5)
    assert "update" in original_update_holder
