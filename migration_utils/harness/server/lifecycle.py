"""OpenCode server lifecycle management - auto start/stop for E2E tests."""

import http.client
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


def start_server(work_dir: str, port: int, auth_header: str = "") -> ServerProcess:
    """Launch opencode server as a subprocess."""
    if shutil.which("opencode") is None:
        raise FileNotFoundError("opencode not found in PATH")

    cmd = ["opencode", "serve", "--port", str(port), "--hostname", "127.0.0.1"]
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
