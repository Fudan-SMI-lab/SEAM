"""KernelBench operator-task validation and trace summarization helpers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


SUPPORTED_SOLUTION_TYPES = {"ascendc"}


def run_kernelbench_eval(
    task_dir: str | Path,
    solution: str | Path,
    kernelbench_root: str | Path,
    output_path: str | Path,
    *,
    timeout: int = 600,
) -> dict[str, Any]:
    """Run KernelBench once and persist complete process output beside traces."""
    task = Path(task_dir).expanduser().resolve()
    solution_path = Path(solution).expanduser().resolve()
    root = Path(kernelbench_root).expanduser().resolve()
    traces = Path(output_path).expanduser().resolve()
    traces.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = traces.with_suffix(".stdout.log")
    stderr_path = traces.with_suffix(".stderr.log")
    command = [
        "uv", "run", "npu-kernelbench", "eval", str(task),
        "--solution", str(solution_path), "--runner", "persistent",
        "--timeout", str(timeout), "-o", str(traces), "--verbose",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        return {
            "evaluation_status": "completed" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "command": command,
            "traces_path": str(traces),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return {
            "evaluation_status": "timeout",
            "exit_code": None,
            "command": command,
            "traces_path": str(traces),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }


def evaluate_and_summarize_operator(
    task_dir: str | Path,
    solution: str | Path,
    kernelbench_root: str | Path,
    output_dir: str | Path,
    *,
    iteration: str = "0",
    timeout: int = 600,
) -> dict[str, Any]:
    """Run one full operator evaluation and summarize its traces."""
    traces_path = Path(output_dir).expanduser().resolve() / f"traces.iteration-{iteration}.jsonl"
    process = run_kernelbench_eval(
        task_dir, solution, kernelbench_root, traces_path, timeout=timeout
    )
    result = dict(process)
    if traces_path.is_file():
        summary_path = traces_path.with_name(f"summary.iteration-{iteration}.json")
        result.update(
            summarize_traces(
                traces_path,
                operator_name=Path(task_dir).name,
                summary_path=summary_path,
            )
        )
    else:
        result.update(
            {
                "total_workloads": 0,
                "passed_workloads": 0,
                "workload_pass_rate": 0.0,
                "operator_pass_rate": 0.0,
                "operator_passed": False,
                "statuses": {},
                "required_matched_ratio": 0.99,
            }
        )
    return result
def validate_operator_task(task_dir: str | Path) -> dict[str, Any]:
    """Validate the minimum files required for one KernelBench operator task."""
    root = Path(task_dir).expanduser().resolve()
    definition_path = root / "definition.json"
    workload_path = root / "workload.jsonl"
    errors: list[str] = []
    definition: dict[str, Any] = {}
    workloads: list[dict[str, Any]] = []

    if not root.is_dir():
        errors.append(f"operator task directory does not exist: {root}")
    if not definition_path.is_file():
        errors.append("definition.json is missing")
    else:
        try:
            loaded = json.loads(definition_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                errors.append("definition.json must contain an object")
            else:
                definition = loaded
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"definition.json cannot be loaded: {exc}")
    if not workload_path.is_file():
        errors.append("workload.jsonl is missing")
    else:
        try:
            for line_number, line in enumerate(
                workload_path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not line.strip():
                    continue
                loaded = json.loads(line)
                if not isinstance(loaded, dict):
                    errors.append(f"workload.jsonl:{line_number} must contain an object")
                else:
                    workloads.append(loaded)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"workload.jsonl cannot be loaded: {exc}")

    operator_name = str(definition.get("name", "")).strip()
    if not operator_name:
        errors.append("definition.name must be a non-empty string")
    if not isinstance(definition.get("reference"), str) or not definition[
        "reference"
    ].strip():
        errors.append("definition.reference must be a non-empty string")
    if not workloads:
        errors.append("workload.jsonl must contain at least one workload")
    level = root.parent.name if root.parent.name.startswith("level") else ""

    return {
        "validation_passed": not errors,
        "errors": errors,
        "operator_task_dir": str(root),
        "operator_name": operator_name,
        "level": level,
        "definition_path": str(definition_path),
        "workload_path": str(workload_path),
        "workload_count": len(workloads),
        "definition": definition,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                traces.append(value)
    return traces


def _status(trace: dict[str, Any]) -> str:
    evaluation = trace.get("evaluation")
    if isinstance(evaluation, dict):
        return str(evaluation.get("status", "UNKNOWN"))
    return str(trace.get("status", "UNKNOWN"))


def _correctness_values(trace: dict[str, Any]) -> dict[str, Any]:
    evaluation = trace.get("evaluation")
    if not isinstance(evaluation, dict):
        return {}
    correctness = evaluation.get("correctness")
    return correctness if isinstance(correctness, dict) else {}


def summarize_traces(
    traces_path: str | Path,
    *,
    operator_name: str = "",
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    """Produce stable workload/operator metrics from KernelBench JSONL traces."""
    path = Path(traces_path).expanduser().resolve()
    traces = _read_jsonl(path)
    passed = sum(_status(trace) == "PASSED" for trace in traces)
    statuses: dict[str, int] = {}
    max_absolute_error: float | None = None
    max_relative_error: float | None = None
    min_matched_ratio: float | None = None
    for trace in traces:
        status = _status(trace)
        statuses[status] = statuses.get(status, 0) + 1
        correctness = _correctness_values(trace)
        for key in ("max_absolute_error", "max_relative_error"):
            value = correctness.get(key)
            if isinstance(value, (int, float)):
                current = (
                    max_absolute_error
                    if key == "max_absolute_error"
                    else max_relative_error
                )
                if current is None or value > current:
                    if key == "max_absolute_error":
                        max_absolute_error = float(value)
                    else:
                        max_relative_error = float(value)
        matched = correctness.get("matched_ratio")
        if isinstance(matched, (int, float)):
            if min_matched_ratio is None or matched < min_matched_ratio:
                min_matched_ratio = float(matched)

    total = len(traces)
    result: dict[str, Any] = {
        "operator_name": operator_name,
        "traces_path": str(path),
        "total_workloads": total,
        "passed_workloads": passed,
        "workload_pass_rate": (passed / total) if total else 0.0,
        "operator_pass_rate": 1.0 if total > 0 and passed == total else 0.0,
        "operator_passed": bool(total > 0 and passed == total),
        "statuses": statuses,
        "max_absolute_error": max_absolute_error,
        "max_relative_error": max_relative_error,
        "min_matched_ratio": min_matched_ratio,
        "required_matched_ratio": 0.99,
    }
    if summary_path is not None:
        destination = Path(summary_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["summary_path"] = str(destination)
    return result
