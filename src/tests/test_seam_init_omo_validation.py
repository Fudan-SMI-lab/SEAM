"""Tests for OMO doctor + consented real-run validation.

Every command is routed through the OmoCommandPort boundary; no real bunx/bun,
no real OMO, no real provider, no real network. Integration tests use a local
fake Python "executable" as the bunx shim, exercising the REAL
SubprocessOmoCommandPort without billable calls. Each test uses an explicit
Given/When/Then block.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Mapping, Sequence
from typing import final

import pytest

from seam_init.models import (
    AuthState,
    BillableCallConsent,
    FailureKind,
    InitializerContractError,
    SafeDetail,
)
from seam_init.omo_validation import (
    OmoCommandPort,
    OmoCommandResult,
    OmoValidationFact,
    OmoValidationOutcome,
    OmoValidationPorts,
    OmoValidationRequest,
    RUN_MESSAGE,
    SEAM_OMO_OK_MARKER,
    validate_omo_runtime,
)

# ---------------------------------------------------------------------------
# constants and helpers
# ---------------------------------------------------------------------------

_PREFIX: tuple[str, ...] = ("bunx", "oh-my-openagent")
_CANARY_KEY = "sk-canary-secret-key-1234567890"


def _request(
    *,
    auth_state: AuthState = AuthState.PROVIDED,
    consent: BillableCallConsent = BillableCallConsent.GIVEN,
    doctor_prefix: Sequence[str] = _PREFIX,
    run_prefix: Sequence[str] = _PREFIX,
    doctor_timeout: float = 60.0,
    run_timeout: float = 120.0,
    base_env: Mapping[str, str] | None = None,
) -> OmoValidationRequest:
    return OmoValidationRequest(
        auth_state=auth_state,
        billable_consent=consent,
        doctor_argv_prefix=tuple(doctor_prefix),
        run_argv_prefix=tuple(run_prefix),
        doctor_timeout_seconds=doctor_timeout,
        run_timeout_seconds=run_timeout,
        base_env=base_env or {},
    )


# ---------------------------------------------------------------------------
# fake command port
# ---------------------------------------------------------------------------


@final
class _RuleCommand:
    """OmoCommandPort double: matches argv by callable, records all calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None, float]] = []
        self._rules: list[tuple[Callable[[list[str]], bool], OmoCommandResult]] = []

    def add(self, match: Callable[[list[str]], bool], result: OmoCommandResult) -> None:
        self._rules.append((match, result))

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> OmoCommandResult:
        argv_list = list(argv)
        env_dict: dict[str, str] | None = dict(env) if env is not None else None
        self.calls.append((argv_list, env_dict, timeout))
        for match, result in self._rules:
            if match(argv_list):
                return result
        raise AssertionError(f"no rule matched argv: {argv_list}")


def _is_doctor(argv: list[str]) -> bool:
    return "doctor" in argv and "--platform" in argv


def _is_run(argv: list[str]) -> bool:
    return "run" in argv and "--json" in argv


def _cmd(
    argv: Sequence[str], *, returncode: int = 0, stdout: str = "",
    stderr: str = "", timed_out: bool = False,
    stdout_truncated: bool = False, cleanup_failed: bool = False,
) -> OmoCommandResult:
    return OmoCommandResult(
        argv=tuple(argv), returncode=returncode, stdout=stdout,
        stderr=SafeDetail(stderr), timed_out=timed_out,
        stdout_truncated=stdout_truncated, cleanup_failed=cleanup_failed,
    )


def _ports(command: OmoCommandPort | None = None) -> OmoValidationPorts:
    return OmoValidationPorts(command=command or _RuleCommand())


# ---------------------------------------------------------------------------
# doctor and run JSON builders — target is ROOT-level per official contract
# ---------------------------------------------------------------------------


def _doctor_json(
    *,
    exit_code: int = 0,
    target: str = "opencode",
    config_valid: bool = True,
    config_path: str = "/home/user/.opencode/oh-my-openagent.jsonc",
    results: Sequence[Mapping[str, object]] | None = None,
) -> str:
    if results is None:
        results = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
        ]
    doc: dict[str, object] = {
        "exitCode": exit_code,
        "target": target,
        "systemInfo": {
            "configValid": config_valid,
            "configPath": config_path,
            "pluginVersion": "5.0.0",
        },
        "results": list(results),
    }
    return json.dumps(doc, indent=2)


def _run_json(
    *,
    success: bool = True,
    session_id: str = "sess-abc-123",
    message_count: int = 2,
    duration_ms: int = 500,
    summary: str = SEAM_OMO_OK_MARKER,
) -> str:
    return json.dumps({
        "success": success,
        "sessionId": session_id,
        "messageCount": message_count,
        "durationMs": duration_ms,
        "summary": summary,
    })


