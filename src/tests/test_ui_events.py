from __future__ import annotations

import json
from pathlib import Path

from core.ui_events import (
    PHASE_DISPLAY,
    UIEventSink,
    dashboard_enabled,
    summarize_text,
)
from core.dashboard import DashboardState, _apply_event, visible_phase_rows
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor
from tests.e2e.e2e_observer import TelemetryObserver
from tests.e2e.e2e_test_v3 import build_parser, write_usage_guide


class _FakeSessionManager:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def get_or_create(self, role: str, lifecycle: str = "persistent", **_: object) -> str:
        return f"ses-{role}"

    def send_command(self, session_id: str, command: str, **_: object) -> str:
        self.sent.append((session_id, command))
        return "phase complete"

    def cleanup_all(self) -> int:
        return 1


def test_ui_event_sink_appends_schema_complete_jsonl(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-1")

    sink.emit(
        "phase_started",
        phase_id="phase_0_env_detect",
        agent_role="main_engineer",
        session_id="ses-1",
        status="running",
        message="Detecting environment",
        details={"platform": "ppu"},
        artifact_path="artifacts/phase0.json",
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "schema_version",
        "timestamp",
        "run_id",
        "event_type",
        "phase_id",
        "subphase_id",
        "agent_role",
        "session_id",
        "status",
        "message",
        "details",
        "artifact_path",
    }
    assert record["schema_version"] == "1.0"
    assert record["run_id"] == "run-1"
    assert record["event_type"] == "phase_started"
    assert record["details"] == {"platform": "ppu"}


def test_ui_event_sink_is_non_critical_when_path_is_unwritable(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path / "missing" / "events", run_id="run-1", create_dir=False)

    sink.emit("runner_notice", message="this should not raise")


def test_phase_display_copy_uses_user_facing_names() -> None:
    assert PHASE_DISPLAY["phase_0_env_detect"].title == "环境检测"
    assert PHASE_DISPLAY["phase_5_validation"].title == "运行验证与自动修复"
    assert "真实" in PHASE_DISPLAY["phase_5_validation"].description


def test_dashboard_enabled_auto_respects_tty_and_ci() -> None:
    assert dashboard_enabled("auto", is_tty=True, environ={}) is True
    assert dashboard_enabled("auto", is_tty=False, environ={}) is False
    assert dashboard_enabled("auto", is_tty=True, environ={"CI": "1"}) is False
    assert dashboard_enabled("on", is_tty=False, environ={"CI": "1"}) is True
    assert dashboard_enabled("off", is_tty=True, environ={}) is False


def test_e2e_v3_parser_accepts_dashboard_flags() -> None:
    parser = build_parser()

    args = parser.parse_args(["--dashboard-mode", "off", "--dashboard", "--no-dashboard"])

    assert args.dashboard_mode == "off"
    assert args.dashboard is True
    assert args.no_dashboard is True


def test_summarize_text_redacts_and_truncates_sensitive_values() -> None:
    text = "OPENAI_API_KEY=sk-abc12345678901234567890 " + ("x" * 200)

    summary = summarize_text(text, limit=80)

    assert "sk-abc" not in summary
    assert "OPENAI_API_KEY=<REDACTED>" in summary
    assert len(summary) <= 83


def test_dashboard_event_application_keeps_long_agent_prompts_compact() -> None:
    state = DashboardState()
    long_prompt = " ".join(["dependency_fixer"] * 80)

    for index in range(12):
        _apply_event(
            state,
            {
                "event_type": "agent_command_started",
                "timestamp": f"2026-07-01T09:13:{index:02d}+00:00",
                "agent_role": "dependency_fixer",
                "status": "running",
                "message": long_prompt,
            },
        )

    assert len(state.current_work) <= 140
    assert len(state.activity) <= 8
    assert all(len(line) <= 140 for line in state.activity)


def test_dashboard_translates_workflow_selector_prompt_to_event_language() -> None:
    state = DashboardState()

    _apply_event(
        state,
        {
            "event_type": "agent_command_started",
            "timestamp": "2026-07-01T09:06:53+00:00",
            "agent_role": "workflow_selector",
            "session_id": "ses-workflow_selector",
            "status": "running",
            "message": "# Workflow Selection You are selecting the best SEAM migration workflow",
            "details": {
                "command_preview": "# Workflow Selection You are selecting the best SEAM migration workflow"
            },
        },
    )

    activity_text = "\n".join(state.activity)
    assert "开始：选择迁移工作流" in activity_text
    assert "# Workflow Selection" not in activity_text
    assert "# Workflow Selection" not in state.current_work


def test_dashboard_translates_dependency_fixer_prompt_to_event_language() -> None:
    state = DashboardState()

    _apply_event(
        state,
        {
            "event_type": "agent_command_started",
            "timestamp": "2026-07-01T09:13:39+00:00",
            "phase_id": "phase_5_validation",
            "agent_role": "dependency_fixer",
            "session_id": "ses-dependency_fixer",
            "status": "running",
            "message": "你是dependency_fixer，只处理环境、包、导入、版本、安装和运行依赖问题；不要处理算子。",
        },
    )

    activity_text = "\n".join(state.activity)
    assert "开始：修复依赖和环境问题" in activity_text
    assert "你是dependency_fixer" not in activity_text
    assert "修复依赖和环境问题" in state.current_work


def test_dashboard_tracks_iteration_subsession_subphase_and_error_history() -> None:
    state = DashboardState()

    events = [
        {
            "event_type": "session_ready",
            "timestamp": "2026-07-01T09:13:38+00:00",
            "phase_id": "phase_5_validation",
            "agent_role": "runtime_analyzer",
            "session_id": "ses-runtime-analyzer",
            "status": "ready",
        },
        {
            "event_type": "repair_iteration_started",
            "timestamp": "2026-07-01T09:13:39+00:00",
            "phase_id": "phase_5_validation",
            "status": "running",
            "details": {"attempt": 2, "max_attempts": 8},
        },
        {
            "event_type": "subphase_started",
            "timestamp": "2026-07-01T09:13:40+00:00",
            "phase_id": "phase_5_validation",
            "subphase_id": "analyze_error",
            "status": "running",
            "details": {"subphase_type": "llm", "iteration": 2},
        },
        {
            "event_type": "agent_command_started",
            "timestamp": "2026-07-01T09:13:41+00:00",
            "phase_id": "phase_5_validation",
            "agent_role": "runtime_analyzer",
            "session_id": "ses-runtime-analyzer",
            "status": "running",
            "details": {"command_sequence": 4},
        },
        {
            "event_type": "subphase_finished",
            "timestamp": "2026-07-01T09:13:42+00:00",
            "phase_id": "phase_5_validation",
            "subphase_id": "analyze_error",
            "status": "failure",
            "message": "analysis failed",
            "details": {
                "subphase_type": "llm",
                "iteration": 2,
                "error": "Insufficient Balance",
            },
        },
    ]
    for event in events:
        _apply_event(state, event)

    assert state.current_iteration["attempt"] == 2
    assert state.sessions["ses-runtime-analyzer"]["command_sequence"] == 4
    assert state.subphases["analyze_error"]["iteration"] == 2
    assert state.subphases["analyze_error"]["status"] == "failure"
    assert any("Insufficient Balance" in error for error in state.error_history)


def test_visible_phase_rows_only_show_current_and_next_with_numbers() -> None:
    state = DashboardState()
    for phase_id in (
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_1_5_constraint_summary",
        "phase_2_venv_create",
        "phase_3_entry_script",
        "phase_35_static_validate",
        "phase_4_rule_migration",
    ):
        _apply_event(
            state,
            {
                "event_type": "phase_finished",
                "phase_id": phase_id,
                "status": "success",
                "message": "done",
            },
        )
    _apply_event(
        state,
        {
            "event_type": "phase_started",
            "phase_id": "phase_5_validation",
            "status": "running",
            "message": "validating",
        },
    )

    rows = visible_phase_rows(state)

    assert len(rows) == 2
    assert rows[0].number == "5"
    assert rows[0].title == "运行验证与自动修复"
    assert rows[0].status == "运行中"
    assert rows[1].number == "6"
    assert rows[1].title == "报告与使用说明"
    assert rows[1].status == "待执行"


def test_telemetry_observer_emits_session_and_command_ui_events(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-1")
    observer = TelemetryObserver(_FakeSessionManager(), tmp_path, ui_event_sink=sink)
    observer.set_active_phase("phase_1_project_analysis")

    session_id = observer.get_or_create("main_engineer", lifecycle="persistent")
    response = observer.send_command(session_id, "inspect project", timeout=7)

    assert response == "phase complete"
    records = [
        json.loads(line)
        for line in (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event_types = [record["event_type"] for record in records]
    assert event_types == [
        "session_ready",
        "agent_command_started",
        "agent_command_finished",
    ]
    assert records[0]["agent_role"] == "main_engineer"
    assert records[1]["phase_id"] == "phase_1_project_analysis"
    assert records[1]["status"] == "running"
    assert records[2]["status"] == "passed"
    assert records[2]["details"]["response_preview"] == "phase complete"


def test_workflow_executor_emits_phase_and_shell_ui_events(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="unused",
        output_schema={},
        type="shell",
        transitions={"on_success": "complete"},
    )
    setattr(phase, "command", "python -c 'print(\"ok\")'")
    setattr(phase, "cwd", str(tmp_path))
    workflow = WorkflowDefinition(
        name="ui-shell",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
    )
    artifact_store = _MemoryArtifactStore()
    sink = UIEventSink(tmp_path, run_id="run-1")
    executor = WorkflowExecutor(
        workflow,
        _FakeSessionManager(),
        artifact_store,
        object(),
        object(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        ui_event_sink=sink,
    )

    executor.execute({"PROJECT_DIR": str(tmp_path)})

    records = [
        json.loads(line)
        for line in (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event_types = [record["event_type"] for record in records]
    assert event_types == [
        "phase_started",
        "shell_command_started",
        "shell_command_finished",
        "phase_finished",
        "workflow_finished",
    ]
    assert records[0]["phase_id"] == "run_entry_script"
    assert records[1]["subphase_id"] == "run_entry_script"
    assert records[2]["details"]["exit_code"] == 0
    assert records[3]["status"] == "success"


def test_usage_guide_contains_project_command_and_debug_paths(tmp_path: Path) -> None:
    usage_path = write_usage_guide(
        tmp_path,
        entry_script="python test_data_and_scripts/run_e2e.py",
        overall_status="PASS",
        output_dir=tmp_path / "reports",
    )

    content = Path(usage_path).read_text(encoding="utf-8")
    assert "E2E TEST PASSED" in content
    assert f"cd {tmp_path}" in content
    assert "python test_data_and_scripts/run_e2e.py" in content
    assert ".sm-artifacts/" in content


class _MemoryArtifactStore:
    def save_phase_output(self, phase_id: str, output: dict[str, object]) -> str:
        return phase_id

    def mark_validated(self, phase_id: str, output: dict[str, object]) -> str:
        return phase_id

    def write_journal(self, entry: dict[str, object]) -> str:
        return "journal"

    def save_shell_attempt_artifacts(self, **_: object) -> dict[str, object]:
        return {}
