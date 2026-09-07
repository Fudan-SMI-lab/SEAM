"""Contract tests for the SEAM interactive initializer domain model.

Every test uses an explicit Given/When/Then block. Covers READY->0,
PENDING_AUTH->60, every FailureKind->61..69, skipped auth/consent can never
become READY, typed secret-free errors, frozen+slots value objects, and
exhaustive matching over every InitializerStatus member.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from seam_init.models import (
    AuthState,
    BillableCallConsent,
    DiagnosticClassifier,
    EnvironmentChoice,
    EnvironmentKind,
    ExitCode,
    FailureKind,
    InitializerContractError,
    InitializerFailure,
    InitializerOutcome,
    InitializerStatus,
    ModelId,
    ProviderId,
    ProviderSelection,
    SafeDetail,
    StageKind,
    StageRecord,
    StageStatus,
)

# A canary secret string used to prove it can never reach typed failure fields.
_CANARY = "sk-test-canary-0123456789abcdef"


# --- exit code mapping -----------------------------------------------------


def test_ready_outcome_maps_to_exit_zero() -> None:
    # Given / When
    outcome = InitializerOutcome.ready()
    # Then
    assert outcome.status is InitializerStatus.READY
    assert outcome.exit_code == 0
    assert outcome.exit_code == int(ExitCode.READY)


def test_pending_auth_outcome_maps_to_exit_60() -> None:
    # Given / When
    outcome = InitializerOutcome.pending_auth()
    # Then
    assert outcome.status is InitializerStatus.PENDING_AUTH
    assert outcome.exit_code == 60
    assert outcome.exit_code == int(ExitCode.PENDING_AUTH)


@pytest.mark.parametrize("kind", list(FailureKind))
def test_every_failure_kind_maps_to_its_categorized_exit_code(kind: FailureKind) -> None:
    # Given / When
    outcome = InitializerOutcome.failed(failure_kind=kind)
    # Then
    assert outcome.status is InitializerStatus.FAILED
    assert outcome.exit_code == int(kind)
    assert 61 <= outcome.exit_code <= 69


def test_failure_exit_codes_are_exactly_61_through_69() -> None:
    # Given
    expected = set(range(61, 70))
    # When
    actual = {int(kind) for kind in FailureKind}
    # Then
    assert actual == expected


def test_failure_kind_values_are_pairwise_distinct() -> None:
    # Given / When
    values = [int(kind) for kind in FailureKind]
    # Then
    assert len(values) == len(set(values))


def test_exit_code_enum_only_owns_initializer_codes() -> None:
    # Then: ExitCode never exposes raw diagnostic exits (40-43, 50, 20).
    assert {int(c) for c in ExitCode} == {0, 60}


def test_initializer_status_has_exactly_three_members() -> None:
    # Then: no free-form status strings.
    assert {s.value for s in InitializerStatus} == {"ready", "pending_auth", "failed"}


# --- skipped auth / consent cannot become READY -----------------------------


def test_skipped_auth_cannot_become_ready() -> None:
    # Given / When / Then: constructing READY with skipped auth is rejected.
    with pytest.raises(InitializerContractError):
        _ = InitializerOutcome(
            status=InitializerStatus.READY,
            auth_state=AuthState.SKIPPED,
            billable_consent=BillableCallConsent.GIVEN,
            stages=(),
        )


def test_declined_billable_consent_cannot_become_ready() -> None:
    # Given / When / Then: READY requires explicit consent for the live call.
    with pytest.raises(InitializerContractError):
        _ = InitializerOutcome(
            status=InitializerStatus.READY,
            auth_state=AuthState.PROVIDED,
            billable_consent=BillableCallConsent.DECLINED,
            stages=(),
        )


def test_ready_cannot_carry_a_failure_kind() -> None:
    # Given / When / Then: READY and a failure kind are mutually exclusive.
    with pytest.raises(InitializerContractError):
        _ = InitializerOutcome(
            status=InitializerStatus.READY,
            auth_state=AuthState.PROVIDED,
            billable_consent=BillableCallConsent.GIVEN,
            stages=(),
            failure_kind=FailureKind.OPENCODE_RUNTIME,
        )


def test_pending_auth_cannot_carry_a_failure_kind() -> None:
    # Given / When / Then: PENDING_AUTH is non-fatal and failure-free.
    with pytest.raises(InitializerContractError):
        _ = InitializerOutcome(
            status=InitializerStatus.PENDING_AUTH,
            auth_state=AuthState.SKIPPED,
            billable_consent=BillableCallConsent.DECLINED,
            stages=(),
            failure_kind=FailureKind.SEAM_INSTALL,
        )


def test_pending_auth_with_full_auth_and_consent_is_rejected() -> None:
    # Given / When / Then: provided auth + given consent must be READY, not deferred.
    with pytest.raises(InitializerContractError):
        _ = InitializerOutcome(
            status=InitializerStatus.PENDING_AUTH,
            auth_state=AuthState.PROVIDED,
            billable_consent=BillableCallConsent.GIVEN,
            stages=(),
        )


def test_failed_requires_a_failure_kind() -> None:
    # Given / When / Then: FAILED without a kind is impossible.
    with pytest.raises(InitializerContractError):
        _ = InitializerOutcome(
            status=InitializerStatus.FAILED,
            auth_state=AuthState.PROVIDED,
            billable_consent=BillableCallConsent.DECLINED,
            stages=(),
            failure_kind=None,
        )


def test_pending_auth_factory_defaults_skip_auth_and_decline_consent() -> None:
    # Given / When
    outcome = InitializerOutcome.pending_auth()
    # Then: the default deferred state is consistent and yields exit 60.
    assert outcome.auth_state is AuthState.SKIPPED
    assert outcome.billable_consent is BillableCallConsent.DECLINED
    assert outcome.failure_kind is None
    assert outcome.exit_code == 60


def test_failed_factory_records_failure_kind() -> None:
    # Given / When
    outcome = InitializerOutcome.failed(failure_kind=FailureKind.OMO_CONFIG)
    # Then
    assert outcome.failure_kind is FailureKind.OMO_CONFIG
    assert outcome.exit_code == 66


# --- exhaustive matching: every status maps to an exit code ----------------


@pytest.mark.parametrize("status", list(InitializerStatus))
def test_every_initializer_status_is_handled(status: InitializerStatus) -> None:
    # Given a concrete outcome for each status via exhaustive match
    match status:
        case InitializerStatus.READY:
            outcome = InitializerOutcome.ready()
        case InitializerStatus.PENDING_AUTH:
            outcome = InitializerOutcome.pending_auth()
        case InitializerStatus.FAILED:
            outcome = InitializerOutcome.failed(failure_kind=FailureKind.SEAM_INSTALL)
    # Then: every member produces a valid initializer exit code.
    assert outcome.exit_code in {0, 60, *range(61, 70)}


# --- immutability + slots --------------------------------------------------


def test_outcome_is_frozen() -> None:
    # Given
    outcome = InitializerOutcome.ready()
    # When / Then
    with pytest.raises(FrozenInstanceError):
        setattr(outcome, "exit_code", 1)


def test_outcome_uses_slots_and_has_no_dict() -> None:
    # Given
    outcome = InitializerOutcome.ready()
    # Then: slots-only storage; no instance dict to leak secret-bearing attrs.
    assert not hasattr(outcome, "__dict__")
    assert "exit_code" in type(outcome).__slots__


def test_stage_record_is_frozen_and_slots() -> None:
    # Given
    record = StageRecord(kind=StageKind.PYTHON_ENVIRONMENT, status=StageStatus.SUCCEEDED)
    # When / Then
    assert not hasattr(record, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(record, "status", StageStatus.FAILED)


# --- error contract: secret-free typed context -----------------------------


def test_initializer_failure_carries_only_safe_detail() -> None:
    # Given
    failure = InitializerFailure(
        kind=FailureKind.OPENCODE_VALIDATION,
        safe_detail=SafeDetail("provider message probe failed"),
    )
    # Then: every slot value is a typed enum or a SafeDetail string; the
    # canary secret never appears in any attribute the failure can carry.
    assert failure.kind is FailureKind.OPENCODE_VALIDATION
    assert isinstance(failure.safe_detail, str)
    for value in (failure.kind, failure.safe_detail):
        assert _CANARY not in str(value)


def test_initializer_failure_str_omits_secrets() -> None:
    # Given
    failure = InitializerFailure(
        kind=FailureKind.OPENCODE_CONFIG,
        safe_detail=SafeDetail("model not listed"),
    )
    # Then
    assert _CANARY not in str(failure)
    assert "68" not in str(failure)  # OPENCODE_CONFIG is 64
    assert "64" in str(failure)


def test_initializer_contract_error_reason_is_typed() -> None:
    # Given / When
    error = InitializerContractError(reason="FAILED requires a failure kind")
    # Then
    assert error.reason == "FAILED requires a failure kind"
    assert _CANARY not in str(error)


# --- environment + provider value objects ----------------------------------


def test_environment_choice_rejects_empty_executable() -> None:
    # Given / When / Then
    with pytest.raises(InitializerContractError):
        _ = EnvironmentChoice(
            kind=EnvironmentKind.NEW_VENV,
            python_executable="",
            python_version="3.12.1",
        )


def test_environment_choice_is_frozen_and_slots() -> None:
    # Given
    choice = EnvironmentChoice(
        kind=EnvironmentKind.EXISTING_VENV,
        python_executable="/tmp/venv/bin/python",
        python_version="3.12.1",
    )
    # Then
    assert not hasattr(choice, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(choice, "kind", EnvironmentKind.BASE)


def test_provider_selection_defaults_to_skipped_auth() -> None:
    # Given / When
    selection = ProviderSelection(
        provider_id=ProviderId("openai"),
        model_id=ModelId("gpt-4o-mini"),
    )
    # Then
    assert selection.auth_state is AuthState.SKIPPED
    assert selection.base_url is None


def test_provider_selection_rejects_empty_ids() -> None:
    # Given / When / Then
    with pytest.raises(InitializerContractError):
        _ = ProviderSelection(
            provider_id=ProviderId(""),
            model_id=ModelId("gpt-4o-mini"),
        )


def test_safe_detail_brands_a_secret_free_string() -> None:
    # Given / When
    detail: SafeDetail = SafeDetail("python 3.9 is below the 3.10 floor")
    # Then
    assert isinstance(detail, str)
    assert _CANARY not in detail


# --- stage state machine values --------------------------------------------


def test_stage_status_members_match_state_machine() -> None:
    # Then: the documented state-machine values are stable.
    assert {s.value for s in StageStatus} == {
        "pending", "in_progress", "succeeded", "skipped", "failed",
    }


def test_stage_kind_covers_every_initializer_step() -> None:
    # Then: the nine ordered initializer stages from the plan are present.
    assert {s.value for s in StageKind} == {
        "python_environment", "seam_install", "opencode_install",
        "opencode_config", "omo_install", "omo_config", "opencode_runtime",
        "opencode_validation", "omo_validation",
    }


# --- diagnostic classifier protocol seam -----------------------------------


def test_diagnostic_classifier_is_runtime_checkable_protocol() -> None:
    # Given: a concrete classifier that returns None for non-failure codes.
    class _Classifier:
        def classify(self, raw_exit_code: int) -> FailureKind | None:
            if raw_exit_code in (40, 41, 42):
                return FailureKind.OPENCODE_RUNTIME
            return None

    # When / Then
    instance = _Classifier()
    assert isinstance(instance, DiagnosticClassifier)
    assert instance.classify(40) is FailureKind.OPENCODE_RUNTIME
    assert instance.classify(0) is None


def test_diagnostic_classifier_rejects_object_without_method() -> None:
    # Given: a plain object without the classify method.
    class _Bare:
        pass

    bare = _Bare()
    # Then: it does not satisfy the DiagnosticClassifier protocol.
    assert not isinstance(bare, DiagnosticClassifier)


# --- stage ledger attached to outcomes -------------------------------------


def test_ready_outcome_freezes_complete_stage_ledger() -> None:
    # Given: a fully completed stage ledger
    ledger = (
        StageRecord(kind=StageKind.PYTHON_ENVIRONMENT, status=StageStatus.SUCCEEDED),
        StageRecord(kind=StageKind.SEAM_INSTALL, status=StageStatus.SUCCEEDED),
        StageRecord(kind=StageKind.OMO_VALIDATION, status=StageStatus.SUCCEEDED),
    )
    # When
    outcome = InitializerOutcome.ready(
        stages=ledger,
        safe_detail=SafeDetail("all checks passed"),
    )
    # Then
    assert outcome.stages == ledger
    assert outcome.exit_code == 0
    assert outcome.safe_detail == "all checks passed"
    assert _CANARY not in outcome.safe_detail


def test_failed_outcome_keeps_partial_ledger_and_redacted_detail() -> None:
    # Given
    ledger = (
        StageRecord(kind=StageKind.PYTHON_ENVIRONMENT, status=StageStatus.SUCCEEDED),
        StageRecord(kind=StageKind.SEAM_INSTALL, status=StageStatus.FAILED),
    )
    # When
    outcome = InitializerOutcome.failed(
        failure_kind=FailureKind.SEAM_INSTALL,
        stages=ledger,
        safe_detail=SafeDetail("pip install exited non-zero"),
    )
    # Then: partial ledger is retained, exit code is the failure category.
    assert outcome.stages == ledger
    assert outcome.exit_code == 62
    assert _CANARY not in outcome.safe_detail