def _doctor_result(
    argv: Sequence[str], *,
    exit_code: int = 0,
    target: str = "opencode",
    config_valid: bool = True,
    config_path: str = "/home/user/.opencode/oh-my-openagent.jsonc",
    results: Sequence[Mapping[str, object]] | None = None,
) -> OmoCommandResult:
    return _cmd(argv, stdout=_doctor_json(
        exit_code=exit_code, target=target, config_valid=config_valid,
        config_path=config_path, results=results,
    ))


def _run_result(
    argv: Sequence[str], *,
    success: bool = True,
    session_id: str = "sess-abc-123",
    message_count: int = 2,
    duration_ms: int = 500,
    summary: str = SEAM_OMO_OK_MARKER,
) -> OmoCommandResult:
    return _cmd(argv, stdout=_run_json(
        success=success, session_id=session_id, message_count=message_count,
        duration_ms=duration_ms, summary=summary,
    ))


# ---------------------------------------------------------------------------
# timeout invariant — FIX 4: [35,600] doctor, [1,600] run, reject inf/NaN
# ---------------------------------------------------------------------------


class TestTimeoutInvariant:
    def test_doctor_timeout_below_minimum_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(doctor_timeout=30.0)

    def test_doctor_timeout_at_minimum_accepted(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(
            _request(doctor_timeout=35.0), ports=_ports(cmd),
        )
        assert outcome.fact is OmoValidationFact.VALIDATED

    def test_doctor_timeout_at_maximum_accepted(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(
            _request(doctor_timeout=600.0), ports=_ports(cmd),
        )
        assert outcome.fact is OmoValidationFact.VALIDATED

    def test_doctor_timeout_above_maximum_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(doctor_timeout=600.1)

    def test_doctor_timeout_infinity_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(doctor_timeout=float("inf"))

    def test_doctor_timeout_nan_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(doctor_timeout=float("nan"))

    def test_run_timeout_zero_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(run_timeout=0.0)

    def test_run_timeout_negative_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(run_timeout=-5.0)

    def test_run_timeout_above_maximum_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(run_timeout=600.1)

    def test_run_timeout_infinity_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(run_timeout=float("inf"))

    def test_run_timeout_nan_rejected(self) -> None:
        with pytest.raises(InitializerContractError):
            _request(run_timeout=float("nan"))


# ---------------------------------------------------------------------------
# FIX 1 regression: official target location
# ---------------------------------------------------------------------------


class TestFix1OfficialTargetLocation:
    def test_official_doctor_shape_with_root_target_accepted(self) -> None:
        # Given: official TS DoctorResult shape — target at ROOT level,
        # tools is an object, summary is a DoctorSummary-like object
        official = json.dumps({
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
            ],
            "systemInfo": {
                "configValid": True,
                "configPath": "/home/user/.opencode/oh-my-openagent.jsonc",
                "pluginVersion": "5.0.0",
            },
            "tools": {"available": ["read", "edit", "grep"], "enabled": ["read", "edit"]},
            "summary": {"total": 4, "passed": 4, "failed": 0, "warnings": 0},
            "exitCode": 0,
            "target": "opencode",
        }, indent=2)
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=official))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        # When
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then
        assert outcome.fact is OmoValidationFact.VALIDATED

    def test_target_only_under_systeminfo_fails_closed(self) -> None:
        # Given: OLD incorrect shape — target ONLY under systemInfo, NOT at root
        old_shape = json.dumps({
            "exitCode": 0,
            "systemInfo": {
                "target": "opencode",
                "configValid": True,
                "configPath": "/x",
                "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
            ],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=old_shape))
        # When
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then: root target missing → DOCTOR_CONFIG_INVALID
        assert outcome.fact is OmoValidationFact.DOCTOR_CONFIG_INVALID

    def test_root_target_wrong_value_fails_closed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), target="cursor"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_CONFIG_INVALID


# ---------------------------------------------------------------------------
# FIX 2 regression: required verification
# ---------------------------------------------------------------------------


