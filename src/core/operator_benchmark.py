"""KernelBench operator-task validation and trace summarization helpers."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
import hashlib
import fcntl
import re
from pathlib import Path
from typing import Any


SUPPORTED_SOLUTION_TYPES = {"ascendc"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _load_solution(solution_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        loaded = json.loads(solution_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"solution.json cannot be loaded: {exc}"]
    if not isinstance(loaded, dict):
        return None, ["solution.json must contain an object"]
    sources = loaded.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("solution.json must declare a non-empty sources list")
    return loaded, errors


def build_candidate_manifest(
    solution: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a hashable description of the candidate actually being evaluated."""
    solution_path = Path(solution).expanduser().resolve()
    solution_bytes = solution_path.read_bytes() if solution_path.is_file() else b""
    loaded, errors = _load_solution(solution_path) if solution_path.is_file() else (
        None,
        ["solution.json is missing"],
    )
    sources = loaded.get("sources", []) if isinstance(loaded, dict) else []
    source_entries: list[dict[str, Any]] = []
    materialized_entries: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            errors.append("sources must contain objects")
            continue
        relative_path = source.get("path")
        content = source.get("content")
        if not isinstance(relative_path, str) or not relative_path:
            errors.append("each source must have a non-empty path")
            continue
        if not isinstance(content, str):
            errors.append(f"source content must be a string: {relative_path}")
            continue
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"source path escapes candidate root: {relative_path}")
            continue
        source_entry = {
            "path": relative_path,
            "content_sha256": _sha256_bytes(content.encode("utf-8")),
            "content_present": True,
        }
        source_entries.append(source_entry)
        materialized_path = solution_path.parent / relative
        if materialized_path.is_file():
            materialized_entries.append(
                {
                    "path": relative_path,
                    "sha256": _sha256_bytes(materialized_path.read_bytes()),
                    "matches_embedded": source_entry["content_sha256"]
                    == _sha256_bytes(materialized_path.read_bytes()),
                }
            )

    embedded_source_hash = _canonical_sha256(source_entries)
    materialized_source_hash = _canonical_sha256(materialized_entries)
    mismatches = [
        entry["path"]
        for entry in materialized_entries
        if entry.get("matches_embedded") is False
    ]
    manifest = {
        "manifest_version": 1,
        "solution_path": str(solution_path),
        "solution_sha256": _sha256_bytes(solution_bytes),
        "embedded_source_sha256": embedded_source_hash,
        "materialized_source_sha256": materialized_source_hash,
        "source_count": len(source_entries),
        "materialized_source_count": len(materialized_entries),
        "source_entries": source_entries,
        "materialized_entries": materialized_entries,
        "writeback_status": "mismatch" if mismatches else "consistent",
        "writeback_mismatches": mismatches,
        "errors": errors,
        "candidate_evaluable": not errors and not mismatches,
    }
    if manifest_path is not None:
        destination = Path(manifest_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_path"] = str(destination)
    return manifest


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

    # Remove old traces to prevent cross-iteration aggregation
    # (each eval should report only current iteration's workloads)
    if traces.exists():
        traces.unlink()

    traces.parent.mkdir(parents=True, exist_ok=True)
    work_dir = traces.parent / "work"
    profile_dir = traces.parent / "profile"
    work_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = traces.with_suffix(".stdout.log")
    stderr_path = traces.with_suffix(".stderr.log")
    command = [
        "uv", "run", "npu-kernelbench", "eval", str(task),
        "--solution", str(solution_path), "--runner", "persistent",
        "--timeout", str(timeout), "-o", str(traces), "--verbose",
        "--work-dir", str(work_dir), "--profile-dir", str(profile_dir),
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
    lock_path = _evaluator_lock_path(root, runtime_env)
    lock_file = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(
        json.dumps(
            {
                "owner": "seam.operator_benchmark",
                "traces": str(traces),
                "device": _visible_device_tag(runtime_env) or "unknown",
            }
        )
        + "\n"
    )
    lock_file.flush()
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=runtime_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except Exception:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        raise
    process_metadata = {
        "pid": process.pid,
        "command": command,
        "timeout_seconds": timeout,
        "cwd": str(root),
        "traces_path": str(traces),
        "runtime_env_file": str(runtime_env_file) if runtime_env_file else "",
        "staging_root": runtime_env.get("SEAM_OPERATOR_STAGING_ROOT", ""),
        "visible_device": _visible_device_tag(runtime_env) or "",
        "evaluator_lock": str(lock_path),
        "work_dir": str(work_dir),
        "profile_dir": str(profile_dir),
        "resource_before": _resource_snapshot(traces.parent),
    }
    traces.with_name("process.json").write_text(
        json.dumps(process_metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        resource_after = _resource_snapshot(traces.parent)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        failure_category = ""
        if process.returncode != 0:
            failure_category = _classify_process_failure(process.returncode, stdout or "", stderr or "")
        return {
            "evaluation_status": "completed" if process.returncode == 0 else "failed",
            "exit_code": process.returncode,
            "failure_category": failure_category,
            "process_group_cleaned": True,
            "command": command,
            "traces_path": str(traces),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "process_metadata_path": str(traces.with_name("process.json")),
            "evaluator_lock": str(lock_path),
            "timeout_classification": "",
            "resource_before": process_metadata["resource_before"],
            "resource_after": resource_after,
            "work_dir": str(work_dir),
            "profile_dir": str(profile_dir),
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
        resource_after = _resource_snapshot(traces.parent)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        timeout_classification = _classify_timeout(
            traces, stdout or "", stderr or "", cleanup_status
        )
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
            "evaluator_lock": str(lock_path),
            "timeout_classification": timeout_classification,
            "resource_before": process_metadata["resource_before"],
            "resource_after": resource_after,
            "work_dir": str(work_dir),
            "profile_dir": str(profile_dir),
        }


def _resource_snapshot(path: Path) -> dict[str, Any]:
    """Capture disk and inode availability without making policy decisions."""
    try:
        usage = shutil.disk_usage(path)
        stat = os.statvfs(path)
        return {
            "path": str(path),
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "free_inodes": stat.f_bavail,
            "total_inodes": stat.f_files,
        }
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}


def _visible_device_tag(environment: dict[str, str]) -> str:
    value = environment.get("ASCEND_RT_VISIBLE_DEVICES") or environment.get(
        "ASCEND_VISIBLE_DEVICES", ""
    )
    devices = [item.strip() for item in value.split(",") if item.strip()]
    if len(devices) != 1:
        return ""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", devices[0])


def _evaluator_lock_path(root: Path, environment: dict[str, str]) -> Path:
    device = _visible_device_tag(environment)
    if device:
        return root / f".seam-kernelbench-evaluator.{device}.lock"
    return root / ".seam-kernelbench-evaluator.lock"


def _classify_timeout(
    traces_path: Path, stdout: str, stderr: str, cleanup_status: str
) -> str:
    evidence = f"{stdout}\n{stderr}".lower()
    if any(marker in evidence for marker in ("segmentation fault", "sigsegv", "core dumped")):
        return "host_crash"
    if any(marker in evidence for marker in ("devmm", "507035", "vector core", "page fault", "aivec")):
        return "device_memory_fault"
    if any(marker in evidence for marker in ("setflag", "waitflag", "synchronize", "device hang")):
        return "device_sync_hang"
    if "no space left on device" in evidence or "cannot allocate memory" in evidence:
        return "resource_exhausted"
    if cleanup_status not in {"cleaned", ""}:
        return "cleanup_failed"
    trace_count = 0
    if traces_path.is_file():
        trace_count = sum(1 for line in traces_path.read_text(encoding="utf-8").splitlines() if line.strip())
    if trace_count:
        return "partial_trace_timeout"
    return "no_trace_timeout"


def _classify_process_failure(returncode: int | None, stdout: str, stderr: str) -> str:
    evidence = f"{stdout}\n{stderr}".lower()
    if returncode == -signal.SIGSEGV or any(
        marker in evidence for marker in ("segmentation fault", "sigsegv", "core dumped")
    ):
        return "host_crash"
    if any(marker in evidence for marker in ("devmm", "507035", "vector core", "page fault", "aivec")):
        return "device_memory_fault"
    if "no space left on device" in evidence or "cannot allocate memory" in evidence:
        return "resource_exhausted"
    if returncode is not None and returncode < 0:
        return f"signal_{-returncode}"
    return "evaluator_failed"


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
    candidate_manifest = build_candidate_manifest(
        solution, manifest_path=evaluation_dir / "candidate_manifest.json"
    )
    if not candidate_manifest["candidate_evaluable"]:
        result = {
            "evaluation_status": "blocked",
            "evaluation_id": evaluation_id,
            "iteration": str(iteration),
            "evaluation_dir": str(evaluation_dir),
            "failure_category": "artifact_writeback_failed"
            if candidate_manifest["writeback_mismatches"]
            else "candidate_invalid",
            "operator_passed": False,
            "evaluation_valid": False,
            "candidate_manifest_path": candidate_manifest["manifest_path"],
            "candidate_manifest": candidate_manifest,
        }
        manifest = build_failure_manifest(result, traces_path, solution)
        manifest["candidate_manifest"] = candidate_manifest
        manifest_path = evaluation_dir / "failure_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["failure_manifest_path"] = str(manifest_path)
        history_path = Path(output_dir).expanduser().resolve() / "iteration_history.json"
        history: list[dict[str, Any]] = []
        if history_path.is_file():
            try:
                loaded_history = json.loads(history_path.read_text(encoding="utf-8"))
                if isinstance(loaded_history, list):
                    history = loaded_history
            except (OSError, json.JSONDecodeError):
                history = []
        history.append(manifest)
        history_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["iteration_history_path"] = str(history_path)
        return result
    evaluation_env_file = _prepare_evaluation_runtime(
        evaluation_dir, runtime_env_file
    )
    process = run_kernelbench_eval(
        task_dir,
        solution,
        kernelbench_root,
        traces_path,
        timeout=timeout,
        runtime_env_file=evaluation_env_file,
    )
    result = dict(process)
    result.update(
        {
            "evaluation_id": evaluation_id,
            "iteration": str(iteration),
            "evaluation_dir": str(evaluation_dir),
            "evaluation_runtime_env_file": str(evaluation_env_file),
            "candidate_manifest_path": candidate_manifest["manifest_path"],
            "candidate_manifest": candidate_manifest,
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
    result.update(_classify_evaluation_scope(result, traces_path))
    if not result.get("failure_category"):
        if result.get("evaluation_status") == "timeout":
            result["failure_category"] = result.get("timeout_classification", "timeout")
        elif not result.get("evaluation_valid"):
            result["failure_category"] = "incomplete_trace"
    manifest = build_failure_manifest(result, traces_path, solution)
    manifest["candidate_manifest"] = candidate_manifest
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


def _prepare_evaluation_runtime(
    evaluation_dir: Path, base_env_file: str | Path | None
) -> Path:
    """Create an evaluation-owned temp/cache/staging namespace.

    KernelBench may create internal staging through ``tempfile`` or toolkit
    cache variables. Keeping these directories below the unique evaluation
    directory prevents a timed-out evaluator or another run from deleting a
    still-active candidate's sources.
    """
    runtime_root = evaluation_dir / "runtime"
    tmp_dir = runtime_root / "tmp"
    cache_dir = runtime_root / "cache"
    staging_dir = runtime_root / "staging"
    for directory in (tmp_dir, cache_dir, staging_dir):
        directory.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] = {}
    if base_env_file:
        path = Path(base_env_file).expanduser().resolve()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    env.update(
        {
            "SEAM_OPERATOR_EVALUATION_ROOT": str(evaluation_dir),
            "SEAM_OPERATOR_STAGING_ROOT": str(staging_dir),
            "TMPDIR": str(tmp_dir),
            "TMP": str(tmp_dir),
            "TEMP": str(tmp_dir),
            "XDG_CACHE_HOME": str(cache_dir),
        }
    )
    env_file = evaluation_dir / "runtime.env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(env.items())),
        encoding="utf-8",
    )
    ownership = {
        "evaluation_dir": str(evaluation_dir),
        "staging_root": str(staging_dir),
        "owner": "seam.operator_benchmark",
    }
    (runtime_root / "ownership.json").write_text(
        json.dumps(ownership, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return env_file


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
        "failure_category": result.get("failure_category", ""),
        "exit_code": result.get("exit_code"),
        "timeout_classification": result.get("timeout_classification", ""),
        "cleanup_status": result.get("cleanup_status", ""),
        "visible_device": result.get("visible_device", ""),
        "resource_before": result.get("resource_before", {}),
        "resource_after": result.get("resource_after", {}),
        "work_dir": result.get("work_dir", ""),
        "profile_dir": result.get("profile_dir", ""),
        "solution_sha256": solution_hash,
        "candidate_manifest_path": result.get("candidate_manifest_path", ""),
        "embedded_source_sha256": result.get("candidate_manifest", {}).get(
            "embedded_source_sha256", ""
        ),
        "materialized_source_sha256": result.get("candidate_manifest", {}).get(
            "materialized_source_sha256", ""
        ),
        "writeback_status": result.get("candidate_manifest", {}).get(
            "writeback_status", ""
        ),
        "total": result.get("total_workloads", 0),
        "passed": result.get("passed_workloads", 0),
        "statuses": result.get("statuses", {}),
        "failure_groups": failures,
        "trace_complete": bool(result.get("trace_complete")),
        "invalid_reason": "" if result.get("evaluation_valid") else result.get("failure_category") or result.get("evaluation_status", "incomplete_traces"),
        "traces_path": str(traces_path),
        "evaluation_scope": result.get("evaluation_scope", "unknown"),
        "evaluation_completeness": result.get("evaluation_completeness", "unknown"),
        "first_failure_workload": result.get("first_failure_workload", ""),
        "cascade_failure_count": result.get("cascade_failure_count", 0),
    }


def _classify_evaluation_scope(
    result: dict[str, Any], traces_path: Path
) -> dict[str, Any]:
    """Separate reference failures, candidate failures, and incomplete runs."""
    evidence_paths = [result.get("stdout_path", ""), result.get("stderr_path", "")]
    evidence = "\n".join(
        Path(path).read_text(encoding="utf-8", errors="replace")
        for path in evidence_paths
        if path and Path(path).is_file()
    ).lower()
    reference_markers = (
        "build_baseline",
        "aclnnflashattentionscorev3",
        "not in libopapi.so",
        "reference runtime error",
    )
    candidate_markers = (
        "candidate",
        "aicore exception",
        "devmm",
        "vector core",
        "device fault",
    )
    traces: list[dict[str, Any]] = []
    if traces_path.is_file():
        try:
            traces = _read_jsonl(traces_path)
        except (OSError, json.JSONDecodeError):
            traces = []
    failed_traces = [trace for trace in traces if _status(trace) != "PASSED"]
    first_failure = ""
    if failed_traces:
        first_failure = str(
            failed_traces[0].get("uuid")
            or failed_traces[0].get("workload_id")
            or ""
        )
    cascade_count = max(0, len(failed_traces) - 1) if first_failure else 0
    if any(marker in evidence for marker in reference_markers):
        scope = "reference"
    elif any(marker in evidence for marker in candidate_markers):
        scope = "candidate"
    elif result.get("evaluation_valid"):
        scope = "candidate"
    else:
        scope = "unknown"
    if result.get("evaluation_valid"):
        completeness = "complete"
    elif scope == "reference":
        completeness = "reference_failed"
    elif result.get("total_workloads", 0):
        completeness = "partial"
    else:
        completeness = "no_trace"
    return {
        "evaluation_scope": scope,
        "evaluation_completeness": completeness,
        "first_failure_workload": first_failure,
        "cascade_failure_count": cascade_count,
    }


def classify_operator_evaluation(result: dict[str, Any]) -> dict[str, Any]:
    """Map evaluator evidence to a deterministic repair role."""
    category = str(result.get("failure_category") or "")
    scope = str(result.get("evaluation_scope") or "unknown")
    completeness = str(result.get("evaluation_completeness") or "unknown")
    if result.get("operator_passed") is True:
        return {
            "category": "passed",
            "repair_role": "none",
            "evaluation_completeness": completeness,
            "requires_single_workload_debug": False,
        }
    if category in {"artifact_writeback_failed", "candidate_invalid", "stale_candidate"}:
        return {
            "category": category,
            "repair_role": "artifact_repair",
            "evaluation_completeness": "not_evaluated",
            "requires_single_workload_debug": False,
        }
    if category in {"resource_exhausted", "staging_missing", "cleanup_failed"}:
        return {
            "category": category,
            "repair_role": "operator_environment_fixer",
            "evaluation_completeness": completeness,
            "requires_single_workload_debug": False,
        }
    if scope == "reference" or completeness == "reference_failed":
        return {
            "category": "reference_unavailable",
            "repair_role": "operator_environment_fixer",
            "evaluation_completeness": "reference_failed",
            "requires_single_workload_debug": False,
        }
    if category in {
        "device_memory_fault",
        "device_sync_hang",
        "host_crash",
        "no_trace_timeout",
        "partial_trace_timeout",
    }:
        return {
            "category": category,
            "repair_role": "operator_fixer",
            "evaluation_completeness": completeness,
            "requires_single_workload_debug": True,
            "first_failure_workload": result.get("first_failure_workload", ""),
        }
    if category in {"compile_error", "incomplete_trace"}:
        return {
            "category": category,
            "repair_role": "operator_fixer",
            "evaluation_completeness": completeness,
            "requires_single_workload_debug": False,
        }
    return {
        "category": category or "inconclusive",
        "repair_role": "operator_fixer" if scope == "candidate" else "none",
        "evaluation_completeness": completeness,
        "requires_single_workload_debug": False,
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


def validate_operator_artifact(
    solution: str | Path,
    task_dir: str | Path,
    *,
    baseline_sha256: str = "",
    require_changed: bool = False,
) -> dict[str, Any]:
    """Check whether the current candidate can be evaluated safely."""
    solution_path = Path(solution).expanduser().resolve()
    task_root = Path(task_dir).expanduser().resolve()
    candidate_manifest = build_candidate_manifest(solution_path)
    errors = list(candidate_manifest["errors"])
    source_dir = solution_path.parent / "sources"
    artifact_complete = solution_path.is_file() and solution_path.stat().st_size > 0
    if source_dir.exists() and not source_dir.is_dir():
        errors.append("sources exists but is not a directory")
        artifact_complete = False
    if candidate_manifest["writeback_status"] == "mismatch":
        errors.append("embedded source content differs from materialized source")
    solution_sha256 = candidate_manifest["solution_sha256"]
    candidate_changed = bool(solution_sha256 and solution_sha256 != baseline_sha256)
    stale_error = (
        "solution.json did not change after a rebuild attempt"
        if require_changed and not candidate_changed and not errors
        else ""
    )
    if stale_error:
        errors.append(stale_error)
    candidate_evaluable = not errors
    return {
        "operation": "operator_artifact_validate",
        "solution_path": str(solution_path),
        "task_dir": str(task_root),
        "solution_ready": not errors,
        "artifact_complete": artifact_complete and not errors,
        "candidate_evaluable": candidate_evaluable,
        "candidate_changed": candidate_changed,
        "stale_candidate": stale_error != "",
        "rebuild_required": bool(errors),
        "errors": errors,
        "solution_sha256": solution_sha256,
        "candidate_manifest": candidate_manifest,
        "embedded_source_sha256": candidate_manifest["embedded_source_sha256"],
        "materialized_source_sha256": candidate_manifest["materialized_source_sha256"],
        "writeback_status": candidate_manifest["writeback_status"],
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
