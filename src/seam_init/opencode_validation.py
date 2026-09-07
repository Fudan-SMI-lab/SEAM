"""Validate OpenCode message acceptance: pre-checks, auth gate, exact marker."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Final, Protocol, final, runtime_checkable

from core.compat import assert_never
from core.secret_redaction import redact_sensitive_text
from seam_init.models import (
    AuthState,
    BillableCallConsent,
    FailureKind,
    InitializerContractError,
    SafeDetail,
)
from seam_init.opencode_discovery import RuntimePort
from seam_init.opencode_marker import (
    SEAM_DIAG_OK_MARKER,
    check_marker,
    cleanup_state,
    extract_probe,
)
from seam_init.opencode_runtime_types import (
    DiagnoseResult,
    DiagnoseRunner,
    ReadinessFact,
    classify_diagnose_exit,
)

__all__ = [
    "SEAM_DIAG_OK_MARKER", "ValidationFact", "ValidationOutcome",
    "ValidationPorts", "ValidationRequest", "VersionProbe",
    "VersionResult", "validate_opencode_messages",
]

_DEFAULT_MESSAGE_TIMEOUT: Final[int] = 120
_MIN_MESSAGE_TIMEOUT: Final[int] = 5
_MAX_MESSAGE_TIMEOUT: Final[int] = 300
_MAX_DETAIL: Final[int] = 512
_AUTH_STATUS_CODES: Final[frozenset[int]] = frozenset({401, 403})
_EMPTY: Final[SafeDetail] = SafeDetail("")


@unique
class ValidationFact(str, Enum):
    MESSAGE_READY = "message_ready"
    AUTH_DEFERRED = "auth_deferred"
    AUTH_FAILURE = "auth_failure"
    MODEL_NOT_FOUND = "model_not_found"
    TRANSPORT_FAILURE = "transport_failure"
    SERVER_FAILURE = "server_failure"
    MARKER_MISSING = "marker_missing"
    MARKER_MALFORMED = "marker_malformed"
    TIMEOUT_FAILURE = "timeout_failure"
    VERSION_FAILURE = "version_failure"
    CONFIG_FAILURE = "config_failure"
    CLEANUP_FAILURE = "cleanup_failure"
    INVALID_ARGUMENT = "invalid_argument"
    UNKNOWN = "unknown"


_NON_FAILURE_FACTS: Final[frozenset[ValidationFact]] = frozenset({
    ValidationFact.MESSAGE_READY, ValidationFact.AUTH_DEFERRED,
})


def _safe(raw: str) -> SafeDetail:
    bounded = raw[:_MAX_DETAIL]
    suffix = "...[truncated]" if len(raw) > _MAX_DETAIL else ""
    return SafeDetail(redact_sensitive_text(bounded) + suffix)


@final
@dataclass(frozen=True, slots=True)
class VersionResult:
    ok: bool
    version: SafeDetail
    error: SafeDetail = _EMPTY


@runtime_checkable
class VersionProbe(Protocol):
    def check(self, executable: str) -> VersionResult: ...


@final
@dataclass(frozen=True, slots=True)
class ValidationRequest:
    server_url: str
    provider_model: str
    auth_state: AuthState
    billable_consent: BillableCallConsent
    opencode_executable: str
    diagnose_argv_prefix: tuple[str, ...]
    message_timeout: int = _DEFAULT_MESSAGE_TIMEOUT
    base_env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _MIN_MESSAGE_TIMEOUT <= self.message_timeout <= _MAX_MESSAGE_TIMEOUT:
            raise InitializerContractError(
                reason=f"message_timeout must be [{_MIN_MESSAGE_TIMEOUT},"
                       f"{_MAX_MESSAGE_TIMEOUT}], got {self.message_timeout}",
            )


@final
@dataclass(frozen=True, slots=True)
class ValidationPorts:
    version_probe: VersionProbe
    runtime: RuntimePort
    diagnose_runner: DiagnoseRunner


@final
@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    fact: ValidationFact
    server_url: str
    marker_found: bool = False
    session_deleted: bool = False
    failure_kind: FailureKind | None = None
    failure_detail: SafeDetail = _EMPTY
    diagnostics: tuple[SafeDetail, ...] = ()

    def __post_init__(self) -> None:
        is_failure = self.fact not in _NON_FAILURE_FACTS
        if is_failure and self.failure_kind is None:
            raise ValueError("failure facts require failure_kind")
        if not is_failure and self.failure_kind is not None:
            raise ValueError("non-failure facts must not carry failure_kind")

    @property
    def ok(self) -> bool:
        return self.fact is ValidationFact.MESSAGE_READY

    @property
    def is_deferred(self) -> bool:
        return self.fact is ValidationFact.AUTH_DEFERRED


def _fail(
    fact: ValidationFact, server_url: str, detail: str,
    *, session_deleted: bool = False, cleanup_diag: SafeDetail = _EMPTY,
) -> ValidationOutcome:
    diags = (cleanup_diag,) if str(cleanup_diag) else ()
    return ValidationOutcome(
        fact=fact, server_url=server_url, session_deleted=session_deleted,
        failure_kind=FailureKind.OPENCODE_VALIDATION,
        failure_detail=_safe(detail), diagnostics=diags,
    )


def validate_opencode_messages(
    request: ValidationRequest, *, ports: ValidationPorts,
) -> ValidationOutcome:
    version = ports.version_probe.check(request.opencode_executable)
    if not version.ok:
        return _fail(ValidationFact.VERSION_FAILURE, request.server_url,
                     f"opencode --version failed: {version.error}")
    models = ports.runtime.debug_models()
    if models is None:
        return _fail(ValidationFact.CONFIG_FAILURE, request.server_url,
                     "model catalog unavailable")
    if request.provider_model not in models:
        return _fail(ValidationFact.MODEL_NOT_FOUND, request.server_url,
                     f"selected model not in catalog ({len(models)} available)")
    if (request.auth_state is AuthState.SKIPPED
            or request.billable_consent is BillableCallConsent.DECLINED):
        return ValidationOutcome(
            fact=ValidationFact.AUTH_DEFERRED, server_url=request.server_url)
    argv = _message_argv(request)
    print(f"[OPENCODE_VALIDATION] Sending test message to provider "
          f"(timeout: {request.message_timeout}s)...", flush=True)
    result = ports.diagnose_runner.run(argv, env=dict(request.base_env))
    outcome = _classify_result(result, request.server_url)
    print(f"[OPENCODE_VALIDATION] Result: {outcome.fact.value}", flush=True)
    return outcome


def _message_argv(request: ValidationRequest) -> list[str]:
    return [
        *request.diagnose_argv_prefix, "--server-url", request.server_url,
        "--mode", "message", "--message-timeout", str(request.message_timeout),
        "--json-only",
    ]


def _classify_result(result: DiagnoseResult, server_url: str) -> ValidationOutcome:
    readiness = classify_diagnose_exit(result.returncode)
    probe = extract_probe(str(result.stdout))
    exact, inconsistent = check_marker(probe)
    deleted, cleanup_diag = cleanup_state(probe)
    match readiness:
        case ReadinessFact.READY:
            if probe is None:
                return _fail(ValidationFact.MARKER_MALFORMED, server_url,
                             "READY exit but message_probe missing or unparseable",
                             cleanup_diag=cleanup_diag)
            if inconsistent:
                return _fail(ValidationFact.MARKER_MALFORMED, server_url,
                             "contains_marker contradicts response_text",
                             session_deleted=deleted, cleanup_diag=cleanup_diag)
            response_text = str(probe.get("response_text", ""))
            contains = probe.get("contains_marker", False)
            if not exact and not contains:
                print(f"[OPENCODE_VALIDATION] Model response: {response_text[:200]}", flush=True)
                return _fail(ValidationFact.MARKER_MISSING, server_url,
                             f"response did not contain SEAM_DIAG_OK: {response_text[:150]}",
                             session_deleted=deleted, cleanup_diag=cleanup_diag)
            if not deleted:
                return ValidationOutcome(
                    fact=ValidationFact.CLEANUP_FAILURE, server_url=server_url,
                    marker_found=True, session_deleted=False,
                    failure_kind=FailureKind.OPENCODE_VALIDATION,
                    failure_detail=SafeDetail("marker found but probe session not deleted"),
                    diagnostics=(cleanup_diag,) if str(cleanup_diag) else ())
            return ValidationOutcome(
                fact=ValidationFact.MESSAGE_READY, server_url=server_url,
                marker_found=True, session_deleted=True)
        case ReadinessFact.BASIC_READY:
            return _fail(ValidationFact.SERVER_FAILURE, server_url,
                         "server basic-ready but message probe skipped",
                         session_deleted=deleted, cleanup_diag=cleanup_diag)
        case ReadinessFact.SERVER_UNREACHABLE:
            return _fail(ValidationFact.TRANSPORT_FAILURE, server_url,
                         f"server unreachable: {server_url}")
        case ReadinessFact.AGENT_UNAVAILABLE | ReadinessFact.SESSION_UNAVAILABLE:
            return _fail(ValidationFact.SERVER_FAILURE, server_url,
                         f"server check failed: {readiness.value}")
        case ReadinessFact.MESSAGE_UNAVAILABLE:
            return _classify_message_failure(probe, server_url, deleted, cleanup_diag)
        case ReadinessFact.INVALID_ARGUMENT:
            return _fail(ValidationFact.INVALID_ARGUMENT, server_url,
                         "diagnose rejected arguments",
                         session_deleted=deleted, cleanup_diag=cleanup_diag)
        case ReadinessFact.UNKNOWN:
            return _fail(ValidationFact.UNKNOWN, server_url,
                         f"unclassified diagnose exit code={result.returncode}",
                         session_deleted=deleted, cleanup_diag=cleanup_diag)
        case unreachable:
            assert_never(unreachable)


def _classify_message_failure(
    probe: dict[str, object] | None, server_url: str,
    deleted: bool, cleanup_diag: SafeDetail,
) -> ValidationOutcome:
    if probe is None:
        return _fail(ValidationFact.MARKER_MALFORMED, server_url,
                     "diagnose output unparseable or missing message_probe")
    message_obj = probe.get("message")
    status = message_obj.get("status") if isinstance(message_obj, dict) else None
    if isinstance(status, int) and status in _AUTH_STATUS_CODES:
        return _fail(ValidationFact.AUTH_FAILURE, server_url,
                     f"provider returned HTTP {status}",
                     session_deleted=deleted, cleanup_diag=cleanup_diag)
    error = str(probe.get("error") or "")
    if "timed out" in error.lower() or "timeout" in error.lower():
        return _fail(ValidationFact.TIMEOUT_FAILURE, server_url,
                     "message round-trip timed out",
                     session_deleted=deleted, cleanup_diag=cleanup_diag)
    snippet = error[:200] if error else "message round-trip failed"
    return _fail(ValidationFact.MARKER_MISSING, server_url,
                 f"message unavailable: {snippet}",
                 session_deleted=deleted, cleanup_diag=cleanup_diag)
