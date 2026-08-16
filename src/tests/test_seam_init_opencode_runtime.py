"""Tests for the initializer-owned OpenCode runtime lifecycle manager.

Every diagnose subprocess call is routed through the DiagnoseRunner boundary,
and every server start/stop is routed through the ServerLifecyclePort boundary.
The suite NEVER spawns a real subprocess, NEVER opens a real socket, and NEVER
calls time.sleep. Each test uses an explicit Given/When/Then block.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence

import pytest

from seam_init.models import FailureKind, SafeDetail
from seam_init.opencode_runtime import (
    DiagnoseResult,
    DiagnoseRunner,
    EnvPatch,
    OwnedProcessRef,
    OwnedServerHandle,
    ReadinessFact,
    ReadinessMode,
    RuntimeOutcome,
    RuntimePorts,
    RuntimeRequest,
    ServerLifecyclePort,
    ServerOwnership,
    classify_diagnose_exit,
    ensure_server,
    parse_env_patch,
)

_PYTHON = "/usr/local/bin/python"
_DIAGNOSE = "/repo/scripts/diagnose_seam_opencode.py"
_DEFAULT_URL = "http://127.0.0.1:4098"
_EXE = "C:\\Users\\test\\.opencode\\bin\\opencode.exe" if os.name == "nt" else "/home/user/.opencode/bin/opencode"


# ---------------------------------------------------------------------------
# helpers and fakes
# ---------------------------------------------------------------------------


def _diag(
    argv: Sequence[str], *, returncode: int = 0,
    stdout: str = "", stderr: str = "",
) -> DiagnoseResult:
    return DiagnoseResult(
        argv=tuple(argv), returncode=returncode,
        stdout=SafeDetail(stdout), stderr=SafeDetail(stderr),
    )


def _request(
    *,
    readiness_mode: ReadinessMode = ReadinessMode.BASIC,
    start_timeout: float = 30.0,
    base_env: Mapping[str, str] | None = None,
    opencode_executable: str = _EXE,
    server_url: str = _DEFAULT_URL,
) -> RuntimeRequest:
    return RuntimeRequest(
        diagnose_argv_prefix=(_PYTHON, _DIAGNOSE),
        opencode_executable=opencode_executable,
        server_url=server_url,
        server_hostname="127.0.0.1",
        server_port=4098,
        readiness_mode=readiness_mode,
        base_env=base_env if base_env is not None else {"PATH": "/usr/bin"},
        start_timeout=start_timeout,
        poll_interval=1.0,
    )


def _ports(
    runner: DiagnoseRunner,
    lifecycle: ServerLifecyclePort,
    *,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> RuntimePorts:
    return RuntimePorts(
        diagnose_runner=runner,
        lifecycle=lifecycle,
        sleep=sleep or _noop_sleep,
        monotonic=monotonic or _static_clock(),
    )


def _noop_sleep(_seconds: float) -> None:
    pass


def _static_clock() -> Callable[[], float]:
    """Clock that advances 100s per call, ensuring deadlines are reached."""
    state = [0.0]

    def _tick() -> float:
        state[0] += 100.0
        return state[0]

    return _tick


def _slow_clock() -> Callable[[], float]:
    """Clock advancing 1s per call, so deadlines are NOT reached immediately."""
    state = [0.0]

    def _tick() -> float:
        state[0] += 1.0
        return state[0]

    return _tick


def _is_env_mode(argv: list[str]) -> bool:
    return "--mode" in argv and argv[argv.index("--mode") + 1] == "env"


def _is_readiness_mode(argv: list[str]) -> bool:
    if "--mode" not in argv:
        return False
    return argv[argv.index("--mode") + 1] in ("basic", "message")


class _RuleRunner:
    """DiagnoseRunner double: matches argv by callable, records all calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self._rules: list[
            tuple[Callable[[list[str]], bool], DiagnoseResult]
        ] = []

    def add(
        self, match: Callable[[list[str]], bool], result: DiagnoseResult,
    ) -> None:
        self._rules.append((match, result))

    def run(
        self, argv: Sequence[str], *, env: Mapping[str, str] | None = None,
    ) -> DiagnoseResult:
        argv_list = list(argv)
        env_dict: dict[str, str] | None = dict(env) if env is not None else None
        self.calls.append((argv_list, env_dict))
        for match, result in self._rules:
            if match(argv_list):
                return result
        raise AssertionError(f"no rule matched argv: {argv_list}")