class TestFix2RequiredVerification:
    def test_missing_system_check_returns_missing_check(self) -> None:
        # Given: System check absent from results
        results = [
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MISSING_CHECK

    def test_required_check_skip_returns_missing_check(self) -> None:
        # Given: Configuration (required) has status skip — does NOT verify readiness
        results = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "skip", "message": "cfg skipped"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MISSING_CHECK

    def test_required_system_skip_returns_missing_check(self) -> None:
        results = [
            {"name": "System", "status": "skip", "message": "sys skipped"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MISSING_CHECK

    def test_optional_check_skip_remains_nonfatal(self) -> None:
        # Given: an OPTIONAL check skips — required checks all pass
        results = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
            {"name": "Optional Telemetry", "status": "skip", "message": "opt skip"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert any("opt skip" in str(d) for d in outcome.diagnostics)

    def test_non_object_result_item_returns_malformed(self) -> None:
        # Given: results contains a non-dict item — build JSON inline
        doc = json.dumps({
            "exitCode": 0, "target": "opencode",
            "systemInfo": {
                "configValid": True,
                "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
                "not-a-dict-item",
            ],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED


# ---------------------------------------------------------------------------
# FIX 3 regression: structured stdout not truncated/redacted at diagnostic cap
# ---------------------------------------------------------------------------


class TestFix3StructuredStdout:
    def test_large_doctor_json_over_8192_bytes_parses(self) -> None:
        # Given: valid doctor JSON >8192 bytes (padding in summary field)
        padding = "x" * 10000
        doc = json.dumps({
            "exitCode": 0,
            "target": "opencode",
            "systemInfo": {
                "configValid": True,
                "configPath": "/cfg",
                "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
            ],
            "summary": padding,
        }, indent=2)
        assert len(doc) > 8192
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        # When
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then: parses correctly despite exceeding the old 8192 diagnostic cap
        assert outcome.fact is OmoValidationFact.VALIDATED

    def test_canary_in_structured_warning_redacted_before_diagnostics(self) -> None:
        # Given: doctor JSON warning contains a canary in raw structured stdout
        results: list[Mapping[str, object]] = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "warn",
             "message": f"advisory key={_CANARY_KEY}"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        # When
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then: validated (warnings nonfatal); canary NEVER in outcome string
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert _CANARY_KEY not in str(outcome)
        assert _CANARY_KEY not in str(outcome.failure_detail)
        for diag in outcome.diagnostics:
            assert _CANARY_KEY not in str(diag)


# ---------------------------------------------------------------------------
# FIX 6 regression: outcome invariant
# ---------------------------------------------------------------------------


class TestFix6OutcomeInvariant:
    def test_failure_fact_rejects_wrong_failure_kind(self) -> None:
        # Given / When / Then: a failure fact with a non-OMO_VALIDATION kind
        with pytest.raises(ValueError):
            OmoValidationOutcome(
                fact=OmoValidationFact.DOCTOR_FAILURE,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
            )

    def test_failure_fact_rejects_none_kind(self) -> None:
        with pytest.raises(ValueError):
            OmoValidationOutcome(
                fact=OmoValidationFact.RUN_TIMEOUT, failure_kind=None,
            )

    def test_all_failure_facts_require_exactly_omo_validation(self) -> None:
        non_failure = {OmoValidationFact.VALIDATED, OmoValidationFact.AUTH_DEFERRED}
        for fact in OmoValidationFact:
            if fact in non_failure:
                continue
            outcome = OmoValidationOutcome(
                fact=fact, failure_kind=FailureKind.OMO_VALIDATION,
            )
            assert outcome.failure_kind is FailureKind.OMO_VALIDATION
            with pytest.raises(ValueError):
                OmoValidationOutcome(
                    fact=fact, failure_kind=FailureKind.SEAM_INSTALL,
                )


# ---------------------------------------------------------------------------
# doctor: pass / warn / skip
# ---------------------------------------------------------------------------


class TestDoctorHappyPath:
    def test_doctor_all_pass_proceeds_to_run(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert outcome.ok
        assert outcome.failure_kind is None

    def test_doctor_warn_proceeds_with_diagnostics(self) -> None:
        results: list[Mapping[str, object]] = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "warn", "message": "optional plugin"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert any("optional plugin" in str(d) for d in outcome.diagnostics)

    def test_doctor_optional_skip_proceeds_with_diagnostics(self) -> None:
        results: list[Mapping[str, object]] = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
            {"name": "Optional Audit", "status": "skip", "message": "audit skipped"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert any("audit skipped" in str(d) for d in outcome.diagnostics)


# ---------------------------------------------------------------------------
# doctor: failure classification
# ---------------------------------------------------------------------------


class TestDoctorFailure:
    def test_doctor_exit_nonzero_returns_doctor_failure(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), returncode=1, stdout=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_FAILURE
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION

    def test_doctor_check_status_fail_returns_doctor_failure(self) -> None:
        results: list[Mapping[str, object]] = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "fail", "message": "plugin missing"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_FAILURE

    def test_doctor_exit_code_field_nonzero_returns_doctor_failure(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), exit_code=1))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_FAILURE

    def test_doctor_fail_makes_zero_run_calls(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), returncode=1))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        run_calls = [c for c in cmd.calls if _is_run(c[0])]
        assert len(run_calls) == 0


class TestDoctorMalformed:
    def test_doctor_unparseable_json_returns_doctor_malformed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout="not json {{{"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_doctor_empty_stdout_returns_doctor_malformed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_doctor_non_dict_root_returns_doctor_malformed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout="[1, 2, 3]"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_doctor_results_not_list_returns_doctor_malformed(self) -> None:
        doc = json.dumps({
            "exitCode": 0, "target": "opencode",
            "systemInfo": {
                "configValid": True,
                "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": "not-a-list",
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_doctor_unknown_status_returns_doctor_malformed(self) -> None:
        results: list[Mapping[str, object]] = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "bogus-status"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED


class TestDoctorMissingCheck:
    def test_missing_configuration_returns_missing_check(self) -> None:
        results = [
            {"name": "System", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MISSING_CHECK

    def test_missing_tui_plugin_returns_missing_check(self) -> None:
        results = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MISSING_CHECK

    def test_missing_models_returns_missing_check(self) -> None:
        results = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MISSING_CHECK

    def test_all_four_required_checks_present_passes(self) -> None:
        results = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "pass"},
            {"name": "Models", "status": "pass"},
            {"name": "Extra Optional", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED


class TestDoctorConfigInvalid:
    def test_missing_root_target_returns_config_invalid(self) -> None:
        doc = json.dumps({
            "exitCode": 0,
            "systemInfo": {
                "configValid": True,
                "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": [],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_CONFIG_INVALID

    def test_config_valid_false_returns_config_invalid(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), config_valid=False))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_CONFIG_INVALID

    def test_empty_config_path_returns_config_invalid(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), config_path=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_CONFIG_INVALID

    def test_missing_system_info_returns_config_invalid(self) -> None:
        doc = json.dumps({"exitCode": 0, "target": "opencode", "results": []})
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_CONFIG_INVALID


class TestDoctorTimeout:
    def test_doctor_timeout_returns_doctor_timeout(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), timed_out=True))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_TIMEOUT
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION


# ---------------------------------------------------------------------------
# auth / consent deferral
# ---------------------------------------------------------------------------


class TestAuthDeferred:
    def test_skipped_auth_returns_auth_deferred(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        outcome = validate_omo_runtime(
            _request(auth_state=AuthState.SKIPPED), ports=_ports(cmd),
        )
        assert outcome.fact is OmoValidationFact.AUTH_DEFERRED
        assert outcome.is_deferred
        assert outcome.failure_kind is None
        assert not outcome.ok

    def test_skipped_auth_makes_zero_run_calls(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        validate_omo_runtime(
            _request(auth_state=AuthState.SKIPPED), ports=_ports(cmd),
        )
        run_calls = [c for c in cmd.calls if _is_run(c[0])]
        assert len(run_calls) == 0

    def test_declined_consent_returns_auth_deferred(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        outcome = validate_omo_runtime(
            _request(consent=BillableCallConsent.DECLINED), ports=_ports(cmd),
        )
        assert outcome.fact is OmoValidationFact.AUTH_DEFERRED

    def test_declined_consent_makes_zero_run_calls(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        validate_omo_runtime(
            _request(consent=BillableCallConsent.DECLINED), ports=_ports(cmd),
        )
        run_calls = [c for c in cmd.calls if _is_run(c[0])]
        assert len(run_calls) == 0

    def test_deferred_carries_doctor_diagnostics(self) -> None:
        results: list[Mapping[str, object]] = [
            {"name": "System", "status": "pass"},
            {"name": "Configuration", "status": "pass"},
            {"name": "TUI Plugin", "status": "warn", "message": "advisory"},
            {"name": "Models", "status": "pass"},
        ]
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX), results=results))
        outcome = validate_omo_runtime(
            _request(auth_state=AuthState.SKIPPED), ports=_ports(cmd),
        )
        assert outcome.fact is OmoValidationFact.AUTH_DEFERRED
        assert any("advisory" in str(d) for d in outcome.diagnostics)


# ---------------------------------------------------------------------------
# run: success and failure classification
# ---------------------------------------------------------------------------


class TestRunSuccess:
    def test_full_happy_path_returns_validated(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert outcome.ok
        assert outcome.failure_kind is None

    def test_exactly_one_doctor_and_one_run_call(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        doctor_calls = [c for c in cmd.calls if _is_doctor(c[0])]
        run_calls = [c for c in cmd.calls if _is_run(c[0])]
        assert len(doctor_calls) == 1
        assert len(run_calls) == 1

    def test_summary_with_surrounding_whitespace_accepted(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), summary="  SEAM_OMO_OK  "))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.VALIDATED


class TestRunNonzero:
    def test_run_exit_nonzero_returns_run_failure(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), returncode=1, stdout=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FAILURE
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION


class TestRunMalformed:
    def test_run_unparseable_json_returns_run_malformed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), stdout="not json"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MALFORMED

    def test_run_empty_stdout_returns_run_malformed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), stdout=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MALFORMED

    def test_run_non_dict_root_returns_run_malformed(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), stdout="[1, 2]"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MALFORMED


class TestRunFalseSuccess:
    def test_success_false_returns_run_false_success(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), success=False))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FALSE_SUCCESS

    def test_success_missing_returns_run_false_success(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        stdout = json.dumps({"sessionId": "s1", "messageCount": 1, "durationMs": 10, "summary": "x"})
        cmd.add(_is_run, _cmd(list(_PREFIX), stdout=stdout))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FALSE_SUCCESS


class TestRunMarkerMissing:
    def test_wrong_summary_returns_marker_missing(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), summary="wrong answer"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MARKER_MISSING

    def test_extra_text_after_marker_returns_marker_missing(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), summary="SEAM_OMO_OK extra"))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MARKER_MISSING

    def test_empty_summary_returns_marker_missing(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), summary=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MARKER_MISSING


class TestRunFieldInvalid:
    def test_empty_session_id_returns_field_invalid(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), session_id=""))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FIELD_INVALID

    def test_zero_message_count_returns_field_invalid(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), message_count=0))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FIELD_INVALID

    def test_negative_duration_returns_field_invalid(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX), duration_ms=-1))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FIELD_INVALID

    def test_non_string_summary_returns_field_invalid(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        stdout = json.dumps({
            "success": True, "sessionId": "s1", "messageCount": 1,
            "durationMs": 10, "summary": 42,
        })
        cmd.add(_is_run, _cmd(list(_PREFIX), stdout=stdout))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FIELD_INVALID


class TestRunTimeout:
    def test_run_timeout_returns_run_timeout(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), timed_out=True))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_TIMEOUT


