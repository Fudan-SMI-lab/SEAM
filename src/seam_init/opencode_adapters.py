"""Concrete bounded OpenCode runtime and live-schema adapters."""
from __future__ import annotations

import json
import http.client
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from core.jsonc import parse_config_object
from core.secret_redaction import redact_json_value
from seam_init.opencode_discovery import JsonDict

__all__ = ["OpencodeCommand", "OpencodeSchemaValidator", "SubprocessRuntimePort"]

_MODEL_RESPONSE_LIMIT: Final[int] = 4 * 1024 * 1024
_MODEL_POLL_INTERVAL: Final[float] = 0.05
_MODEL_STOP_TIMEOUT: Final[float] = 1.0


@final
@dataclass(frozen=True, slots=True)
class OpencodeCommand:
    """Command prefix, working directory, and timeout for OpenCode probes."""

    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = 30.0
    model_server_port: int | None = None


@final
@dataclass(frozen=True, slots=True)
class SubprocessRuntimePort:
    """Run bounded config probes and query the public local provider API."""

    command: OpencodeCommand

    def _run(self, suffix: tuple[str, ...]) -> subprocess.CompletedProcess[str] | None:
        try:
            result = subprocess.run(
                [*_command_prefix(self.command.argv), *suffix], cwd=self.command.cwd,
                capture_output=True, text=True,
                timeout=self.command.timeout_seconds, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result if result.returncode == 0 else None

    def debug_config(self) -> JsonDict | None:
        result = self._run(("debug", "config"))
        if result is None:
            return None
        try:
            parsed = parse_config_object(result.stdout)
        except ValueError:
            return None
        return parsed.value if isinstance(parsed.value, dict) else None

    def debug_models(self, config_bytes: bytes | None = None) -> tuple[str, ...] | None:
        environment = os.environ.copy()
        if config_bytes is None:
            return self._serve_models(environment)
        try:
            parsed = parse_config_object(config_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        redacted = redact_json_value(parsed.value)
        safe_bytes = json.dumps(redacted, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="seam-opencode-models-") as temporary:
            candidate = Path(temporary) / "opencode.json"
            try:
                _write_private(candidate, safe_bytes)
            except OSError:
                return None
            environment["OPENCODE_CONFIG"] = str(candidate)
            environment["OPENCODE_CONFIG_CONTENT"] = safe_bytes.decode("utf-8")
            return self._serve_models(environment)

    def _serve_models(self, environment: dict[str, str]) -> tuple[str, ...] | None:
        for attempt in range(3):
            port = self.command.model_server_port or _available_port()
            try:
                process = subprocess.Popen(
                    [*_command_prefix(self.command.argv), "serve", "--hostname", "127.0.0.1",
                     "--port", str(port)],
                    cwd=self.command.cwd, env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                )
            except OSError:
                return None
            try:
                result = self._poll_models(process, port)
                if result is not None:
                    return result
            finally:
                _stop_process(process)
                _wait_port_closed(port)
            time.sleep(2)
        return None

    def _poll_models(
        self, process: subprocess.Popen[bytes], port: int,
    ) -> tuple[str, ...] | None:
        deadline = time.monotonic() + self.command.timeout_seconds
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                with closing(http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=min(remaining, 0.2),
                )) as connection:
                    connection.request("GET", "/config/providers")
                    with closing(connection.getresponse()) as response:
                        if response.status != 200:
                            return None
                        payload = response.read(_MODEL_RESPONSE_LIMIT + 1)
            except (OSError, http.client.HTTPException):
                time.sleep(min(_MODEL_POLL_INTERVAL, remaining))
                continue
            if len(payload) > _MODEL_RESPONSE_LIMIT:
                return None
            return _parse_models(payload)
        return None


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _command_prefix(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv or os.name != "nt":
        return argv
    resolved = shutil.which(argv[0])
    executable = resolved or argv[0]
    if Path(executable).suffix.lower() not in {".bat", ".cmd"}:
        return argv
    return ("cmd", "/d", "/s", "/c", executable, *argv[1:])


def _parse_models(payload: bytes) -> tuple[str, ...] | None:
    try:
        parsed = parse_config_object(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    value = parsed.value
    if not isinstance(value, dict):
        return None
    providers = value.get("providers")
    if not isinstance(providers, list):
        return None
    models: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            return None
        provider_id = provider.get("id")
        provider_models = provider.get("models")
        if not isinstance(provider_id, str) or not isinstance(provider_models, dict):
            return None
        if not provider_id or any(character.isspace() for character in provider_id):
            return None
        for model_id in provider_models:
            if not model_id or any(character.isspace() for character in model_id):
                return None
            models.add(f"{provider_id}/{model_id}")
    return tuple(sorted(models))


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            _ = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=_MODEL_STOP_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
        try:
            _ = process.wait(timeout=_MODEL_STOP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        _ = process.wait(timeout=_MODEL_STOP_TIMEOUT)
    except OSError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            _ = process.wait(timeout=_MODEL_STOP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return


def _wait_port_closed(port: int) -> None:
    deadline = time.monotonic() + _MODEL_STOP_TIMEOUT
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(_MODEL_POLL_INTERVAL)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(_MODEL_POLL_INTERVAL)


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        _ = handle.write(data)


@final
@dataclass(frozen=True, slots=True)
class OpencodeSchemaValidator:
    """Validate a redacted candidate through installed OpenCode's live v1 loader."""

    command: OpencodeCommand

    def validate(self, config_bytes: bytes) -> bool:
        try:
            parsed = parse_config_object(config_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False
        redacted = redact_json_value(parsed.value)
        safe_bytes = json.dumps(redacted, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="seam-opencode-schema-") as temporary:
            temporary_path = Path(temporary)
            candidate = temporary_path / "opencode.json"
            try:
                _write_private(candidate, safe_bytes)
                environment = os.environ.copy()
                environment["OPENCODE_CONFIG"] = str(candidate)
                _ = environment.pop("OPENCODE_CONFIG_CONTENT", None)
                result = subprocess.run(
                    [*_command_prefix(self.command.argv), "debug", "config"], cwd=temporary_path,
                    capture_output=True, text=True, env=environment,
                    timeout=self.command.timeout_seconds, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            if result.returncode != 0:
                return False
            try:
                _ = parse_config_object(result.stdout)
            except ValueError:
                return False
            return True
