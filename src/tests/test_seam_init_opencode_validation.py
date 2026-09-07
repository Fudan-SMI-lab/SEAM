"""Tests for OpenCode message acceptance validation through the SEAM contract.

Every diagnose subprocess call is routed through the DiagnoseRunner boundary;
every version probe through the VersionProbe boundary; every config/model check
through the RuntimePort boundary. The suite NEVER spawns a real provider, NEVER
uses real credentials, and NEVER touches the real internet. Integration tests
use a local fake SEAM-compatible HTTP server on 127.0.0.1 only. Each test uses
an explicit Given/When/Then block.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import final

import pytest

from seam_init.models import (
    AuthState,
    BillableCallConsent,
    FailureKind,
    InitializerContractError,
    SafeDetail,
)
from seam_init.opencode_discovery import JsonDict, RuntimePort
from seam_init.opencode_runtime_types import DiagnoseResult, DiagnoseRunner
from seam_init.opencode_validation import (
    SEAM_DIAG_OK_MARKER,
    ValidationFact,
    ValidationOutcome,
    ValidationPorts,
    ValidationRequest,
    VersionProbe,
    VersionResult,
    validate_opencode_messages,
)

# ---------------------------------------------------------------------------
# constants and helpers
# ---------------------------------------------------------------------------

_PYTHON = "/usr/local/bin/python"
_DIAGNOSE = "/repo/scripts/diagnose_seam_opencode.py"
_URL = "http://127.0.0.1:4098"
_EXE = (
    r"C:\Users\test\.opencode\bin\opencode.exe"
    if sys.platform == "win32"
    else "/home/user/.opencode/bin/opencode"
)
_CANARY_KEY = "sk-canary-secret-key-1234567890"


def _request(
    *,
    auth_state: AuthState = AuthState.PROVIDED,
    consent: BillableCallConsent = BillableCallConsent.GIVEN,
    provider_model: str = "openai/gpt-4",
    message_timeout: int = 30,
    server_url: str = _URL,
    opencode_executable: str = _EXE,
) -> ValidationRequest:
    return ValidationRequest(
        server_url=server_url,
        provider_model=provider_model,
        auth_state=auth_state,
        billable_consent=consent,
        opencode_executable=opencode_executable,
        diagnose_argv_prefix=(_PYTHON, _DIAGNOSE),
        message_timeout=message_timeout,
        base_env={"PATH": "/usr/bin"},
    )


# ---------------------------------------------------------------------------
# fake boundaries
# ---------------------------------------------------------------------------


@final
class _FakeVersionProbe:
    def __init__(self, *, ok: bool = True, version: str = "1.0.180") -> None:
        self._ok = ok
        self._version = version
        self.calls: list[str] = []

    def check(self, executable: str) -> VersionResult:
        self.calls.append(executable)
        if self._ok:
            return VersionResult(ok=True, version=SafeDetail(self._version))
        return VersionResult(
            ok=False, version=SafeDetail(""), error=SafeDetail("version probe failed"),
        )


@final
class _FakeRuntime:
    """RuntimePort double: returns canned debug config + models."""

    def __init__(
        self,
        *,
        config: JsonDict | None = {"provider": {}},
        models: tuple[str, ...] | None = ("openai/gpt-4",),
    ) -> None:
        self._config = config
        self._models = models

    def debug_config(self) -> JsonDict | None:
        return self._config

    def debug_models(self, config_bytes: bytes | None = None) -> tuple[str, ...] | None:
        _ = config_bytes
        return self._models


@final
class _RuleRunner:
    """DiagnoseRunner double: matches argv by callable, records all calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self._rules: list[tuple[Callable[[list[str]], bool], DiagnoseResult]] = []

    def add(self, match: Callable[[list[str]], bool], result: DiagnoseResult) -> None:
        self._rules.append((match, result))

    def run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> DiagnoseResult:
        argv_list = list(argv)
        env_dict: dict[str, str] | None = dict(env) if env is not None else None
        self.calls.append((argv_list, env_dict))
        for match, result in self._rules:
            if match(argv_list):
                return result
        raise AssertionError(f"no rule matched argv: {argv_list}")


