"""Tests for single-operator KernelBench helpers."""

import json
from pathlib import Path

from core.config import load_workflow
from core.operator_benchmark import (
    _classify_timeout,
    _evaluator_lock_path,
    _prepare_evaluation_runtime,
    build_candidate_manifest,
    build_failure_manifest,
    classify_operator_evaluation,
    configure_operator_runtime,
    summarize_traces,
    validate_operator_artifact,
    validate_operator_task,
)
from validators.validate_operator_env_detect import validate as validate_env
from validators.validate_operator_evaluation import validate as validate_evaluation


def test_kernelbench_workflow_combines_generation_evaluation_and_repair_loop() -> None:
    workflow = load_workflow("src/workflows/npu_kernelbench_custom_op.yaml")

    assert [phase.id for phase in workflow.phases] == [
        "phase_0_operator_env_detect",
        "phase_1_operator_runtime_detect",
        "phase_1_5_operator_runtime_configure",
        "phase_2_operator_analysis",
        "phase_3_custom_op_prepare",
        "phase_4_operator_report",
    ]
    generation_loop = workflow.sub_workflows["operator_repair_loop"]
    assert generation_loop.phases[0]["id"] == "validate_operator_artifact"
    assert generation_loop.phases[0]["operation"] == "operator_artifact_validate"
    assert generation_loop.phases[1]["id"] == "prepare_operator"
    assert "operator_artifact_state.rebuild_required" in generation_loop.phases[1]["condition"]
    assert generation_loop.phases[1]["on_failure"] == "continue"
    assert generation_loop.phases[2]["id"] == "refresh_operator_artifact"
    assert generation_loop.phases[2]["output_as"] == "operator_artifact_state"
    assert generation_loop.phases[2]["params"]["require_changed"] is True
    assert generation_loop.phases[3]["id"] == "evaluate_operator"
    assert "condition" not in generation_loop.phases[3]
    assert generation_loop.phases[4]["id"] == "classify_operator_evaluation"
    assert generation_loop.phases[5]["condition"] == (
        "${loop_state.operator_passed} != true"
    )
    assert generation_loop.phases[6]["id"] == "fix_operator_environment"
    assert "evaluation_classification.repair_role" in generation_loop.phases[6]["condition"]
    assert generation_loop.phases[7]["id"] == "fix_operator"
    assert "evaluation_classification.repair_role" in generation_loop.phases[7]["condition"]
    assert generation_loop.phases[6]["condition"].count("operator_environment_fixer") == 2
    assert generation_loop.phases[7]["condition"] == (
        "${loop_state.operator_passed} != true "
        "and ${loop_state.evaluation_classification.repair_role} != 'operator_environment_fixer' "
        "and ${loop_state.error_analysis.repair_role} != 'operator_environment_fixer'"
    )
    assert generation_loop.phases[6]["on_failure"] == "continue"
    assert generation_loop.phases[7]["on_failure"] == "continue"
    assert generation_loop.phases[3]["on_failure"] == "continue"


def test_operator_evaluation_classification_routes_reference_and_device_failures() -> None:
    reference = classify_operator_evaluation(
        {
            "failure_category": "evaluator_failed",
            "evaluation_scope": "reference",
            "evaluation_completeness": "reference_failed",
        }
    )
    assert reference["category"] == "reference_unavailable"
    assert reference["repair_role"] == "operator_environment_fixer"

    device = classify_operator_evaluation(
        {
            "failure_category": "device_memory_fault",
            "evaluation_scope": "candidate",
            "evaluation_completeness": "partial",
            "first_failure_workload": "case-014",
        }
    )
    assert device["repair_role"] == "operator_fixer"
    assert device["requires_single_workload_debug"] is True
    assert device["first_failure_workload"] == "case-014"

    ordinary_candidate_log = classify_operator_evaluation(
        {
            "failure_category": "evaluator_failed",
            "evaluation_scope": "unknown",
            "evaluation_completeness": "no_trace",
        }
    )
    assert ordinary_candidate_log["category"] == "evaluator_failed"
    assert ordinary_candidate_log["repair_role"] == "none"