class _StatefulRunner:
    """Returns exit 40 on the first readiness call, then *after_code*."""

    def __init__(self, *, after_code: int = 20, env_stdout: str = "") -> None:
        self._after_code = after_code
        self._env_stdout = env_stdout
        self._readiness_calls = 0
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(
        self, argv: Sequence[str], *, env: Mapping[str, str] | None = None,
    ) -> DiagnoseResult:
        argv_list = list(argv)
        env_dict: dict[str, str] | None = dict(env) if env is not None else None
        self.calls.append((argv_list, env_dict))
        if _is_env_mode(argv_list):
            return _diag(argv_list, stdout=self._env_stdout)
        if _is_readiness_mode(argv_list):
            self._readiness_calls += 1
            rc = 40 if self._readiness_calls == 1 else self._after_code
            return _diag(argv_list, returncode=rc)
        raise AssertionError(f"unexpected argv: {argv_list}")


class _FakeLifecycle:
    """ServerLifecyclePort double: records start/stop, never does I/O."""

    def __init__(
        self, *,
        stop_always_raises: Exception | None = None,
        stop_fails_first: bool = False,
    ) -> None:
        self.start_calls: list[tuple[list[str], dict[str, str], str]] = []
        self.stop_calls: list[int] = []
        self._stop_always_raises = stop_always_raises
        self._stop_fails_first = stop_fails_first
        self._next_id = 0
        self._running: set[int] = set()

    def start(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str,
    ) -> OwnedProcessRef:
        self._next_id += 1
        ref = OwnedProcessRef(id=self._next_id)
        self.start_calls.append((list(argv), dict(env), cwd))
        self._running.add(ref.id)
        return ref

    def stop(self, ref: OwnedProcessRef) -> SafeDetail:
        self.stop_calls.append(ref.id)
        call_num = len(self.stop_calls)
        if self._stop_fails_first and call_num == 1:
            raise RuntimeError("first stop attempt failed")
        if self._stop_always_raises is not None:
            raise self._stop_always_raises
        self._running.discard(ref.id)
        return SafeDetail(f"stopped owned server ref={ref.id}")

    def is_running(self, ref: OwnedProcessRef) -> bool:
        return ref.id in self._running


class _DyingLifecycle(_FakeLifecycle):
    """Lifecycle where the owned process dies immediately after start."""

    def start(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str,
    ) -> OwnedProcessRef:
        ref = super().start(argv, env=env, cwd=cwd)
        self._running.discard(ref.id)
        return ref


# ---------------------------------------------------------------------------
# parse_env_patch
# ---------------------------------------------------------------------------


class TestParseEnvPatch:
    def test_parses_all_allowed_keys_from_shell_quoted_output(self) -> None:
        # Given: realistic --emit-env output
        stdout = (
            "export NO_PROXY='127.0.0.1,localhost,::1'\n"
            "export no_proxy='127.0.0.1,localhost,::1'\n"
            "export PYTHONUNBUFFERED='1'\n"
        )
        # When
        patch = parse_env_patch(stdout)
        # Then: all three allowed keys extracted with correct values
        assert not patch.is_empty
        child = patch.apply_to({"PATH": "/bin"})
        assert child["NO_PROXY"] == "127.0.0.1,localhost,::1"
        assert child["no_proxy"] == "127.0.0.1,localhost,::1"
        assert child["PYTHONUNBUFFERED"] == "1"

    def test_empty_output_yields_empty_patch(self) -> None:
        # Given / When
        patch = parse_env_patch("")
        # Then
        assert patch.is_empty
        assert patch.apply_to({"X": "1"}) == {"X": "1"}

    def test_rejects_non_export_lines(self) -> None:
        # Given: lines without 'export ' prefix
        stdout = "NO_PROXY=127.0.0.1\neval $(malicious)\nrm -rf /\n"
        # When
        patch = parse_env_patch(stdout)
        # Then: nothing parsed
        assert patch.is_empty

    def test_rejects_disallowed_keys_silently(self) -> None:
        # Given: export lines with keys outside the allowlist
        stdout = (
            "export SECRET_KEY='sk-live-xxx'\n"
            "export PATH='/evil:$PATH'\n"
            "export NO_PROXY='127.0.0.1'\n"
        )
        # When
        patch = parse_env_patch(stdout)
        # Then: only NO_PROXY extracted; SECRET_KEY dropped
        child = patch.apply_to({})
        assert child == {"NO_PROXY": "127.0.0.1"}
        assert "SECRET_KEY" not in child

    def test_rejects_injection_lines_with_trailing_commands(self) -> None:
        # Given: line with shell metacharacters after the quoted value
        stdout = (
            "export NO_PROXY='127.0.0.1'; rm -rf /\n"
            "export PYTHONUNBUFFERED='1'\n"
        )
        # When
        patch = parse_env_patch(stdout)
        # Then: the injection line produces too many shlex tokens → dropped;
        # only the well-formed PYTHONUNBUFFERED line is parsed
        child = patch.apply_to({})
        assert child == {"PYTHONUNBUFFERED": "1"}

    def test_command_substitution_treated_as_literal_not_executed(self) -> None:
        # Given: shlex treats $(...) as literal text, never as command subst
        stdout = "export NO_PROXY='$(whoami)'\n"
        # When
        patch = parse_env_patch(stdout)
        # Then: the value is the literal string, NOT the output of whoami
        child = patch.apply_to({})
        assert child["NO_PROXY"] == "$(whoami)"

    def test_apply_to_does_not_mutate_base_env(self) -> None:
        # Given
        base: dict[str, str] = {"PATH": "/usr/bin"}
        patch = parse_env_patch("export NO_PROXY='127.0.0.1'\n")
        # When
        child = patch.apply_to(base)
        # Then: base is untouched
        assert base == {"PATH": "/usr/bin"}
        assert child is not base
        assert child["NO_PROXY"] == "127.0.0.1"