def _is_message_mode(argv: list[str]) -> bool:
    return "--mode" in argv and argv[argv.index("--mode") + 1] == "message"


def _diag(
    argv: Sequence[str], *, returncode: int = 0, stdout: str = "", stderr: str = "",
) -> DiagnoseResult:
    return DiagnoseResult(
        argv=tuple(argv), returncode=returncode,
        stdout=SafeDetail(stdout), stderr=SafeDetail(stderr),
    )


def _ports(
    version: VersionProbe | None = None,
    runtime: RuntimePort | None = None,
    runner: DiagnoseRunner | None = None,
) -> ValidationPorts:
    return ValidationPorts(
        version_probe=version or _FakeVersionProbe(),
        runtime=runtime or _FakeRuntime(),
        diagnose_runner=runner or _RuleRunner(),
    )


# ---------------------------------------------------------------------------
# diagnose JSON output builder
# ---------------------------------------------------------------------------


def _diag_json(
    *,
    exit_code: int = 0,
    status: str = "ready",
    marker: bool = True,
    cleanup_ok: bool = True,
    message_status: int | None = 200,
    message_ok: bool = True,
    error: str = "",
    response_text: str | None = None,
    session_id: str = "probe-session-1",
) -> str:
    """Build realistic --json-only output from the diagnose script."""
    if response_text is None:
        response_text = SEAM_DIAG_OK_MARKER if marker else "wrong answer"
    probe: dict[str, object] = {
        "enabled": True,
        "ok": bool(message_ok and response_text),
        "session_id": session_id,
        "response_text": response_text,
        "contains_marker": marker,
        "error": error,
        "cleanup": {"ok": cleanup_ok, "status": 200 if cleanup_ok else 500},
    }
    if message_status is not None:
        probe["message"] = {"ok": message_ok, "status": message_status}
    result = {
        "ok": exit_code in {0, 20},
        "status": status,
        "exit_code": exit_code,
        "server_url": _URL,
        "message_probe": probe,
    }
    return json.dumps(result)


# ---------------------------------------------------------------------------
# timeout invariant
# ---------------------------------------------------------------------------


