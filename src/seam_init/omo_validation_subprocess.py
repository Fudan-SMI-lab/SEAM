"""Production subprocess adapter for OMO doctor/run commands.

Stdout is raw structured JSON: a separate generous finite cap (``_MAX_STDOUT``)
applies, with explicit ``stdout_truncated`` signaling when the cap is exceeded.
No 8192-byte diagnostic truncation or redaction before parsing. Stderr remains
bounded-before-redacted SafeDetail.

On Windows, detects ``.cmd``/``.bat`` prefixes and routes through ``cmd /d /s /c``.
Uses the real inherited environment when no explicit env is supplied (``env=None``).

On timeout: tree-kill (POSIX process group / Windows ``taskkill /T /F``), then
``_ensure_dead`` which does bounded communicate → if still alive: direct
``proc.kill()`` + bounded communicate → re-check ``proc.poll()``. Returns
``cleanup_failed=True`` when the direct process is NOT confirmed dead after all
bounded attempts. Never claims confirmed death without a final ``poll()`` check.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol, final

from core.secret_redaction import redact_sensitive_text
from seam_init.models import SafeDetail
from seam_init.omo_validation import OmoCommandResult

__all__ = ["SubprocessOmoCommandPort"]

_MAX_STDOUT: Final[int] = 1_048_576  # 1 MiB generous cap for raw structured stdout
_MAX_OUTPUT: Final[int] = 8192  # diagnostic cap for stderr only
_TRUNCATED: Final[str] = "...[truncated]"
_TASKKILL_TIMEOUT: Final[float] = 10.0
_KILL_WAIT: Final[float] = 5.0
_WIN_CMD_SUFFIXES: Final[frozenset[str]] = frozenset({".bat", ".cmd"})


class _Reapable(Protocol):
    """Structural type for process objects used by cleanup logic."""
    pid: int
    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]: ...
    def poll(self) -> int | None: ...
    def kill(self) -> None: ...


def _bound_and_redact(text: str) -> SafeDetail:
    bounded = text[:_MAX_OUTPUT]
    suffix = _TRUNCATED if len(text) > _MAX_OUTPUT else ""
    return SafeDetail(redact_sensitive_text(bounded) + suffix)


def _win_prefix(argv: list[str]) -> list[str]:
    if os.name != "nt" or not argv:
        return argv
    if Path(argv[0]).suffix.lower() not in _WIN_CMD_SUFFIXES:
        return argv
    return ["cmd", "/d", "/s", "/c", *argv]


@final
class SubprocessOmoCommandPort:
    """OMO command boundary: raw stdout, bounded+redacted stderr, tree kill."""

    __slots__ = ()

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> OmoCommandResult:
        full_argv = _win_prefix(list(argv))
        pass_env: dict[str, str] | None = dict(env) if env is not None else None
        try:
            if sys.platform != "win32":
                proc = subprocess.Popen(
                    full_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=pass_env, start_new_session=True,
                )
            else:
                proc = subprocess.Popen(
                    full_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=pass_env,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            return OmoCommandResult(
                argv=tuple(argv), returncode=1,
                stdout="", stderr=_bound_and_redact(str(exc)),
            )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            cleanup_failed = not _ensure_dead(proc)
            return OmoCommandResult(
                argv=tuple(argv), returncode=124,
                stdout="", stderr=SafeDetail(f"timeout after {timeout}s"),
                timed_out=True, cleanup_failed=cleanup_failed,
            )
        returncode = int(proc.returncode)
        return OmoCommandResult(
            argv=tuple(argv), returncode=returncode,
            stdout=stdout[:_MAX_STDOUT],
            stderr=_bound_and_redact(stderr),
            stdout_truncated=len(stdout) > _MAX_STDOUT,
        )


def _ensure_dead(proc: _Reapable) -> bool:
    """Return True only if the direct process is confirmed dead via poll()."""
    try:
        proc.communicate(timeout=_KILL_WAIT)
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=_KILL_WAIT)
        except subprocess.TimeoutExpired:
            pass
    return proc.poll() is not None


def _kill_tree(proc: _Reapable) -> None:
    if sys.platform == "win32":
        try:
            _ = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=_TASKKILL_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
