from __future__ import annotations

from tests.terminal_run_continuation_parent_scenarios import (
    ParentPhaseFixture,
    ParentRun,
    ParentRunScenario,
    SummaryPayload,
)


def build_summary_payload(
    parent: ParentRun,
    status: str,
    phase_status: str,
    scenario: ParentRunScenario | None,
) -> SummaryPayload:
    phases = (
        scenario.phases
        if scenario is not None
        else (ParentPhaseFixture("phase_5_validation", phase_status),)
    )
    return {
        "run_id": parent.report_dir.name,
        "base_url": "http://127.0.0.1:4096",
        "workflow_path": str(parent.workflow_path),
        "output_dir": str(parent.report_dir),
        "temp_dir": str(parent.project_dir),
        "keep_temp_dir": True,
        "requested_max_phase5_iter": 5,
        "effective_max_phase5_iter": 5,
        "phases": [
            {
                "phase_number": index,
                "phase_id": phase.phase_id,
                "label": phase.phase_id,
                "status": phase.status,
                "duration_seconds": 1.25,
                "error": "phase failed" if phase.status == "failed" else None,
            }
            for index, phase in enumerate(phases, start=1)
        ],
        "session_count": 2,
        "command_count": 3,
        "overall_status": status,
        "total_duration_seconds": 4.5,
        "artifact_dir": None,
        "telemetry_paths": {},
        "before_snapshot_path": None,
        "after_snapshot_path": None,
        "entry_script": "python app.py",
        "errors": ["validation failed"] if status == "FAIL" else [],
        "review_timeout_observability": {
            "schema_version": "1.0",
            "review_count": 0,
            "reviews": [],
            "timeout_count": 0,
            "timeouts": [],
            "exhaustion_count": 0,
            "dropped_event_count": 0,
            "review_duration_seconds": 0.0,
            "timeout_elapsed_seconds": 0.0,
        },
    }