class TestTimeoutInvariant:
    def test_zero_timeout_rejected_at_construction(self) -> None:
        # Given / When / Then: construction raises before any external call
        with pytest.raises(InitializerContractError):
            _request(message_timeout=0)

    def test_negative_timeout_rejected_at_construction(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(message_timeout=-5)

    def test_huge_timeout_rejected_at_construction(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(message_timeout=10000)

    def test_minimum_timeout_accepted(self) -> None:
        # Given: minimum valid timeout
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        # When
        outcome = validate_opencode_messages(
            _request(message_timeout=5), ports=_ports(runner=runner),
        )
        # Then
        assert outcome.fact is ValidationFact.MESSAGE_READY

    def test_maximum_timeout_accepted(self) -> None:
        # Given: maximum valid timeout
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        # When
        outcome = validate_opencode_messages(
            _request(message_timeout=300), ports=_ports(runner=runner),
        )
        # Then
        assert outcome.fact is ValidationFact.MESSAGE_READY


# ---------------------------------------------------------------------------
# pre-checks: version, config, model membership
# ---------------------------------------------------------------------------


class TestVersionPreCheck:
    def test_version_failure_returns_version_fact(self) -> None:
        # Given: version probe reports failure
        version = _FakeVersionProbe(ok=False)
        runner = _RuleRunner()
        # When
        outcome = validate_opencode_messages(
            _request(), ports=_ports(version=version, runner=runner),
        )
        # Then: VERSION_FAILURE, zero diagnose calls
        assert outcome.fact is ValidationFact.VERSION_FAILURE
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION
        assert runner.calls == []

    def test_version_probe_receives_executable(self) -> None:
        # Given
        version = _FakeVersionProbe()
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        # When
        validate_opencode_messages(
            _request(opencode_executable="/opt/opencode/bin/opencode"),
            ports=_ports(version=version, runner=runner),
        )
        # Then: version probe called with the exact executable
        assert version.calls == ["/opt/opencode/bin/opencode"]


class TestConfigPreCheck:
    def test_config_unavailable_returns_config_failure(self) -> None:
        # Given: runtime returns None for debug_config
        runtime = _FakeRuntime(config=None)
        runner = _RuleRunner()
        # When
        outcome = validate_opencode_messages(
            _request(), ports=_ports(runtime=runtime, runner=runner),
        )
        # Then
        assert outcome.fact is ValidationFact.CONFIG_FAILURE
        assert runner.calls == []

    def test_model_catalog_unavailable_returns_config_failure(self) -> None:
        # Given: runtime returns None for debug_models
        runtime = _FakeRuntime(config={"provider": {}}, models=None)
        runner = _RuleRunner()
        # When
        outcome = validate_opencode_messages(
            _request(), ports=_ports(runtime=runtime, runner=runner),
        )
        # Then
        assert outcome.fact is ValidationFact.CONFIG_FAILURE
        assert runner.calls == []


class TestModelMembership:
    def test_model_not_in_catalog_returns_model_not_found(self) -> None:
        # Given: selected model not in available catalog
        runtime = _FakeRuntime(models=("anthropic/claude-3",))
        runner = _RuleRunner()
        # When
        outcome = validate_opencode_messages(
            _request(provider_model="openai/gpt-4"),
            ports=_ports(runtime=runtime, runner=runner),
        )
        # Then
        assert outcome.fact is ValidationFact.MODEL_NOT_FOUND
        assert runner.calls == []

    def test_empty_catalog_returns_model_not_found(self) -> None:
        runtime = _FakeRuntime(models=())
        runner = _RuleRunner()
        outcome = validate_opencode_messages(
            _request(), ports=_ports(runtime=runtime, runner=runner),
        )
        assert outcome.fact is ValidationFact.MODEL_NOT_FOUND

    def test_model_in_catalog_proceeds_to_validation(self) -> None:
        runtime = _FakeRuntime(models=("openai/gpt-4",))
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        outcome = validate_opencode_messages(
            _request(), ports=_ports(runtime=runtime, runner=runner),
        )
        assert outcome.fact is ValidationFact.MESSAGE_READY


# ---------------------------------------------------------------------------
# auth / consent deferral
# ---------------------------------------------------------------------------


class TestAuthDeferred:
    def test_skipped_auth_returns_auth_deferred(self) -> None:
        runner = _RuleRunner()
        outcome = validate_opencode_messages(
            _request(auth_state=AuthState.SKIPPED), ports=_ports(runner=runner),
        )
        assert outcome.fact is ValidationFact.AUTH_DEFERRED
        assert outcome.is_deferred
        assert outcome.failure_kind is None
        assert not outcome.ok

    def test_skipped_auth_makes_zero_diagnose_calls(self) -> None:
        runner = _RuleRunner()
        validate_opencode_messages(
            _request(auth_state=AuthState.SKIPPED), ports=_ports(runner=runner),
        )
        assert len(runner.calls) == 0

    def test_declined_consent_returns_auth_deferred(self) -> None:
        runner = _RuleRunner()
        outcome = validate_opencode_messages(
            _request(consent=BillableCallConsent.DECLINED), ports=_ports(runner=runner),
        )
        assert outcome.fact is ValidationFact.AUTH_DEFERRED

    def test_declined_consent_makes_zero_diagnose_calls(self) -> None:
        runner = _RuleRunner()
        validate_opencode_messages(
            _request(consent=BillableCallConsent.DECLINED), ports=_ports(runner=runner),
        )
        assert len(runner.calls) == 0

    def test_deferred_outcome_has_no_failure_kind(self) -> None:
        outcome = validate_opencode_messages(
            _request(auth_state=AuthState.SKIPPED), ports=_ports(),
        )
        assert outcome.failure_kind is None
        assert outcome.failure_detail == SafeDetail("")


# ---------------------------------------------------------------------------
# happy path: exact marker + session deleted
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_exact_marker_and_deletion_returns_message_ready(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE],
            stdout=_diag_json(exit_code=0, marker=True, cleanup_ok=True),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MESSAGE_READY
        assert outcome.ok
        assert outcome.marker_found
        assert outcome.session_deleted
        assert outcome.failure_kind is None

    def test_one_diagnose_call_made_in_happy_path(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert len(runner.calls) == 1

    def test_marker_with_surrounding_whitespace_accepted(self) -> None:
        # Given: response has whitespace around the marker
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE],
            stdout=_diag_json(response_text="  SEAM_DIAG_OK  "),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: whitespace-normalized match accepted
        assert outcome.fact is ValidationFact.MESSAGE_READY
        assert outcome.marker_found


# ---------------------------------------------------------------------------
# exact marker verification (not trusting contains_marker alone)
# ---------------------------------------------------------------------------


class TestExactMarkerVerification:
    def test_extra_text_after_marker_rejected(self) -> None:
        # Given: response has marker plus extra text
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE],
            stdout=_diag_json(response_text="SEAM_DIAG_OK plus extra text"),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: NOT MESSAGE_READY — exact match required
        assert outcome.fact is ValidationFact.MARKER_MISSING
        assert not outcome.marker_found

    def test_contains_marker_true_but_empty_response_is_malformed(self) -> None:
        # Given: boolean claims marker but response_text is empty
        runner = _RuleRunner()
        stdout = json.dumps({
            "ok": True, "exit_code": 0, "status": "ready",
            "message_probe": {
                "enabled": True, "ok": True, "session_id": "s1",
                "response_text": "", "contains_marker": True, "error": "",
                "cleanup": {"ok": True, "status": 200},
            },
        })
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=stdout))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: inconsistency detected
        assert outcome.fact is ValidationFact.MARKER_MALFORMED

    def test_contains_marker_false_but_response_has_marker_is_malformed(self) -> None:
        # Given: boolean says absent but response_text IS the marker
        runner = _RuleRunner()
        stdout = json.dumps({
            "ok": True, "exit_code": 0, "status": "ready",
            "message_probe": {
                "enabled": True, "ok": True, "session_id": "s1",
                "response_text": "SEAM_DIAG_OK", "contains_marker": False,
                "error": "", "cleanup": {"ok": True, "status": 200},
            },
        })
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=stdout))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: inconsistency detected
        assert outcome.fact is ValidationFact.MARKER_MALFORMED