# ---------------------------------------------------------------------------
# argv contract
# ---------------------------------------------------------------------------


class TestArgvContract:
    def test_doctor_argv_exact_shape(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        doctor_argv = cmd.calls[0][0]
        assert doctor_argv == [
            "bunx", "oh-my-openagent", "doctor", "--json", "--platform", "opencode",
        ]

    def test_run_argv_exact_shape(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        run_argv = cmd.calls[1][0]
        assert run_argv == [
            "bunx", "oh-my-openagent", "run", RUN_MESSAGE, "--json",
        ]

    def test_run_argv_has_no_platform_flag(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        assert "--platform" not in cmd.calls[1][0]

    def test_run_argv_has_no_json_stream_flag(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        assert "--json-stream" not in cmd.calls[1][0]

    def test_custom_prefix_propagated_to_both_commands(self) -> None:
        prefix = ("/opt/bun/bin/bunx", "oh-my-openagent")
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(prefix)))
        cmd.add(_is_run, _run_result(list(prefix)))
        validate_omo_runtime(
            _request(doctor_prefix=prefix, run_prefix=prefix), ports=_ports(cmd),
        )
        assert cmd.calls[0][0][:2] == list(prefix)
        assert cmd.calls[1][0][:2] == list(prefix)

    def test_no_secret_in_argv(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(), ports=_ports(cmd))
        for argv, _, _ in cmd.calls:
            assert _CANARY_KEY not in " ".join(argv)
            assert "api_key" not in " ".join(argv).lower()


# ---------------------------------------------------------------------------
# env contract
# ---------------------------------------------------------------------------


class TestEnvContract:
    def test_empty_base_env_passes_none_to_command(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(_request(base_env={}), ports=_ports(cmd))
        for _, env, _ in cmd.calls:
            assert env is None

    def test_explicit_base_env_passed_to_command(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(
            _request(base_env={"PATH": "/usr/bin"}), ports=_ports(cmd),
        )
        for _, env, _ in cmd.calls:
            assert env is not None
            assert env["PATH"] == "/usr/bin"

    def test_timeout_passed_to_command(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(list(_PREFIX)))
        validate_omo_runtime(
            _request(doctor_timeout=45.0, run_timeout=90.0), ports=_ports(cmd),
        )
        assert cmd.calls[0][2] == 45.0
        assert cmd.calls[1][2] == 90.0


# ---------------------------------------------------------------------------
# secret hygiene
# ---------------------------------------------------------------------------


class TestSecretHygiene:
    def test_canary_in_doctor_stderr_redacted_from_detail(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(
            list(_PREFIX), returncode=1,
            stderr=f"error with key={_CANARY_KEY}",
        ))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_FAILURE
        assert _CANARY_KEY not in str(outcome.failure_detail)
        assert _CANARY_KEY not in str(outcome)

    def test_canary_in_run_stderr_redacted_from_detail(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(
            list(_PREFIX), returncode=1,
            stderr=f"crash with secret={_CANARY_KEY}",
        ))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_FAILURE
        assert _CANARY_KEY not in str(outcome.failure_detail)

    def test_extra_text_with_canary_rejected_not_validated(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _run_result(
            list(_PREFIX), summary=f"SEAM_OMO_OK key={_CANARY_KEY}",
        ))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_MARKER_MISSING
        assert _CANARY_KEY not in str(outcome)


# ---------------------------------------------------------------------------
# R3 FIX 1 regression: stdout cap overflow signaled explicitly
# ---------------------------------------------------------------------------


class TestFix3StdoutOverflow:
    def test_doctor_stdout_truncated_fails_malformed(self) -> None:
        # Given: doctor result with stdout_truncated=True
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout_truncated=True))
        # When
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then: DOCTOR_MALFORMED, not accidentally parsed
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION

    def test_run_stdout_truncated_fails_malformed(self) -> None:
        # Given: doctor ok, run result with stdout_truncated=True
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), stdout_truncated=True))
        # When
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then
        assert outcome.fact is OmoValidationFact.RUN_MALFORMED

    def test_stdout_truncated_detail_does_not_expose_raw_stdout(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(
            list(_PREFIX), stdout_truncated=True,
            stdout=f"secret={_CANARY_KEY}",
        ))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED
        assert _CANARY_KEY not in str(outcome)


