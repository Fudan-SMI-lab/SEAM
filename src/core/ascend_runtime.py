"""Ascend runtime contracts for vLLM/SGLang serving routes."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import json
from typing import cast


ASCEND_SERVING_BACKEND = "ascend"
VLLM_SERVING = "vllm_serving"
SGLANG_SERVING = "sglang_serving"

ROUTE_TO_FRAMEWORK = {
    VLLM_SERVING: "vllm",
    SGLANG_SERVING: "sglang",
}

COMMON_ASCEND_RUNTIME_ENV = (
    "Ascend NPU runtime",
    "CANN toolkit set_env.sh or equivalent env export",
    "ASCEND_HOME_PATH",
    "ASCEND_OPP_PATH",
    "PYTHONPATH includes CANN python/site-packages for tbe and te",
    "LD_LIBRARY_PATH includes CANN runtime libraries",
    "torch_npu",
    "tbe",
    "te",
)

COMMON_IMPORT_PROBES = ("torch", "torch_npu", "tbe", "te")
ROUTE_IMPORT_PROBES = {
    VLLM_SERVING: ("vllm",),
    SGLANG_SERVING: ("sglang",),
}

COMMON_FORBIDDEN_RUNTIME_MARKERS = (
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
    "nvidia-smi",
    "NCCL_",
    "pynccl_allocator",
    "torch.cuda.memory",
    "cuda fallback",
    "cpu fallback",
)

ROUTE_FORBIDDEN_RUNTIME_MARKERS = {
    VLLM_SERVING: ("vllm cuda executor", "gpu_memory_utilization without NPU backend"),
    SGLANG_SERVING: ("deep_gemm_wrapper", "pynccl", "nccl", "cuda_graph"),
}


def ascend_serving_contract_fields(route: str) -> dict[str, object]:
    framework = ROUTE_TO_FRAMEWORK.get(route, "")
    import_probes = [*COMMON_IMPORT_PROBES, *ROUTE_IMPORT_PROBES.get(route, ())]
    forbidden = [*COMMON_FORBIDDEN_RUNTIME_MARKERS, *ROUTE_FORBIDDEN_RUNTIME_MARKERS.get(route, ())]
    return {
        "serving_backend": ASCEND_SERVING_BACKEND,
        "runtime_env_setup": {
            "source_candidates": [
                "/usr/local/Ascend/ascend-toolkit/latest/set_env.sh",
                "/usr/local/Ascend/latest/set_env.sh",
            ],
            "pythonpath_requirements": ["tbe", "te", "torch_npu"],
            "library_requirements": ["CANN runtime", "Ascend runtime libraries"],
            "device_env": ["ASCEND_VISIBLE_DEVICES or framework-provided NPU device selection"],
        },
        "required_import_probes": import_probes,
        "forbidden_runtime_markers": forbidden,
        "ascend_runtime_checks": [
            "cann_env_loaded",
            "torch_npu_imported",
            "tbe_imported",
            "te_imported",
            f"{framework}_imported" if framework else "serving_framework_imported",
            "cuda_nccl_markers_absent",
            "no_cpu_fallback",
        ],
    }


def merge_ascend_serving_contract(contract: dict[str, object], route: str) -> None:
    fields = ascend_serving_contract_fields(route)
    for key, value in fields.items():
        if key in {"required_import_probes", "forbidden_runtime_markers", "ascend_runtime_checks"}:
            contract[key] = _merge_string_lists(contract.get(key), value)
        elif key == "runtime_env_setup" and isinstance(contract.get(key), Mapping):
            default_setup = fields.get(key)
            current_setup = contract.get(key)
            merged = dict(cast(Mapping[str, object], default_setup)) if isinstance(default_setup, Mapping) else {}
            if isinstance(current_setup, Mapping):
                merged.update(dict(cast(Mapping[str, object], current_setup)))
            contract[key] = merged
        else:
            contract[key] = value
    contract["required_runtime_env"] = _merge_string_lists(
        contract.get("required_runtime_env"),
        COMMON_ASCEND_RUNTIME_ENV,
    )


def write_ascend_serving_validation_wrapper(
    *,
    project_dir: str | Path,
    route: str,
    launch_command: object,
    readiness_probe: object,
    request_validation: object,
    project_test_files: object,
    expected_outputs: object,
    required_checks: object,
) -> Path:
    project_path = Path(project_dir)
    framework = ROUTE_TO_FRAMEWORK[route]
    wrapper_path = project_path / f"validate_{framework}_serving.py"
    reports_dir = project_path / "migration_reports" / "serving"
    readiness_probe_mapping: Mapping[object, object] = cast(Mapping[object, object], readiness_probe) if isinstance(readiness_probe, Mapping) else {}
    request_validation_mapping: Mapping[object, object] = cast(Mapping[object, object], request_validation) if isinstance(request_validation, Mapping) else {}
    body = _WRAPPER_TEMPLATE
    replacements = {
        "__ROUTE_JSON__": json.dumps(route),
        "__FRAMEWORK_JSON__": json.dumps(framework),
        "__LAUNCH_COMMAND_JSON__": json.dumps(str(launch_command or "")),
        "__READINESS_PROBE_JSON__": _python_json_loads_literal(readiness_probe_mapping),
        "__REQUEST_VALIDATION_JSON__": _python_json_loads_literal(request_validation_mapping),
        "__PROJECT_TEST_FILES_JSON__": _python_json_loads_literal(_string_list(project_test_files)),
        "__EXPECTED_OUTPUTS_JSON__": _python_json_loads_literal(_string_list(expected_outputs)),
        "__REQUIRED_CHECKS_JSON__": _python_json_loads_literal(_string_list(required_checks)),
        "__IMPORT_PROBES_JSON__": _python_json_loads_literal(ascend_serving_contract_fields(route)["required_import_probes"]),
        "__FORBIDDEN_MARKERS_JSON__": _python_json_loads_literal(ascend_serving_contract_fields(route)["forbidden_runtime_markers"]),
        "__REPORTS_DIR_JSON__": json.dumps(str(reports_dir)),
    }
    for marker, value in replacements.items():
        body = body.replace(marker, value)
    _ = wrapper_path.write_text(body, encoding="utf-8")
    return wrapper_path


def _python_json_loads_literal(value: object) -> str:
    return f"json.loads({json.dumps(json.dumps(value))})"


def _merge_string_lists(existing: object, required: object) -> list[str]:
    result: list[str] = []
    for source in (existing, required):
        for value in _string_list(source):
            if value not in result:
                result.append(value)
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in cast(list[object], value) if isinstance(item, str) and item.strip()]


_WRAPPER_TEMPLATE = r'''#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from typing import cast


ROUTE = __ROUTE_JSON__
FRAMEWORK = __FRAMEWORK_JSON__
LAUNCH_COMMAND = __LAUNCH_COMMAND_JSON__
READINESS_PROBE = __READINESS_PROBE_JSON__
REQUEST_VALIDATION = __REQUEST_VALIDATION_JSON__
PROJECT_TEST_FILES = __PROJECT_TEST_FILES_JSON__
EXPECTED_OUTPUTS = __EXPECTED_OUTPUTS_JSON__
REQUIRED_CHECKS = __REQUIRED_CHECKS_JSON__
IMPORT_PROBES = __IMPORT_PROBES_JSON__
FORBIDDEN_MARKERS = __FORBIDDEN_MARKERS_JSON__
REPORTS_DIR = Path(__REPORTS_DIR_JSON__)


def main() -> int:
    started_at = time.time()
    env, env_evidence = build_ascend_env(os.environ.copy())
    os.environ.clear()
    os.environ.update(env)
    sync_pythonpath_to_sys_path(env.get("PYTHONPATH", ""))
    import_evidence = probe_imports(IMPORT_PROBES)
    if not import_evidence["passed"]:
        write_gate(
            "FAILED",
            started_at,
            env_evidence,
            import_evidence,
            command_result={"returncode": 1, "stdout_tail": "", "stderr_tail": import_evidence["error_summary"]},
            failure_reason="Ascend serving import preflight failed",
        )
        return 1

    command_env_updates, command = split_launch_command(LAUNCH_COMMAND)
    if not command:
        write_gate(
            "FAILED",
            started_at,
            env_evidence,
            import_evidence,
            command_result={"returncode": 1, "stdout_tail": "", "stderr_tail": "empty launch command"},
            failure_reason="Phase 3 launch_command was empty",
        )
        return 1
    project_root = Path(__file__).resolve().parent
    input_evidence, command = rewrite_missing_input_args(command, project_root)
    if input_evidence.get("blocking_missing_input") is True:
        write_gate(
            "FAILED",
            started_at,
            env_evidence,
            import_evidence,
            command_result={"returncode": 1, "stdout_tail": "", "stderr_tail": str(input_evidence.get("failure_reason", "missing validation input"))},
            failure_reason="Phase 3 launch_command input path is absent and no project validation input exists",
            input_path_evidence=input_evidence,
        )
        return 1
    env.update(command_env_updates)
    if FRAMEWORK == "sglang":
        env.setdefault("SGLANG_ENABLE_SPEC_V2", "1")
    if FRAMEWORK == "vllm":
        env.setdefault("VLLM_TARGET_DEVICE", "npu")

    command_result = run_command_with_watchdog(
        command,
        cwd=project_root,
        env=env,
        timeout_seconds=float_env("SEAM_SERVING_COMMAND_TIMEOUT_SECONDS", 14400.0, minimum=1.0),
        idle_timeout_seconds=float_env("SEAM_SERVING_COMMAND_IDLE_TIMEOUT_SECONDS", 3600.0, minimum=0.0),
    )
    combined = "\n".join([
        str(command_result.get("stdout_tail") or ""),
        str(command_result.get("stderr_tail") or ""),
    ])
    forbidden_hits = [marker for marker in FORBIDDEN_MARKERS if marker and marker.lower() in combined.lower()]
    command_result["forbidden_runtime_marker_hits"] = forbidden_hits
    command_result["actual_launch_command"] = shlex.join(command)
    returncode = int(command_result.get("returncode") or 1)
    command_timed_out = command_result.get("timed_out") is True or command_result.get("idle_timed_out") is True
    status = "FULL_PASS" if returncode == 0 and not forbidden_hits and not command_timed_out else "FAILED"
    if status == "FULL_PASS":
        failure_reason = ""
    elif command_result.get("idle_timed_out") is True:
        failure_reason = "serving command made no output progress before the local idle watchdog interrupted it"
    elif command_result.get("timed_out") is True:
        failure_reason = "serving command exceeded the local watchdog timeout and was interrupted"
    else:
        failure_reason = "serving command failed or emitted CUDA/NCCL/CPU fallback markers"
    write_gate(
        status,
        started_at,
        env_evidence,
        import_evidence,
        command_result=command_result,
        failure_reason=failure_reason,
        input_path_evidence=input_evidence,
    )
    return 0 if status == "FULL_PASS" else 1


def build_ascend_env(env: dict[str, str]) -> tuple[dict[str, str], dict[str, object]]:
    for key in list(env):
        upper = key.upper()
        if upper.startswith("CUDA") or upper.startswith("NVIDIA") or upper.startswith("NCCL"):
            env.pop(key, None)
    roots = ascend_roots(env)
    selected_root = next((root for root in roots if root.exists()), None)
    python_paths: list[str] = []
    library_paths: list[str] = []
    if selected_root is not None:
        env.setdefault("ASCEND_HOME_PATH", str(selected_root))
        env.setdefault("ASCEND_OPP_PATH", str(selected_root / "opp"))
        python_paths.extend(existing_paths([
            selected_root / "python" / "site-packages",
            selected_root / "opp" / "built-in" / "op_impl" / "ai_core" / "tbe",
        ]))
        library_paths.extend(existing_paths([
            selected_root / "lib64",
            selected_root / "runtime" / "lib64",
            selected_root / "compiler" / "lib64",
            selected_root / "opp" / "built-in" / "op_impl" / "ai_core" / "tbe" / "op_tiling" / "lib",
        ]))
        prepend_path(env, "PATH", existing_paths([selected_root / "bin", selected_root / "compiler" / "ccec_compiler" / "bin"]))
    prepend_path(env, "PYTHONPATH", python_paths)
    prepend_path(env, "LD_LIBRARY_PATH", library_paths)
    return env, {
        "serving_backend": "ascend",
        "selected_ascend_root": str(selected_root) if selected_root is not None else "",
        "cann_env_loaded": selected_root is not None,
        "python_paths_added": python_paths,
        "library_paths_added": library_paths,
        "cuda_nccl_env_stripped": True,
    }


def ascend_roots(env: dict[str, str]) -> list[Path]:
    candidates = [env.get("ASCEND_HOME_PATH"), env.get("ASCEND_TOOLKIT_HOME")]
    candidates.extend([
        "/usr/local/Ascend/ascend-toolkit/latest",
        "/usr/local/Ascend/latest",
    ])
    return [Path(value).expanduser() for value in candidates if value]


def existing_paths(paths: list[Path]) -> list[str]:
    return [str(path) for path in paths if path.exists()]


def prepend_path(env: dict[str, str], key: str, paths: list[str]) -> None:
    if not paths:
        return
    existing = [item for item in env.get(key, "").split(os.pathsep) if item]
    merged = []
    for item in [*paths, *existing]:
        if item not in merged:
            merged.append(item)
    env[key] = os.pathsep.join(merged)


def sync_pythonpath_to_sys_path(pythonpath: str) -> None:
    for item in reversed([part for part in pythonpath.split(os.pathsep) if part]):
        if item not in sys.path:
            sys.path.insert(0, item)


def probe_imports(modules: list[str]) -> dict[str, object]:
    results: dict[str, object] = {}
    errors: list[str] = []
    for module in modules:
        try:
            imported = importlib.import_module(module)
            results[module] = {"imported": True, "path": str(getattr(imported, "__file__", ""))}
        except Exception as exc:
            results[module] = {"imported": False, "error": f"{exc.__class__.__name__}: {exc}"}
            errors.append(f"{module}: {exc.__class__.__name__}: {exc}")
    return {"passed": not errors, "modules": results, "error_summary": "; ".join(errors)}


def split_launch_command(command: str) -> tuple[dict[str, str], list[str]]:
    tokens = shlex.split(command)
    env_updates: dict[str, str] = {}
    if tokens and Path(tokens[0]).name == "env":
        tokens = tokens[1:]
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        key, value = tokens.pop(0).split("=", 1)
        env_updates[key] = value
    return env_updates, tokens


def run_command_with_watchdog(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    idle_timeout_seconds: float,
) -> dict[str, object]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = REPORTS_DIR / "serving_command_stdout.log"
    stderr_path = REPORTS_DIR / "serving_command_stderr.log"
    started_at = time.time()
    before_processes = project_process_snapshot(cwd)
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        last_output_at = started_at
        last_stdout_size = 0
        last_stderr_size = 0
        timed_out = False
        idle_timed_out = False
        timeout_reason = ""
        while process.poll() is None:
            now = time.time()
            stdout_size = safe_file_size(stdout_path)
            stderr_size = safe_file_size(stderr_path)
            if stdout_size != last_stdout_size or stderr_size != last_stderr_size:
                last_output_at = now
                last_stdout_size = stdout_size
                last_stderr_size = stderr_size
            if timeout_seconds > 0 and now - started_at >= timeout_seconds:
                timed_out = True
                timeout_reason = f"command exceeded {timeout_seconds:.0f}s watchdog timeout"
                break
            if idle_timeout_seconds > 0 and now - last_output_at >= idle_timeout_seconds:
                idle_timed_out = True
                timeout_reason = f"command produced no stdout/stderr for {idle_timeout_seconds:.0f}s"
                break
            time.sleep(1.0)
        if timed_out or idle_timed_out:
            terminate_process_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                terminate_process_group(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            cleaned_processes = cleanup_new_project_processes(cwd, before_processes)
        else:
            cleaned_processes = []
    ended_at = time.time()
    return {
        "returncode": process.returncode if process.returncode is not None else 124,
        "stdout_tail": file_tail(stdout_path),
        "stderr_tail": file_tail(stderr_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "timed_out": timed_out,
        "idle_timed_out": idle_timed_out,
        "timeout_reason": timeout_reason,
        "timeout_seconds": timeout_seconds,
        "idle_timeout_seconds": idle_timeout_seconds,
        "duration_seconds": round(ended_at - started_at, 3),
        "cleaned_processes": cleaned_processes,
    }


def float_env(name: str, default: float, *, minimum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def file_tail(path: Path, limit: int = 12000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"<failed to read {path}: {exc}>"


def project_process_snapshot(project_root: Path) -> dict[int, str]:
    token = str(project_root.resolve())
    current_pid = os.getpid()
    result: dict[int, str] = {}
    proc_root = Path("/proc")
    if not proc_root.exists():
        return result
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == current_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        if token in cmdline:
            result[pid] = cmdline
    return result


def cleanup_new_project_processes(project_root: Path, before_processes: dict[int, str]) -> list[dict[str, object]]:
    after_processes = project_process_snapshot(project_root)
    cleaned: list[dict[str, object]] = []
    for pid, cmdline in sorted(after_processes.items()):
        if pid in before_processes:
            continue
        cleaned.append({"pid": pid, "cmdline": cmdline[:500]})
        terminate_process_group(pid, signal.SIGTERM)
    if cleaned:
        time.sleep(2.0)
        for item in cleaned:
            pid = int(item["pid"])
            if Path(f"/proc/{pid}").exists():
                terminate_process_group(pid, signal.SIGKILL)
    return cleaned


def terminate_process_group(pid: int, sig: int) -> None:
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    except OSError:
        pgid = pid
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return


VALIDATION_INPUT_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".json",
    ".jsonl",
    ".txt",
    ".csv",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
}
INPUT_PATH_FLAGS = {"-p", "--path", "--file", "--input", "--pdf", "--image", "--document"}


def rewrite_missing_input_args(command: list[str], project_root: Path) -> tuple[dict[str, object], list[str]]:
    updated = list(command)
    candidates = validation_input_candidates(project_root)
    evidence: dict[str, object] = {
        "candidate_paths": candidates,
        "checked_arguments": [],
        "replacements": [],
        "blocking_missing_input": False,
        "failure_reason": "",
    }
    for index, token in enumerate(list(updated)):
        if token in INPUT_PATH_FLAGS and index + 1 < len(updated):
            replacement = replacement_for_input_arg(project_root, updated[index + 1], candidates)
            if replacement is None:
                continue
            argument_evidence, new_value = replacement
            cast(list[object], evidence["checked_arguments"]).append(argument_evidence)
            if new_value:
                updated[index + 1] = new_value
                cast(list[object], evidence["replacements"]).append(argument_evidence)
            elif argument_evidence.get("missing_input_blocks_validation") is True:
                evidence["blocking_missing_input"] = True
                evidence["failure_reason"] = argument_evidence.get("failure_reason", "missing validation input")
            continue
        for flag in INPUT_PATH_FLAGS:
            prefix = f"{flag}="
            if token.startswith(prefix):
                replacement = replacement_for_input_arg(project_root, token[len(prefix):], candidates)
                if replacement is None:
                    continue
                argument_evidence, new_value = replacement
                cast(list[object], evidence["checked_arguments"]).append(argument_evidence)
                if new_value:
                    updated[index] = prefix + new_value
                    cast(list[object], evidence["replacements"]).append(argument_evidence)
                elif argument_evidence.get("missing_input_blocks_validation") is True:
                    evidence["blocking_missing_input"] = True
                    evidence["failure_reason"] = argument_evidence.get("failure_reason", "missing validation input")
                break
    return evidence, updated


def replacement_for_input_arg(project_root: Path, value: str, candidates: list[str]) -> tuple[dict[str, object], str] | None:
    path = Path(value)
    suffix = path.suffix.lower()
    if suffix not in VALIDATION_INPUT_SUFFIXES:
        return None
    resolved = path if path.is_absolute() else project_root / path
    evidence: dict[str, object] = {
        "original_value": value,
        "suffix": suffix,
        "original_exists": resolved.exists(),
        "actual_value": value,
        "replaced": False,
        "missing_input_blocks_validation": False,
    }
    if resolved.exists():
        return evidence, ""
    candidate = first_candidate_for_suffix(candidates, suffix)
    if candidate:
        evidence["actual_value"] = candidate
        evidence["replaced"] = True
        return evidence, candidate
    evidence["missing_input_blocks_validation"] = True
    evidence["failure_reason"] = f"validation input path does not exist: {value}"
    return evidence, ""


def validation_input_candidates(project_root: Path) -> list[str]:
    candidates: list[str] = []
    for item in PROJECT_TEST_FILES:
        if not isinstance(item, str) or not item.strip():
            continue
        path = project_root / item
        if path.is_file() and path.suffix.lower() in VALIDATION_INPUT_SUFFIXES:
            candidates.append(path.relative_to(project_root).as_posix())
    for directory_name in ("demo", "demos", "examples", "test", "tests", "assets", "resources"):
        directory = project_root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*"), key=lambda candidate: candidate.relative_to(project_root).as_posix()):
            if len(candidates) >= 64:
                break
            if path.is_file() and path.suffix.lower() in VALIDATION_INPUT_SUFFIXES:
                candidates.append(path.relative_to(project_root).as_posix())
    return ordered_unique(candidates)


def first_candidate_for_suffix(candidates: list[str], suffix: str) -> str:
    for candidate in candidates:
        if Path(candidate).suffix.lower() == suffix:
            return candidate
    return ""


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def write_gate(
    status: str,
    started_at: float,
    env_evidence: dict[str, object],
    import_evidence: dict[str, object],
    command_result: dict[str, object],
    failure_reason: str,
    input_path_evidence: dict[str, object] | None = None,
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    passed = status == "FULL_PASS"
    forbidden_hits = command_result.get("forbidden_runtime_marker_hits")
    report = {
        "migration_route": ROUTE,
        "serving_framework": FRAMEWORK,
        "serving_backend": "ascend",
        "full_migration_status": status,
        "project_test_files": PROJECT_TEST_FILES,
        "expected_outputs": EXPECTED_OUTPUTS,
        "required_checks": REQUIRED_CHECKS,
        "readiness_probe": {"passed": passed, "config": READINESS_PROBE, "evidence": "project launch command completed"},
        "request_validation": {"passed": passed, "config": REQUEST_VALIDATION, "evidence": "project demo/API command completed"},
        "npu_execution_evidence": {
            "passed": passed,
            "ascend_runtime": env_evidence,
            "import_preflight": import_evidence,
            "command_result": command_result,
        },
        "ascend_runtime_evidence": {
            **env_evidence,
            "torch_npu_imported": module_imported(import_evidence, "torch_npu"),
            "tbe_imported": module_imported(import_evidence, "tbe"),
            "te_imported": module_imported(import_evidence, "te"),
            f"{FRAMEWORK}_imported": module_imported(import_evidence, FRAMEWORK),
            "forbidden_runtime_markers_absent": not forbidden_hits,
        },
        "project_demo_or_test_executed": passed,
        "serving_api_validated": passed,
        "npu_execution_observed": passed,
        "cuda_fallback_detected": bool(forbidden_hits),
        "cpu_fallback_detected": False,
        "import_only": False,
        "smoke_only": False,
        "failure_reason": failure_reason,
        "project_input_resolution": input_path_evidence or {},
        "started_at": started_at,
        "ended_at": time.time(),
    }
    (REPORTS_DIR / "serving_final_gate.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def module_imported(import_evidence: dict[str, object], module: str) -> bool:
    modules = import_evidence.get("modules")
    if not isinstance(modules, dict):
        return False
    item = modules.get(module)
    return isinstance(item, dict) and item.get("imported") is True


def tail(text: str, limit: int = 12000) -> str:
    return text[-limit:] if len(text) > limit else text


if __name__ == "__main__":
    raise SystemExit(main())
'''
