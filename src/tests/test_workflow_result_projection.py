from __future__ import annotations

from harness.run.workflow_result_projection import project_workflow_result


def test_projection_orders_maps_and_truncates_executor_results() -> None:
    failure = "x" * 600

    projection = project_workflow_result(
        ("phase_a", "phase_b"),
        {
            "unknown_phase": {"status": "custom", "duration": 1.23456},
            "phase_b": {
                "status": "failure",
                "duration": 2.34567,
                "output_summary": failure,
            },
            "phase_a": {"status": "success", "duration": 0.11119},
        },
        {"phase_3_entry_script": {"run_command": "python app.py"}},
    )

    assert [phase.phase_id for phase in projection.phases] == [
        "phase_a",
        "phase_b",
        "unknown_phase",
    ]
    assert [phase.status for phase in projection.phases] == [
        "passed",
        "failed",
        "custom",
    ]
    assert [phase.duration_seconds for phase in projection.phases] == [
        0.111,
        2.346,
        1.235,
    ]
    assert projection.phases[1].error == failure[:500]
    assert projection.phases[2].phase_number == 1000
    assert projection.entry_script == "python app.py"