# ---------------------------------------------------------------------------
# R3 FIX 2 regression: timeout cleanup failure reported explicitly
# ---------------------------------------------------------------------------


class TestFix3CleanupFailure:
    def test_doctor_timeout_cleanup_failed_surfaces_in_detail(self) -> None:
        # Given: doctor timed out with cleanup_failed=True
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), timed_out=True, cleanup_failed=True))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        # Then: DOCTOR_TIMEOUT with cleanup info in detail
        assert outcome.fact is OmoValidationFact.DOCTOR_TIMEOUT
        assert "cleanup incomplete" in str(outcome.failure_detail)

    def test_run_timeout_cleanup_failed_surfaces_in_detail(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _doctor_result(list(_PREFIX)))
        cmd.add(_is_run, _cmd(list(_PREFIX), timed_out=True, cleanup_failed=True))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.RUN_TIMEOUT
        assert "cleanup incomplete" in str(outcome.failure_detail)

    def test_timeout_without_cleanup_failure_has_clean_detail(self) -> None:
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), timed_out=True, cleanup_failed=False))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_TIMEOUT
        assert "cleanup incomplete" not in str(outcome.failure_detail)


# ---------------------------------------------------------------------------
# R3 FIX 3 regression: result item name must be nonempty string
# ---------------------------------------------------------------------------


