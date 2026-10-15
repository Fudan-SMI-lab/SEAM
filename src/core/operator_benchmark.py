"""KernelBench operator-task validation and trace summarization helpers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
import hashlib
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
    runtime_env_file: str | Path | None = None,
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
    runtime_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if runtime_env_file:
        env_path = Path(runtime_env_file).expanduser().resolve()
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                runtime_env[key.strip()] = value.strip()
    process = subprocess.Popen(
        command,
        cwd=root,
        env=runtime_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    process_metadata = {
        "pid": process.pid,
        "command": command,
        "timeout_seconds": timeout,
        "cwd": str(root),
        "traces_path": str(traces),
        "runtime_env_file": str(runtime_env_file) if runtime_env_file else "",
    }
    traces.with_name("process.json").write_text(
        json.dumps(process_metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        return {
            "evaluation_status": "completed" if process.returncode == 0 else "failed",
            "exit_code": process.returncode,
            "process_group_cleaned": True,
            "command": command,
            "traces_path": str(traces),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "process_metadata_path": str(traces.with_name("process.json")),
        }
    except subprocess.TimeoutExpired:
        cleanup_status = _terminate_process_group(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        return {
            "evaluation_status": "timeout",
            "exit_code": None,
            "process_group_cleaned": cleanup_status == "cleaned",
            "cleanup_status": cleanup_status,
            "command": command,
            "traces_path": str(traces),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "process_metadata_path": str(traces.with_name("process.json")),
        }


def _terminate_process_group(pid: int | None) -> str:
    """Terminate an evaluator process group after an outer timeout.

    The caller must pass the process-group leader PID created with
    ``start_new_session=True``. The fallback is intentionally conservative:
    without a PID we report that cleanup could not be verified.
    """
    if not isinstance(pid, int) or pid <= 0:
        return "unverified"
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "cleaned"
    except OSError:
        return "failed"
    for _ in range(30):
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return "cleaned"
        except OSError:
            return "unverified"
        time.sleep(1)
    try:
        os.killpg(pid, signal.SIGKILL)
        return "cleaned"
    except ProcessLookupError:
        return "cleaned"
    except OSError:
        return "failed"


def evaluate_and_summarize_operator(
    task_dir: str | Path,
    solution: str | Path,
    kernelbench_root: str | Path,
    output_dir: str | Path,
    *,
    iteration: str = "0",
    timeout: int = 600,
    runtime_env_file: str | Path | None = None,
) -> dict[str, Any]:
    """Run one full operator evaluation and summarize its traces."""
    evaluation_id = f"eval-{uuid.uuid4().hex}"
    evaluation_dir = (
        Path(output_dir).expanduser().resolve()
        / "evaluations"
        / f"iteration-{iteration}"
        / evaluation_id
    )
    traces_path = evaluation_dir / "traces.jsonl"
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    process = run_kernelbench_eval(
        task_dir,
        solution,
        kernelbench_root,
        traces_path,
        timeout=timeout,
        runtime_env_file=runtime_env_file,
    )
    result = dict(process)
    result.update(
        {
            "evaluation_id": evaluation_id,
            "iteration": str(iteration),
            "evaluation_dir": str(evaluation_dir),
        }
    )
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
    expected_workloads = len(
        [line for line in (Path(task_dir) / "workload.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    ) if (Path(task_dir) / "workload.jsonl").is_file() else 0
    result["expected_workloads"] = expected_workloads
    result["trace_complete"] = result.get("total_workloads", 0) == expected_workloads
    result["evaluation_valid"] = bool(
        result.get("evaluation_status") == "completed"
        and result.get("trace_complete")
    )
    manifest = build_failure_manifest(result, traces_path, solution)
    manifest_path = evaluation_dir / "failure_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["failure_manifest_path"] = str(manifest_path)
    history_path = Path(output_dir).expanduser().resolve() / "iteration_history.json"
    history = []
    if history_path.is_file():
        try:
            loaded = json.loads(history_path.read_text(encoding="utf-8"))
            history = loaded if isinstance(loaded, list) else []
        except json.JSONDecodeError:
            history = []
    history.append(manifest)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["iteration_history_path"] = str(history_path)
    return result


def build_failure_manifest(
    result: dict[str, Any], traces_path: Path, solution: str | Path
) -> dict[str, Any]:
    """Build a prompt-friendly, validity-aware summary for one evaluation."""
    traces = []
    if traces_path.is_file():
        for line in traces_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    traces.append(value)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trace in traces:
        status = _status(trace)
        if status != "PASSED":
            grouped.setdefault(status, []).append(trace)
    failures = []
    for status, rows in grouped.items():
        failures.append(
            {
                "category": status.lower(),
                "count": len(rows),
                "representative_workloads": [
                    str(row.get("uuid") or row.get("workload_id") or "")
                    for row in rows[:5]
                ],
            }
        )
    solution_path = Path(solution).expanduser().resolve()
    solution_hash = ""
    if solution_path.is_file():
        solution_hash = hashlib.sha256(solution_path.read_bytes()).hexdigest()
    return {
        "iteration": str(result.get("iteration", "0")),
        "evaluation_id": result.get("evaluation_id", ""),
        "evaluation_valid": bool(result.get("evaluation_valid")),
        "evaluation_status": result.get("evaluation_status", ""),
        "solution_sha256": solution_hash,
        "total": result.get("total_workloads", 0),
        "passed": result.get("passed_workloads", 0),
        "statuses": result.get("statuses", {}),
        "failure_groups": failures,
        "trace_complete": bool(result.get("trace_complete")),
        "invalid_reason": "" if result.get("evaluation_valid") else result.get("evaluation_status", "incomplete_traces"),
        "traces_path": str(traces_path),
    }


def detect_operator_runtime(
    *,
    kernelbench_root: str | Path,
    target_soc: str,
) -> dict[str, Any]:
    """Collect deterministic runtime facts before an operator is generated."""
    root = Path(kernelbench_root).expanduser().resolve()
    python_candidates = []
    configured_python = os.environ.get("PYTHON")
    if configured_python:
        python_candidates.append(configured_python)
    python_candidates.extend([str(root / ".venv/bin/python"), sys.executable])
    python_executable = next(
        (candidate for candidate in python_candidates if Path(candidate).exists()),
        sys.executable,
    )

    def command_output(command: list[str]) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        output = (completed.stdout or completed.stderr).strip()
        return completed.returncode == 0, output

    def python_probe(script: str) -> tuple[bool, str]:
        return command_output([python_executable, "-c", script])

    _, python_version = command_output([python_executable, "--version"])
    torch_ok, torch_data = python_probe(
        "import torch; print(torch.__version__); "
        "print(bool(getattr(torch, 'npu', None) and torch.npu.is_available()))"
    )
    torch_npu_ok, torch_npu_data = python_probe(
        "import torch_npu; print(getattr(torch_npu, '__version__', 'unknown'))"
    )
    deps: dict[str, bool] = {}
    for package in ("decorator", "scipy", "attrs", "psutil"):
        ok, _ = python_probe(f"import {package}")
        deps[package] = ok
    npu_ok, npu_data = python_probe(
        "import torch; print(torch.npu.device_count()); "
        "print(torch.npu.get_device_name(0) if torch.npu.device_count() else '')"
    )
    smi_ok, smi_data = command_output(["npu-smi", "info"])
    kernelbench_ok, kernelbench_data = command_output(
        ["uv", "run", "npu-kernelbench", "--help"]
    )
    ascend_home = os.environ.get("ASCEND_HOME_PATH", "")
    opp_path = os.environ.get("ASCEND_OPP_PATH", "")
    ascendc_path = (
        Path(ascend_home) / "compiler" if ascend_home else Path("/nonexistent")
    )
    blocking: list[str] = []
    warnings: list[str] = []
    if not torch_ok or "True" not in torch_data:
        blocking.append("torch_npu NPU availability check failed")
    if not torch_npu_ok:
        blocking.append("torch_npu import failed")
    if not npu_ok:
        blocking.append("NPU device probe failed")
    if not smi_ok:
        warnings.append("npu-smi info failed")
    if not kernelbench_ok:
        blocking.append("KernelBench CLI probe failed")
    missing_deps = [package for package, ok in deps.items() if not ok]
    if missing_deps:
        blocking.append(f"missing runtime dependencies: {', '.join(missing_deps)}")
    if not root.is_dir():
        blocking.append(f"KernelBench root does not exist: {root}")
    return {
        "operation": "operator_runtime_detect",
        "phase_id": "phase_1_operator_runtime_detect",
        "runtime_status": "blocked" if blocking else ("warning" if warnings else "ready"),
        "python_executable": python_executable,
        "python_version": python_version,
        "torch_version": torch_data.splitlines()[0] if torch_data else "",
        "torch_npu_version": torch_npu_data,
        "npu_available": "True" in torch_data,
        "device_count": int(npu_data.splitlines()[0]) if npu_ok and npu_data.splitlines() and npu_data.splitlines()[0].isdigit() else 0,
        "device_names": npu_data.splitlines()[1:] if npu_ok else [],
        "target_soc": target_soc,
        "driver_version": smi_data[:1000] if smi_ok else "",
        "firmware_version": "",
        "cann_version": os.environ.get("ASCEND_TOOLKIT_HOME", ascend_home),
        "ascendc_available": ascendc_path.is_dir(),
        "opp_path": opp_path,
        "kernelbench_available": kernelbench_ok,
        "dependency_checks": deps,
        "path_checks": {
            "kernelbench_root": root.is_dir(),
            "ascend_home": bool(ascend_home),
            "opp_path": bool(opp_path),
            "output_writable": os.access(root, os.W_OK),
        },
        "smoke_test": {"status": "not_run", "summary": "runtime facts only"},
        "blocking_issues": blocking,
        "warnings": warnings,
        "evidence": [kernelbench_data[:1000]],
    }


def configure_operator_runtime(
    *, output_dir: str | Path, runtime_report: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a run-scoped runtime environment and isolated temp directories."""
    root = Path(output_dir).expanduser().resolve()
    runtime_root = root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = runtime_root / "tmp"
    cache_dir = runtime_root / "cache"
    staging_dir = runtime_root / "staging"
    for directory in (tmp_dir, cache_dir, staging_dir):
        directory.mkdir(parents=True, exist_ok=True)
    env_path = runtime_root / "runtime.env"
    env_values = {
        "SEAM_OPERATOR_RUN_ROOT": str(root),
        "SEAM_OPERATOR_STAGING_ROOT": str(staging_dir),
        "TMPDIR": str(tmp_dir),
        "TEMP": str(tmp_dir),
        "TMP": str(tmp_dir),
        "XDG_CACHE_HOME": str(cache_dir),
    }
    if runtime_report:
        python_executable = runtime_report.get("python_executable")
        if isinstance(python_executable, str) and python_executable:
            env_values["SEAM_OPERATOR_PYTHON"] = python_executable
        opp_path = runtime_report.get("opp_path")
        if isinstance(opp_path, str) and opp_path:
            env_values["ASCEND_OPP_PATH"] = opp_path
    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in env_values.items()),
        encoding="utf-8",
    )
    result = {
        "operation": "operator_runtime_configure",
        "phase_id": "phase_1_5_operator_runtime_configure",
        "configuration_status": "ready",
        "environment_file": str(env_path),
        "staging_root": str(staging_dir),
        "changes": [f"created {directory}" for directory in (tmp_dir, cache_dir, staging_dir)],
        "verified_checks": ["runtime.env written", "run-scoped tmp/cache/staging created"],
        "remaining_blocking_issues": [],
        "handoff_role": "",
        "summary": "Created run-scoped runtime configuration without modifying vendor installations.",
    }
    (runtime_root / "configuration.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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