# ---------------------------------------------------------------------------
# classify_diagnose_exit
# ---------------------------------------------------------------------------


class TestClassifyDiagnoseExit:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (0, ReadinessFact.READY),
            (20, ReadinessFact.BASIC_READY),
            (40, ReadinessFact.SERVER_UNREACHABLE),
            (41, ReadinessFact.AGENT_UNAVAILABLE),
            (42, ReadinessFact.SESSION_UNAVAILABLE),
            (43, ReadinessFact.MESSAGE_UNAVAILABLE),
            (50, ReadinessFact.INVALID_ARGUMENT),
        ],
    )
    def test_documented_codes_map_to_typed_facts(
        self, code: int, expected: ReadinessFact,
    ) -> None:
        assert classify_diagnose_exit(code) is expected

    def test_undocumented_code_maps_to_unknown(self) -> None:
        assert classify_diagnose_exit(99) is ReadinessFact.UNKNOWN

    def test_never_returns_raw_int(self) -> None:
        result = classify_diagnose_exit(40)
        assert isinstance(result, ReadinessFact)
        assert not isinstance(result, int)


# ---------------------------------------------------------------------------
# reuse foreign server
# ---------------------------------------------------------------------------


class TestReuseForeignServer:
    def test_ready_foreign_server_reused_without_start_or_stop(self) -> None:
        # Given: diagnose reports ready (exit 0)
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(
            _is_readiness_mode,
            _diag([_PYTHON, _DIAGNOSE], returncode=0),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: server reused, nothing started or stopped
        assert outcome.ownership is ServerOwnership.REUSED_FOREIGN
        assert outcome.readiness_fact is ReadinessFact.READY
        assert outcome.owned_handle is None
        assert outcome.ok is True
        assert lifecycle.start_calls == []
        assert lifecycle.stop_calls == []

    def test_basic_ready_foreign_server_reused(self) -> None:
        # Given
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(
            _is_readiness_mode,
            _diag([_PYTHON, _DIAGNOSE], returncode=20),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then
        assert outcome.ownership is ServerOwnership.REUSED_FOREIGN
        assert outcome.readiness_fact is ReadinessFact.BASIC_READY

    def test_readiness_uses_message_mode_when_requested(self) -> None:
        # Given: readiness_mode=MESSAGE
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(_is_readiness_mode, _diag([_PYTHON, _DIAGNOSE], returncode=0))
        lifecycle = _FakeLifecycle()
        # When
        ensure_server(
            _request(readiness_mode=ReadinessMode.MESSAGE),
            ports=_ports(runner, lifecycle),
        )
        # Then: the readiness argv includes --mode message
        readiness_calls = [c for c, _ in runner.calls if _is_readiness_mode(c)]
        assert len(readiness_calls) == 1
        idx = readiness_calls[0].index("--mode")
        assert readiness_calls[0][idx + 1] == "message"


# ---------------------------------------------------------------------------
# start owned server
# ---------------------------------------------------------------------------


class TestStartOwnedServer:
    def test_unreachable_starts_owned_server_then_succeeds(self) -> None:
        # Given: first readiness returns 40, then after start returns 20
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: server started, ownership=OWNED
        assert outcome.ownership is ServerOwnership.OWNED
        assert outcome.readiness_fact is ReadinessFact.BASIC_READY
        assert outcome.owned_handle is not None
        assert outcome.ok is True
        assert len(lifecycle.start_calls) == 1
        assert lifecycle.stop_calls == []

    def test_owned_server_argv_is_exact_serve_command(self) -> None:
        # Given: unreachable, then ready after start
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: start argv uses resolved executable + exact serve flags
        assert len(lifecycle.start_calls) == 1
        argv = lifecycle.start_calls[0][0]
        assert argv == [_EXE, "serve", "--port", "4098", "--hostname", "127.0.0.1"]

    def test_owned_server_env_has_no_proxy_patch(self) -> None:
        # Given: env patch includes NO_PROXY
        runner = _StatefulRunner(
            after_code=20,
            env_stdout="export NO_PROXY='127.0.0.1,localhost,::1'\n",
        )
        lifecycle = _FakeLifecycle()
        # When
        ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: start env contains NO_PROXY and the original base env value
        start_env = lifecycle.start_calls[0][1]
        assert start_env["NO_PROXY"] == "127.0.0.1,localhost,::1"
        assert start_env["PATH"] == "/usr/bin"

    def test_owned_server_fails_to_become_ready_is_stopped(self) -> None:
        # Given: unreachable, then stays agent-unavailable after start
        runner = _StatefulRunner(after_code=41)
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            _request(start_timeout=0.0),
            ports=_ports(runner, lifecycle),
        )
        # Then: failure, server was started then stopped
        assert outcome.ownership is ServerOwnership.OWNED
        assert outcome.readiness_fact is ReadinessFact.AGENT_UNAVAILABLE
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert outcome.ok is False
        assert len(lifecycle.start_calls) == 1
        assert len(lifecycle.stop_calls) == 1

    def test_env_mode_called_before_readiness_mode(self) -> None:
        # Given
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: first call is env mode, second is readiness mode
        assert _is_env_mode(runner.calls[0][0])
        assert _is_readiness_mode(runner.calls[1][0])

    def test_child_env_used_for_readiness_has_patch(self) -> None:
        # Given: env patch is emitted
        runner = _StatefulRunner(
            after_code=20,
            env_stdout="export PYTHONUNBUFFERED='1'\n",
        )
        lifecycle = _FakeLifecycle()
        # When
        ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: the first readiness call's env includes PYTHONUNBUFFERED
        readiness_env = next(
            e for c, e in runner.calls if _is_readiness_mode(c) and e is not None
        )
        assert readiness_env.get("PYTHONUNBUFFERED") == "1"


# ---------------------------------------------------------------------------
# fail safely on occupied / non-session-capable port
# ---------------------------------------------------------------------------


class TestFailSafelyOnOccupied:
    @pytest.mark.parametrize(
        ("code", "fact"),
        [
            (41, ReadinessFact.AGENT_UNAVAILABLE),
            (42, ReadinessFact.SESSION_UNAVAILABLE),
            (43, ReadinessFact.MESSAGE_UNAVAILABLE),
        ],
    )
    def test_occupied_port_fails_without_kill_or_second_server(
        self, code: int, fact: ReadinessFact,
    ) -> None:
        # Given: diagnose reports agent/session/message unavailable
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(
            _is_readiness_mode, _diag([_PYTHON, _DIAGNOSE], returncode=code),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: no start, no stop, typed failure
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert outcome.readiness_fact is fact
        assert outcome.ownership is ServerOwnership.NONE
        assert lifecycle.start_calls == []
        assert lifecycle.stop_calls == []

    def test_invalid_argument_fails_safely(self) -> None:
        # Given
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(
            _is_readiness_mode,
            _diag([_PYTHON, _DIAGNOSE], returncode=50),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert outcome.readiness_fact is ReadinessFact.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# normal failure path: stop raises → typed outcome (not raw RuntimeError)
# ---------------------------------------------------------------------------


class TestNormalFailureStopRaises:
    def test_stop_raising_in_normal_failure_returns_typed_outcome(self) -> None:
        # Given: unreachable → start → not-ready, AND lifecycle.stop raises
        runner = _StatefulRunner(after_code=41)
        lifecycle = _FakeLifecycle(stop_always_raises=RuntimeError("stop broken"))
        # When
        outcome = ensure_server(
            _request(start_timeout=0.0),
            ports=_ports(runner, lifecycle),
        )
        # Then: typed RuntimeOutcome returned, NOT raw RuntimeError
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert outcome.readiness_fact is ReadinessFact.AGENT_UNAVAILABLE
        assert len(lifecycle.stop_calls) == 1

    def test_stop_detail_in_diagnostics_when_stop_fails(self) -> None:
        # Given
        runner = _StatefulRunner(after_code=42)
        lifecycle = _FakeLifecycle(stop_always_raises=RuntimeError("boom"))
        # When
        outcome = ensure_server(
            _request(start_timeout=0.0),
            ports=_ports(runner, lifecycle),
        )
        # Then: diagnostics contain the stop failure detail
        assert any("stop failed" in str(d).lower() for d in outcome.diagnostics)


# ---------------------------------------------------------------------------
# canonical URL: only 127.0.0.1:4098 may have an owned server started
# ---------------------------------------------------------------------------


class TestCanonicalUrl:
    def test_non_canonical_url_unreachable_does_not_start_server(self) -> None:
        # Given: server unreachable at a non-canonical URL
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(
            _is_readiness_mode, _diag([_PYTHON, _DIAGNOSE], returncode=40),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            _request(server_url="http://192.168.1.5:4098"),
            ports=_ports(runner, lifecycle),
        )
        # Then: no server started, typed failure
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert outcome.readiness_fact is ReadinessFact.SERVER_UNREACHABLE
        assert lifecycle.start_calls == []

    def test_canonical_url_but_wrong_hostname_does_not_start(self) -> None:
        # Given: URL is canonical but hostname is 0.0.0.0
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            RuntimeRequest(
                diagnose_argv_prefix=(_PYTHON, _DIAGNOSE),
                opencode_executable=_EXE,
                server_url=_DEFAULT_URL,
                server_hostname="0.0.0.0",
                server_port=4098,
                readiness_mode=ReadinessMode.BASIC,
                base_env={"PATH": "/usr/bin"},
            ),
            ports=_ports(runner, lifecycle),
        )
        # Then: no server started
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert lifecycle.start_calls == []

    def test_canonical_url_but_wrong_port_does_not_start(self) -> None:
        # Given: URL is canonical but port is 4099
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            RuntimeRequest(
                diagnose_argv_prefix=(_PYTHON, _DIAGNOSE),
                opencode_executable=_EXE,
                server_url=_DEFAULT_URL,
                server_hostname="127.0.0.1",
                server_port=4099,
                readiness_mode=ReadinessMode.BASIC,
                base_env={"PATH": "/usr/bin"},
            ),
            ports=_ports(runner, lifecycle),
        )
        # Then: no server started
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert lifecycle.start_calls == []

    def test_relative_executable_rejected_before_start(self) -> None:
        # Given: relative executable path
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            _request(opencode_executable="opencode"),
            ports=_ports(runner, lifecycle),
        )
        # Then: no server started
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert "absolute" in str(outcome.failure_detail).lower()
        assert lifecycle.start_calls == []

    def test_bare_name_executable_rejected_before_start(self) -> None:
        # Given: bare name without path separator
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            _request(opencode_executable="./opencode"),
            ports=_ports(runner, lifecycle),
        )
        # Then: no server started
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert lifecycle.start_calls == []

    def test_non_canonical_url_ready_is_reused(self) -> None:
        # Given: server ready at non-canonical URL
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE]))
        runner.add(
            _is_readiness_mode, _diag([_PYTHON, _DIAGNOSE], returncode=0),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(
            _request(server_url="http://192.168.1.5:4098"),
            ports=_ports(runner, lifecycle),
        )
        # Then: reused, no start
        assert outcome.ownership is ServerOwnership.REUSED_FOREIGN
        assert outcome.ok is True


# ---------------------------------------------------------------------------
# env-mode failure: fail closed before readiness/start
# ---------------------------------------------------------------------------


class TestEnvModeFailure:
    def test_nonzero_env_returncode_returns_typed_failure(self) -> None:
        # Given: env-mode diagnose returns rc=1
        runner = _RuleRunner()
        runner.add(
            _is_env_mode, _diag([_PYTHON, _DIAGNOSE], returncode=1, stderr="boom"),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: typed failure, no readiness/start calls
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert outcome.readiness_fact is ReadinessFact.UNKNOWN
        assert outcome.ownership is ServerOwnership.NONE
        assert lifecycle.start_calls == []
        readiness_calls = [c for c, _ in runner.calls if _is_readiness_mode(c)]
        assert readiness_calls == []

    def test_env_failure_detail_includes_returncode(self) -> None:
        # Given
        runner = _RuleRunner()
        runner.add(_is_env_mode, _diag([_PYTHON, _DIAGNOSE], returncode=2))
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then
        assert "rc=2" in str(outcome.failure_detail)

    def test_env_failure_detail_is_bounded(self) -> None:
        # Given: env-mode returns nonzero with huge stderr
        huge = "X" * 10000
        runner = _RuleRunner()
        runner.add(
            _is_env_mode, _diag([_PYTHON, _DIAGNOSE], returncode=1, stderr=huge),
        )
        lifecycle = _FakeLifecycle()
        # When
        outcome = ensure_server(_request(), ports=_ports(runner, lifecycle))
        # Then: detail is bounded
        assert len(str(outcome.failure_detail)) <= 600


# ---------------------------------------------------------------------------
# cleanup guard: interruption during polling
# ---------------------------------------------------------------------------


class TestCleanupGuardInterruption:
    def _setup_unreachable(self) -> tuple[_StatefulRunner, _FakeLifecycle]:
        runner = _StatefulRunner(after_code=41)
        lifecycle = _FakeLifecycle()
        return runner, lifecycle

    def test_keyboard_interrupt_during_poll_stops_owned_process(self) -> None:
        # Given: server unreachable, then poll sleep raises KeyboardInterrupt
        runner, lifecycle = self._setup_unreachable()

        def _interrupt(_seconds: float) -> None:
            raise KeyboardInterrupt

        # When
        with pytest.raises(KeyboardInterrupt):
            ensure_server(
                _request(start_timeout=30.0),
                ports=_ports(runner, lifecycle, sleep=_interrupt, monotonic=_slow_clock()),
            )
        # Then: owned process was stopped despite the interrupt
        assert len(lifecycle.stop_calls) == 1

    def test_system_exit_during_poll_stops_owned_process(self) -> None:
        # Given
        runner, lifecycle = self._setup_unreachable()

        def _interrupt(_seconds: float) -> None:
            raise SystemExit(1)

        # When
        with pytest.raises(SystemExit):
            ensure_server(
                _request(start_timeout=30.0),
                ports=_ports(runner, lifecycle, sleep=_interrupt, monotonic=_slow_clock()),
            )
        # Then
        assert len(lifecycle.stop_calls) == 1

    def test_base_exception_during_poll_stops_owned_process(self) -> None:
        # Given: a non-KeyboardInterrupt/SystemExit BaseException
        runner, lifecycle = self._setup_unreachable()

        class _Sentinel(BaseException):
            pass

        def _interrupt(_seconds: float) -> None:
            raise _Sentinel

        # When
        with pytest.raises(_Sentinel):
            ensure_server(
                _request(start_timeout=30.0),
                ports=_ports(runner, lifecycle, sleep=_interrupt, monotonic=_slow_clock()),
            )
        # Then
        assert len(lifecycle.stop_calls) == 1

    def test_diagnose_runner_during_poll_stops_owned_process(self) -> None:
        # Given: diagnose runner raises during polling (2nd readiness call)
        class _ExplodingRunner:
            def __init__(self) -> None:
                self._readiness_calls = 0
                self.calls: list[tuple[list[str], dict[str, str] | None]] = []

            def run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> DiagnoseResult:
                argv_list = list(argv)
                self.calls.append((argv_list, dict(env) if env else None))
                if _is_env_mode(argv_list):
                    return _diag(argv_list)
                if _is_readiness_mode(argv_list):
                    self._readiness_calls += 1
                    if self._readiness_calls == 1:
                        return _diag(argv_list, returncode=40)
                    raise OSError("poll blew up")
                raise AssertionError(f"unexpected: {argv_list}")

        runner = _ExplodingRunner()
        lifecycle = _FakeLifecycle()
        # When
        with pytest.raises(OSError):
            ensure_server(
                _request(start_timeout=30.0),
                ports=_ports(runner, lifecycle, monotonic=_slow_clock()),
            )
        # Then
        assert len(lifecycle.stop_calls) == 1

    def test_original_exception_preserved_when_stop_also_fails(self) -> None:
        # Given: stop() raises when cleanup guard tries it
        runner, _ = self._setup_unreachable()
        lifecycle = _FakeLifecycle(stop_always_raises=RuntimeError("stop broken"))

        def _interrupt(_seconds: float) -> None:
            raise KeyboardInterrupt

        # When: KeyboardInterrupt fires; stop() also fails
        with pytest.raises(KeyboardInterrupt):
            ensure_server(
                _request(start_timeout=30.0),
                ports=_ports(runner, lifecycle, sleep=_interrupt, monotonic=_slow_clock()),
            )
        # Then: the ORIGINAL KeyboardInterrupt is raised, not RuntimeError
        # (stop-failure is best-effort suppressed in cleanup guard)


# ---------------------------------------------------------------------------
# early process death during polling
# ---------------------------------------------------------------------------


class TestEarlyProcessDeath:
    def test_owned_process_dies_immediately_returns_failure_fast(self) -> None:
        # Given: process dies immediately after start
        runner = _StatefulRunner(after_code=41)
        lifecycle = _DyingLifecycle()
        # When: slow clock (1s/call) so deadline is far (30s)
        outcome = ensure_server(
            _request(start_timeout=30.0),
            ports=_ports(runner, lifecycle, monotonic=_slow_clock()),
        )
        # Then: failure returned, not timed out; only 2 readiness calls total
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert len(lifecycle.stop_calls) == 1
        readiness_calls = [c for c, _ in runner.calls if _is_readiness_mode(c)]
        assert len(readiness_calls) == 2

    def test_early_death_does_not_sleep_until_timeout(self) -> None:
        # Given: process dies after start; sleep counter tracks calls
        runner = _StatefulRunner(after_code=41)
        lifecycle = _DyingLifecycle()
        sleep_count = [0]

        def _counting_sleep(_s: float) -> None:
            sleep_count[0] += 1

        # When
        ensure_server(
            _request(start_timeout=30.0),
            ports=_ports(runner, lifecycle, sleep=_counting_sleep, monotonic=_slow_clock()),
        )
        # Then: sleep was never called (early death detected before first sleep)
        assert sleep_count[0] == 0


# ---------------------------------------------------------------------------
# resolved executable path in serve argv
# ---------------------------------------------------------------------------


class TestResolvedExecutable:
    def test_serve_argv_uses_resolved_executable(self) -> None:
        # Given: resolved executable from Task 7
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        exe = "/home/user/.opencode/bin/opencode"
        # When
        ensure_server(
            _request(opencode_executable=exe),
            ports=_ports(runner, lifecycle),
        )
        # Then: serve argv[0] is the resolved path
        argv = lifecycle.start_calls[0][0]
        assert argv[0] == exe

    def test_serve_argv_preserves_path_with_spaces(self) -> None:
        # Given: path with spaces
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        exe = "/path with spaces/opencode"
        # When
        ensure_server(
            _request(opencode_executable=exe),
            ports=_ports(runner, lifecycle),
        )
        # Then: argv[0] is the exact path, not split on spaces
        argv = lifecycle.start_calls[0][0]
        assert argv[0] == "/path with spaces/opencode"
        assert len(argv) == 6

    def test_owned_handle_serve_argv_uses_resolved_executable(self) -> None:
        # Given
        runner = _StatefulRunner(after_code=20)
        lifecycle = _FakeLifecycle()
        exe = "/opt/opencode/bin/opencode"
        # When
        outcome = ensure_server(
            _request(opencode_executable=exe),
            ports=_ports(runner, lifecycle),
        )
        # Then: the handle's serve_argv also uses the resolved path
        assert outcome.owned_handle is not None
        assert outcome.owned_handle.serve_argv[0] == exe


# ---------------------------------------------------------------------------
# stop-failure in OwnedServerHandle.close
# ---------------------------------------------------------------------------


class TestStopFailureInClose:
    def _make_handle(
        self, lifecycle: _FakeLifecycle,
    ) -> tuple[OwnedServerHandle, _FakeLifecycle, OwnedProcessRef]:
        ref = lifecycle.start(
            [_EXE, "serve"], env={"PATH": "/bin"}, cwd="/tmp",
        )
        handle = OwnedServerHandle(
            lifecycle=lifecycle, ref=ref, serve_argv=(_EXE, "serve"),
        )
        return handle, lifecycle, ref

    def test_stop_failure_returns_diagnostic_not_false_success(self) -> None:
        # Given: lifecycle.stop always raises
        lifecycle = _FakeLifecycle(stop_always_raises=RuntimeError("terminate failed"))
        handle, _, _ = self._make_handle(lifecycle)
        # When
        detail = handle.close()
        # Then: diagnostic mentions failure
        assert "stop failed" in str(detail).lower()

    def test_close_does_not_mark_stopped_when_stop_raises(self) -> None:
        # Given: stop raises
        lifecycle = _FakeLifecycle(stop_always_raises=RuntimeError("boom"))
        handle, _, _ = self._make_handle(lifecycle)
        # When
        handle.close()
        # Then: is_stopped is False — close is retryable
        assert not handle.is_stopped

    def test_close_retry_succeeds_after_first_stop_failure(self) -> None:
        # Given: stop fails on first call, succeeds on second
        lifecycle = _FakeLifecycle(stop_fails_first=True)
        handle, _, _ = self._make_handle(lifecycle)
        # When: first close fails
        first = handle.close()
        assert "stop failed" in str(first).lower()
        assert not handle.is_stopped
        # When: second close succeeds
        second = handle.close()
        # Then: process stopped, handle marked stopped
        assert "stopped" in str(second).lower()
        assert handle.is_stopped
        assert len(lifecycle.stop_calls) == 2

    def test_repeated_close_after_success_returns_already_stopped(self) -> None:
        # Given: normal lifecycle (stop succeeds)
        lifecycle = _FakeLifecycle()
        handle, _, _ = self._make_handle(lifecycle)
        # When: close succeeds, then close again
        _ = handle.close()
        second = handle.close()
        # Then: second says "already stopped"
        assert "already" in str(second).lower()
        assert handle.is_stopped
        assert len(lifecycle.stop_calls) == 1


# ---------------------------------------------------------------------------
# OwnedServerHandle cleanup
# ---------------------------------------------------------------------------


class TestOwnedServerHandleCleanup:
    def _make_handle(
        self,
    ) -> tuple[OwnedServerHandle, _FakeLifecycle, OwnedProcessRef]:
        lifecycle = _FakeLifecycle()
        ref = lifecycle.start(
            ["opencode", "serve"], env={"PATH": "/bin"}, cwd="/tmp",
        )
        handle = OwnedServerHandle(
            lifecycle=lifecycle,
            ref=ref,
            serve_argv=("opencode", "serve", "--port", "4098"),
        )
        return handle, lifecycle, ref

    def test_close_stops_owned_process(self) -> None:
        # Given
        handle, lifecycle, ref = self._make_handle()
        # When
        detail = handle.close()
        # Then
        assert handle.is_stopped
        assert lifecycle.stop_calls == [ref.id]
        assert "stopped" in str(detail).lower()

    def test_repeated_close_is_idempotent(self) -> None:
        # Given
        handle, lifecycle, _ = self._make_handle()
        # When: close twice
        _ = handle.close()
        second = handle.close()
        # Then: only one stop call, second returns safe diagnostic
        assert len(lifecycle.stop_calls) == 1
        assert handle.is_stopped
        assert "already" in str(second).lower()

    def test_context_manager_stops_on_normal_exit(self) -> None:
        # Given
        handle, lifecycle, ref = self._make_handle()
        # When
        with handle:
            pass
        # Then
        assert handle.is_stopped
        assert lifecycle.stop_calls == [ref.id]

    def test_context_manager_stops_on_keyboard_interrupt(self) -> None:
        # Given
        handle, lifecycle, ref = self._make_handle()
        # When
        with pytest.raises(KeyboardInterrupt):
            with handle:
                raise KeyboardInterrupt
        # Then: stopped even on interrupt
        assert handle.is_stopped
        assert lifecycle.stop_calls == [ref.id]

    def test_context_manager_stops_on_system_exit(self) -> None:
        # Given
        handle, lifecycle, ref = self._make_handle()
        # When
        with pytest.raises(SystemExit):
            with handle:
                raise SystemExit(1)
        # Then: stopped even on SystemExit
        assert handle.is_stopped
        assert lifecycle.stop_calls == [ref.id]

    def test_serve_argv_property_exposes_started_command(self) -> None:
        # Given
        handle, _, _ = self._make_handle()
        # Then
        assert handle.serve_argv == ("opencode", "serve", "--port", "4098")


# ---------------------------------------------------------------------------
# RuntimeOutcome invariants
# ---------------------------------------------------------------------------


class TestRuntimeOutcomeInvariants:
    def test_failed_requires_failure_kind(self) -> None:
        with pytest.raises(ValueError):
            RuntimeOutcome(
                readiness_fact=ReadinessFact.SERVER_UNREACHABLE,
                ownership=ServerOwnership.REUSED_FOREIGN,
                server_url=_DEFAULT_URL,
                env_patch=EnvPatch(),
            )

    def test_ready_outcome_has_no_failure(self) -> None:
        outcome = RuntimeOutcome(
            readiness_fact=ReadinessFact.READY,
            ownership=ServerOwnership.REUSED_FOREIGN,
            server_url=_DEFAULT_URL,
            env_patch=EnvPatch(),
        )
        assert outcome.failure_kind is None
        assert outcome.ok is True


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_rule_runner_satisfies_diagnose_runner(self) -> None:
        assert isinstance(_RuleRunner(), DiagnoseRunner)

    def test_stateful_runner_satisfies_diagnose_runner(self) -> None:
        assert isinstance(_StatefulRunner(), DiagnoseRunner)

    def test_fake_lifecycle_satisfies_lifecycle_port(self) -> None:
        assert isinstance(_FakeLifecycle(), ServerLifecyclePort)