class TestFix3ResultItemName:
    def test_missing_name_returns_malformed(self) -> None:
        # Given: result item with no name field at all
        doc = json.dumps({
            "exitCode": 0, "target": "opencode",
            "systemInfo": {
                "configValid": True, "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
                {"status": "pass"},
            ],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_empty_name_returns_malformed(self) -> None:
        doc = json.dumps({
            "exitCode": 0, "target": "opencode",
            "systemInfo": {
                "configValid": True, "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
                {"name": "", "status": "pass"},
            ],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_non_string_name_returns_malformed(self) -> None:
        doc = json.dumps({
            "exitCode": 0, "target": "opencode",
            "systemInfo": {
                "configValid": True, "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
                {"name": 42, "status": "pass"},
            ],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED

    def test_whitespace_only_name_returns_malformed(self) -> None:
        doc = json.dumps({
            "exitCode": 0, "target": "opencode",
            "systemInfo": {
                "configValid": True, "configPath": "/x", "pluginVersion": "5.0.0",
            },
            "results": [
                {"name": "System", "status": "pass"},
                {"name": "Configuration", "status": "pass"},
                {"name": "TUI Plugin", "status": "pass"},
                {"name": "Models", "status": "pass"},
                {"name": "   ", "status": "pass"},
            ],
        })
        cmd = _RuleCommand()
        cmd.add(_is_doctor, _cmd(list(_PREFIX), stdout=doc))
        outcome = validate_omo_runtime(_request(), ports=_ports(cmd))
        assert outcome.fact is OmoValidationFact.DOCTOR_MALFORMED


# ---------------------------------------------------------------------------
# R4 repr secrecy: raw stdout and base_env cannot leak through default repr
# ---------------------------------------------------------------------------


class TestFix4ReprSecrecy:
    def test_command_result_repr_excludes_raw_stdout(self) -> None:
        # Given: result with canary in raw stdout
        result = OmoCommandResult(
            argv=tuple(_PREFIX), returncode=0,
            stdout=f'{{"secret": "{_CANARY_KEY}"}}',
            stderr=SafeDetail(""),
        )
        # Then: repr and str do NOT contain the canary
        assert _CANARY_KEY not in repr(result)
        assert _CANARY_KEY not in str(result)
        # But direct field access still works for parsing
        assert _CANARY_KEY in result.stdout

    def test_request_repr_excludes_base_env(self) -> None:
        # Given: request with canary in base_env
        request = OmoValidationRequest(
            auth_state=AuthState.PROVIDED,
            billable_consent=BillableCallConsent.GIVEN,
            doctor_argv_prefix=_PREFIX,
            run_argv_prefix=_PREFIX,
            base_env={"API_KEY": _CANARY_KEY},
        )
        # Then: repr and str do NOT contain the canary
        assert _CANARY_KEY not in repr(request)
        assert _CANARY_KEY not in str(request)
        # But direct field access still works for subprocess execution
        assert request.base_env["API_KEY"] == _CANARY_KEY


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_rule_command_satisfies_omo_command_port(self) -> None:
        assert isinstance(_RuleCommand(), OmoCommandPort)


# ---------------------------------------------------------------------------
# SubprocessOmoCommandPort integration with real Python children
# ---------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "")}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _is_pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class TestSubprocessOmoCommandPort:
    def test_real_success_returns_parsed_output(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "print('hello world')"],
            env=_child_env(), timeout=15.0,
        )
        assert result.returncode == 0
        assert "hello world" in result.stdout
        assert not result.timed_out

    def test_nonzero_exit_captured(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            env=_child_env(), timeout=10.0,
        )
        assert result.returncode == 3
        assert not result.timed_out

    def test_timeout_kills_process_and_returns_typed(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env=_child_env(), timeout=2.0,
        )
        assert result.timed_out is True
        assert result.returncode != 0

    def test_timeout_direct_process_is_dead(self, tmp_path) -> None:
        # FIX 5: the direct process must be verified dead, not merely timed_out=True
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        pid_file = tmp_path / "child_pid.txt"
        script = tmp_path / "hang_with_pid.py"
        script.write_text(textwrap.dedent("""\
            import os, time, sys
            with open(sys.argv[1], 'w') as f:
                f.write(str(os.getpid()))
            time.sleep(120)
        """))
        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, str(script), str(pid_file)],
            env=_child_env(), timeout=2.0,
        )
        assert result.timed_out
        assert pid_file.exists()
        pid = int(pid_file.read_text().strip())
        # Poll for up to 5 seconds for the process to be confirmed dead
        for _ in range(50):
            if not _is_pid_alive(pid):
                break
            time.sleep(0.1)
        assert not _is_pid_alive(pid), f"process pid={pid} still alive after timeout kill"

    def test_stdout_raw_not_truncated_at_diagnostic_cap(self) -> None:
        # FIX 3: stdout is raw structured data; 20KB is under 1MB cap
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('X' * 20000)"],
            env=_child_env(), timeout=15.0,
        )
        assert len(result.stdout) == 20000
        assert "[truncated]" not in result.stdout

    def test_stderr_bounded_and_redacted(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('Y' * 20000)"],
            env=_child_env(), timeout=15.0,
        )
        assert len(str(result.stderr)) <= 8192 + 20
        assert "[truncated]" in str(result.stderr)

    def test_stderr_secret_pattern_redacted(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('api_key=sk-secret-1234567890abcdef')"],
            env=_child_env(), timeout=10.0,
        )
        assert "sk-secret-1234567890abcdef" not in str(result.stderr)

    def test_none_env_inherits_real_environment(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import os; print(os.environ.get('PATH', ''))"],
            env=None, timeout=15.0,
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""

    def test_stdout_over_1mib_sets_truncated_flag(self) -> None:
        # R3 FIX 1: stdout exceeding 1 MiB cap signals stdout_truncated=True
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort, _MAX_STDOUT

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", f"import sys; sys.stdout.write('X' * {_MAX_STDOUT + 100})"],
            env=_child_env(), timeout=15.0,
        )
        assert result.stdout_truncated is True
        assert len(result.stdout) == _MAX_STDOUT

    def test_stdout_under_1mib_no_truncation_flag(self) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('X' * 100000)"],
            env=_child_env(), timeout=15.0,
        )
        assert result.stdout_truncated is False

    def test_ensure_dead_returns_false_for_unkillable_process(self) -> None:
        # R3 FIX 2: deterministic fake-Popen seam — all kill/communicate fail
        from seam_init.omo_validation_subprocess import _ensure_dead

        fake = _UnkillableFakeProcess()
        assert _ensure_dead(fake) is False

    def test_ensure_dead_returns_false_when_communicate_ok_but_poll_alive(self) -> None:
        # R4 FIX 1: communicate returns normally but poll() says still alive
        from seam_init.omo_validation_subprocess import _ensure_dead

        fake = _CommunicatesButAliveFake()
        assert _ensure_dead(fake) is False

    def test_ensure_dead_returns_true_for_already_exited_process(self) -> None:
        from seam_init.omo_validation_subprocess import _ensure_dead

        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        proc.wait(timeout=5.0)
        assert _ensure_dead(proc) is True

    def test_run_reports_cleanup_failed_when_ensure_dead_fails(self, monkeypatch) -> None:
        # Given: _ensure_dead patched to return False (simulates unkillable process)
        import seam_init.omo_validation_subprocess as mod

        monkeypatch.setattr(mod, "_ensure_dead", lambda proc: False)
        port = mod.SubprocessOmoCommandPort()
        # When: a process that hangs past the timeout
        result = port.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env=_child_env(), timeout=1.5,
        )
        # Then: timed_out AND cleanup_failed both True
        assert result.timed_out is True
        assert result.cleanup_failed is True

    def test_run_cleanup_failed_false_on_normal_timeout(self, tmp_path) -> None:
        # Given: a real process that gets killed successfully
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        port = SubprocessOmoCommandPort()
        result = port.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env=_child_env(), timeout=1.5,
        )
        # Then: timed_out=True, cleanup_failed=False (process was killed)
        assert result.timed_out is True
        assert result.cleanup_failed is False