def test_operator_artifact_validation_detects_materialized_source_mismatch(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    solution = task_dir / "solution.json"
    solution.write_text(
        json.dumps(
            {"sources": [{"path": "adapter.py", "content": "return 1\n"}]}
        ),
        encoding="utf-8",
    )
    (task_dir / "adapter.py").write_text("return 2\n", encoding="utf-8")
    result = validate_operator_artifact(solution, task_dir)
    assert result["solution_ready"] is False
    assert result["writeback_status"] == "mismatch"
    assert result["candidate_manifest"]["writeback_mismatches"] == ["adapter.py"]


def test_operator_artifact_validation_requires_rebuild(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    missing = validate_operator_artifact(task_dir / "solution.json", task_dir)
    assert missing["solution_ready"] is False
    assert missing["artifact_complete"] is False
    assert missing["rebuild_required"] is True

    solution = task_dir / "solution.json"
    solution.write_text(
        json.dumps(
            {"sources": [{"path": "adapter.py", "content": "return 1\n"}]}
        ),
        encoding="utf-8",
    )
    ready = validate_operator_artifact(solution, task_dir)
    assert ready["solution_ready"] is True
    assert ready["artifact_complete"] is True

    stale = validate_operator_artifact(
        solution,
        task_dir,
        baseline_sha256=ready["solution_sha256"],
        require_changed=True,
    )
    assert stale["candidate_evaluable"] is False
    assert stale["stale_candidate"] is True


def test_candidate_manifest_detects_source_writeback_mismatch(tmp_path: Path) -> None:
    solution_dir = tmp_path / "candidate"
    solution_dir.mkdir()
    solution = solution_dir / "solution.json"
    solution.write_text(
        json.dumps(
            {
                "sources": [
                    {"path": "adapter.py", "content": "return 1\n"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (solution_dir / "adapter.py").write_text("return 2\n", encoding="utf-8")

    manifest = build_candidate_manifest(solution)

    assert manifest["writeback_status"] == "mismatch"
    assert manifest["candidate_evaluable"] is False
    assert manifest["writeback_mismatches"] == ["adapter.py"]


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


def test_runtime_configuration_is_run_scoped(tmp_path: Path) -> None:
    result = configure_operator_runtime(
        output_dir=tmp_path,
        runtime_report={"python_executable": "/opt/python", "opp_path": "/opt/opp"},
    )

    env_path = Path(result["environment_file"])
    assert result["configuration_status"] == "ready"
    assert env_path.is_file()
    env_text = env_path.read_text(encoding="utf-8")
    assert f"SEAM_OPERATOR_RUN_ROOT={tmp_path}" in env_text
    assert "TMPDIR=" in env_text
    assert "ASCEND_OPP_PATH=/opt/opp" in env_text
    assert (tmp_path / "runtime" / "configuration.json").is_file()


def test_failure_manifest_marks_incomplete_evaluation(tmp_path: Path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    traces_path.write_text(
        json.dumps(
            {
                "uuid": "case-001",
                "evaluation": {"status": "RUNTIME_ERROR"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    solution = tmp_path / "solution.json"
    solution.write_text("{}", encoding="utf-8")
    manifest = build_failure_manifest(
        {
            "iteration": "2",
            "evaluation_id": "eval-test",
            "evaluation_status": "timeout",
            "evaluation_valid": False,
            "total_workloads": 1,
            "passed_workloads": 0,
            "statuses": {"RUNTIME_ERROR": 1},
            "trace_complete": False,
        },
        traces_path,
        solution,
    )
    assert manifest["evaluation_valid"] is False
    assert manifest["invalid_reason"] == "timeout"
    assert manifest["failure_groups"][0]["category"] == "runtime_error"
    assert manifest["failure_groups"][0]["representative_workloads"] == ["case-001"]


def test_timeout_evidence_is_classified_without_hiding_the_timeout(tmp_path: Path) -> None:
    traces = tmp_path / "traces.jsonl"
    traces.write_text(json.dumps({"uuid": "case-001"}) + "\n", encoding="utf-8")
    assert _classify_timeout(traces, "", "", "cleaned") == "partial_trace_timeout"
    assert _classify_timeout(tmp_path / "missing.jsonl", "", "", "cleaned") == "no_trace_timeout"
    assert _classify_timeout(tmp_path / "missing.jsonl", "", "Segmentation fault", "cleaned") == "host_crash"
    assert _classify_timeout(tmp_path / "missing.jsonl", "", "devmm acl error 507035", "cleaned") == "device_memory_fault"
    assert _classify_timeout(tmp_path / "missing.jsonl", "", "No space left on device", "cleaned") == "resource_exhausted"


def test_evaluator_lock_is_device_scoped_when_one_device_is_visible(tmp_path: Path) -> None:
    lock = _evaluator_lock_path(
        tmp_path, {"ASCEND_RT_VISIBLE_DEVICES": "3"}
    )
    assert lock.name == ".seam-kernelbench-evaluator.3.lock"
    assert _evaluator_lock_path(tmp_path, {"ASCEND_RT_VISIBLE_DEVICES": "0,1"}).name == ".seam-kernelbench-evaluator.lock"


def test_evaluation_runtime_isolated_from_base_runtime(tmp_path: Path) -> None:
    base_env = tmp_path / "base.env"
    base_env.write_text("ASCEND_OPP_PATH=/opt/opp\nTMPDIR=/shared/tmp\n", encoding="utf-8")
    first = _prepare_evaluation_runtime(tmp_path / "eval-1", base_env)
    second = _prepare_evaluation_runtime(tmp_path / "eval-2", base_env)

    first_text = first.read_text(encoding="utf-8")
    second_text = second.read_text(encoding="utf-8")
    assert "ASCEND_OPP_PATH=/opt/opp" in first_text
    assert "TMPDIR=/shared/tmp" not in first_text
    assert "TMPDIR=" in first_text
    assert first_text != second_text
    assert (tmp_path / "eval-1" / "runtime" / "ownership.json").is_file()
    assert (tmp_path / "eval-2" / "runtime" / "ownership.json").is_file()


def test_kernelbench_repair_prompt_uses_injected_text_not_evidence_paths() -> None:
    prompt = Path("src/prompts/repair_operator_fixer_kernelbench_ascendc.md").read_text(
        encoding="utf-8"
    )

    assert "{operator_definition_text}" in prompt
    assert "{operator_workload_text}" in prompt
    assert "{operator_evaluation_text}" in prompt
    assert "{operator_error_analysis_text}" in prompt
    assert "{build_artifact_path}" not in prompt
    assert "{latest_trace_path}" not in prompt
    assert "do not issue parallel tool calls" in prompt
    assert "Do not spend tool calls reopening" in prompt