# ---------------------------------------------------------------------------
# READY with malformed/missing probe
# ---------------------------------------------------------------------------


class TestReadyWithMalformedProbe:
    def test_ready_exit_with_no_message_probe_is_malformed(self) -> None:
        # Given: exit 0 but JSON has no message_probe
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=0,
            stdout=json.dumps({"ok": True, "status": "ready", "exit_code": 0}),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MALFORMED

    def test_ready_exit_with_unparseable_json_is_malformed(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=0, stdout="not json at all",
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MALFORMED


# ---------------------------------------------------------------------------
# failure classification
# ---------------------------------------------------------------------------


class TestAuthFailure:
    @pytest.mark.parametrize("status_code", [401, 403])
    def test_auth_status_codes_classified_as_auth_failure(self, status_code: int) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, status="message_unavailable",
                message_status=status_code, message_ok=False,
                marker=False, error=f"HTTP {status_code}", response_text="",
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.AUTH_FAILURE
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION

    def test_auth_failure_detail_does_not_expose_key(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, message_status=401, message_ok=False,
                marker=False, error="Unauthorized", response_text="",
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert _CANARY_KEY not in str(outcome.failure_detail)

    def test_auth_failure_with_cleanup_failed_reports_both(self) -> None:
        # Given: 401 AND cleanup failed
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, message_status=401, message_ok=False,
                marker=False, error="Unauthorized", response_text="",
                cleanup_ok=False,
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: primary fact preserved; cleanup diagnostic added
        assert outcome.fact is ValidationFact.AUTH_FAILURE
        assert not outcome.session_deleted
        assert any("not deleted" in str(d) for d in outcome.diagnostics)

    def test_auth_failure_with_cleanup_succeeded_tracks_deletion(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, message_status=401, message_ok=False,
                marker=False, error="Unauthorized", response_text="",
                cleanup_ok=True,
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.AUTH_FAILURE
        assert outcome.session_deleted
        assert outcome.diagnostics == ()


class TestTransportFailure:
    def test_server_unreachable_returns_transport_failure(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], returncode=40, stdout="{}"))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.TRANSPORT_FAILURE


class TestServerFailure:
    @pytest.mark.parametrize(
        ("exit_code", "expected_label"),
        [(41, "agent_unavailable"), (42, "session_unavailable")],
    )
    def test_agent_session_unavailable_returns_server_failure(
        self, exit_code: int, expected_label: str,
    ) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=exit_code,
            stdout=json.dumps({"status": expected_label, "exit_code": exit_code}),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.SERVER_FAILURE

    def test_basic_ready_in_message_mode_returns_server_failure(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=20,
            stdout=_diag_json(exit_code=20, status="basic_ready"),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.SERVER_FAILURE


class TestMarkerMissing:
    def test_exit_zero_without_exact_marker_returns_marker_missing(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=0,
            stdout=_diag_json(exit_code=0, marker=False, response_text="hello"),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MISSING
        assert not outcome.marker_found

    def test_exit_43_generic_message_failure_returns_marker_missing(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, message_status=500, message_ok=False,
                marker=False, error="internal error", response_text="",
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MISSING


class TestMarkerMalformed:
    def test_exit_43_unparseable_json_returns_marker_malformed(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43, stdout="this is not json {{{",
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MALFORMED

    def test_exit_43_empty_stdout_returns_marker_malformed(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], returncode=43, stdout=""))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MALFORMED

    def test_exit_43_json_without_message_probe_returns_marker_malformed(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=json.dumps({"ok": False, "status": "x"}),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.MARKER_MALFORMED


class TestTimeoutFailure:
    def test_timeout_error_classified_as_timeout_failure(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, message_status=None, message_ok=False,
                marker=False, error="message request timed out", response_text="",
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.TIMEOUT_FAILURE


class TestCleanupFailure:
    def test_marker_found_but_cleanup_failed_returns_cleanup_failure(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=0,
            stdout=_diag_json(exit_code=0, marker=True, cleanup_ok=False),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.CLEANUP_FAILURE
        assert outcome.marker_found
        assert not outcome.session_deleted

    def test_no_session_created_has_no_cleanup_diagnostic(self) -> None:
        # Given: probe failed before session creation
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE], returncode=43,
            stdout=_diag_json(
                exit_code=43, message_status=500, message_ok=False,
                marker=False, error="fail", response_text="",
                session_id="", cleanup_ok=False,
            ),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: no cleanup diagnostic (no session was created)
        assert outcome.diagnostics == ()


class TestInvalidArgument:
    def test_exit_50_returns_invalid_argument(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], returncode=50, stdout="{}"))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.INVALID_ARGUMENT


class TestUnknownExitCode:
    def test_undocumented_exit_code_returns_unknown(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], returncode=99, stdout="{}"))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert outcome.fact is ValidationFact.UNKNOWN


# ---------------------------------------------------------------------------
# argv contract
# ---------------------------------------------------------------------------


class TestArgvContract:
    def test_argv_includes_server_url_mode_message_timeout_jsononly(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        validate_opencode_messages(
            _request(server_url="http://127.0.0.1:4098", message_timeout=45),
            ports=_ports(runner=runner),
        )
        argv = runner.calls[0][0]
        assert argv[0] == _PYTHON
        assert argv[1] == _DIAGNOSE
        assert argv[argv.index("--server-url") + 1] == "http://127.0.0.1:4098"
        assert argv[argv.index("--mode") + 1] == "message"
        assert argv[argv.index("--message-timeout") + 1] == "45"
        assert "--json-only" in argv

    def test_argv_uses_bounded_message_timeout(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        validate_opencode_messages(_request(message_timeout=15), ports=_ports(runner=runner))
        argv = runner.calls[0][0]
        assert int(argv[argv.index("--message-timeout") + 1]) == 15

    def test_no_secret_in_argv(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        validate_opencode_messages(_request(), ports=_ports(runner=runner))
        argv_str = " ".join(runner.calls[0][0])
        assert _CANARY_KEY not in argv_str
        assert "api_key" not in argv_str.lower()

    def test_env_passed_to_diagnose_runner(self) -> None:
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], stdout=_diag_json()))
        validate_opencode_messages(_request(), ports=_ports(runner=runner))
        _, env = runner.calls[0]
        assert env is not None
        assert env["PATH"] == "/usr/bin"


# ---------------------------------------------------------------------------
# secret hygiene
# ---------------------------------------------------------------------------


class TestSecretHygiene:
    def test_no_secret_in_failure_detail(self) -> None:
        contaminated = _diag_json(
            exit_code=43, message_status=401, message_ok=False,
            marker=False, error=f"auth failed key={_CANARY_KEY}", response_text="",
        )
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag([_PYTHON, _DIAGNOSE], returncode=43, stdout=contaminated))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        assert _CANARY_KEY not in str(outcome.failure_detail)
        assert _CANARY_KEY not in " ".join(str(d) for d in outcome.diagnostics)
        assert _CANARY_KEY not in str(outcome)

    def test_extra_text_with_canary_rejected_not_message_ready(self) -> None:
        # Given: response has marker AND a canary — must NOT be MESSAGE_READY
        runner = _RuleRunner()
        runner.add(_is_message_mode, _diag(
            [_PYTHON, _DIAGNOSE],
            stdout=_diag_json(response_text=f"SEAM_DIAG_OK key={_CANARY_KEY}"),
        ))
        outcome = validate_opencode_messages(_request(), ports=_ports(runner=runner))
        # Then: extra text means NOT exact marker → MARKER_MISSING, not READY
        assert outcome.fact is ValidationFact.MARKER_MISSING
        assert not outcome.marker_found
        assert _CANARY_KEY not in str(outcome)


