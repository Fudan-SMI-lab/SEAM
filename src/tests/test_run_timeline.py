"""Bug #13 regression tests: run timeline facts at phase boundaries.

Reproduces the defect where Phase 6 migration reports show ``—`` for time
fields because the executor never records wall-clock start/end times and
never wires ``TelemetryBridge.on_phase_start/on_phase_end`` into the main
loop.  Locks the new behaviour:

- ``TelemetryBridge.phases`` is populated with started_at/ended_at facts.
- ``phase_results[phase.id]`` entries carry ``started_at``/``ended_at``.
- ``run_timeline.json`` is atomically persisted to ``output_dir`` after
  each phase (including failed/interrupted phases).
- Phase 6 prompt context receives the ``run_timeline`` facts.
- The 3 Phase 6 report prompts declare a time-facts contract.
- ``validate_reports`` rejects ``—`` / missing time facts; the schema
  declares ``run_timeline`` as an optional property.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.telemetry_bridge import TelemetryBridge
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor
from validators.validate_reports import validate as validate_reports

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    return d


def noop_workflow():
    return WorkflowDefinition(
        name="timeline",
        version="1.0",
        phases=[
            PhaseDefinition(
                id="phase_a",
                name="A",
                prompt_template="unused.md",
                output_schema={},
                type="builtin",
                params={"operation": "noop"},
                transitions={"on_success": "phase_b"},
            ),
            PhaseDefinition(
                id="phase_b",
                name="B",
                prompt_template="unused.md",
                output_schema={},
                type="builtin",
                params={"operation": "noop"},
                transitions={"on_success": "complete"},
            ),
        ],
        terminals=["complete", "failed"],
        agents={"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}},
    )


def make_executor(
    workflow,
    temp_dir,
    telemetry_bridge=None,
    session_mgr=None,
    prompt_loader=None,
):
    executor = WorkflowExecutor(
        workflow,
        session_mgr or MagicMock(),
        MagicMock(),
        prompt_loader or MagicMock(),
        MagicMock(),
        project_dir=temp_dir,
        output_dir=temp_dir,
        telemetry_bridge=telemetry_bridge,
    )
    executor.hook_manager = MagicMock()
    return executor


class TestRunTimeline:
    def test_telemetry_bridge_phases_recorded_after_execute(self, temp_dir):
        bridge = TelemetryBridge(str(Path(temp_dir) / "telemetry"))
        executor = make_executor(noop_workflow(), temp_dir, telemetry_bridge=bridge)

        result = executor.execute({"PROJECT_DIR": temp_dir, "USER_CONSTRAINTS": ""})

        assert result["status"] == "complete"
        metrics = bridge.save_metrics()
        payload = json.loads(
            Path(metrics["telemetry_json"]).read_text(encoding="utf-8")
        )
        assert len(payload["phases"]) == 2
        by_id = {p["phase_id"]: p for p in payload["phases"]}
        for pid in ("phase_a", "phase_b"):
            entry = by_id[pid]
            assert entry["started_at"]
            assert entry["ended_at"]
            assert entry["duration_seconds"] >= 0
            assert entry["status"] == "success"

    def test_phase_results_contain_start_and_end_timestamps(self, temp_dir):
        executor = make_executor(noop_workflow(), temp_dir)

        result = executor.execute({"PROJECT_DIR": temp_dir, "USER_CONSTRAINTS": ""})

        assert result["status"] == "complete"
        for pid in ("phase_a", "phase_b"):
            entry = executor.phase_results[pid]
            assert entry["status"] == "success"
            assert "started_at" in entry
            assert "ended_at" in entry
            started = datetime.fromisoformat(entry["started_at"])
            ended = datetime.fromisoformat(entry["ended_at"])
            assert ended >= started
            assert entry["started_at"].endswith("+00:00")

    def test_run_timeline_json_persisted_after_each_phase(self, temp_dir):
        executor = make_executor(noop_workflow(), temp_dir)

        result = executor.execute({"PROJECT_DIR": temp_dir, "USER_CONSTRAINTS": ""})

        assert result["status"] == "complete"
        timeline_path = Path(temp_dir) / "run_timeline.json"
        assert timeline_path.exists()
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        assert timeline["run_started_at"]
        assert timeline["run_ended_at"]
        phases = timeline["phases"]
        assert len(phases) == 2
        assert {p["phase_id"] for p in phases} == {"phase_a", "phase_b"}
        for p in phases:
            assert p["status"] == "success"
            assert p["started_at"] and p["ended_at"]
            assert p["duration_seconds"] >= 0

    def test_run_timeline_records_failed_phase_with_ended_at(self, temp_dir):
        workflow = WorkflowDefinition(
            name="timeline-fail",
            version="1.0",
            phases=[
                PhaseDefinition(
                    id="phase_fail",
                    name="F",
                    prompt_template="unused.md",
                    output_schema={},
                    type="llm",
                    agent="main_engineer",
                    validator=None,
                    transitions={"on_success": "complete"},
                ),
            ],
            terminals=["complete", "failed"],
            agents={
                "main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}
            },
        )
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session:fail"
        session_mgr.send_command.side_effect = RuntimeError("boom")
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.return_value = "prompt"
        executor = make_executor(
            workflow, temp_dir, session_mgr=session_mgr, prompt_loader=prompt_loader
        )

        result = executor.execute({"PROJECT_DIR": temp_dir, "USER_CONSTRAINTS": ""})

        # execute() terminates the loop via plan_next_phase on non-success/skipped
        # status; the bug contract is the time facts in phase_results/timeline.
        assert result["status"] == "complete"
        assert executor.phase_results["phase_fail"]["status"] == "failure"
        timeline_path = Path(temp_dir) / "run_timeline.json"
        assert timeline_path.exists()
        phases = json.loads(timeline_path.read_text(encoding="utf-8"))["phases"]
        assert len(phases) == 1
        assert phases[0]["phase_id"] == "phase_fail"
        assert phases[0]["status"] == "failure"
        assert phases[0]["ended_at"]

    def test_phase6_context_includes_run_timeline(self, temp_dir):
        executor = make_executor(noop_workflow(), temp_dir)
        executor.phase_results = {
            "phase_a": {
                "status": "success",
                "started_at": "2026-08-06T00:00:00+00:00",
                "ended_at": "2026-08-06T00:00:05+00:00",
                "duration_seconds": 5.0,
            }
        }
        executor.artifact_store.artifact_dir = str(Path(temp_dir) / "artifacts")
        input_ctx: dict = {}
        phase = PhaseDefinition(
            id="phase_6_report",
            name="Reports",
            prompt_template="phase_6_report.md",
            output_schema={},
            type="llm",
            agent="main_engineer",
            validator=None,
            transitions={"on_success": "complete"},
        )
        executor._inject_llm_phase_specific_context(input_ctx, phase, executor.state)

        assert "run_timeline" in input_ctx
        timeline = input_ctx["run_timeline"]
        assert isinstance(timeline, dict)
        assert timeline["phases"][0]["phase_id"] == "phase_a"

    @pytest.mark.parametrize(
        "filename",
        ["phase_6_report.md", "phase_6_report_musa.md", "phase_6_report_ppu.md"],
    )
    def test_phase6_prompts_declare_time_contract(self, filename):
        prompt_path = PROJECT_ROOT / "prompts" / filename
        text = prompt_path.read_text(encoding="utf-8")
        assert "run_timeline" in text
        assert "started_at" in text

    def test_validate_reports_accepts_valid_run_timeline(self):
        result = validate_reports(
            {
                "report_paths": ["/tmp/r.md"],
                "migration_summary": {"files_migrated": 1, "files_skipped": 0},
                "run_timeline": {
                    "run_started_at": "2026-08-06T00:00:00+00:00",
                    "run_ended_at": "2026-08-06T00:01:00+00:00",
                    "phases": [
                        {
                            "phase_id": "phase_a",
                            "status": "success",
                            "started_at": "2026-08-06T00:00:00+00:00",
                            "ended_at": "2026-08-06T00:00:30+00:00",
                            "duration_seconds": 30.0,
                        }
                    ],
                },
            }
        )
        assert result["passed"] is True

    def test_validate_reports_rejects_em_dash_placeholder_time(self):
        result = validate_reports(
            {
                "report_paths": ["/tmp/r.md"],
                "migration_summary": {"files_migrated": 1, "files_skipped": 0},
                "run_timeline": {
                    "phases": [
                        {
                            "phase_id": "phase_a",
                            "started_at": "—",
                            "ended_at": "—",
                            "status": "success",
                        }
                    ]
                },
            }
        )
        assert result["passed"] is False
        assert any("run_timeline" in err for err in result["errors"])

    def test_validate_reports_accepts_missing_run_timeline(self):
        result = validate_reports(
            {
                "report_paths": ["/tmp/r.md"],
                "migration_summary": {"files_migrated": 1, "files_skipped": 0},
            }
        )
        assert result["passed"] is True

    def test_schema_declares_optional_run_timeline(self):
        schema_path = PROJECT_ROOT / "schemas" / "phase_6_reports.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert "run_timeline" in schema["properties"]
        assert "run_timeline" not in schema.get("required", [])


def test_utc_now_iso_timezone_suffix():
    now = datetime.now(timezone.utc)
    assert now.isoformat().endswith("+00:00")
