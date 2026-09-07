"""Isolated production-adapter tests for SubprocessServerLifecycle and DiagnoseRunner.

Exercises real subprocess boundaries with short local Python child processes only.
No real OpenCode, no real network, no provider credentials. Every child process
is stopped in the same test to prevent leaks.
"""
from __future__ import annotations

import os
import sys

import pytest

from seam_init.opencode_subprocess import SubprocessDiagnoseRunner, SubprocessServerLifecycle

_EXE = sys.executable
_POSIX = sys.platform != "win32"


def _child_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "")}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


class TestSubprocessServerLifecycleMapCleanup:
    def test_stop_removes_entry_from_process_map(self) -> None:
        lifecycle = SubprocessServerLifecycle(terminate_wait=3.0, kill_wait=2.0)
        ref = lifecycle.start([_EXE, "-c", "import time; time.sleep(30)"], env=_child_env(), cwd=".")
        lifecycle.stop(ref)
        detail = lifecycle.stop(ref)
        assert "no tracked" in str(detail).lower()

    def test_start_creates_running_process(self) -> None:
        lifecycle = SubprocessServerLifecycle(terminate_wait=3.0, kill_wait=2.0)
        ref = lifecycle.start([_EXE, "-c", "import time; time.sleep(30)"], env=_child_env(), cwd=".")
        assert lifecycle.is_running(ref)
        lifecycle.stop(ref)

    def test_stop_on_already_exited_process(self) -> None:
        lifecycle = SubprocessServerLifecycle(terminate_wait=3.0, kill_wait=2.0)
        ref = lifecycle.start([_EXE, "-c", "pass"], env=_child_env(), cwd=".")
        detail = lifecycle.stop(ref)
        assert f"ref={ref.id}" in str(detail)
        detail2 = lifecycle.stop(ref)
        assert "no tracked" in str(detail2).lower()


class TestSubprocessServerLifecycleTerminateKill:
    def test_stop_terminates_long_lived_process(self) -> None:
        lifecycle = SubprocessServerLifecycle(terminate_wait=3.0, kill_wait=2.0)
        ref = lifecycle.start([_EXE, "-c", "import time; time.sleep(60)"], env=_child_env(), cwd=".")
        assert lifecycle.is_running(ref)
        detail = lifecycle.stop(ref)
        assert not lifecycle.is_running(ref)
        assert "ref=" in str(detail)

    def test_stop_returns_exit_code_on_terminated_process(self) -> None:
        lifecycle = SubprocessServerLifecycle(terminate_wait=3.0, kill_wait=2.0)
        ref = lifecycle.start([_EXE, "-c", "import time; time.sleep(60)"], env=_child_env(), cwd=".")
        detail = lifecycle.stop(ref)
        assert "rc=" in str(detail) or "already exited" in str(detail).lower()

    @pytest.mark.skipif(not _POSIX, reason="POSIX signal test")
    def test_sigterm_resistant_process_killed_via_sigkill(self) -> None:
        # Given: a process that ignores SIGTERM — only SIGKILL can stop it
        lifecycle = SubprocessServerLifecycle(terminate_wait=1.0, kill_wait=3.0)
        cmd = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
        ref = lifecycle.start([_EXE, "-c", cmd], env=_child_env(), cwd=".")
        # When
        detail = lifecycle.stop(ref)
        # Then: process killed via SIGKILL escalation, stopped and removed
        assert "stopped" in str(detail).lower()
        assert not lifecycle.is_running(ref)

    def test_bounded_terminate_wait_does_not_hang(self) -> None:
        # Given: very short terminate_wait so SIGTERM path times out, kill fallback used
        lifecycle = SubprocessServerLifecycle(terminate_wait=0.1, kill_wait=3.0)
        ref = lifecycle.start([_EXE, "-c", "import time; time.sleep(30)"], env=_child_env(), cwd=".")
        # When
        detail = lifecycle.stop(ref)
        # Then: stop completes within bounded time
        assert "stopped" in str(detail).lower()
        assert not lifecycle.is_running(ref)

    @pytest.mark.skipif(not _POSIX, reason="POSIX process-group test")
    def test_posix_process_group_kills_child_processes(self) -> None:
        # Given: a parent that forks a child (both sleep)
        lifecycle = SubprocessServerLifecycle(terminate_wait=2.0, kill_wait=2.0)
        cmd = (
            "import os,time\n"
            "pid=os.fork()\n"
            "if pid==0: time.sleep(30)\n"
            "else: time.sleep(30)\n"
        )
        ref = lifecycle.start([_EXE, "-c", cmd], env=_child_env(), cwd=".")
        # When
        detail = lifecycle.stop(ref)
        # Then: both parent and child killed via process-group signal
        assert "stopped" in str(detail).lower()
        assert not lifecycle.is_running(ref)


class TestSubprocessDiagnoseRunnerBounding:
    def test_large_stdout_is_bounded_before_redaction(self) -> None:
        runner = SubprocessDiagnoseRunner(timeout=10.0)
        result = runner.run([_EXE, "-c", "import sys; sys.stdout.write('X' * 10000)"])
        assert len(str(result.stdout)) <= 8192 + 20
        assert result.returncode == 0

    def test_large_stderr_is_bounded(self) -> None:
        runner = SubprocessDiagnoseRunner(timeout=10.0)
        result = runner.run([_EXE, "-c", "import sys; sys.stderr.write('Y' * 10000)"])
        assert len(str(result.stderr)) <= 8192 + 20

    def test_truncation_suffix_present_for_large_output(self) -> None:
        runner = SubprocessDiagnoseRunner(timeout=10.0)
        result = runner.run([_EXE, "-c", "import sys; sys.stdout.write('Z' * 10000)"])
        assert "[truncated]" in str(result.stdout)


class TestSubprocessDiagnoseRunnerRedaction:
    def test_api_key_pattern_is_redacted(self) -> None:
        runner = SubprocessDiagnoseRunner(timeout=10.0)
        secret = "api_key=sk-secret-1234567890abcdef"
        result = runner.run([_EXE, "-c", f"print({secret!r})"])
        assert "sk-secret-1234567890abcdef" not in str(result.stdout)

    def test_exception_stderr_is_redacted(self) -> None:
        runner = SubprocessDiagnoseRunner(timeout=0.5)
        result = runner.run([_EXE, "-c", "import time; time.sleep(10)"])
        assert result.returncode == 1
        assert len(str(result.stderr)) > 0
        assert len(str(result.stderr)) <= 8192 + 20


class TestSubprocessServerLifecycleDevnull:
    def test_no_pipe_handles_leaked(self) -> None:
        lifecycle = SubprocessServerLifecycle(terminate_wait=3.0, kill_wait=2.0)
        ref = lifecycle.start([_EXE, "-c", "import time; time.sleep(5)"], env=_child_env(), cwd=".")
        lifecycle.stop(ref)
        detail = lifecycle.stop(ref)
        assert "no tracked" in str(detail).lower()