# ---------------------------------------------------------------------------
# outcome invariants
# ---------------------------------------------------------------------------


class TestOutcomeInvariants:
    def test_failure_fact_requires_failure_kind(self) -> None:
        with pytest.raises(ValueError):
            ValidationOutcome(
                fact=ValidationFact.AUTH_FAILURE, server_url=_URL, failure_kind=None,
            )

    def test_non_failure_fact_rejects_failure_kind(self) -> None:
        with pytest.raises(ValueError):
            ValidationOutcome(
                fact=ValidationFact.MESSAGE_READY, server_url=_URL,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
            )

    def test_auth_deferred_rejects_failure_kind(self) -> None:
        with pytest.raises(ValueError):
            ValidationOutcome(
                fact=ValidationFact.AUTH_DEFERRED, server_url=_URL,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
            )


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_fake_version_satisfies_version_probe(self) -> None:
        assert isinstance(_FakeVersionProbe(), VersionProbe)

    def test_fake_runtime_satisfies_runtime_port(self) -> None:
        assert isinstance(_FakeRuntime(), RuntimePort)

    def test_rule_runner_satisfies_diagnose_runner(self) -> None:
        assert isinstance(_RuleRunner(), DiagnoseRunner)


# ---------------------------------------------------------------------------
# SubprocessVersionProbe (real subprocess, safe target)
# ---------------------------------------------------------------------------