@final
class _UnkillableFakeProcess:
    """Deterministic fake: communicate always times out, poll always None, kill fails."""
    pid: int = 999999
    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
        raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout or 0.0)
    def poll(self) -> int | None:
        return None
    def kill(self) -> None:
        raise OSError("permission denied")


@final
class _CommunicatesButAliveFake:
    """Communicate returns normally but poll() still reports alive — catches the missing poll() bug."""
    pid: int = 888888
    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
        return ("", "")
    def poll(self) -> int | None:
        return None
    def kill(self) -> None:
        pass


# ---------------------------------------------------------------------------
# fake-executable integration: full validation through real subprocess
# ---------------------------------------------------------------------------


_FAKE_OMO_SCRIPT = """\
import json, sys
argv = sys.argv[1:]
if 'doctor' in argv:
    print(json.dumps({
        'exitCode': 0,
        'target': 'opencode',
        'systemInfo': {
            'configValid': True,
            'configPath': '/tmp/omo-plugin.jsonc',
            'pluginVersion': '5.0.0',
        },
        'results': [
            {'name': 'System', 'status': 'pass'},
            {'name': 'Configuration', 'status': 'pass'},
            {'name': 'TUI Plugin', 'status': 'pass'},
            {'name': 'Models', 'status': 'pass'},
        ],
    }, indent=2))
    sys.exit(0)
if 'run' in argv:
    sys.stderr.write('live event text on stderr\\n')
    print(json.dumps({
        'success': True, 'sessionId': 'fake-session-1',
        'messageCount': 2, 'durationMs': 100,
        'summary': 'SEAM_OMO_OK',
    }))
    sys.exit(0)
sys.exit(1)
"""


