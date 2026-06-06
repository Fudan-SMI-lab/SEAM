"""OpenCode server lifecycle management - auto start/stop for E2E tests."""

from dataclasses import dataclass
import http.client
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from typing import Literal

ServerProcess = subprocess.Popen[bytes]
ServerType = Literal["opencode"]
ServerState = Literal["free", "matching", "conflict"]

DEFAULT_SERVER_TYPE: ServerType = "opencode"


@dataclass(frozen=True)
class ServerSpec:
    server_url: str
    hostname: str
    port: int


@dataclass(frozen=True)
class ServerProbe:
    state: ServerState
    base_url: str
    detail: str = ""


@dataclass(frozen=True)
class ManagedServer:
    base_url: str
    port: int
    process: ServerProcess | None
    reused: bool
    started: bool


def find_available_port(start: int = 4096, end: int = 4099) -> int:
    """Find a free TCP port in [start, end] inclusive."""
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports in range [{start}, {end}]")


def parse_server_url(server_url: str) -> ServerSpec:
    raw_url = server_url.strip()
    if not raw_url:
        raise ValueError("server_url must not be empty")
    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("server_url must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("server_url must include a hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("server_url includes an invalid port") from exc
    if port is None:
        raise ValueError("server_url must include an explicit port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("server_url must be a base URL like http://127.0.0.1:4098")
    host = parsed.hostname
    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    return ServerSpec(server_url=f"{parsed.scheme}://{netloc}", hostname=host, port=port)


def server_url_with_port(spec: ServerSpec, port: int) -> str:
    host = spec.hostname
    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    parsed = urllib.parse.urlsplit(spec.server_url)
    return f"{parsed.scheme}://{netloc}"


def validate_server_type(server_type: str) -> ServerType:
    normalized = server_type.strip().lower()
    if normalized != DEFAULT_SERVER_TYPE:
        raise ValueError(f"Unsupported server_type: {server_type!r}; supported values: {DEFAULT_SERVER_TYPE}")
    return DEFAULT_SERVER_TYPE


def is_port_open(hostname: str, port: int, timeout: float = 1.0) -> bool:
    host = hostname.strip() or "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_server(server_url: str, server_type: str) -> ServerProbe:
    """Classify server_url as free, matching server_type, or occupied by something else."""
    normalized_type = validate_server_type(server_type)
    spec = parse_server_url(server_url)
    base_url = spec.server_url
    if normalized_type == "opencode" and health_check(f"{base_url}/agent"):
        return ServerProbe(state="matching", base_url=base_url, detail="OpenCode /agent endpoint responded")
    if is_port_open(spec.hostname, spec.port):
        return ServerProbe(
            state="conflict",
            base_url=base_url,
            detail=f"{spec.hostname}:{spec.port} accepts TCP connections but does not expose {normalized_type} health checks",
        )
    return ServerProbe(state="free", base_url=base_url, detail=f"{spec.hostname}:{spec.port} is available")


def _server_log_path(port: int) -> Path:
    """Return the log file path for an opencode server on the given port."""
    log_dir = Path(os.environ.get("SEAM_SERVER_LOG_DIR", "/tmp/seam-server-logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"opencode-{port}.log"


def start_server(
    work_dir: str,
    port: int | None = None,
    auth_header: str = "",
    *,
    server_url: str | None = None,
    server_type: str = DEFAULT_SERVER_TYPE,
) -> ServerProcess:
    """Launch opencode server as a subprocess.

    Supports both legacy start_server(work_dir, port, auth_header) and
    root-style start_server(work_dir=..., server_url=...).

    Stdout and stderr are written to log files under SEAM_SERVER_LOG_DIR
    (default /tmp/seam-server-logs/) so that startup crashes can be diagnosed.
    """
    _ = validate_server_type(server_type)
    if server_url is not None:
        spec = parse_server_url(server_url)
        hostname = spec.hostname
        selected_port = spec.port
    elif port is not None:
        hostname = "127.0.0.1"
        selected_port = port
    else:
        raise TypeError("start_server requires either port or server_url")

    if shutil.which("opencode") is None:
        raise FileNotFoundError("opencode not found in PATH")

    cmd = ["opencode", "serve", "--port", str(selected_port), "--hostname", hostname, "--log-level", "INFO"]
    env: dict[str, str] | None = None
    if auth_header:
        env = {**os.environ, "AUTH_HEADER": auth_header}

    log_path = _server_log_path(selected_port)
    log_fh = open(str(log_path), "a")
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        cwd=work_dir,
        env=env,
        start_new_session=True,
    )

    return proc


def replacement_port_for_conflict(requested_port: int) -> int:
    start = requested_port + 1 if requested_port < 65535 else 4096
    end = min(start + 99, 65535)
    try:
        return find_available_port(start, end)
    except RuntimeError:
        return find_available_port(4096, 65535)


def should_start_after_conflict(hostname: str, port: int, server_type: str) -> bool:
    if not sys.stdin.isatty():
        message = (
            f"Port {hostname}:{port} is occupied by a non-{server_type} service. Non-interactive runs cannot prompt; "
            + "free the port or pass --server-conflict-action start."
        )
        raise RuntimeError(message)
    question = (
        f"端口 {hostname}:{port} 已被非 {server_type} 服务占用。"
        + f"要不要由 SEAM 在其他可用端口建立 {server_type} server? [yes/no]: "
    )
    answer = input(question).strip().lower()
    return answer in {"y", "yes"}


def ensure_server(
    *,
    work_dir: str,
    server_url: str,
    server_type: str,
    auto_start: bool = True,
    conflict_action: str = "prompt",
    auth_header: str = "",
    startup_timeout: int = 30,
) -> ManagedServer:
    """Reuse a compatible server or start one according to the requested server spec."""
    normalized_type = validate_server_type(server_type)
    spec = parse_server_url(server_url)
    probe = probe_server(spec.server_url, normalized_type)
    if probe.state == "matching":
        return ManagedServer(base_url=probe.base_url, port=spec.port, process=None, reused=True, started=False)

    if not auto_start:
        raise RuntimeError(
            f"No reusable {normalized_type} server at {probe.base_url}: {probe.detail}. Auto-start is disabled."
        )

    selected_url = spec.server_url
    selected_port = spec.port
    if probe.state == "conflict":
        if conflict_action not in {"prompt", "start", "error"}:
            raise ValueError("server conflict action must be one of: prompt, start, error")
        if conflict_action == "error":
            raise RuntimeError(f"Port conflict for {normalized_type} server at {probe.base_url}: {probe.detail}")
        if conflict_action == "prompt" and not should_start_after_conflict(spec.hostname, spec.port, normalized_type):
            raise RuntimeError(f"Port conflict for {normalized_type} server at {probe.base_url}: {probe.detail}")
        selected_port = replacement_port_for_conflict(spec.port)
        selected_url = server_url_with_port(spec, selected_port)

    proc = start_server(
        work_dir=work_dir,
        server_url=selected_url,
        auth_header=auth_header,
        server_type=normalized_type,
    )
    if not wait_for_server(selected_url, timeout=startup_timeout):
        _ = stop_server(proc)
        raise RuntimeError(f"{normalized_type} server failed to start on {selected_url}")
    return ManagedServer(base_url=selected_url, port=selected_port, process=proc, reused=False, started=True)


def _get_session(base_url: str, timeout: int = 10) -> str | None:
    """Create a lightweight probe session to verify the server can handle full requests.

    Uses the opencode API: POST /session with {"title": "probe"}.
    Response: {"ok": true, "data": {"id": "session_xxx"}}.

    Returns the session ID on success, None on failure.
    """
    import json as _json
    import urllib.request as _urllib

    session_url = f"{base_url.rstrip('/')}/session"
    payload = _json.dumps({"title": "seam-probe"}).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}

    try:
        req = _urllib.Request(session_url, data=payload, headers=headers, method="POST")
        resp = _urllib.urlopen(req, timeout=timeout)
        body = _json.loads(resp.read().decode("utf-8"))
        data = body.get("data")
        if isinstance(data, dict):
            session_id = str(data.get("id", ""))
            if session_id:
                return session_id
        return None
    except Exception:
        return None


def verify_server_ready(base_url: str, *, session_timeout: int = 10) -> bool:
    """Deep health check that verifies the server can handle real workflow requests.

    Goes beyond a simple HTTP 200 on /agent:
      1. HTTP GET /agent → 200
      2. Create a probe session via POST /session → get session_id
      3. (Future) Send a trivial message to the session and verify response

    Returns True only when all checks pass.
    """
    agent_url = f"{base_url.rstrip('/')}/agent"
    if not health_check(agent_url):
        return False

    session_id = _get_session(base_url, timeout=session_timeout)
    if not session_id:
        return False

    session_check_url = f"{base_url.rstrip('/')}/session/{session_id}"
    return health_check(session_check_url)


def wait_for_server(url: str, timeout: int = 30, *, deep_check: bool = True) -> bool:
    """Poll server health endpoint until 200 response or timeout.

    When deep_check=True (default), also attempts to verify the server can create
    sessions after the basic HTTP check passes.  If the deep check repeatedly fails
    but the basic health endpoint is up, the caller still gets True (the server is
    alive — session creation may need more warm-up time).
    """
    agent_url = f"{url.rstrip('/')}/agent"
    deadline = time.time() + timeout
    deep_failures = 0

    while time.time() < deadline:
        if health_check(agent_url):
            if not deep_check:
                return True
            if verify_server_ready(url, session_timeout=min(10, max(3, int(deadline - time.time())))):
                return True
            deep_failures += 1
            if deep_failures >= 5:
                return True
        time.sleep(1)

    return False


def stop_server(proc: ServerProcess) -> int:
    """Gracefully terminate a server process."""
    if proc.poll() is not None:
        return proc.returncode or 0

    proc.terminate()
    try:
        _ = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _ = proc.wait()

    return proc.returncode or 0


def health_check(url: str) -> bool:
    """Single GET request to server health endpoint."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if not parsed.scheme or not parsed.hostname:
            return False

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        connection_cls = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_cls(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status == 200
        finally:
            connection.close()
    except (urllib.error.URLError, OSError, ValueError):
        return False
