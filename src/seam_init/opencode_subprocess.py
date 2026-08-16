"""Production subprocess implementations for the OpenCode runtime boundaries.

Bound raw output before redaction to avoid catastrophic regex backtracking.
Use DEVNULL for server stdout/stderr to avoid undrained pipe handles.
On POSIX, start a new session so child processes are group-killed.
Remove process-map entries only after confirmed terminal exit.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import final

from core.secret_redaction import redact_sensitive_text
from seam_init.models import SafeDetail
from seam_init.opencode_runtime_types import DiagnoseResult, OwnedProcessRef
from seam_init.opencode_validation import VersionResult

__all__ = ["SubprocessDiagnoseRunner", "SubprocessServerLifecycle", "SubprocessVersionProbe"]

_MAX_OUTPUT = 262144
_TRUNCATED = "...[truncated]"
_TASKKILL_TIMEOUT = 10.0
_WIN_CMD_SUFFIXES = frozenset({".bat", ".cmd"})


def _bound_and_redact(text: str) -> SafeDetail:
    bounded = text[:_MAX_OUTPUT]
    suffix = _TRUNCATED if len(text) > _MAX_OUTPUT else ""
    return SafeDetail(redact_sensitive_text(bounded) + suffix)


@final
class SubprocessDiagnoseRunner:
    """DiagnoseRunner backed by ``subprocess.run``; bounds before redaction."""

    __slots__ = ("_timeout",)

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    def run(
        self, argv: Sequence[str], *, env: Mapping[str, str] | None = None,
    ) -> DiagnoseResult:
        pass_env = dict(env) if env is not None else None
        try:
            result = subprocess.run(
                list(argv), capture_output=True, text=True,
                timeout=self._timeout, check=False, env=pass_env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return DiagnoseResult(
                argv=tuple(argv), returncode=1,
                stdout=SafeDetail(""), stderr=_bound_and_redact(str(exc)),
            )
        return DiagnoseResult(
            argv=tuple(argv), returncode=int(result.returncode),
            stdout=_bound_and_redact(result.stdout),
            stderr=_bound_and_redact(result.stderr),
        )


@final
class SubprocessServerLifecycle:
    """ServerLifecyclePort with DEVNULL pipes, POSIX process groups, bounded waits.

    stop() raises RuntimeError if the process does not exit after kill,
    so safe_stop marks the result as failed and the handle stays retryable.
    The process-map entry is deleted only after confirmed terminal exit.
    """

    __slots__ = ("_processes", "_next_id", "_terminate_wait", "_kill_wait")

    def __init__(self, *, terminate_wait: float = 5.0, kill_wait: float = 3.0) -> None:
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._next_id = 0
        self._terminate_wait = terminate_wait
        self._kill_wait = kill_wait

    def start(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str,
    ) -> OwnedProcessRef:
        self._next_id += 1
        ref = OwnedProcessRef(id=self._next_id)
        if sys.platform != "win32":
            proc = subprocess.Popen(
                list(argv), cwd=cwd, env=dict(env),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            proc = subprocess.Popen(
                list(argv), cwd=cwd, env=dict(env),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        self._processes[ref.id] = proc
        return ref

    def stop(self, ref: OwnedProcessRef) -> SafeDetail:
        proc = self._processes.get(ref.id)
        if proc is None:
            return SafeDetail(f"no tracked process for ref={ref.id}")
        if proc.poll() is not None:
            del self._processes[ref.id]
            return SafeDetail(f"process ref={ref.id} already exited rc={proc.returncode}")
        self._signal_tree(proc.pid, force=False)
        try:
            _ = proc.wait(timeout=self._terminate_wait)
        except subprocess.TimeoutExpired:
            self._signal_tree(proc.pid, force=True)
            try:
                _ = proc.wait(timeout=self._kill_wait)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"process ref={ref.id} did not exit after kill")
        del self._processes[ref.id]
        return SafeDetail(f"stopped owned server ref={ref.id} rc={proc.returncode}")

    def is_running(self, ref: OwnedProcessRef) -> bool:
        proc = self._processes.get(ref.id)
        return proc is not None and proc.poll() is None

    @staticmethod
    def _signal_tree(pid: int, *, force: bool) -> None:
        if sys.platform == "win32":
            args = ["taskkill", "/T", "/F", "/PID", str(pid)]
            try:
                _ = subprocess.run(
                    args, capture_output=True, timeout=_TASKKILL_TIMEOUT, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            try:
                os.killpg(os.getpgid(pid), sig)
            except (ProcessLookupError, PermissionError):
                pass


@final
class SubprocessVersionProbe:
    """VersionProbe backed by ``subprocess.run``; bounds before redaction."""

    __slots__ = ("_timeout",)

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def check(self, executable: str) -> VersionResult:
        prefix = _win_prefix(executable)
        argv = [*prefix, executable, "--version"] if prefix else [executable, "--version"]
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=self._timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VersionResult(ok=False, version=SafeDetail(""), error=_bound_and_redact(str(exc)))
        raw = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            return VersionResult(
                ok=False, version=SafeDetail(""),
                error=_bound_and_redact(f"exit code {result.returncode}"))
        return VersionResult(ok=True, version=_bound_and_redact(raw), error=SafeDetail(""))


def _win_prefix(executable: str) -> tuple[str, ...]:
    if os.name != "nt" or Path(executable).suffix.lower() not in _WIN_CMD_SUFFIXES:
        return ()
    return ("cmd", "/d", "/s", "/c")
