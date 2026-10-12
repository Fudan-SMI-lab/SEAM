"""Tests for single-operator KernelBench helpers."""

import json
from pathlib import Path

from core.config import load_workflow
from core.operator_benchmark import summarize_traces, validate_operator_task
from validators.validate_operator_env_detect import validate as validate_env
from validators.validate_operator_evaluation import validate as validate_evaluation


def test_kernelbench_workflow_has_five_numbered_phases() -> None:
    workflow = load_workflow("src/workflows/npu_kernelbench_custom_op.yaml")

    assert [phase.id for phase in workflow.phases] == [
        "phase_0_operator_env_detect",
        "phase_1_operator_analysis",
        "phase_2_custom_op_prepare",
        "phase_3_operator_validation",
        "phase_4_operator_report",
    ]
    assert workflow.sub_workflows["operator_repair_loop"].phases[0]["id"] == (
        "evaluate_operator"
    )


def test_validate_real_gelu_task() -> None:
    task_dir = Path(
        "/Users/yiding/code/ascendc-kernelgen-data-master-npu-kernelbench-"
    ) / "npu-kernelbench/data/kernel_generator/level1/GELU"

    result = validate_operator_task(task_dir)

    assert result["validation_passed"] is True
    assert result["operator_name"] == "GELU"
    assert result["workload_count"] == 50
    assert validate_env(
        {"operator_task_dir": str(task_dir), "solution_type": "ascendc"}
    )["passed"]


def test_non_ascendc_solution_type_is_rejected() -> None:
    task_dir = Path(
        "/Users/yiding/code/ascendc-kernelgen-data-master-npu-kernelbench-"
    ) / "npu-kernelbench/data/kernel_generator/level1/GELU"

    assert validate_env(
        {"operator_task_dir": str(task_dir), "solution_type": "triton"}
    )["passed"] is False


def test_task_without_reference_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "definition.json").write_text(
        json.dumps({"name": "MissingReference"}), encoding="utf-8"
    )
    (tmp_path / "workload.jsonl").write_text(
        json.dumps({"uuid": "case-001"}) + "\n", encoding="utf-8"
    )

    result = validate_operator_task(tmp_path)

    assert result["validation_passed"] is False
    assert "definition.reference must be a non-empty string" in result["errors"]


def test_summarize_traces_preserves_error_metrics(tmp_path: Path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "evaluation": {
                            "status": "PASSED",
                            "correctness": {
                                "max_absolute_error": 0.001,
                                "max_relative_error": 0.002,
                                "matched_ratio": 1.0,
                            },
                        }
                    }
                ),
                json.dumps(
                    {
                        "evaluation": {
                            "status": "INCORRECT_NUMERICAL",
                            "correctness": {
                                "max_absolute_error": 0.004,
                                "max_relative_error": 0.01,
                                "matched_ratio": 0.98,
                            },
                        }
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_traces(traces_path, operator_name="GELU")

    assert summary["total_workloads"] == 2
    assert summary["passed_workloads"] == 1
    assert summary["workload_pass_rate"] == 0.5
    assert summary["operator_pass_rate"] == 0.0
    assert summary["max_absolute_error"] == 0.004
    assert summary["max_relative_error"] == 0.01
    assert summary["min_matched_ratio"] == 0.98
    assert validate_evaluation(summary)["passed"]