class TestSubprocessVersionProbe:
    def test_real_python_version_probe_succeeds(self) -> None:
        from seam_init.opencode_subprocess import SubprocessVersionProbe

        probe = SubprocessVersionProbe(timeout=10.0)
        result = probe.check(sys.executable)
        assert result.ok
        assert str(result.version)

    def test_nonexistent_executable_returns_failure(self) -> None:
        from seam_init.opencode_subprocess import SubprocessVersionProbe

        probe = SubprocessVersionProbe(timeout=5.0)
        result = probe.check("/nonexistent/opencode-binary")
        assert not result.ok
        assert result.error != SafeDetail("")

    def test_version_output_is_bounded(self) -> None:
        # Given: SubprocessVersionProbe against a real interpreter
        from seam_init.opencode_subprocess import SubprocessVersionProbe

        probe = SubprocessVersionProbe(timeout=10.0)
        result = probe.check(sys.executable)
        # The version field is a SafeDetail bounded by _bound_and_redact (8192 chars)
        assert len(str(result.version)) <= 8192 + 20  # bound + truncation suffix


# ---------------------------------------------------------------------------
# integration: real fake SEAM-compatible HTTP server
# ---------------------------------------------------------------------------


# Default response state — reset before every integration test
_DEFAULT_AGENT_BODY = b'{"data": [{"name": "build"}]}'
_DEFAULT_SESSION_BODY = b'{"data": {"id": "probe-session-1"}}'
_DEFAULT_MESSAGE_BODY = b'{"data": [{"type": "text", "text": "SEAM_DIAG_OK"}]}'
_DEFAULT_DELETE_BODY = b'{"ok": true}'


