#!/usr/bin/env python3
"""Standalone OpenCode readiness diagnostic for SEAM users.

Run this script from any directory. It checks whether the OpenCode server side
is ready for SEAM and explains the most likely root cause when it is not.
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any
import re


DEFAULT_URL = "http://127.0.0.1:4098"
DIAG_PROMPT = "Reply with exactly SEAM_DIAG_OK and no other text."
LOCAL_NO_PROXY_VALUES = ("127.0.0.1", "localhost", "::1")

EXIT_READY = 0
EXIT_BASIC_READY = 20
EXIT_SERVER_UNREACHABLE = 40
EXIT_AGENT_UNAVAILABLE = 41
EXIT_SESSION_UNAVAILABLE = 42
EXIT_MESSAGE_UNAVAILABLE = 43
EXIT_INVALID_ARGUMENT = 50


def run_cmd(
    cmd: list[str],
    *,
    timeout: int = 5,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            cwd=str(cwd) if cwd else None,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": f"timed out after {timeout}s",
        }


def parse_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlsplit(url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"invalid server URL: {url}")
    return parsed


def default_port(parsed: urllib.parse.ParseResult) -> int:
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def is_local_host(hostname: str) -> bool:
    return hostname in {"127.0.0.1", "localhost", "::1"}


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def merge_csv_values(existing: str, required: tuple[str, ...]) -> str:
    parts = [part.strip() for part in existing.split(",") if part.strip()]
    seen = set(parts)
    for item in required:
        if item not in seen:
            parts.append(item)
            seen.add(item)
    return ",".join(parts)


def build_env_patch(server_url: str, env: dict[str, str] | None = None) -> dict[str, str]:
    current = env if env is not None else os.environ
    patch: dict[str, str] = {}
    try:
        parsed = parse_url(server_url)
    except ValueError:
        return patch

    if is_local_host(parsed.hostname or ""):
        merged_upper = merge_csv_values(current.get("NO_PROXY", ""), LOCAL_NO_PROXY_VALUES)
        merged_lower = merge_csv_values(current.get("no_proxy", ""), LOCAL_NO_PROXY_VALUES)
        if merged_upper != current.get("NO_PROXY", ""):
            patch["NO_PROXY"] = merged_upper
        if merged_lower != current.get("no_proxy", ""):
            patch["no_proxy"] = merged_lower
    if current.get("PYTHONUNBUFFERED") != "1":
        patch["PYTHONUNBUFFERED"] = "1"
    return patch


def emit_env_patch(patch: dict[str, str]) -> None:
    for key in ("NO_PROXY", "no_proxy", "PYTHONUNBUFFERED"):
        if key in patch:
            print(f"export {key}={shell_quote(patch[key])}")


def print_env_preflight_report(server_url: str, env_patch: dict[str, str]) -> None:
    print_section("SEAM OpenCode Environment Preflight")
    print(f"server_url: {server_url.rstrip('/')}")
    if env_patch:
        print("finding: launcher environment needs normalization before OpenCode readiness checks")
        for key, value in env_patch.items():
            print(f"applied_preflight_fix: export {key}={value}")
    else:
        print("finding: launcher environment already satisfies OpenCode preflight checks")
        print("applied_preflight_fix: none")


def resolve_start_cwd(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def http_request(
    parsed: urllib.parse.ParseResult,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 5,
    max_body: int = 65536,
) -> dict[str, Any]:
    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    try:
        conn = connection_cls(parsed.hostname, default_port(parsed), timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read(max_body)
            text = raw.decode("utf-8", errors="replace")
            parsed_json: Any = None
            try:
                parsed_json = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed_json = None
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "reason": response.reason,
                "body": text.strip(),
                "json": parsed_json,
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - diagnostic output should expose the concrete failure.
        return {"ok": False, "status": None, "reason": type(exc).__name__, "body": str(exc)}


def extract_session_id(response: dict[str, Any]) -> str:
    payload = response.get("json")
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if isinstance(data, dict):
            for key in ("id", "sessionID", "session_id"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    body = str(response.get("body") or "")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if isinstance(data, dict):
            value = data.get("id")
            if isinstance(value, str):
                return value
    return ""


def extract_message_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        parts = [extract_message_text(item) for item in payload]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(payload, dict):
        for key in ("content", "text", "message", "response"):
            value = payload.get(key)
            text = extract_message_text(value)
            if text:
                return text
        parts = payload.get("parts")
        text = extract_message_text(parts)
        if text:
            return text
        data = payload.get("data")
        if data is not payload:
            text = extract_message_text(data)
            if text:
                return text
    return ""


def agent_probe(agent_response: dict[str, Any]) -> dict[str, Any]:
    payload = agent_response.get("json")
    names: list[str] = []
    malformed = False
    agents = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(agents, list):
        for item in agents:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
    elif isinstance(agents, dict):
        for key, value in agents.items():
            if isinstance(value, dict):
                name = value.get("name") if isinstance(value.get("name"), str) else key
                if name:
                    names.append(str(name))
            elif isinstance(key, str):
                names.append(key)
    else:
        malformed = bool(agent_response.get("ok"))
    return {"names": sorted(set(names)), "count": len(set(names)), "malformed": malformed}


def session_message_probe(
    parsed: urllib.parse.ParseResult,
    *,
    enabled: bool,
    agent: str = "",
    timeout: int = 120,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "ok": None, "skipped": True, "reason": "message probe disabled"}

    create = http_request(
        parsed,
        "POST",
        "/session",
        body=json.dumps({"title": "seam-diagnose-message-probe"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    session_id = extract_session_id(create)
    result: dict[str, Any] = {
        "enabled": True,
        "ok": False,
        "session_id": session_id,
        "create": create,
        "message": None,
        "status": None,
        "history": None,
        "cleanup": None,
        "response_text": "",
        "error": "",
    }
    if not create.get("ok") or not session_id:
        result["error"] = "failed to create diagnostic session"
        return result

    payload: dict[str, Any] = {"parts": [{"type": "text", "text": DIAG_PROMPT}]}
    if agent:
        payload["agent"] = agent
    message = http_request(
        parsed,
        "POST",
        f"/session/{session_id}/message",
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
        max_body=262144,
    )
    result["message"] = message
    if message.get("ok"):
        result["response_text"] = extract_message_text(message.get("json")) or extract_message_text(message.get("body"))
    else:
        result["error"] = str(message.get("body") or message.get("reason") or "message request failed")[:1000]

    status = http_request(parsed, "GET", "/session/status", timeout=10, max_body=262144)
    history = http_request(parsed, "GET", f"/session/{session_id}/message?limit=1", timeout=10, max_body=262144)
    cleanup = http_request(parsed, "DELETE", f"/session/{session_id}", timeout=10)
    result["status"] = status
    result["history"] = history
    result["cleanup"] = cleanup

    if not result["response_text"] and history.get("ok"):
        result["response_text"] = extract_message_text(history.get("json")) or extract_message_text(history.get("body"))
    result["ok"] = bool(message.get("ok") and result["response_text"])
    result["contains_marker"] = "SEAM_DIAG_OK" in str(result.get("response_text") or "")
    if not result["ok"] and not result["error"]:
        result["error"] = "message endpoint returned no usable response text"
    return result


def cleanup_created_session(parsed: urllib.parse.ParseResult, create_response: dict[str, Any]) -> dict[str, Any] | None:
    session_id = extract_session_id(create_response)
    if not session_id:
        return None
    return http_request(parsed, "DELETE", f"/session/{session_id}", timeout=10)


def curl_probe(url: str, *, force_no_proxy: bool) -> dict[str, Any]:
    if shutil.which("curl") is None:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "curl not found", "http_status": None}
    env = os.environ.copy()
    if force_no_proxy:
        existing = env.get("NO_PROXY") or env.get("no_proxy") or ""
        parts = [part for part in existing.split(",") if part]
        for required in ["127.0.0.1", "localhost", "::1"]:
            if required not in parts:
                parts.append(required)
        merged = ",".join(parts)
        env["NO_PROXY"] = merged
        env["no_proxy"] = merged
    result = run_cmd(
        ["curl", "-sS", "-i", "--max-time", "5", f"{url.rstrip('/')}/agent"],
        timeout=8,
        env=env,
    )
    stdout = str(result.get("stdout") or "")
    status: int | None = None
    for line in stdout.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])
    result["http_status"] = status
    if status is not None:
        result["ok"] = 200 <= status < 300
    return result


def redact_url_secret(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if not parsed.username and not parsed.password:
        return value
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    redacted_netloc = f"***:***@{host}"
    return urllib.parse.urlunsplit((parsed.scheme, redacted_netloc, parsed.path, parsed.query, parsed.fragment))


def redact_proxy_env(proxies: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in proxies.items():
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            redacted[key] = redact_url_secret(value)
        else:
            redacted[key] = value
    return redacted


def port_probe(host: str, port: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=3):
            return {"open": True, "error": "", "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"open": False, "error": f"{type(exc).__name__}: {exc}", "elapsed_ms": int((time.monotonic() - started) * 1000)}


def listener_probe(port: int) -> dict[str, Any]:
    if shutil.which("ss"):
        result = run_cmd(["ss", "-ltnp"], timeout=5)
        lines = [line for line in result.get("stdout", "").splitlines() if f":{port}" in line]
        return {"tool": "ss", "lines": lines, "error": result.get("stderr", "")}
    if shutil.which("lsof"):
        result = run_cmd(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=5)
        lines = result.get("stdout", "").splitlines()
        return {"tool": "lsof", "lines": lines, "error": result.get("stderr", "")}
    return {"tool": None, "lines": [], "error": "neither ss nor lsof found"}


def listener_pid(listener: dict[str, Any]) -> int | None:
    for line in listener.get("lines") or []:
        match = re.search(r"pid=(\d+)", line)
        if match:
            return int(match.group(1))
        parts = line.split()
        if listener.get("tool") == "lsof" and len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
    return None


def process_cwd(pid: int | None) -> str:
    if pid is None:
        return ""
    proc_cwd = Path("/proc") / str(pid) / "cwd"
    try:
        return str(proc_cwd.resolve(strict=True))
    except OSError:
        return ""


def effective_start_cwd(value: str | None, inferred_server_cwd: str) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    if inferred_server_cwd:
        return Path(inferred_server_cwd).resolve()
    return Path.cwd().resolve()


def opencode_info() -> dict[str, Any]:
    path = shutil.which("opencode")
    info: dict[str, Any] = {"path": path or "", "version": "", "version_error": ""}
    if path:
        version = run_cmd([path, "--version"], timeout=5)
        info["version"] = version.get("stdout") or version.get("stderr") or ""
        info["version_error"] = "" if version.get("ok") else version.get("stderr", "")
    return info


def config_probe(start_cwd: Path) -> list[dict[str, Any]]:
    candidates = [
        start_cwd / ".opencode" / "opencode.jsonc",
        start_cwd / ".opencode" / "opencode.json",
        start_cwd / ".opencode" / "oh-my-opencode.json",
        Path.home() / ".config" / "opencode" / "opencode.jsonc",
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".config" / "opencode" / "oh-my-opencode.json",
        Path.home() / ".opencode" / "opencode.jsonc",
        Path.home() / ".opencode" / "opencode.json",
        Path.home() / ".opencode" / "oh-my-opencode.json",
    ]
    seen: set[Path] = set()
    results: list[dict[str, Any]] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        exists = path.exists()
        results.append({"path": str(path), "exists": exists, "size": path.stat().st_size if exists else None})
    return results


def proxy_probe() -> dict[str, str]:
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
    return {key: os.environ.get(key, "") for key in keys if os.environ.get(key)}


def start_opencode(parsed: urllib.parse.ParseResult, work_dir: Path, wait_seconds: int) -> dict[str, Any]:
    binary = shutil.which("opencode")
    if not binary:
        return {"started": False, "error": "opencode not found in PATH"}
    if not is_local_host(parsed.hostname or ""):
        return {"started": False, "error": "auto-start probe only supports local URLs"}

    port = default_port(parsed)
    hostname = parsed.hostname or "127.0.0.1"
    log_file = tempfile.NamedTemporaryFile(
        prefix="seam-opencode-diagnose-",
        suffix=".log",
        delete=False,
        mode="wb",
    )
    cmd = [binary, "serve", "--port", str(port), "--hostname", hostname]
    proc = subprocess.Popen(cmd, cwd=str(work_dir), stdout=log_file, stderr=subprocess.STDOUT)
    deadline = time.time() + wait_seconds
    agent: dict[str, Any] = {"ok": False, "status": None, "reason": "not checked", "body": ""}
    session: dict[str, Any] = {"ok": False, "status": None, "reason": "not checked", "body": ""}
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        agent = http_request(parsed, "GET", "/agent", timeout=3)
        if agent.get("ok"):
            session = http_request(
                parsed,
                "POST",
                "/session",
                body=json.dumps({"title": "seam-diagnose-autostart"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=3,
            )
            if session.get("ok"):
                break
        time.sleep(1)

    running = proc.poll() is None
    if running:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log_file.close()
    try:
        log_tail = Path(log_file.name).read_text(errors="replace")[-4000:].strip()
    except OSError:
        log_tail = ""
    return {
        "started": True,
        "cmd": " ".join(cmd),
        "log_path": log_file.name,
        "process_returncode": proc.returncode,
        "agent_after_start": agent,
        "session_after_start": session,
        "log_tail": log_tail,
    }


def has_localhost_no_proxy(proxies: dict[str, str]) -> bool:
    values = [value for key, value in proxies.items() if key.lower() == "no_proxy"]
    return any("127.0.0.1" in value or "localhost" in value or "::1" in value for value in values)


def summarize(
    listener: dict[str, Any],
    socket_check: dict[str, Any],
    agent_direct: dict[str, Any],
    session_direct: dict[str, Any],
    curl_current: dict[str, Any],
    curl_no_proxy: dict[str, Any],
    open_info: dict[str, Any],
    proxies: dict[str, str],
    configs: list[dict[str, Any]],
    start_result: dict[str, Any] | None,
    agent_details: dict[str, Any],
    message_probe: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    findings: list[str] = []
    actions: list[str] = []
    cause = "undetermined"

    listener_text = "\n".join(listener.get("lines") or [])
    direct_status = agent_direct.get("status")
    session_status = session_direct.get("status")
    existing_configs = [item for item in configs if item.get("exists")]

    if proxies and not has_localhost_no_proxy(proxies):
        findings.append("Proxy variables are set, but NO_PROXY/no_proxy does not include 127.0.0.1, localhost, or ::1.")
    if not existing_configs:
        findings.append("No common OpenCode config file was found for the current user and SEAM root.")
    if agent_direct.get("ok") and agent_details.get("malformed"):
        findings.append("GET /agent returned HTTP 2xx but the response was not a recognizable agent list.")
    if agent_direct.get("ok") and agent_details.get("count") == 0:
        findings.append("GET /agent returned HTTP 2xx but no agent names could be extracted.")

    if curl_current.get("ok") is False and curl_no_proxy.get("ok") is True:
        cause = "localhost requests are being routed through a proxy"
        actions.append("Run `export NO_PROXY=127.0.0.1,localhost,::1` and `export no_proxy=127.0.0.1,localhost,::1`, then rerun SEAM.")
    elif socket_check.get("open") is False:
        if not open_info.get("path"):
            cause = "OpenCode is not installed or not in PATH, and no server is listening"
            actions.append("Install OpenCode or fix PATH, then verify `opencode serve --port 4098 --hostname 127.0.0.1` works.")
        else:
            cause = "no process is listening on the configured OpenCode port"
            actions.append("Start OpenCode manually, wait for `/agent` to return 200, then rerun SEAM with `--server-no-auto-start`.")
    elif listener_text and "opencode" not in listener_text.lower():
        cause = "the configured port is occupied by a non-OpenCode process"
        actions.append("Stop the conflicting process or pass SEAM a different `--server_url` using a free local port.")
    elif direct_status == 503:
        cause = "OpenCode is reachable but not ready or misconfigured; /agent returns HTTP 503"
        actions.append("Run `opencode serve --port 4098 --hostname 127.0.0.1` in the foreground and fix the provider/model/API-key error shown in its log.")
        actions.append("If running as root, configure OpenCode and API-key environment for root, or rerun SEAM as the user that owns the working OpenCode config.")
    elif direct_status and direct_status != 200:
        cause = f"OpenCode /agent returns HTTP {direct_status}, not 200"
        actions.append("Inspect the foreground OpenCode server log and fix the reported server-side error.")
    elif direct_status == 200 and (not session_status or not (200 <= int(session_status) < 300)):
        cause = f"/agent is healthy but POST /session fails with HTTP {session_status}"
        actions.append("Restart OpenCode manually and verify session creation before running SEAM.")
    elif direct_status == 200 and session_status and 200 <= int(session_status) < 300:
        if message_probe.get("enabled") is False:
            cause = "OpenCode server passed basic /agent and /session checks; message probe was skipped"
            actions.append("Run this diagnostic without `--no-message-probe` to verify model-backed session messaging before running SEAM.")
        elif message_probe.get("ok"):
            cause = "OpenCode server is ready for SEAM"
            actions.append("Run SEAM with `--server-no-auto-start` to reuse the verified server, or keep auto-start enabled if no existing server is running.")
        else:
            cause = "OpenCode session creation works but message round-trip failed"
            actions.append("Inspect the OpenCode server log and fix model/provider/API-key errors before running SEAM.")
            if message_probe.get("error"):
                findings.append(f"Message probe error: {message_probe.get('error')}")

    if curl_current.get("http_status") == 503 and direct_status != 503:
        findings.append("curl sees HTTP 503 while direct Python HTTP does not; this strongly indicates proxy interference or a transient readiness race.")
    if curl_no_proxy.get("http_status") == 503 and direct_status == 503:
        findings.append("Both curl with NO_PROXY and direct Python HTTP see HTTP 503, so the 503 is from the local OpenCode service.")

    if start_result:
        if not start_result.get("started"):
            findings.append(f"Auto-start probe did not run: {start_result.get('error')}")
        else:
            agent_after = start_result.get("agent_after_start", {})
            session_after = start_result.get("session_after_start", {})
            if agent_after.get("status") == 503:
                findings.append("A freshly auto-started OpenCode process also returned HTTP 503 on /agent.")
                if cause == "undetermined":
                    cause = "fresh OpenCode auto-start reaches the port but fails readiness with HTTP 503"
            elif agent_after.get("ok") and session_after.get("ok"):
                findings.append("A freshly auto-started OpenCode process passed both /agent and /session checks.")

    return cause, findings, actions


def print_section(title: str) -> None:
    print(f"\n== {title} ==")


def readiness_status(
    socket_check: dict[str, Any],
    agent_direct: dict[str, Any],
    session_direct: dict[str, Any],
    message_probe: dict[str, Any],
) -> tuple[str, int]:
    if socket_check.get("open") is False:
        return "server_unreachable", EXIT_SERVER_UNREACHABLE
    if not agent_direct.get("ok"):
        return "agent_unavailable", EXIT_AGENT_UNAVAILABLE
    if not session_direct.get("ok"):
        return "session_unavailable", EXIT_SESSION_UNAVAILABLE
    if message_probe.get("enabled") is False or message_probe.get("skipped"):
        return "basic_ready", EXIT_BASIC_READY
    if not message_probe.get("ok"):
        return "message_unavailable", EXIT_MESSAGE_UNAVAILABLE
    return "ready", EXIT_READY


def print_human_report(
    *,
    args: argparse.Namespace,
    host: str,
    port: int,
    start_cwd: Path,
    start_cwd_source: str,
    pid: int | None,
    open_info: dict[str, Any],
    socket_check: dict[str, Any],
    agent_direct: dict[str, Any],
    agent_details: dict[str, Any],
    session_direct: dict[str, Any],
    session_direct_cleanup: dict[str, Any] | None,
    curl_current: dict[str, Any],
    curl_no_proxy: dict[str, Any],
    listener: dict[str, Any],
    proxies: dict[str, str],
    configs: list[dict[str, Any]],
    start_result: dict[str, Any] | None,
    message_probe: dict[str, Any],
    status: str,
    cause: str,
    findings: list[str],
    actions: list[str],
    env_patch: dict[str, str],
) -> None:
    print_section("SEAM OpenCode Readiness Diagnostic")
    print(f"server_url: {args.server_url.rstrip('/')}")
    print(f"status: {status}")
    print(f"user: {getpass.getuser()}")
    print(f"start_cwd: {start_cwd}")
    print(f"start_cwd_source: {start_cwd_source}")
    if pid is not None:
        print(f"server_pid: {pid}")
    print(f"opencode: {open_info.get('path') or 'not found'}")
    if open_info.get("version"):
        print(f"opencode_version: {open_info['version']}")

    print_section("Preflight Environment")
    if env_patch:
        for key, value in env_patch.items():
            print(f"applied_preflight_fix: export {key}={value}")
    else:
        print("applied_preflight_fix: none")

    print_section("Connectivity")
    print(f"socket {host}:{port}: {'open' if socket_check.get('open') else 'closed'} {socket_check.get('error', '')}".rstrip())
    print(f"direct GET /agent: HTTP {agent_direct.get('status')} {agent_direct.get('reason')}")
    if agent_direct.get("body"):
        print(f"direct /agent body: {str(agent_direct.get('body'))[:500]}")
    if agent_details.get("names"):
        print(f"agents: {', '.join(agent_details['names'])}")
    print(f"direct POST /session: HTTP {session_direct.get('status')} {session_direct.get('reason')}")
    if session_direct.get("body"):
        print(f"direct /session body: {str(session_direct.get('body'))[:500]}")
    if session_direct_cleanup:
        print(f"direct DELETE diagnostic session: HTTP {session_direct_cleanup.get('status')} {session_direct_cleanup.get('reason')}")

    print_section("Curl Comparison")
    print(f"curl current env: rc={curl_current.get('returncode')} http={curl_current.get('http_status')} ok={curl_current.get('ok')}")
    if curl_current.get("stderr"):
        print(f"curl current stderr: {curl_current.get('stderr')}")
    print(f"curl forced NO_PROXY: rc={curl_no_proxy.get('returncode')} http={curl_no_proxy.get('http_status')} ok={curl_no_proxy.get('ok')}")
    if curl_no_proxy.get("stderr"):
        print(f"curl forced stderr: {curl_no_proxy.get('stderr')}")

    print_section("Port Listener")
    lines = listener.get("lines") or []
    if lines:
        for line in lines:
            print(line)
    else:
        print(listener.get("error") or "no listener found by probe")

    print_section("Environment")
    safe_proxies = redact_proxy_env(proxies)
    if proxies:
        for key, value in safe_proxies.items():
            print(f"{key}={value}")
    else:
        print("no proxy variables set")
    existing_configs = [item for item in configs if item.get("exists")]
    if existing_configs:
        for item in existing_configs:
            print(f"config: {item['path']} size={item['size']}")
    else:
        print("no common OpenCode config files found")

    if start_result:
        print_section("Auto-start Probe")
        print(f"started: {start_result.get('started')}")
        if start_result.get("cmd"):
            print(f"cmd: {start_result.get('cmd')}")
        agent_after = start_result.get("agent_after_start") or {}
        session_after = start_result.get("session_after_start") or {}
        print(f"agent_after_start: HTTP {agent_after.get('status')} {agent_after.get('reason')}")
        print(f"session_after_start: HTTP {session_after.get('status')} {session_after.get('reason')}")
        if start_result.get("log_path"):
            print(f"log_path: {start_result.get('log_path')}")
        if start_result.get("log_tail"):
            print("log_tail:")
            print(str(start_result.get("log_tail"))[-2000:])

    print_section("Session Message Probe")
    if message_probe.get("enabled") is False:
        print("message_probe: skipped")
    elif message_probe.get("skipped"):
        print(f"message_probe: skipped ({message_probe.get('reason')})")
    else:
        print(f"message_probe_ok: {message_probe.get('ok')}")
        print(f"message_session_id: {message_probe.get('session_id')}")
        msg = message_probe.get("message") or {}
        status_probe = message_probe.get("status") or {}
        history = message_probe.get("history") or {}
        cleanup = message_probe.get("cleanup") or {}
        print(f"POST /session/{{id}}/message: HTTP {msg.get('status')} {msg.get('reason')}")
        print(f"GET /session/status: HTTP {status_probe.get('status')} {status_probe.get('reason')}")
        print(f"GET /session/{{id}}/message: HTTP {history.get('status')} {history.get('reason')}")
        print(f"DELETE /session/{{id}}: HTTP {cleanup.get('status')} {cleanup.get('reason')}")
        print(f"message_contains_marker: {message_probe.get('contains_marker')}")
        if message_probe.get("response_text"):
            print(f"message_response: {str(message_probe.get('response_text'))[:500]}")
        if message_probe.get("error"):
            print(f"message_error: {message_probe.get('error')}")

    print_section("Conclusion")
    print(f"likely_cause: {cause}")
    for finding in findings:
        print(f"finding: {finding}")
    for action in actions:
        print(f"next_step: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone OpenCode readiness diagnostic for SEAM.")
    parser.add_argument("--server-url", default=DEFAULT_URL, help=f"OpenCode base URL (default: {DEFAULT_URL})")
    parser.add_argument("--start-cwd", help="Directory used only for project-local .opencode lookup and --try-start. Defaults to local server process cwd when detectable, otherwise current directory.")
    parser.add_argument("--seam-root", help=argparse.SUPPRESS)
    parser.add_argument("--try-start", action="store_true", help="Temporarily start OpenCode from --start-cwd if the endpoint is not healthy.")
    parser.add_argument("--wait", type=int, default=30, help="Seconds to wait during --try-start (default: 30).")
    parser.add_argument("--agent", default="", help="Optional OpenCode agent name for the message round-trip probe.")
    parser.add_argument("--mode", choices=("env", "off", "basic", "message"), default="message", help="Diagnostic mode: env emits only shell fixes, off skips HTTP readiness, basic checks /agent and /session, message also checks model-backed messaging.")
    parser.add_argument("--message-timeout", type=int, default=120, help="Seconds to wait for the message round-trip probe (default: 120).")
    parser.add_argument("--no-message-probe", action="store_true", help="Skip the model-backed /session/{id}/message round-trip probe.")
    parser.add_argument("--emit-env", action="store_true", help="Print shell exports for safe launcher-side environment fixes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON in addition to the human report.")
    parser.add_argument("--json-only", action="store_true", help="Print only machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        parsed = parse_url(args.server_url)
    except ValueError as exc:
        if args.json_only:
            print(json.dumps({"ok": False, "status": "invalid_url", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID_ARGUMENT

    env_patch = build_env_patch(args.server_url)
    if args.emit_env:
        emit_env_patch(env_patch)
        if args.mode == "env":
            return EXIT_READY

    if args.mode == "env":
        if args.json_only:
            print(json.dumps({
                "ok": True,
                "status": "env_preflight",
                "server_url": args.server_url.rstrip("/"),
                "env_patch": env_patch,
            }, ensure_ascii=False, indent=2))
        else:
            print_env_preflight_report(args.server_url, env_patch)
        return EXIT_READY

    port = default_port(parsed)
    host = parsed.hostname or "127.0.0.1"
    proxies = proxy_probe()
    open_info = opencode_info()
    listener = listener_probe(port)
    pid = listener_pid(listener) if is_local_host(host) else None
    inferred_server_cwd = process_cwd(pid)
    start_cwd_value = args.start_cwd or args.seam_root
    start_cwd = effective_start_cwd(start_cwd_value, inferred_server_cwd)
    if start_cwd_value:
        start_cwd_source = "argument"
    elif inferred_server_cwd:
        start_cwd_source = "local_server_process_cwd"
    else:
        start_cwd_source = "current_directory_fallback"
    configs = config_probe(start_cwd)
    socket_check = port_probe(host, port)
    agent_direct = http_request(parsed, "GET", "/agent") if args.mode != "off" else {
        "ok": False,
        "status": None,
        "reason": "skipped",
        "body": "mode=off",
    }
    agent_details = agent_probe(agent_direct)
    session_direct = http_request(
        parsed,
        "POST",
        "/session",
        body=json.dumps({"title": "seam-diagnose-health-check"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    ) if args.mode in {"basic", "message"} else {
        "ok": False,
        "status": None,
        "reason": "skipped",
        "body": f"mode={args.mode}",
    }
    session_direct_cleanup = cleanup_created_session(parsed, session_direct)
    curl_current = curl_probe(args.server_url, force_no_proxy=False) if args.mode != "off" else {"ok": None, "skipped": True}
    curl_no_proxy = curl_probe(args.server_url, force_no_proxy=True) if args.mode != "off" else {"ok": None, "skipped": True}
    message_enabled = args.mode == "message" and not args.no_message_probe
    message_probe = session_message_probe(
        parsed,
        enabled=message_enabled,
        agent=args.agent,
        timeout=args.message_timeout,
    ) if message_enabled and agent_direct.get("ok") and session_direct.get("ok") else {
        "enabled": message_enabled,
        "ok": False,
        "skipped": True,
        "reason": "message probe disabled" if not message_enabled else "basic /agent or /session check failed",
    }

    start_result: dict[str, Any] | None = None
    should_try_start = args.try_start and is_local_host(host) and not (agent_direct.get("ok") and session_direct.get("ok"))
    if should_try_start:
        start_result = start_opencode(parsed, start_cwd, args.wait)

    cause, findings, actions = summarize(
        listener,
        socket_check,
        agent_direct,
        session_direct,
        curl_current,
        curl_no_proxy,
        open_info,
        proxies,
        configs,
        start_result,
        agent_details,
        message_probe,
    )

    status, exit_code = readiness_status(socket_check, agent_direct, session_direct, message_probe)
    if args.mode == "off":
        status = "skipped"
        exit_code = EXIT_READY
        cause = "OpenCode readiness check skipped"

    safe_proxies = redact_proxy_env(proxies)
    result = {
        "ok": exit_code in {EXIT_READY, EXIT_BASIC_READY},
        "status": status,
        "exit_code": exit_code,
        "server_url": args.server_url.rstrip("/"),
        "user": getpass.getuser(),
        "mode": args.mode,
        "start_cwd": str(start_cwd),
        "start_cwd_source": start_cwd_source,
        "server_pid": pid,
        "inferred_server_cwd": inferred_server_cwd,
        "opencode": open_info,
        "configs": configs,
        "proxies": safe_proxies,
        "env_patch": env_patch,
        "listener": listener,
        "socket": socket_check,
        "agent_direct": agent_direct,
        "agent_details": agent_details,
        "session_direct": session_direct,
        "session_direct_cleanup": session_direct_cleanup,
        "message_probe": message_probe,
        "curl_current": curl_current,
        "curl_no_proxy": curl_no_proxy,
        "auto_start_probe": start_result,
        "likely_cause": cause,
        "findings": findings,
        "next_steps": actions,
    }

    if args.json_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human_report(
            args=args,
            host=host,
            port=port,
            start_cwd=start_cwd,
            start_cwd_source=start_cwd_source,
            pid=pid,
            open_info=open_info,
            socket_check=socket_check,
            agent_direct=agent_direct,
            agent_details=agent_details,
            session_direct=session_direct,
            session_direct_cleanup=session_direct_cleanup,
            curl_current=curl_current,
            curl_no_proxy=curl_no_proxy,
            listener=listener,
            proxies=proxies,
            configs=configs,
            start_result=start_result,
            message_probe=message_probe,
            status=status,
            cause=cause,
            findings=findings,
            actions=actions,
            env_patch=env_patch,
        )

    if args.json and not args.json_only:
        print_section("JSON")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