class TestFakeExecutableIntegration:
    def test_full_validation_succeeds_through_real_subprocess(self, tmp_path) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        script = tmp_path / "fake_omo.py"
        script.write_text(textwrap.dedent(_FAKE_OMO_SCRIPT))
        prefix = (sys.executable, str(script))
        request = _request(doctor_prefix=prefix, run_prefix=prefix)
        port = SubprocessOmoCommandPort()
        outcome = validate_omo_runtime(
            request, ports=OmoValidationPorts(command=port),
        )
        assert outcome.fact is OmoValidationFact.VALIDATED
        assert outcome.ok
        assert outcome.failure_kind is None

    def test_fake_doctor_fail_through_real_subprocess(self, tmp_path) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        script = tmp_path / "fake_omo_fail.py"
        script.write_text(textwrap.dedent("""\
            import sys
            argv = sys.argv[1:]
            if 'doctor' in argv:
                sys.exit(1)
            sys.exit(1)
        """))
        prefix = (sys.executable, str(script))
        request = _request(doctor_prefix=prefix, run_prefix=prefix)
        port = SubprocessOmoCommandPort()
        outcome = validate_omo_runtime(
            request, ports=OmoValidationPorts(command=port),
        )
        assert outcome.fact is OmoValidationFact.DOCTOR_FAILURE

    def test_timeout_through_real_subprocess(self, tmp_path) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        doctor_script = tmp_path / "fake_omo_doctor.py"
        doctor_script.write_text(textwrap.dedent(_FAKE_OMO_SCRIPT))
        run_script = tmp_path / "fake_omo_hang.py"
        run_script.write_text(textwrap.dedent("""\
            import time
            time.sleep(120)
        """))
        request = _request(
            doctor_prefix=(sys.executable, str(doctor_script)),
            run_prefix=(sys.executable, str(run_script)),
            run_timeout=3.0,
        )
        port = SubprocessOmoCommandPort()
        outcome = validate_omo_runtime(
            request, ports=OmoValidationPorts(command=port),
        )
        assert outcome.fact is OmoValidationFact.RUN_TIMEOUT
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION

    def test_no_network_and_no_billable_call(self, tmp_path) -> None:
        from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort

        script = tmp_path / "fake_omo_assert.py"
        script.write_text(textwrap.dedent("""\
            import json, sys, socket
            argv = sys.argv[1:]
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', 0))
            s.close()
            if 'doctor' in argv:
                print(json.dumps({
                    'exitCode': 0,
                    'target': 'opencode',
                    'systemInfo': {
                        'configValid': True,
                        'configPath': '/tmp/x.jsonc', 'pluginVersion': '5.0.0',
                    },
                    'results': [
                        {'name': 'System', 'status': 'pass'},
                        {'name': 'Configuration', 'status': 'pass'},
                        {'name': 'TUI Plugin', 'status': 'pass'},
                        {'name': 'Models', 'status': 'pass'},
                    ],
                }, indent=2))
                sys.exit(0)
            if 'run' in argv:
                print(json.dumps({
                    'success': True, 'sessionId': 's1',
                    'messageCount': 1, 'durationMs': 5,
                    'summary': 'SEAM_OMO_OK',
                }))
                sys.exit(0)
            sys.exit(1)
        """))
        prefix = (sys.executable, str(script))
        request = _request(doctor_prefix=prefix, run_prefix=prefix)
        port = SubprocessOmoCommandPort()
        outcome = validate_omo_runtime(
            request, ports=OmoValidationPorts(command=port),
        )
        assert outcome.fact is OmoValidationFact.VALIDATED
