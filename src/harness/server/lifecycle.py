"""OpenCode server lifecycle management - auto start/stop for E2E tests."""

import http.client
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse

ServerProcess = subprocess.Popen[bytes]


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


def is_local_url(url: str) -> bool:
    """Return True if the URL host resolves to a loopback address."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except (ValueError, AttributeError):
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def parse_host_port(url: str, default_port: int = 4096) -> tuple[str, int]:
    """Extract (hostname, port) from a server URL.

    Uses the scheme-default port (80/443) when no explicit port is given
    and the scheme is recognised.  Falls back to ``default_port`` for URLs
    without a known scheme.
    """
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        port: int = parsed.port
    elif parsed.scheme == "https":
        port = 443
    elif parsed.scheme == "http":
        port = 80
    else:
        port = default_port
    return hostname, port


def resolve_server_url(
    base_url: str | None,
    *,
    auto_start: bool,
    default_url: str = "http://127.0.0.1:4096",
    work_dir: str,
    server_port: int = 0,
) -> tuple[str, ServerProcess | None]:
    """Resolve *base_url* and auto-start a local server when needed.

    Returns ``(resolved_url, server_proc)``.  *server_proc* is non-None
    when a new child process was started; the caller is responsible for
    stopping it later via :func:`stop_server`.

    Raises :exc:`RuntimeError` when the server is unreachable and
    auto-start is not possible (disabled, remote URL, or startup failure).
    """
    server_proc: ServerProcess | None = None

    if auto_start and base_url is None:
        port = server_port if server_port > 0 else find_available_port()
        resolved = f"http://127.0.0.1:{port}"
        server_proc = start_server(work_dir=work_dir, port=port)
        if not wait_for_server(resolved, timeout=30):
            _ = stop_server(server_proc)
            raise RuntimeError(f"Server failed to start on {resolved}")
        if not check_session_capable(resolved):
            _ = stop_server(server_proc)
            raise RuntimeError(
                f"Server started on {resolved} but POST /session failed: "
                f"server is not session-capable"
            )
        return resolved, server_proc

    if auto_start and base_url is not None:
        health_url = f"{base_url.rstrip('/')}/agent"
        if not health_check(health_url):
            if is_local_url(base_url):
                host, port = parse_host_port(base_url)
                server_proc = start_server(work_dir=work_dir, port=port, hostname=host)
                if not wait_for_server(base_url, timeout=30):
                    _ = stop_server(server_proc)
                    raise RuntimeError(f"Server failed to start on {base_url}")
                if not check_session_capable(base_url):
                    _ = stop_server(server_proc)
                    raise RuntimeError(
                        f"Server started on {base_url} but POST /session failed: "
                        f"server is not session-capable"
                    )
                return base_url, server_proc
            else:
                raise RuntimeError(
                    f"OpenCode server is not reachable at {base_url}. "
                    f"Auto-start is only supported for local addresses "
                    f"(127.0.0.1 / localhost / ::1).  Ensure the remote "
                    f"server is running, or disable auto-start."
                )
        # Server /agent is reachable — verify session capability.
        if not check_session_capable(base_url):
            if is_local_url(base_url):
                raise RuntimeError(
                    f"OpenCode server at {base_url} responds to /agent "
                    f"but POST /session failed.  A process is already "
                    f"listening on this port — restarting it blindly is "
                    f"unsafe.  Please restart the server manually, then "
                    f"re-run the tests."
                )
            else:
                raise RuntimeError(
                    f"OpenCode server at {base_url} is reachable on /agent "
                    f"but POST /session failed. Session creation is required "
                    f"for E2E tests. Ensure the remote server is fully "
                    f"operational, or disable auto-start."
                )
        return base_url, None

    # auto_start is False — caller must provide a reachable URL.
    resolved = base_url or default_url
    return resolved, None


def start_server(work_dir: str, port: int, auth_header: str = "",
                 hostname: str = "127.0.0.1") -> ServerProcess:
    """Launch opencode server as a subprocess."""
    if shutil.which("opencode") is None:
        raise FileNotFoundError("opencode not found in PATH")

    cmd = ["opencode", "serve", "--port", str(port), "--hostname", hostname]
    env: dict[str, str] | None = None
    if auth_header:
        env = {**os.environ, "AUTH_HEADER": auth_header}

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=work_dir,
        env=env,
    )
    return proc


def wait_for_server(url: str, timeout: int = 30) -> bool:
    """Poll server health endpoint until 200 response or timeout."""
    health_url = f"{url}/agent"
    deadline = time.time() + timeout

    while time.time() < deadline:
        if health_check(health_url):
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


def check_session_capable(base_url: str, timeout: int = 5) -> bool:
    """Verify the server can create sessions via POST /session.

    Performs a minimal POST /session probe and cleans up the created
    session on success.  Returns ``True`` when the server responds with
    2xx and returns a usable ``session_id``.

    This catches the case where ``GET /agent`` succeeds but the session
    endpoint is broken (e.g. returning HTTP 500).
    """
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if not parsed.scheme or not parsed.hostname:
        return False

    connection_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https"
        else http.client.HTTPConnection
    )

    try:
        conn = connection_cls(parsed.hostname, parsed.port, timeout=timeout)
        try:
            payload = json.dumps({"title": "health-check"})
            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/session", body=payload, headers=headers)
            resp = conn.getresponse()
            if resp.status < 200 or resp.status >= 300:
                return False

            body_bytes = resp.read()
            body_text = body_bytes.decode("utf-8", errors="replace")
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                return False

            session_id: str | None = None
            if isinstance(data.get("data"), dict):
                session_id = data["data"].get("id")  # type: ignore[assignment]
            else:
                session_id = data.get("id")  # type: ignore[assignment]

            usable = isinstance(session_id, str) and bool(session_id)
            if not usable:
                return False

            # Clean up the health-check session.
            try:
                cleanup_conn = connection_cls(
                    parsed.hostname, parsed.port, timeout=timeout,
                )
                try:
                    cleanup_conn.request("DELETE", f"/session/{session_id}")
                    cleanup_conn.getresponse().read()
                finally:
                    cleanup_conn.close()
            except Exception:
                pass

            return True
        finally:
            conn.close()
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return False


def _session_probe_details(
    base_url: str, timeout: int = 5,
) -> tuple[bool, int, str]:
    """Low-level POST /session probe that returns status and body.

    Returns ``(ok, http_status, body_text)`` where *ok* is ``True`` only
    when the probe succeeds.  Callers that need the exact status code or
    response body for error messages should use this instead of
    :func:`check_session_capable`.
    """
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if not parsed.scheme or not parsed.hostname:
        return False, 0, "invalid base URL"

    connection_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https"
        else http.client.HTTPConnection
    )

    try:
        conn = connection_cls(parsed.hostname, parsed.port, timeout=timeout)
        try:
            payload = json.dumps({"title": "health-check"})
            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/session", body=payload, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            body_text = resp.read().decode("utf-8", errors="replace")
            ok = 200 <= status < 300

            if ok:
                _cleanup_probe_session(
                    body_text, parsed.hostname, parsed.port,
                    connection_cls, timeout,
                )

            return ok, status, body_text
        finally:
            conn.close()
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        return False, 0, str(exc)


def _cleanup_probe_session(
    body_text: str,
    hostname: str,
    port: int,
    connection_cls: type,
    timeout: int,
) -> None:
    """Parse session_id from *body_text* and DELETE it.

    Best-effort — failures are silently ignored so cleanup never affects
    the probe result.
    """
    try:
        data = json.loads(body_text)
    except json.JSONDecodeError:
        return

    session_id: str | None = None
    if isinstance(data.get("data"), dict):
        session_id = data["data"].get("id")  # type: ignore[assignment]
    else:
        session_id = data.get("id")  # type: ignore[assignment]

    if not isinstance(session_id, str) or not session_id:
        return

    try:
        cleanup_conn = connection_cls(hostname, port, timeout=timeout)
        try:
            cleanup_conn.request("DELETE", f"/session/{session_id}")
            cleanup_conn.getresponse().read()
        finally:
            cleanup_conn.close()
    except Exception:
        pass