class _SeamFakeHandler(http.server.BaseHTTPRequestHandler):
    """Minimal SEAM/OpenCode-compatible handler for integration tests."""

    server_version = "SeamFake/1.0"
    _agent_status: int = 200
    _agent_body: bytes = _DEFAULT_AGENT_BODY
    _session_status: int = 200
    _session_body: bytes = _DEFAULT_SESSION_BODY
    _message_status: int = 200
    _message_body: bytes = _DEFAULT_MESSAGE_BODY
    _delete_status: int = 200
    _delete_body: bytes = _DEFAULT_DELETE_BODY

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        cls = type(self)
        if path == "/agent":
            self._send(cls._agent_status, cls._agent_body)
        elif path == "/session/status":
            self._send(200, b'{"status": "idle"}')
        elif "/message" in path and "/session/" in path:
            self._send(200, b'{"data": []}')
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        cls = type(self)
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length) if length else b""
        if path == "/session":
            self._send(cls._session_status, cls._session_body)
        elif "/message" in path and "/session/" in path:
            self._send(cls._message_status, cls._message_body)
        else:
            self._send(404, b'{"error": "not found"}')

    def do_DELETE(self) -> None:
        cls = type(self)
        self._send(cls._delete_status, cls._delete_body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _reset_handler_defaults() -> None:
    """Reset class-level mutable state to prevent inter-test leakage."""
    cls = _SeamFakeHandler
    cls._agent_status = 200
    cls._agent_body = _DEFAULT_AGENT_BODY
    cls._session_status = 200
    cls._session_body = _DEFAULT_SESSION_BODY
    cls._message_status = 200
    cls._message_body = _DEFAULT_MESSAGE_BODY
    cls._delete_status = 200
    cls._delete_body = _DEFAULT_DELETE_BODY


class _FakeServer:
    """Start/stop a ThreadingHTTPServer with configurable responses."""

    def __init__(self) -> None:
        _reset_handler_defaults()
        self.port = _free_port()
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self.port), _SeamFakeHandler,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)
        _reset_handler_defaults()

    def configure(
        self, *,
        message_status: int = 200,
        message_body: bytes | None = None,
        delete_status: int = 200,
    ) -> None:
        cls = _SeamFakeHandler
        cls._message_status = message_status
        if message_body is not None:
            cls._message_body = message_body
        cls._delete_status = delete_status


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIAGNOSE_SCRIPT = _REPO_ROOT / "scripts" / "diagnose_seam_opencode.py"


def _integration_request(server_url: str, *, message_timeout: int = 15) -> ValidationRequest:
    return ValidationRequest(
        server_url=server_url,
        provider_model="openai/gpt-4",
        auth_state=AuthState.PROVIDED,
        billable_consent=BillableCallConsent.GIVEN,
        opencode_executable=_EXE,
        diagnose_argv_prefix=(sys.executable, str(_DIAGNOSE_SCRIPT)),
        message_timeout=message_timeout,
        base_env=dict(os.environ),
    )


