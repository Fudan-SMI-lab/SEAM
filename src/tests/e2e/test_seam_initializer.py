"""Public Bash E2E for the SEAM interactive initializer with fake boundaries.

Runs the real ``bash src/scripts/init_seam.sh --non-interactive --answers <file>``
against an isolated sandbox project for exactly four MVP scenarios:

1. fresh READY (exit 0) with the parser-valid ``run_seam.sh`` handoff command;
2. skip-key PENDING_AUTH (exit 60) with zero paid validation calls on the ledger;
3. one deterministic install failure (OPENCODE_INSTALL, exit 63);
4. an idempotent second run returning READY with semantically unchanged configs.

Host bridge: this Windows host resolves ``bash`` to WSL bash. Each scenario
exports an isolated HOME/XDG_CONFIG_HOME/constrained PATH plus
``SEAM_INIT_PROJECT_ROOT`` inside a WSL driver script and selects the WSL
``python3`` via ``SEAM_PYTHON``, so the real CLI and the real nine-stage
workflow run as Linux child processes while pytest observes from Windows.
Every external boundary is faked at the executable or HTTP layer under
``tmp_path``: fake ``opencode``/``bun``/``bunx`` PATH executables, a fake venv
interpreter shim (canned probe + satisfied editable install, real delegation
for the diagnose script), and the OpenCode HTTP API served by the fake
``opencode serve``. No real network, installer, or provider credential is ever
touched; a JSONL ledger records every fake-boundary call for the assertions.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from core.jsonc import parse_config_object
from seam_init.reporting import READY_COMMAND

_SRC_DIR: Final[Path] = Path(__file__).resolve().parents[2]
_REPO_DIR: Final[Path] = _SRC_DIR.parent
_OUTER_REPO_DIR: Final[Path] = _REPO_DIR.parent
_LAUNCHER: Final[Path] = _SRC_DIR / "scripts" / "init_seam.sh"
_SEAM_INIT_INNER: Final[Path] = _REPO_DIR / ".seam-init"
_SEAM_INIT_OUTER: Final[Path] = _OUTER_REPO_DIR / ".seam-init"

_BASH: Final[str | None] = shutil.which("bash")
# Type narrowing mirrors test_seam_init_shell.py: when bash is absent every test
# skips first, so call sites always see a real path. ``or ""`` keeps ``str``.
_BASH_EXE: Final[str] = _BASH or ""

_FAKE_KEY_ENV: Final[str] = "SEAM_E2E_FAKE_KEY"
_FAKE_KEY_VALUE: Final[str] = "e2e-fake-key-not-a-real-credential"
_PROVIDER_ID: Final[str] = "openai"
_MODEL_ID: Final[str] = "gpt-4o"
_PROVIDER_MODEL: Final[str] = f"{_PROVIDER_ID}/{_MODEL_ID}"
_RUN_TIMEOUT: Final[float] = 300.0

_READY_ANSWERS: Final[dict[str, object]] = {
    "provider_id": _PROVIDER_ID,
    "model_id": _MODEL_ID,
    "api_key_env": _FAKE_KEY_ENV,
    "billable_consent": True,
}
_SKIP_KEY_ANSWERS: Final[dict[str, object]] = {
    "provider_id": _PROVIDER_ID,
    "model_id": _MODEL_ID,
}

# --- fake boundary executables (POSIX scripts; LF bytes are mandatory) -------


_FAKE_OPENCODE = '''#!/usr/bin/env python3
"""Fake OpenCode CLI for the SEAM initializer E2E: --version/debug config/serve.

