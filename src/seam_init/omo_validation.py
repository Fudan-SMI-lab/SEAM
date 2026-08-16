"""Validate OMO runtime: doctor health gate + consented real-run marker check."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Final, Protocol, final, runtime_checkable

from core.secret_redaction import redact_sensitive_text
from seam_init.models import (
    AuthState, BillableCallConsent, FailureKind, InitializerContractError, SafeDetail,
)

__all__ = [
    "OmoCommandPort", "OmoCommandResult", "OmoValidationFact",
    "OmoValidationOutcome", "OmoValidationPorts", "OmoValidationRequest",
    "RUN_MESSAGE", "SEAM_OMO_OK_MARKER", "validate_omo_runtime",
]

SEAM_OMO_OK_MARKER: Final[str] = "SEAM_OMO_OK"
RUN_MESSAGE: Final[str] = "Reply with exactly: SEAM_OMO_OK"
_EMPTY: Final[SafeDetail] = SafeDetail("")
_MAX_DETAIL: Final[int] = 512
_MIN_DOCTOR_TIMEOUT: Final[float] = 35.0
_MAX_TIMEOUT: Final[float] = 600.0
_MIN_RUN_TIMEOUT: Final[float] = 1.0
_REQUIRED_CHECK_NAMES: Final[frozenset[str]] = frozenset({"System", "Configuration", "TUI Plugin", "Models"})


@unique
class OmoValidationFact(str, Enum):
    VALIDATED = "validated"
    AUTH_DEFERRED = "auth_deferred"
    DOCTOR_FAILURE = "doctor_failure"
    DOCTOR_MALFORMED = "doctor_malformed"
    DOCTOR_MISSING_CHECK = "doctor_missing_check"
    DOCTOR_CONFIG_INVALID = "doctor_config_invalid"
    DOCTOR_TIMEOUT = "doctor_timeout"
    RUN_FAILURE = "run_failure"
    RUN_MALFORMED = "run_malformed"
    RUN_FALSE_SUCCESS = "run_false_success"
    RUN_MARKER_MISSING = "run_marker_missing"
    RUN_FIELD_INVALID = "run_field_invalid"
    RUN_TIMEOUT = "run_timeout"


_NON_FAILURE_FACTS: Final[frozenset[OmoValidationFact]] = frozenset({OmoValidationFact.VALIDATED, OmoValidationFact.AUTH_DEFERRED})


def _safe(raw: str) -> SafeDetail:
    bounded = raw[:_MAX_DETAIL]
    suffix = "...[truncated]" if len(raw) > _MAX_DETAIL else ""
    return SafeDetail(redact_sensitive_text(bounded) + suffix)


def _check_timeout(name: str, value: float, lo: float, hi: float) -> None:
    if math.isnan(value) or math.isinf(value) or not lo <= value <= hi:
        raise InitializerContractError(
            reason=f"{name} must be [{lo},{hi}] finite, got {value}")


@final
@dataclass(frozen=True, slots=True)
class OmoCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = field(repr=False)  # raw structured output; parsed as JSON, never user-exposed
    stderr: SafeDetail
    timed_out: bool = False
    stdout_truncated: bool = False
    cleanup_failed: bool = False


@runtime_checkable
class OmoCommandPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> OmoCommandResult: ...


@final
@dataclass(frozen=True, slots=True)
class OmoValidationRequest:
    auth_state: AuthState
    billable_consent: BillableCallConsent
    doctor_argv_prefix: tuple[str, ...]
    run_argv_prefix: tuple[str, ...]
    doctor_timeout_seconds: float = 60.0
    run_timeout_seconds: float = 120.0
    base_env: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _check_timeout("doctor_timeout_seconds", self.doctor_timeout_seconds,
                       _MIN_DOCTOR_TIMEOUT, _MAX_TIMEOUT)
        _check_timeout("run_timeout_seconds", self.run_timeout_seconds,
                       _MIN_RUN_TIMEOUT, _MAX_TIMEOUT)


@final
@dataclass(frozen=True, slots=True)
class OmoValidationPorts:
    command: OmoCommandPort


@final
@dataclass(frozen=True, slots=True)
class OmoValidationOutcome:
    fact: OmoValidationFact
    failure_kind: FailureKind | None = None
    failure_detail: SafeDetail = _EMPTY
    diagnostics: tuple[SafeDetail, ...] = ()
    doctor_config_path: str = ""

    def __post_init__(self) -> None:
        is_failure = self.fact not in _NON_FAILURE_FACTS
        if is_failure and self.failure_kind is not FailureKind.OMO_VALIDATION:
            raise ValueError("failure facts require exactly FailureKind.OMO_VALIDATION")
        if not is_failure and self.failure_kind is not None:
            raise ValueError("non-failure facts must not carry failure_kind")

    @property
    def ok(self) -> bool:
        return self.fact is OmoValidationFact.VALIDATED

    @property
    def is_deferred(self) -> bool:
        return self.fact is OmoValidationFact.AUTH_DEFERRED


def _fail(
    fact: OmoValidationFact, detail: str, *,
    diagnostics: tuple[SafeDetail, ...] = (), path: str = "",
) -> OmoValidationOutcome:
    return OmoValidationOutcome(
        fact=fact, failure_kind=FailureKind.OMO_VALIDATION,
        failure_detail=_safe(detail), diagnostics=diagnostics,
        doctor_config_path=path,
    )


def _timeout_detail(name: str, result: OmoCommandResult) -> str:
    base = f"{name} exceeded external timeout"
    if result.cleanup_failed:
        base += "; cleanup incomplete (process may still be alive)"
    return base


def _env(request: OmoValidationRequest) -> Mapping[str, str] | None:
    return dict(request.base_env) if request.base_env else None


def _doctor_argv(request: OmoValidationRequest) -> list[str]:
    return [*request.doctor_argv_prefix, "doctor", "--json", "--platform", "opencode"]


def _run_argv(request: OmoValidationRequest) -> list[str]:
    return [*request.run_argv_prefix, "run", RUN_MESSAGE, "--json"]


def validate_omo_runtime(
    request: OmoValidationRequest, *, ports: OmoValidationPorts,
) -> OmoValidationOutcome:
    doctor_result = ports.command.run(
        _doctor_argv(request), env=_env(request),
        timeout=request.doctor_timeout_seconds,
    )
    if doctor_result.timed_out:
        return _fail(OmoValidationFact.DOCTOR_TIMEOUT, _timeout_detail("doctor", doctor_result))
    if doctor_result.returncode != 0:
        return _fail(OmoValidationFact.DOCTOR_FAILURE,
                     f"doctor exited {doctor_result.returncode}: {doctor_result.stderr}")
    if doctor_result.stdout_truncated:
        return _fail(OmoValidationFact.DOCTOR_MALFORMED, "doctor stdout exceeded protocol cap")
    doctor = _parse_json(doctor_result.stdout)
    if doctor is None:
        return _fail(OmoValidationFact.DOCTOR_MALFORMED, "doctor stdout unparseable")
    info = doctor.get("systemInfo")
    raw_path = info.get("configPath") if isinstance(info, dict) else None
    live = raw_path.strip() if isinstance(raw_path, str) else ""
    doctor_fact, diags = _classify_doctor(doctor)
    if doctor_fact is not None:
        return _fail(doctor_fact, f"doctor gate failed: {doctor_fact.value}",
                     diagnostics=diags, path=live)
    if (request.auth_state is AuthState.SKIPPED
            or request.billable_consent is BillableCallConsent.DECLINED):
        return OmoValidationOutcome(fact=OmoValidationFact.AUTH_DEFERRED, diagnostics=diags, doctor_config_path=live)
    run_result = ports.command.run(
        _run_argv(request), env=_env(request),
        timeout=request.run_timeout_seconds,
    )
    if run_result.timed_out:
        return _fail(OmoValidationFact.RUN_TIMEOUT, _timeout_detail("run", run_result), path=live)
    if run_result.returncode != 0:
        return _fail(OmoValidationFact.RUN_FAILURE,
                     f"run exited {run_result.returncode}: {run_result.stderr}", path=live)
    if run_result.stdout_truncated:
        return _fail(OmoValidationFact.RUN_MALFORMED, "run stdout exceeded protocol cap", path=live)
    run = _parse_json(run_result.stdout)
    if run is None:
        return _fail(OmoValidationFact.RUN_MALFORMED, "run stdout unparseable", path=live)
    run_fact = _classify_run(run)
    if run_fact is OmoValidationFact.VALIDATED:
        return OmoValidationOutcome(fact=OmoValidationFact.VALIDATED, diagnostics=diags, doctor_config_path=live)
    return _fail(run_fact, f"run gate failed: {run_fact.value}", diagnostics=diags, path=live)


def _parse_json(stdout: str) -> dict[str, object] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _classify_doctor(
    doctor: dict[str, object],
) -> tuple[OmoValidationFact | None, tuple[SafeDetail, ...]]:
    exit_code = doctor.get("exitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return OmoValidationFact.DOCTOR_MALFORMED, ()
    if exit_code != 0:
        return OmoValidationFact.DOCTOR_FAILURE, ()
    if doctor.get("target") != "opencode":
        return OmoValidationFact.DOCTOR_CONFIG_INVALID, ()
    info = doctor.get("systemInfo")
    config_path = info.get("configPath") if isinstance(info, dict) else None
    if (not isinstance(info, dict) or info.get("configValid") is not True
            or not (isinstance(config_path, str) and config_path.strip())):
        return OmoValidationFact.DOCTOR_CONFIG_INVALID, ()
    results = doctor.get("results")
    if not isinstance(results, list):
        return OmoValidationFact.DOCTOR_MALFORMED, ()
    verified: set[str] = set()
    diagnostics: list[SafeDetail] = []
    for item in results:
        if not isinstance(item, dict):
            return OmoValidationFact.DOCTOR_MALFORMED, tuple(diagnostics)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return OmoValidationFact.DOCTOR_MALFORMED, tuple(diagnostics)
        match item.get("status"):
            case "pass" | "warn":
                verified.add(name)
                if item.get("status") == "warn":
                    msg = item.get("message")
                    if isinstance(msg, str) and msg.strip():
                        diagnostics.append(_safe(msg))
            case "skip":
                msg = item.get("message")
                if isinstance(msg, str) and msg.strip():
                    diagnostics.append(_safe(msg))
            case "fail":
                msg = item.get("message")
                detail = str(msg) if isinstance(msg, str) else "check failed"
                return OmoValidationFact.DOCTOR_FAILURE, (*diagnostics, _safe(detail))
            case _:
                return OmoValidationFact.DOCTOR_MALFORMED, tuple(diagnostics)
    if _REQUIRED_CHECK_NAMES - verified:
        return OmoValidationFact.DOCTOR_MISSING_CHECK, tuple(diagnostics)
    return None, tuple(diagnostics)


def _classify_run(run: dict[str, object]) -> OmoValidationFact:
    if run.get("success") is not True:
        return OmoValidationFact.RUN_FALSE_SUCCESS
    session_id = run.get("sessionId")
    if not (isinstance(session_id, str) and session_id.strip()):
        return OmoValidationFact.RUN_FIELD_INVALID
    message_count = run.get("messageCount")
    if not (isinstance(message_count, int) and not isinstance(message_count, bool)
            and message_count > 0):
        return OmoValidationFact.RUN_FIELD_INVALID
    duration_ms = run.get("durationMs")
    if not (isinstance(duration_ms, int) and not isinstance(duration_ms, bool)
            and duration_ms >= 0):
        return OmoValidationFact.RUN_FIELD_INVALID
    summary = run.get("summary")
    if not isinstance(summary, str):
        return OmoValidationFact.RUN_FIELD_INVALID
    if summary.strip() != SEAM_OMO_OK_MARKER:
        return OmoValidationFact.RUN_MARKER_MISSING
    return OmoValidationFact.VALIDATED