@pytest.mark.skipif(
    not _DIAGNOSE_SCRIPT.is_file(),
    reason="diagnose_seam_opencode.py not found",
)
class TestIntegrationFakeServer:
    """Integration tests using the real diagnose script against a fake server."""

    def test_full_message_lifecycle_returns_ready(self) -> None:
        # Given: fake server with exact marker response
        server = _FakeServer()
        server.configure(
            message_body=b'{"data": [{"type": "text", "text": "SEAM_DIAG_OK"}]}',
        )
        server.start()
        try:
            from seam_init.opencode_subprocess import SubprocessDiagnoseRunner

            runner = SubprocessDiagnoseRunner(timeout=30.0)
            ports = ValidationPorts(
                version_probe=_FakeVersionProbe(),
                runtime=_FakeRuntime(), diagnose_runner=runner,
            )
            request = _integration_request(server.url)
            # When
            outcome = validate_opencode_messages(request, ports=ports)
            # Then: MESSAGE_READY with marker and deletion
            assert outcome.fact is ValidationFact.MESSAGE_READY
            assert outcome.marker_found
            assert outcome.session_deleted
        finally:
            server.stop()

    def test_unreachable_server_returns_transport_failure(self) -> None:
        # Given: a port with no server listening
        dead_port = _free_port()
        dead_url = f"http://127.0.0.1:{dead_port}"
        from seam_init.opencode_subprocess import SubprocessDiagnoseRunner

        runner = SubprocessDiagnoseRunner(timeout=15.0)
        ports = ValidationPorts(
            version_probe=_FakeVersionProbe(),
            runtime=_FakeRuntime(), diagnose_runner=runner,
        )
        request = _integration_request(dead_url, message_timeout=10)
        outcome = validate_opencode_messages(request, ports=ports)
        assert outcome.fact is ValidationFact.TRANSPORT_FAILURE

    def test_missing_marker_returns_strict_marker_missing(self) -> None:
        # Given: fake server returns a message WITHOUT the marker
        server = _FakeServer()
        server.configure(
            message_body=b'{"data": [{"type": "text", "text": "wrong answer"}]}',
        )
        server.start()
        try:
            from seam_init.opencode_subprocess import SubprocessDiagnoseRunner

            runner = SubprocessDiagnoseRunner(timeout=30.0)
            ports = ValidationPorts(
                version_probe=_FakeVersionProbe(),
                runtime=_FakeRuntime(), diagnose_runner=runner,
            )
            request = _integration_request(server.url)
            outcome = validate_opencode_messages(request, ports=ports)
            # Then: STRICTLY MARKER_MISSING (not the permissive tuple)
            assert outcome.fact is ValidationFact.MARKER_MISSING
            assert not outcome.marker_found
        finally:
            server.stop()

    def test_marker_with_extra_text_rejected_strictly(self) -> None:
        # Given: server returns "SEAM_DIAG_OK plus extra" — NOT exact
        server = _FakeServer()
        server.configure(
            message_body=b'{"data": [{"type": "text", "text": "SEAM_DIAG_OK plus extra"}]}',
        )
        server.start()
        try:
            from seam_init.opencode_subprocess import SubprocessDiagnoseRunner

            runner = SubprocessDiagnoseRunner(timeout=30.0)
            ports = ValidationPorts(
                version_probe=_FakeVersionProbe(),
                runtime=_FakeRuntime(), diagnose_runner=runner,
            )
            request = _integration_request(server.url)
            outcome = validate_opencode_messages(request, ports=ports)
            # Then: NOT MESSAGE_READY — exact match required
            assert outcome.fact is ValidationFact.MARKER_MISSING
            assert not outcome.marker_found
        finally:
            server.stop()

    def test_auth_failure_returns_auth_failure(self) -> None:
        server = _FakeServer()
        server.configure(message_status=401, message_body=b'{"error": "Unauthorized"}')
        server.start()
        try:
            from seam_init.opencode_subprocess import SubprocessDiagnoseRunner

            runner = SubprocessDiagnoseRunner(timeout=30.0)
            ports = ValidationPorts(
                version_probe=_FakeVersionProbe(),
                runtime=_FakeRuntime(), diagnose_runner=runner,
            )
            request = _integration_request(server.url)
            outcome = validate_opencode_messages(request, ports=ports)
            assert outcome.fact is ValidationFact.AUTH_FAILURE
        finally:
            server.stop()