``debug config`` behaves like a real merged-runtime projection: it loads the
global ($XDG_CONFIG_HOME/opencode/opencode.json), custom ($OPENCODE_CONFIG),
and project (cwd .opencode/opencode.jsonc / opencode.json) layers that ACTUALLY
EXIST, merges them in precedence order, applies $OPENCODE_CONFIG_CONTENT last,
and reports only the loaded files in ``config_files``. A fresh install with no
config files therefore projects an empty merged config and an empty file list;
it never claims the sandbox project target exists before it is created. Secret
values (apiKey/token/...) are emitted only as a presence placeholder, never as
the real bytes.
"""
import http.server
import json
import os
import sys

_LEDGER = os.environ.get("SEAM_E2E_LEDGER", "")
_PROVIDERS = {"providers": [{"id": "openai", "models": {"gpt-4o": {"name": "gpt-4o"}}}]}
_SECRET_KEYS = ("apikey", "authorization", "key", "password", "secret", "token")


def _record(event):
    if not _LEDGER:
        return
    try:
        with open(_LEDGER, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\\n")
    except OSError:
        pass


def _redact(key, value):
    if isinstance(value, dict):
        return {name: _redact(name, item) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(key, item) for item in value]
    if isinstance(value, str) and value.strip() and key.lower() in _SECRET_KEYS:
        return "<redacted>"
    return value


def _merge(base, override):
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _merge(current, value)
        else:
            result[key] = value
    return result


def _load_json_file(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _debug_config():
    xdg = os.environ.get("XDG_CONFIG_HOME", "") or os.path.join(os.path.expanduser("~"), ".config")
    project_path = os.path.join(os.getcwd(), ".opencode", "opencode.jsonc")
    candidates = [
        os.path.join(xdg, "opencode", "opencode.json"),
        os.environ.get("OPENCODE_CONFIG", ""),
        project_path,
        os.path.join(os.getcwd(), "opencode.json"),
    ]
    layers = []
    project_loaded = False
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        data = _load_json_file(candidate)
        if data is None:
            sys.stderr.write(f"fake opencode: cannot parse config {candidate}\\n")
            return 1
        layers.append((candidate, data))
        if os.path.realpath(candidate) == os.path.realpath(project_path):
            project_loaded = True
    content = os.environ.get("OPENCODE_CONFIG_CONTENT", "")
    if content.strip():
        try:
            data = json.loads(content)
        except ValueError:
            sys.stderr.write("fake opencode: cannot parse OPENCODE_CONFIG_CONTENT\\n")
            return 1
        if isinstance(data, dict):
            layers.append(("", data))
    merged = {}
    for _path, data in layers:
        merged = _merge(merged, data)
    payload = _redact("", merged)
    payload["config_files"] = [os.path.realpath(path) for path, _data in layers if path]
    if project_loaded:
        payload["configPath"] = os.path.realpath(project_path)
        payload["configPathRaw"] = os.path.abspath(project_path)
    print(json.dumps(payload))
    return 0


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _track(self):
        _record({"who": "opencode", "event": "http", "method": self.command,
                 "path": self.path.split("?", 1)[0]})

    def do_GET(self):
        self._track()
        path = self.path.split("?", 1)[0]
        if path == "/config/providers":
            self._send(200, _PROVIDERS)
        elif path == "/agent":
            self._send(200, {"data": [{"name": "build"}]})
        elif path == "/session/status":
            self._send(200, {"status": "idle"})
        elif path.startswith("/session/") and path.endswith("/message"):
            self._send(200, {"data": []})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        self._track()
        length = int(self.headers.get("Content-Length") or "0")
        if length:
            _ = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        if path == "/session":
            self._send(200, {"data": {"id": "e2e-session-1"}})
        elif path.startswith("/session/") and path.endswith("/message"):
            self._send(200, {"data": [{"type": "text", "text": "SEAM_DIAG_OK"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        self._track()
        self._send(200, {"ok": True})

    def log_message(self, *args):
        return


def _serve(argv):
    port, host = 4098, "127.0.0.1"
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--port" and index + 1 < len(argv):
            port = int(argv[index + 1])
            index += 2
        elif token == "--hostname" and index + 1 < len(argv):
            host = argv[index + 1]
            index += 2
        else:
            index += 1
    server = http.server.ThreadingHTTPServer((host, port), _Handler)
    _record({"who": "opencode", "event": "serve_start", "port": port})
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def main(argv):
    _record({"who": "opencode", "event": "cli", "argv": argv})
    if argv[:1] == ["--version"]:
        print("1.18.7")
        return 0
    if argv[:2] == ["debug", "config"]:
        return _debug_config()
    if argv[:1] == ["serve"]:
        return _serve(argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

_FAKE_BUN = "#!/usr/bin/env bash\necho '1.1.29'\n"

_FAKE_BUNX = '''#!/usr/bin/env python3
"""Fake bunx oh-my-openagent for the SEAM initializer E2E: install/doctor/run."""
import json
import os
import sys

_LEDGER = os.environ.get("SEAM_E2E_LEDGER", "")


def _record(event):
    if not _LEDGER:
        return
    try:
        with open(_LEDGER, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\\n")
    except OSError:
        pass


def _doctor():
    payload = {
        "results": [
            {"name": "System", "status": "pass", "message": "ok"},
            {"name": "Configuration", "status": "pass", "message": "ok"},
            {"name": "TUI Plugin", "status": "pass", "message": "ok"},
            {"name": "Models", "status": "pass", "message": "ok"},
        ],
        "systemInfo": {
            "pluginVersion": "5.0.0",
            "configPath": os.environ.get("SEAM_E2E_DOCTOR_CONFIG", "/tmp/e2e-doctor-config"),
            "configValid": True,
        },
        "tools": {"available": [], "enabled": []},
        "summary": {"total": 4, "passed": 4, "failed": 0, "warnings": 0},
        "exitCode": 0,
        "target": "opencode",
    }
    print(json.dumps(payload))
    return 0


def _run():
    payload = {
        "success": True,
        "sessionId": "e2e-omo-session",
        "messageCount": 1,
        "durationMs": 3,
        "summary": "SEAM_OMO_OK",
    }
    print(json.dumps(payload))
    return 0


def main(argv):
    _record({"who": "bunx", "event": "cli", "argv": argv})
    command = argv[1] if len(argv) > 1 else ""
    if command == "doctor":
        return _doctor()
    if command == "run":
        return _run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

_FAKE_VENV_PYTHON = '''#!/usr/bin/env python3
"""Fake venv interpreter: canned environment probe + satisfied pip; delegates the rest."""
import json
import os
import sys


def _probe():
    self_path = os.path.realpath(sys.argv[0])
    info = {
        "executable": self_path,
        "version": [3, 12, 3],
        "in_venv": True,
        "prefix": os.path.dirname(self_path),
        "base_prefix": "/usr",
        "writable": True,
        "externally_managed": False,
        "has_pip": True,
        "euid": None,
    }
    print(json.dumps(info))
    return 0


def _pip(argv):
    if argv[:2] == ["show", "sm-adapt"]:
        source = os.environ.get("SEAM_E2E_SOURCE_POSIX", "")
        print("Name: sm-adapt")
        print("Version: 2.0.0")
        print(f"Location: {source}")
        print(f"Editable project location: {source}")
        return 0
    if argv[:1] == ["install"]:
        sys.stderr.write("fake pip refuses installs inside the E2E sandbox\\n")
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    if len(argv) > 1 and argv[0] == "-c" and "EXTERNALLY-MANAGED" in argv[1] and "in_venv" in argv[1]:
        return _probe()
    if len(argv) > 1 and argv[0] == "-m" and argv[1] == "pip":
        return _pip(argv[2:])
    os.execvp(sys.executable, [sys.executable, *argv])
    return 127


if __name__ == "__main__":
    sys.exit(main())
'''

_FAKE_CURL = '''#!/usr/bin/env python3
"""Fake curl that always fails: the OpenCode install boundary stays fake, never real."""
import json
import os
import sys

_LEDGER = os.environ.get("SEAM_E2E_LEDGER", "")
if _LEDGER:
    try:
        with open(_LEDGER, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"who": "curl", "event": "cli", "argv": sys.argv[1:]}) + "\\n")
    except OSError:
        pass
sys.exit(1)
'''


# --- WSL bridge helpers ------------------------------------------------------


def _wsl_python_ready() -> bool:
    """True when WSL ``python3`` is >= 3.10 and can import typing_extensions."""
    if _BASH is None:
        return False
    # The WSL bash.exe bridge drops argv tokens after ``-c`` scripts, so the
    # probe code is embedded directly (it contains no single quotes).
    script = "python3 -c 'import sys,typing_extensions;sys.exit(0 if sys.version_info>=(3,10) else 1)'"
    result = subprocess.run(
        [_BASH_EXE, "-c", script],
        capture_output=True, timeout=60, check=False,
    )
    return result.returncode == 0


_WSL_READY: Final[bool] = _wsl_python_ready()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(_BASH is None, reason="bash (WSL) is required for the public Bash E2E"),
    pytest.mark.skipif(
        not _WSL_READY,
        reason="WSL python3 >= 3.10 with typing_extensions is required for the public Bash E2E",
    ),
]


def _wsl_paths(paths: list[Path]) -> list[str]:
    """Translate Windows paths to WSL POSIX paths via ``WSLENV``/p.

    Windows paths must never cross as raw ``bash`` argv tokens: the WSL argv
    bridge eats backslashes and drops trailing ``-c`` arguments. The
    repository-proven channel (see test_seam_init_shell.py) is
    ``WSLENV=<name>/p``, which delivers each path already translated to its
    absolute ``/mnt/<drive>/...`` form; the script itself arrives via stdin.
    """
    names = [f"SEAM_E2E_TP_{index}" for index in range(len(paths))]
    env = os.environ.copy()
    for name, path in zip(names, paths, strict=True):
        env[name] = str(path)
    env["WSLENV"] = ":".join(f"{name}/p" for name in names)
    script = "".join(f'printf "%s\\n" "${name}"\n' for name in names)
    result = subprocess.run(
        [_BASH_EXE, "-s"],
        input=script.encode("utf-8"),
        capture_output=True, env=env, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"WSL path translation failed: {result.stderr!r}")
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    if len(lines) != len(paths):
        raise RuntimeError(f"WSL translation returned {len(lines)} paths for {len(paths)} inputs")
    return lines


def _port_4098_open() -> bool:
    """True when something is listening on 127.0.0.1:4098 inside WSL."""
    result = subprocess.run(
        [_BASH_EXE, "-c", "ss -ltn | grep -q ':4098 '"],
        capture_output=True, timeout=60, check=False,
    )
    return result.returncode == 0


def _require_free_4098() -> None:
    """Skip truthfully when a foreign listener owns the canonical port in WSL."""
    if _port_4098_open():
        pytest.skip(
            "127.0.0.1:4098 is occupied inside WSL; the owned-server E2E "
            "cannot start its fake server truthfully",
        )


def _count_fake_serve_processes(bin_wsl: str) -> int:
    """Count leftover fake ``opencode serve`` processes for one sandbox bin dir."""
    pattern = f"{bin_wsl}/[o]pencode serve"
    result = subprocess.run(
        [_BASH_EXE, "-c", f"pgrep -fc {shlex.quote(pattern)} || true"],
        capture_output=True, timeout=60, check=False,
    )
    text = result.stdout.decode("utf-8", "replace").strip()
    return int(text) if text.isdigit() else -1


def _assert_no_leftovers(bin_wsl: str) -> None:
    """Assert the owned server port is closed and no fake serve process lives."""
    assert not _port_4098_open(), "port 4098 is still listening inside WSL after the run"
    count = _count_fake_serve_processes(bin_wsl)
    assert count == 0, f"{count} fake opencode serve processes still running from {bin_wsl}"


# --- sandbox preparation -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    """One isolated initializer sandbox: Windows observation paths + driver."""

    project: Path
    answers: Path
    ledger: Path
    bin_wsl: str
    driver: bytes


def _write_fake(path: Path, content: str) -> None:
    """Write a fake executable with strict LF bytes (WSL-consumed)."""
    path.write_bytes(content.encode("utf-8"))


def _build_driver(wsl: dict[str, str], *, export_key: bool) -> bytes:
    """Build the WSL driver script: isolated env, then exec the real launcher."""
    lines = [
        f"export HOME={shlex.quote(wsl['home'])}",
        f"export XDG_CONFIG_HOME={shlex.quote(wsl['xdg'])}",
        f"export PATH={shlex.quote(wsl['bin'])}:/usr/bin:/bin",
        "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy",
        f"export SEAM_INIT_PROJECT_ROOT={shlex.quote(wsl['project'])}",
        f"export SEAM_E2E_LEDGER={shlex.quote(wsl['ledger'])}",
        f"export SEAM_E2E_SOURCE_POSIX={shlex.quote(wsl['src'])}",
        f"export SEAM_E2E_DOCTOR_CONFIG={shlex.quote(wsl['doctor'])}",
        "export SEAM_PYTHON=python3",
    ]
    if export_key:
        lines.append(f"export {_FAKE_KEY_ENV}={shlex.quote(_FAKE_KEY_VALUE)}")
    lines.append(
        f"exec bash {shlex.quote(wsl['launcher'])} --non-interactive "
        f"--answers {shlex.quote(wsl['answers'])}",
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _prepare(
    tmp_path: Path,
    *,
    with_opencode: bool,
    fail_curl: bool,
    answers: dict[str, object],
    export_key: bool,
) -> _PreparedRun:
    """Create the sandbox project, fake PATH executables, answers, and driver."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    project = tmp_path / "project"
    venv_dir = tmp_path / "venv"
    for directory in (home, xdg, bin_dir, project, venv_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if with_opencode:
        _write_fake(bin_dir / "opencode", _FAKE_OPENCODE)
    _write_fake(bin_dir / "bun", _FAKE_BUN)
    _write_fake(bin_dir / "bunx", _FAKE_BUNX)
    if fail_curl:
        _write_fake(bin_dir / "curl", _FAKE_CURL)
    shim = venv_dir / "fake-python"
    _write_fake(shim, _FAKE_VENV_PYTHON)
    ledger = tmp_path / "ledger.jsonl"
    answers_path = tmp_path / "answers.json"
    names = ("home", "xdg", "bin", "project", "shim", "ledger",
             "launcher", "src", "doctor", "answers")
    win_paths = [
        home, xdg, bin_dir, project, shim, ledger, _LAUNCHER, _SRC_DIR,
        project / ".opencode" / "oh-my-openagent.jsonc", answers_path,
    ]
    wsl: dict[str, str] = dict(zip(names, _wsl_paths(win_paths), strict=True))
    full_answers = {**answers, "environment": "existing", "venv_path": wsl["shim"]}
    answers_path.write_text(json.dumps(full_answers, indent=2) + "\n", encoding="utf-8")
    driver = _build_driver(wsl, export_key=export_key)
    return _PreparedRun(
        project=project, answers=answers_path, ledger=ledger,
        bin_wsl=wsl["bin"], driver=driver,
    )


def _run_initializer(driver: bytes, *, timeout: float = _RUN_TIMEOUT) -> tuple[int, str, str]:
    """Run the real public Bash initializer via ``bash -s``; returns (rc, out, err)."""
    result = subprocess.run(
        [_BASH_EXE, "-s", "--"],
        input=driver,
        capture_output=True,
        env=os.environ.copy(),
        cwd=str(_SRC_DIR),
        timeout=timeout,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", "replace")
    stderr = result.stderr.decode("utf-8", "replace")
    return result.returncode, stdout, stderr


# --- observation helpers ------------------------------------------------------


def _assert_absent_configs(project: Path) -> None:
    """Assert the fresh sandbox starts with NO OpenCode/OMO project config."""
    oc = project / ".opencode" / "opencode.jsonc"
    omo = project / ".omo" / "omo.jsonc"
    assert not oc.exists(), f"fresh sandbox must not seed the OpenCode config: {oc}"
    assert not omo.exists(), f"fresh sandbox must not seed the OMO config: {omo}"


def _read_report(project: Path) -> dict[str, object]:
    """Read the sandbox ``.seam-init/last-report.json`` as a JSON object."""
    report = project / ".seam-init" / "last-report.json"
    assert report.is_file(), f"missing initializer report: {report}"
    parsed = json.loads(report.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"report is not a JSON object: {report}"
    return parsed


def _parse_semantic(path: Path) -> object:
    """Parse a config file with the production JSONC parser (semantic value)."""
    assert path.is_file(), f"missing config file: {path}"
    return parse_config_object(path.read_text(encoding="utf-8")).value


def _ledger_entries(path: Path) -> list[dict[str, object]]:
    """Read the fake-boundary JSONL ledger (empty when no fake was called)."""
    if not path.is_file():
        return []
    entries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def _is_oc_message_call(entry: dict[str, object]) -> bool:
    """True for the billable OpenCode message round-trip (POST .../message)."""
    return (
        entry.get("who") == "opencode"
        and entry.get("method") == "POST"
        and str(entry.get("path", "")).endswith("/message")
    )


def _is_omo_run_call(entry: dict[str, object]) -> bool:
    """True for the billable OMO ``run`` invocation on the fake bunx boundary."""
    argv = entry.get("argv")
    tokens = [str(token) for token in argv] if isinstance(argv, list) else []
    return entry.get("who") == "bunx" and "run" in tokens


def _is_omo_doctor_call(entry: dict[str, object]) -> bool:
    """True for the structural (non-billable) OMO ``doctor`` invocation."""
    argv = entry.get("argv")
    tokens = [str(token) for token in argv] if isinstance(argv, list) else []
    return entry.get("who") == "bunx" and "doctor" in tokens


# --- tests --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _assert_no_seam_init_artifact() -> Iterator[None]:
    # Given: neither repository root may hold initializer state before tests
    assert not _SEAM_INIT_INNER.exists(), f".seam-init exists before test: {_SEAM_INIT_INNER}"
    assert not _SEAM_INIT_OUTER.exists(), f".seam-init exists before test: {_SEAM_INIT_OUTER}"
    yield
    # Then: the real repositories must stay untouched after tests
    assert not _SEAM_INIT_INNER.exists(), f".seam-init created during test: {_SEAM_INIT_INNER}"
    assert not _SEAM_INIT_OUTER.exists(), f".seam-init created during test: {_SEAM_INIT_OUTER}"


class TestPublicBashInitializer:
    """The four minimal public Bash E2E journeys against fake boundaries."""

    def test_fresh_ready_reaches_ready_and_prints_handoff(self, tmp_path: Path) -> None:
        # Given: a fresh sandbox project with every boundary faked and a key + consent.
        _require_free_4098()
        prepared = _prepare(
            tmp_path, with_opencode=True, fail_curl=False,
            answers=_READY_ANSWERS, export_key=True,
        )
        # And: the sandbox starts with NO project configs at all (true fresh clone).
        _assert_absent_configs(prepared.project)
        # When: the real public Bash initializer runs non-interactively.
        rc, stdout, stderr = _run_initializer(prepared.driver)
        # Then: READY/0 with the parser-valid run_seam.sh handoff command.
        assert rc == 0, f"stdout:\n{stdout}\nstderr:\n{stderr}"
        assert "SEAM setup is READY." in stdout
        assert READY_COMMAND in stdout
        tokens = shlex.split(READY_COMMAND)
        assert tokens[:2] == ["bash", "src/scripts/run_seam.sh"]
        assert "--server_url" in tokens
        assert "http://127.0.0.1:4098" in tokens
        # And: the sandbox report records the READY terminal state truthfully.
        report = _read_report(prepared.project)
        assert report.get("status") == "ready"
        assert report.get("exit_code") == 0
        assert report.get("auth_state") == "provided"
        assert report.get("billable_consent") == "given"
        # And: the OpenCode config was created transactionally at the exact path.
        oc_path = prepared.project / ".opencode" / "opencode.jsonc"
        assert oc_path.is_file(), f"fresh run did not create the OpenCode config: {oc_path}"
        report_facts = report.get("facts")
        assert isinstance(report_facts, dict)
        assert str(report_facts.get("opencode_transaction_id", "")).strip(), (
            "report lacks the OpenCode commit transaction id"
        )
        # And: both configs committed with the selected provider/model semantics.
        oc_config = _parse_semantic(oc_path)
        assert isinstance(oc_config, dict)
        assert oc_config.get("model") == _PROVIDER_MODEL
        provider = oc_config.get("provider")
        assert isinstance(provider, dict)
        provider_entry = provider.get(_PROVIDER_ID)
        assert isinstance(provider_entry, dict)
        options = provider_entry.get("options")
        assert isinstance(options, dict)
        assert str(options.get("apiKey", "")).strip(), "committed config lacks the api key"
        assert oc_config.get("plugin") == ["oh-my-openagent"]
        omo_config = _parse_semantic(prepared.project / ".omo" / "omo.jsonc")
        assert isinstance(omo_config, dict)
        agents = omo_config.get("agents")
        assert isinstance(agents, dict)
        sisyphus = agents.get("sisyphus")
        assert isinstance(sisyphus, dict)
        assert sisyphus.get("model") == _PROVIDER_MODEL
        # And: the paid validation calls happened against the fake boundaries.
        entries = _ledger_entries(prepared.ledger)
        assert any(_is_oc_message_call(e) for e in entries), entries
        assert any(_is_omo_run_call(e) for e in entries), entries
        # And: all child processes exited; no port listener or fake serve remains.
        _assert_no_leftovers(prepared.bin_wsl)

    def test_skip_key_returns_pending_auth_without_paid_calls(self, tmp_path: Path) -> None:
        # Given: a fresh sandbox without an API key and without billable consent.
        _require_free_4098()
        prepared = _prepare(
            tmp_path, with_opencode=True, fail_curl=False,
            answers=_SKIP_KEY_ANSWERS, export_key=False,
        )
        # And: the sandbox starts with NO project configs at all (true fresh clone).
        _assert_absent_configs(prepared.project)
        # When: the real public Bash initializer runs non-interactively.
        rc, stdout, stderr = _run_initializer(prepared.driver)
        # Then: PENDING_AUTH/60 with neutral wording (never a READY claim).
        assert rc == 60, f"stdout:\n{stdout}\nstderr:\n{stderr}"
        assert "SEAM setup is NOT runnable-ready (PENDING_AUTH)." in stdout
        assert "SEAM setup is READY." not in stdout
        report = _read_report(prepared.project)
        assert report.get("status") == "pending_auth"
        assert report.get("exit_code") == 60
        assert report.get("auth_state") == "skipped"
        assert report.get("billable_consent") == "declined"
        # And: the fresh OpenCode config was still created transactionally.
        oc_path = prepared.project / ".opencode" / "opencode.jsonc"
        assert oc_path.is_file(), f"skip-key run did not create the OpenCode config: {oc_path}"
        report_facts = report.get("facts")
        assert isinstance(report_facts, dict)
        assert str(report_facts.get("opencode_transaction_id", "")).strip(), (
            "report lacks the OpenCode commit transaction id"
        )
        # And: structural stages committed, but no api key was written anywhere.
        oc_config = _parse_semantic(oc_path)
        assert "apiKey" not in json.dumps(oc_config)
        # And: the ledger proves zero paid calls while the structural doctor ran.
        entries = _ledger_entries(prepared.ledger)
        assert not any(_is_oc_message_call(e) for e in entries), entries
        assert not any(_is_omo_run_call(e) for e in entries), entries
        assert any(_is_omo_doctor_call(e) for e in entries), entries
        _assert_no_leftovers(prepared.bin_wsl)

    def test_opencode_install_failure_returns_categorized_exit(self, tmp_path: Path) -> None:
        # Given: no opencode on PATH and a curl boundary that deterministically fails,
        # so the official installer pipeline cannot place any binary.
        prepared = _prepare(
            tmp_path, with_opencode=False, fail_curl=True,
            answers=_READY_ANSWERS, export_key=True,
        )
        # And: the sandbox starts with NO project configs at all (true fresh clone).
        _assert_absent_configs(prepared.project)
        # When: the real public Bash initializer runs non-interactively.
        rc, stdout, stderr = _run_initializer(prepared.driver)
        # Then: the categorized OPENCODE_INSTALL failure (63), never a false READY.
        assert rc == 63, f"stdout:\n{stdout}\nstderr:\n{stderr}"
        assert "SEAM setup FAILED at step: OPENCODE_INSTALL (exit 63)" in stdout
        assert "SEAM setup is READY." not in stdout
        report = _read_report(prepared.project)
        assert report.get("status") == "failed"
        assert report.get("exit_code") == 63
        assert report.get("failure_kind") == "OPENCODE_INSTALL"
        # And: the categorized failure wrote NO project configs (exact failure).
        _assert_absent_configs(prepared.project)
        # And: the installer boundary was exercised through the fake (no real network).
        entries = _ledger_entries(prepared.ledger)
        assert any(entry.get("who") == "curl" for entry in entries), entries
        assert not any(_is_oc_message_call(entry) for entry in entries), entries
        _assert_no_leftovers(prepared.bin_wsl)

    def test_second_run_is_idempotent_with_same_semantic_config(self, tmp_path: Path) -> None:
        # Given: a fresh sandbox that completed one READY run already.
        _require_free_4098()
        prepared = _prepare(
            tmp_path, with_opencode=True, fail_curl=False,
            answers=_READY_ANSWERS, export_key=True,
        )
        # And: the first run starts with NO project configs at all (true fresh clone).
        _assert_absent_configs(prepared.project)
        rc1, stdout1, stderr1 = _run_initializer(prepared.driver)
        assert rc1 == 0, f"first run stdout:\n{stdout1}\nstderr:\n{stderr1}"
        assert "SEAM setup is READY." in stdout1
        oc_first = _parse_semantic(prepared.project / ".opencode" / "opencode.jsonc")
        omo_first = _parse_semantic(prepared.project / ".omo" / "omo.jsonc")
        _assert_no_leftovers(prepared.bin_wsl)
        # When: the same sandbox is initialized a second time.
        rc2, stdout2, stderr2 = _run_initializer(prepared.driver)
        # Then: the same functional result (READY/0) again.
        assert rc2 == 0, f"second run stdout:\n{stdout2}\nstderr:\n{stderr2}"
        assert "SEAM setup is READY." in stdout2
        report = _read_report(prepared.project)
        assert report.get("status") == "ready"
        assert report.get("exit_code") == 0
        # And: semantic config is unchanged (formatting/timestamps may differ).
        oc_second = _parse_semantic(prepared.project / ".opencode" / "opencode.jsonc")
        omo_second = _parse_semantic(prepared.project / ".omo" / "omo.jsonc")
        assert oc_second == oc_first
        assert omo_second == omo_first
        _assert_no_leftovers(prepared.bin_wsl)
