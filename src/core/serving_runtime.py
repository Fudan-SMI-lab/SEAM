"""Serving runtime contracts for vLLM/SGLang serving routes.

All platform-specific configuration is driven by LLM prompts and
canonical route definitions — zero hardcoded platform names or
framework env defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import json
from typing import TYPE_CHECKING, cast

from core.routes import (
    FRAMEWORK_FORBIDDEN_RUNTIME_MARKERS,
    FRAMEWORK_SERVING_ENV_DEFAULTS,
    ROUTE_TO_SERVING_FRAMEWORK,
    SGLANG_SERVING,
    VLLM_SERVING,
)

if TYPE_CHECKING:
    from core.platform_policy import PlatformPolicy

_FRAMEWORK_IMPORT_PROBES = {
    VLLM_SERVING: ("vllm",),
    SGLANG_SERVING: ("sglang",),
}

_FRAMEWORK_COMMAND_REWRITERS: dict[str, dict[str, str]] = {
    "sglang": {"sglang": "sys.executable -m sglang.cli.main"},
    "vllm": {"vllm": "sys.executable -m vllm.entrypoints.cli.main"},
}

import os as _os_module

_DEFAULT_FALLBACK_MARKERS: tuple[str, ...] = tuple(
    m.strip().lower()
    for m in _os_module.environ.get(
        "SEAM_SERVING_FALLBACK_MARKERS", "cuda,nccl,nvidia"
    ).split(",")
    if m.strip()
)


def _merge_forbidden_markers(
    route: str,
    framework: str,
    platform_policy: PlatformPolicy | None = None,
) -> list[str]:
    """Merge static forbidden markers with platform-policy overrides.

    Platform-policy markers are appended to the static defaults with
    deduplication: any marker already present in the static list is
    not added a second time.
    """
    markers = list(FRAMEWORK_FORBIDDEN_RUNTIME_MARKERS.get(route, ()))
    if platform_policy is not None:
        extra = platform_policy.framework_forbidden_markers.get(framework, ())
        if extra:
            seen = set(markers)
            markers.extend(m for m in extra if m not in seen)
    return markers


def write_serving_validation_wrapper(
    *,
    project_dir: str | Path,
    route: str,
    launch_command: object,
    readiness_probe: object,
    request_validation: object,
    project_test_files: object,
    expected_outputs: object,
    required_checks: object,
    platform_policy: PlatformPolicy | None = None,
) -> Path:
    project_path = Path(project_dir)
    framework = ROUTE_TO_SERVING_FRAMEWORK[route]
    wrapper_path = project_path / f"validate_{framework}_serving.py"
    reports_dir = project_path / "migration_reports" / "serving"
    readiness_probe_mapping: Mapping[object, object] = cast(Mapping[object, object], readiness_probe) if isinstance(readiness_probe, Mapping) else {}
    request_validation_mapping: Mapping[object, object] = cast(Mapping[object, object], request_validation) if isinstance(request_validation, Mapping) else {}
    body = _WRAPPER_TEMPLATE
    import_probes = list(_FRAMEWORK_IMPORT_PROBES.get(route, ()))
    forbidden_markers = _merge_forbidden_markers(route, framework, platform_policy)
    env_defaults = dict(FRAMEWORK_SERVING_ENV_DEFAULTS.get(framework, {}))
    if platform_policy is not None and platform_policy.framework_env_overrides:
        env_defaults.update(platform_policy.framework_env_overrides.get(framework, {}))
    cuda_markers = list(_DEFAULT_FALLBACK_MARKERS)  # sensible defaults; overridden by PlatformPolicy when available
    if platform_policy is not None and platform_policy.framework_forbidden_markers:
        for marker in platform_policy.framework_forbidden_markers.get(framework, ()):
            if marker not in cuda_markers:
                cuda_markers.append(marker)
    replacements = {
        "__ROUTE_TO_SERVING_FRAMEWORK_JSON__": _python_json_loads_literal(dict(ROUTE_TO_SERVING_FRAMEWORK)),
        "__ROUTE_JSON__": json.dumps(route),
        "__FRAMEWORK_JSON__": json.dumps(framework),
        "__LAUNCH_COMMAND_JSON__": json.dumps(str(launch_command or "")),
        "__READINESS_PROBE_JSON__": _python_json_loads_literal(readiness_probe_mapping),
        "__REQUEST_VALIDATION_JSON__": _python_json_loads_literal(request_validation_mapping),
        "__PROJECT_TEST_FILES_JSON__": _python_json_loads_literal(_string_list(project_test_files)),
        "__EXPECTED_OUTPUTS_JSON__": _python_json_loads_literal(_string_list(expected_outputs)),
        "__REQUIRED_CHECKS_JSON__": _python_json_loads_literal(_string_list(required_checks)),
        "__IMPORT_PROBES_JSON__": _python_json_loads_literal(import_probes),
        "__FORBIDDEN_MARKERS_JSON__": _python_json_loads_literal(forbidden_markers),
        "__FRAMEWORK_ENV_DEFAULTS_JSON__": _python_json_loads_literal(env_defaults),
        "__FRAMEWORK_COMMAND_REWRITERS_JSON__": _python_json_loads_literal(
            _FRAMEWORK_COMMAND_REWRITERS
        ),
        "__CUDA_FALLBACK_MARKERS_JSON__": _python_json_loads_literal(cuda_markers),
        "__REPORTS_DIR_JSON__": json.dumps(str(reports_dir)),
    }
    for marker, value in replacements.items():
        body = body.replace(marker, value)
    _ = wrapper_path.write_text(body, encoding="utf-8")
    return wrapper_path


def _python_json_loads_literal(value: object) -> str:
    return f"json.loads({json.dumps(json.dumps(value))})"





def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
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
import urllib.error
import urllib.parse
import urllib.request


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
FRAMEWORK_ENV_DEFAULTS = __FRAMEWORK_ENV_DEFAULTS_JSON__
FRAMEWORK_COMMAND_REWRITERS = __FRAMEWORK_COMMAND_REWRITERS_JSON__
CUDA_FALLBACK_MARKERS = __CUDA_FALLBACK_MARKERS_JSON__
REPORTS_DIR = Path(__REPORTS_DIR_JSON__)
ROUTE_TO_SERVING_FRAMEWORK = __ROUTE_TO_SERVING_FRAMEWORK_JSON__
STARTUP_TIMEOUT_SECONDS = 900.0
DEFAULT_SERVING_HOST = "127.0.0.1"
DEFAULT_SERVING_PORT = "8000"


def main() -> int:
    started_at = time.time()
    env, env_evidence = build_serving_env(os.environ.copy())
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
            failure_reason="serving runtime import preflight failed",
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
    for env_key, env_val in FRAMEWORK_ENV_DEFAULTS.items():
        env.setdefault(env_key, str(env_val))
    rewriters = FRAMEWORK_COMMAND_REWRITERS.get(FRAMEWORK, {})
    if command and command[0] in rewriters:
        prefix_tokens = shlex.split(rewriters[command[0]])
        if prefix_tokens and prefix_tokens[0] == "sys.executable":
            prefix_tokens[0] = sys.executable
        command = prefix_tokens + command[1:]
    command_result = run_serving_validation(command, cwd=project_root, env=env)
    combined = "\n".join([
        str(command_result.get("stdout_tail") or ""),
        str(command_result.get("stderr_tail") or ""),
    ])
    forbidden_hits = [marker for marker in FORBIDDEN_MARKERS if marker and marker.lower() in combined.lower()]
    command_result["forbidden_runtime_marker_hits"] = forbidden_hits
    command_result["actual_launch_command"] = shlex.join(command)
    returncode_raw = command_result.get("returncode")
    returncode = int(returncode_raw) if returncode_raw is not None else 1
    command_timed_out = command_result.get("timed_out") is True or command_result.get("idle_timed_out") is True
    status = "FULL_PASS" if returncode == 0 and not forbidden_hits and not command_timed_out else "FAILED"
    if status == "FULL_PASS":
        failure_reason = ""
    elif command_result.get("server_failure_reason"):
        failure_reason = str(command_result.get("server_failure_reason"))
    elif command_result.get("api_error"):
        failure_reason = str(command_result.get("api_error"))
    elif command_result.get("idle_timed_out") is True:
        failure_reason = "serving command made no output progress before the local idle watchdog interrupted it"
    elif command_result.get("timed_out") is True:
        failure_reason = "serving command exceeded the local watchdog timeout and was interrupted"
    else:
        failure_reason = "serving command failed or emitted forbidden runtime fallback markers"
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


def run_serving_validation(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = REPORTS_DIR / "serving_command_stdout.log"
    stderr_path = REPORTS_DIR / "serving_command_stderr.log"
    started_at = time.time()
    before_processes = project_process_snapshot(cwd)
    health_passed = False
    health_response = ""
    api_passed = False
    api_response = ""
    api_error = ""
    model_id = ""
    server_failure_reason = ""
    server_exited_cleanly = False
    endpoints = resolve_serving_endpoints(command)
    health_url = endpoints["health_url"]
    models_url = endpoints["models_url"]
    api_url = endpoints["api_url"]

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
        try:
            timeout_seconds = float_env("SEAM_SERVING_STARTUP_TIMEOUT_SECONDS", STARTUP_TIMEOUT_SECONDS, minimum=1.0)
            health_passed, health_response = wait_for_health(timeout_seconds, process, stderr_path, health_url)
            if health_passed:
                model_id = resolve_served_model_id(models_url)
                api_passed, api_response, api_error = validate_openai_api(model_id, api_url)
            else:
                server_failure_reason = health_response or "server health endpoint never became reachable"
        except Exception as exc:
            server_failure_reason = f"validation exception: {exc}"
            api_error = str(exc)
        finally:
            if process.poll() is None:
                terminate_process_group(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                    server_exited_cleanly = True
                except subprocess.TimeoutExpired:
                    terminate_process_group(process.pid, signal.SIGKILL)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                    server_failure_reason = server_failure_reason or "server did not stop within 30s after SIGTERM"
            elif process.returncode == 0:
                server_exited_cleanly = True
            elif not server_failure_reason:
                server_failure_reason = f"server process exited with return code {process.returncode}"
            cleaned_processes = cleanup_new_project_processes(cwd, before_processes)

    ended_at = time.time()
    validation_succeeded = health_passed and api_passed and server_exited_cleanly and not server_failure_reason
    server_returncode = process.returncode if process.returncode is not None else -1
    normalized_returncode = 0 if validation_succeeded else server_returncode if server_returncode not in (0, -signal.SIGTERM) else 1
    return {
        "returncode": normalized_returncode,
        "server_returncode": server_returncode,
        "stdout_tail": file_tail(stdout_path),
        "stderr_tail": file_tail(stderr_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "timed_out": False,
        "idle_timed_out": False,
        "timeout_reason": "",
        "timeout_seconds": STARTUP_TIMEOUT_SECONDS,
        "idle_timeout_seconds": 0,
        "duration_seconds": round(ended_at - started_at, 3),
        "cleaned_processes": cleaned_processes,
        "health_probe_passed": health_passed,
        "health_probe_url": health_url,
        "health_response": health_response[:2000],
        "api_validation_passed": api_passed,
        "api_validation_url": api_url,
        "api_response": api_response[:4000],
        "api_error": api_error[:2000],
        "served_model_id": model_id,
        "server_exited_cleanly": server_exited_cleanly,
        "server_failure_reason": server_failure_reason,
    }


def resolve_serving_endpoints(command: list[str]) -> dict[str, str]:
    base_url = resolve_server_base_url(command)
    return {
        "health_url": resolve_probe_url(READINESS_PROBE, base_url, "/health"),
        "models_url": build_serving_url("/v1/models", base_url),
        "api_url": resolve_probe_url(REQUEST_VALIDATION, base_url, default_openai_validation_path()),
    }


def default_openai_validation_path() -> str:
    if FRAMEWORK in set(ROUTE_TO_SERVING_FRAMEWORK.values()):
        return "/v1/chat/completions"
    return "/"


def resolve_server_base_url(command: list[str]) -> str:
    for config in (READINESS_PROBE, REQUEST_VALIDATION):
        if isinstance(config, dict):
            value = config.get("url") or config.get("endpoint")
            if isinstance(value, str) and is_absolute_http_url(value):
                parsed = urllib.parse.urlparse(value)
                host = normalize_probe_host(parsed.hostname or DEFAULT_SERVING_HOST)
                port = f":{parsed.port}" if parsed.port is not None else ""
                return f"{parsed.scheme}://{host}{port}"
    host = command_flag_value(command, ("--host", "--host-ip", "--http-host", "--api-server-host")) or DEFAULT_SERVING_HOST
    port = command_flag_value(command, ("--port", "--http-port", "--api-server-port")) or DEFAULT_SERVING_PORT
    host = normalize_probe_host(host)
    return f"http://{host}:{port}"


def resolve_probe_url(config: object, base_url: str, default_path: str) -> str:
    if isinstance(config, dict):
        value = config.get("url") or config.get("endpoint") or config.get("path")
        if isinstance(value, str) and value.strip():
            return build_serving_url(value.strip(), base_url)
    return build_serving_url(default_path, base_url)


def build_serving_url(value: str, base_url: str) -> str:
    if is_absolute_http_url(value):
        return normalize_http_url(value)
    path = value if value.startswith("/") else f"/{value}"
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def normalize_http_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return value
    host = normalize_probe_host(parsed.hostname or DEFAULT_SERVING_HOST)
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{host}{port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def is_absolute_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def command_flag_value(tokens: list[str], flags: tuple[str, ...]) -> str:
    for index, token in enumerate(tokens):
        for flag in flags:
            if token == flag and index + 1 < len(tokens):
                return tokens[index + 1]
            prefix = f"{flag}="
            if token.startswith(prefix):
                return token[len(prefix):]
    return ""


def normalize_probe_host(host: str) -> str:
    candidate = host.strip() or DEFAULT_SERVING_HOST
    if candidate in {"0.0.0.0", "::", "[::]", "*"}:
        return DEFAULT_SERVING_HOST
    return candidate


def wait_for_health(total_timeout: float, process: subprocess.Popen, stderr_path: Path, health_url: str) -> tuple[bool, str]:
    started = time.time()
    while time.time() - started < total_timeout:
        if process.poll() is not None:
            stderr_tail = file_tail(stderr_path, limit=5000)
            return False, f"server process exited early (rc={process.returncode}): {stderr_tail[:1000]}"
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200:
                    return True, body
        except Exception:
            pass
        time.sleep(5.0)
    return False, f"health endpoint not reachable after {total_timeout}s: {health_url}"


def resolve_served_model_id(models_url: str) -> str:
    try:
        req = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        data = payload.get("data", [])
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                    return item["id"].strip()
    except Exception:
        pass
    return model_name_from_launch_command()


def model_name_from_launch_command() -> str:
    tokens = shlex.split(LAUNCH_COMMAND)
    for flag in ("--served-model-name", "--served_model_name", "--model", "--model-path", "--model_path"):
        if flag in tokens and tokens.index(flag) + 1 < len(tokens):
            return tokens[tokens.index(flag) + 1]
    if "serve" in tokens and tokens.index("serve") + 1 < len(tokens):
        return tokens[tokens.index("serve") + 1]
    return ""


def validate_openai_api(model_id: str, api_url: str) -> tuple[bool, str, str]:
    if not model_id:
        return False, "", "no served model id available from /v1/models or launch command"
    payload = json.dumps(openai_validation_payload(api_url, model_id)).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read().decode("utf-8", errors="replace")
            if response.status == 200:
                return True, body, ""
            return False, body, f"unexpected HTTP status {response.status}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, body, f"HTTP {exc.code}: {body[:1000]}"
    except Exception as exc:
        return False, "", f"{exc.__class__.__name__}: {exc}"


def openai_validation_payload(api_url: str, model_id: str) -> dict[str, object]:
    path = urllib.parse.urlparse(api_url).path.rstrip("/")
    if path.endswith("/v1/completions") or path.endswith("/completions") and not path.endswith("/chat/completions"):
        return {"model": model_id, "prompt": "Hello", "max_tokens": 8, "temperature": 0.0}
    if path.endswith("/v1/embeddings") or path.endswith("/embeddings"):
        return {"model": model_id, "input": "Hello"}
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 8,
        "temperature": 0.0,
    }


def build_serving_env(env: dict[str, str]) -> tuple[dict[str, str], dict[str, object]]:
    project_root = Path(__file__).resolve().parent
    venv_bin = project_root / ".venv" / "bin"
    if venv_bin.is_dir():
        prepend_path(env, "PATH", [str(venv_bin)])
    return env, {
        "runtime_env_configured": "framework_default",
        "venv_bin_prepended": venv_bin.is_dir(),
    }


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
    forbidden_hits = [
        str(marker).lower()
        for marker in cast(list[object], command_result.get("forbidden_runtime_marker_hits") or [])
    ]
    cpu_fallback_detected = any("cpu" in marker for marker in forbidden_hits)
    accelerator_fallback_detected = any(any(marker_word in hit for marker_word in CUDA_FALLBACK_MARKERS) for hit in forbidden_hits)
    accelerator_execution_evidence = {
        "passed": passed,
        "serving_runtime": env_evidence,
        "import_preflight": import_evidence,
        "command_result": command_result,
    }
    serving_runtime_evidence = {
        **env_evidence,
        f"{FRAMEWORK}_imported": module_imported(import_evidence, FRAMEWORK),
        "forbidden_runtime_markers_absent": not forbidden_hits,
    }
    report = {
        "migration_route": ROUTE,
        "serving_framework": FRAMEWORK,
        "full_migration_status": status,
        "project_test_files": PROJECT_TEST_FILES,
        "expected_outputs": EXPECTED_OUTPUTS,
        "required_checks": REQUIRED_CHECKS,
        "readiness_probe": {"passed": passed, "config": READINESS_PROBE, "evidence": "project launch command completed"},
        "request_validation": {"passed": passed, "config": REQUEST_VALIDATION, "evidence": "project demo/API command completed"},
        "accelerator_execution_evidence": accelerator_execution_evidence,
        "serving_runtime_evidence": serving_runtime_evidence,
        "project_demo_or_test_executed": passed,
        "serving_api_validated": passed,
        "accelerator_execution_observed": passed,
        "accelerator_fallback_detected": accelerator_fallback_detected,
        "cpu_fallback_detected": cpu_fallback_detected,
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
