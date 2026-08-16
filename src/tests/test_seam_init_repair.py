"""Tests for the bounded config-specific repair and revalidation loop.

Every edit/revalidation boundary is routed through injected no-argument
callables; no real OpenCode/OMO/provider/network is contacted. Integration
tests use a real ``ConfigTransaction`` against ``tmp_path`` for byte-exact
restore verification. Each test uses an explicit Given/When/Then block.
"""
from __future__ import annotations

from pathlib import Path
from typing import Generic, TypeVar, final

import pytest

from core.secret_redaction import redact_sensitive_text
from seam_init.config_transaction import ConfigTransaction
from seam_init.models import FailureKind, SafeDetail
from seam_init.opencode_config import OpencodeConfigResult
from seam_init.opencode_validation import ValidationFact, ValidationOutcome
from seam_init.omo_config import OmoConfigResult
from seam_init.omo_validation import OmoValidationFact, OmoValidationOutcome
from seam_init.repair import (
    OmoEditPort,
    OmoRevalidatePort,
    OpencodeEditPort,
    OpencodeRevalidatePort,
    RepairOutcome,
    RepairRequest,
    RepairStatus,
    run_repair,
)
from seam_init.repair_classify import (
    RepairCategory,
    RepairValidation,
    RepairableDomain,
    classify_repair,
    omo_repairable_fact,
    omo_terminal_fact,
    opencode_repairable_fact,
    opencode_terminal_fact,
    repairable_domain_for,
)

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# constants and outcome builders
# ---------------------------------------------------------------------------

_URL = "http://127.0.0.1:4098"
_CANARY = "sk-canary-secret-key-1234567890"

_OC_NON_FAILURE = frozenset({ValidationFact.MESSAGE_READY, ValidationFact.AUTH_DEFERRED})
_OMO_NON_FAILURE = frozenset({OmoValidationFact.VALIDATED, OmoValidationFact.AUTH_DEFERRED})


def _oc_outcome(
    fact: ValidationFact, *, detail: str = "",
) -> ValidationOutcome:
    if fact in _OC_NON_FAILURE:
        return ValidationOutcome(fact=fact, server_url=_URL)
    return ValidationOutcome(
        fact=fact, server_url=_URL,
        failure_kind=FailureKind.OPENCODE_VALIDATION,
        failure_detail=SafeDetail(detail),
    )


def _omo_outcome(
    fact: OmoValidationFact, *, detail: str = "",
) -> OmoValidationOutcome:
    if fact in _OMO_NON_FAILURE:
        return OmoValidationOutcome(fact=fact)
    return OmoValidationOutcome(
        fact=fact, failure_kind=FailureKind.OMO_VALIDATION,
        failure_detail=SafeDetail(detail),
    )


def _validation(
    oc: ValidationFact = ValidationFact.MESSAGE_READY,
    omo: OmoValidationFact = OmoValidationFact.VALIDATED,
) -> RepairValidation:
    return RepairValidation(
        opencode=_oc_outcome(oc), omo=_omo_outcome(omo))


def _oc_result(*, committed: bool = True) -> OpencodeConfigResult:
    return OpencodeConfigResult(
        committed=committed, pending_auth=False, facts=(),
        transaction=None, safe_detail=SafeDetail("oc edit result"),
    )


def _omo_result(*, committed: bool = True) -> OmoConfigResult:
    return OmoConfigResult(
        committed=committed, migrated=False, fresh=False, facts=(),
        transaction=None, safe_detail=SafeDetail("omo edit result"),
    )


# ---------------------------------------------------------------------------
# fake boundaries
# ---------------------------------------------------------------------------


@final
class _ScriptedPrompt:
    """PromptPort double: returns scripted ask answers; rejects secret/confirm."""

    def __init__(self, answers: list[str] | None = None) -> None:
        self._answers = list(answers) if answers else []
        self.ask_calls: int = 0
        self.prompts: list[str] = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.ask_calls += 1
        self.prompts.append(prompt)
        if default is not None:
            self.prompts[-1] += f" [default={default}]"
        if not self._answers:
            raise AssertionError("scripted prompt exhausted")
        return self._answers.pop(0)

    def secret(self, prompt: str) -> str:
        raise AssertionError(f"repair must not call secret: {prompt}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        raise AssertionError(
            f"repair must not call confirm: {prompt} default={default}")


@final
class _Revalidator(Generic[_T]):
    """Callable returning outcomes from a queue; last repeats forever."""

    def __init__(self, *outcomes: _T) -> None:
        self._queue = list(outcomes)
        self._last: _T | None = outcomes[-1] if outcomes else None
        self.calls = 0

    def __call__(self) -> _T:
        self.calls += 1
        if self._queue:
            self._last = self._queue.pop(0)
        assert self._last is not None, "revalidator has no outcomes"
        return self._last


@final
class _EditPort(Generic[_T]):
    """Callable edit port double: records calls and returns a canned result."""

    def __init__(self, result: _T) -> None:
        self._result = result
        self.calls = 0

    def __call__(self) -> _T:
        self.calls += 1
        return self._result


@final
class _SequenceEditPort(Generic[_T]):
    """Edit port that returns results from a sequence, one per call."""

    def __init__(self, *results: _T) -> None:
        self._results = list(results)
        self.calls = 0

    def __call__(self) -> _T:
        self.calls += 1
        return self._results.pop(0)


def _request(
    tmp_path: Path,
    *,
    initial: RepairValidation,
    prompt: _ScriptedPrompt,
    edit_oc: OpencodeEditPort | None = None,
    edit_omo: OmoEditPort | None = None,
    revalidate_oc: OpencodeRevalidatePort | None = None,
    revalidate_omo: OmoRevalidatePort | None = None,
    opencode_target: Path | None = None,
    omo_target: Path | None = None,
) -> RepairRequest:
    oc_target = opencode_target or tmp_path / ".opencode" / "opencode.jsonc"
    om_target = omo_target or tmp_path / ".omo" / "omo.jsonc"
    return RepairRequest(
        project_root=tmp_path,
        opencode_target=oc_target,
        omo_target=om_target,
        prompt=prompt,
        initial=initial,
        revalidate_opencode=revalidate_oc
        or _Revalidator(initial.opencode),
        revalidate_omo=revalidate_omo
        or _Revalidator(initial.omo),
        edit_opencode=edit_oc or _EditPort(_oc_result()),
        edit_omo=edit_omo or _EditPort(_omo_result()),
    )


# ---------------------------------------------------------------------------
# classification exhaustiveness
# ---------------------------------------------------------------------------


class TestClassificationExhaustiveness:
    """Every ValidationFact/OmoValidationFact member in exactly one bucket."""

    def test_every_opencode_fact_classified(self) -> None:
        # Given: all 14 ValidationFact members.
        # When/Then: each is in exactly one of repairable/terminal/non-failure.
        for fact in ValidationFact:
            is_repairable = opencode_repairable_fact(fact)
            is_terminal = opencode_terminal_fact(fact)
            is_non_failure = fact in _OC_NON_FAILURE
            assert is_repairable + is_terminal + is_non_failure == 1, (
                f"{fact} must be in exactly one bucket")

    def test_every_omo_fact_classified(self) -> None:
        # Given: all 13 OmoValidationFact members.
        # When/Then: each is in exactly one of repairable/terminal/non-failure.
        for fact in OmoValidationFact:
            is_repairable = omo_repairable_fact(fact)
            is_terminal = omo_terminal_fact(fact)
            is_non_failure = fact in _OMO_NON_FAILURE
            assert is_repairable + is_terminal + is_non_failure == 1, (
                f"{fact} must be in exactly one bucket")

    def test_classify_success_requires_both_pass(self) -> None:
        validation = _validation(
            ValidationFact.MESSAGE_READY, OmoValidationFact.VALIDATED)
        assert classify_repair(validation) is RepairCategory.SUCCESS

    def test_classify_terminal_takes_priority_over_repairable(self) -> None:
        validation = RepairValidation(
            opencode=_oc_outcome(ValidationFact.TRANSPORT_FAILURE),
            omo=_omo_outcome(OmoValidationFact.DOCTOR_CONFIG_INVALID),
        )
        assert classify_repair(validation) is RepairCategory.TERMINAL

    def test_classify_repairable_when_one_domain_repairable(self) -> None:
        validation = _validation(
            ValidationFact.AUTH_FAILURE, OmoValidationFact.VALIDATED)
        assert classify_repair(validation) is RepairCategory.REPAIRABLE

    def test_classify_pending_auth_when_deferred(self) -> None:
        validation = _validation(
            ValidationFact.AUTH_DEFERRED, OmoValidationFact.AUTH_DEFERRED)
        assert classify_repair(validation) is RepairCategory.PENDING_AUTH

    def test_classify_pending_auth_one_ready_one_deferred(self) -> None:
        validation = _validation(
            ValidationFact.MESSAGE_READY, OmoValidationFact.AUTH_DEFERRED)
        assert classify_repair(validation) is RepairCategory.PENDING_AUTH

    def test_repairable_domain_prefers_opencode(self) -> None:
        validation = RepairValidation(
            opencode=_oc_outcome(ValidationFact.AUTH_FAILURE),
            omo=_omo_outcome(OmoValidationFact.DOCTOR_CONFIG_INVALID),
        )
        assert repairable_domain_for(validation) is RepairableDomain.OPENCODE

    def test_repairable_domain_omo_when_opencode_ok(self) -> None:
        validation = _validation(
            ValidationFact.MESSAGE_READY,
            OmoValidationFact.DOCTOR_CONFIG_INVALID)
        assert repairable_domain_for(validation) is RepairableDomain.OMO

    def test_repairable_domain_none_when_no_repairable(self) -> None:
        validation = _validation()
        assert repairable_domain_for(validation) is None


# ---------------------------------------------------------------------------
# immediate terminal / success / pending-auth — no prompt, no writes
# ---------------------------------------------------------------------------


class TestNoPromptNoWrite:
    def test_immediate_success_no_prompt(self, tmp_path: Path) -> None:
        # Given: both validators pass initially.
        prompt = _ScriptedPrompt(["should-not-ask"])
        edit_oc = _EditPort(_oc_result())
        edit_omo = _EditPort(_omo_result())

        # When
        outcome = run_repair(_request(
            tmp_path, initial=_validation(), prompt=prompt,
            edit_oc=edit_oc, edit_omo=edit_omo))

        # Then: READY immediately, zero prompts, zero edits.
        assert outcome.status is RepairStatus.READY
        assert outcome.rounds_used == 0
        assert outcome.edits == 0
        assert outcome.restorations == 0
        assert outcome.failure_kind is None
        assert outcome.exhausted is False
        assert prompt.ask_calls == 0
        assert edit_oc.calls == 0
        assert edit_omo.calls == 0

    @pytest.mark.parametrize("oc_fact,omo_fact", [
        (ValidationFact.TRANSPORT_FAILURE, OmoValidationFact.VALIDATED),
        (ValidationFact.SERVER_FAILURE, OmoValidationFact.VALIDATED),
        (ValidationFact.MARKER_MISSING, OmoValidationFact.VALIDATED),
        (ValidationFact.MARKER_MALFORMED, OmoValidationFact.VALIDATED),
        (ValidationFact.TIMEOUT_FAILURE, OmoValidationFact.VALIDATED),
        (ValidationFact.VERSION_FAILURE, OmoValidationFact.VALIDATED),
        (ValidationFact.CLEANUP_FAILURE, OmoValidationFact.VALIDATED),
        (ValidationFact.INVALID_ARGUMENT, OmoValidationFact.VALIDATED),
        (ValidationFact.UNKNOWN, OmoValidationFact.VALIDATED),
    ])
    def test_opencode_terminal_no_writes(
        self, tmp_path: Path, oc_fact: ValidationFact, omo_fact: OmoValidationFact,
    ) -> None:
        # Given: a terminal non-config OpenCode failure.
        prompt = _ScriptedPrompt(["should-not-ask"])
        edit_oc = _EditPort(_oc_result())

        # When
        outcome = run_repair(_request(
            tmp_path, initial=_validation(oc_fact, omo_fact),
            prompt=prompt, edit_oc=edit_oc))

        # Then: TERMINAL immediately, no prompt, no edits.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION
        assert prompt.ask_calls == 0
        assert edit_oc.calls == 0

    @pytest.mark.parametrize("omo_fact", [
        OmoValidationFact.DOCTOR_FAILURE,
        OmoValidationFact.DOCTOR_MALFORMED,
        OmoValidationFact.DOCTOR_MISSING_CHECK,
        OmoValidationFact.DOCTOR_TIMEOUT,
        OmoValidationFact.RUN_FAILURE,
        OmoValidationFact.RUN_MALFORMED,
        OmoValidationFact.RUN_FALSE_SUCCESS,
        OmoValidationFact.RUN_MARKER_MISSING,
        OmoValidationFact.RUN_FIELD_INVALID,
        OmoValidationFact.RUN_TIMEOUT,
    ])
    def test_omo_terminal_no_writes(
        self, tmp_path: Path, omo_fact: OmoValidationFact,
    ) -> None:
        # Given: a terminal non-config OMO failure.
        prompt = _ScriptedPrompt(["should-not-ask"])
        edit_omo = _EditPort(_omo_result())

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.MESSAGE_READY, omo_fact),
            prompt=prompt, edit_omo=edit_omo))

        # Then: TERMINAL immediately, no prompt, no edits.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION
        assert prompt.ask_calls == 0
        assert edit_omo.calls == 0

    def test_auth_deferred_no_prompt_no_write(self, tmp_path: Path) -> None:
        # Given: both validators deferred auth (no terminal/repairable failure).
        prompt = _ScriptedPrompt(["should-not-ask"])
        edit_oc = _EditPort(_oc_result())
        edit_omo = _EditPort(_omo_result())

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.AUTH_DEFERRED, OmoValidationFact.AUTH_DEFERRED),
            prompt=prompt, edit_oc=edit_oc, edit_omo=edit_omo))

        # Then: PENDING_AUTH, no prompt, no edits, failure_kind=None.
        assert outcome.status is RepairStatus.PENDING_AUTH
        assert outcome.failure_kind is None
        assert prompt.ask_calls == 0
        assert edit_oc.calls == 0
        assert edit_omo.calls == 0


class TestTerminalFailureKindPriority:
    def test_repairable_oc_never_hides_terminal_omo(self, tmp_path: Path) -> None:
        # Given: OC repairable (AUTH_FAILURE) + OMO terminal (RUN_FAILURE).
        prompt = _ScriptedPrompt(["should-not-ask"])

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.AUTH_FAILURE, OmoValidationFact.RUN_FAILURE),
            prompt=prompt))

        # Then: TERMINAL; the OMO terminal failure kind wins over the
        # repairable OpenCode failure kind.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.failure_kind is FailureKind.OMO_VALIDATION
        assert prompt.ask_calls == 0

    def test_terminal_oc_beats_repairable_omo(self, tmp_path: Path) -> None:
        # Given: OC terminal (TRANSPORT_FAILURE) + OMO repairable
        # (DOCTOR_CONFIG_INVALID).
        prompt = _ScriptedPrompt(["should-not-ask"])

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.TRANSPORT_FAILURE,
                OmoValidationFact.DOCTOR_CONFIG_INVALID),
            prompt=prompt))

        # Then: inverse priority — the OpenCode terminal kind is kept.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION
        assert prompt.ask_calls == 0

    def test_both_terminal_is_deterministic_omo_first(self, tmp_path: Path) -> None:
        # Given: both validators terminal.
        prompt = _ScriptedPrompt(["should-not-ask"])

        # When
        first = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.TRANSPORT_FAILURE, OmoValidationFact.RUN_FAILURE),
            prompt=prompt))
        second = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.TRANSPORT_FAILURE, OmoValidationFact.RUN_FAILURE),
            prompt=prompt))

        # Then: deterministic terminal-OMO-first kind on both runs.
        assert first.status is RepairStatus.TERMINAL
        assert first.failure_kind is FailureKind.OMO_VALIDATION
        assert second.failure_kind is FailureKind.OMO_VALIDATION


# ---------------------------------------------------------------------------
# repair loop: edit scenarios
# ---------------------------------------------------------------------------


class TestEditScenarios:
    def test_one_edit_success_opencode_auth(
        self, tmp_path: Path,
    ) -> None:
        # Given: OpenCode AUTH_FAILURE, OMO VALIDATED.
        prompt = _ScriptedPrompt(["edit"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When: edit fixes OpenCode; revalidate returns MESSAGE_READY.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: READY after one edit round.
        assert outcome.status is RepairStatus.READY
        assert outcome.rounds_used == 1
        assert outcome.edits == 1
        assert outcome.restorations == 0
        assert edit_oc.calls == 1
        assert prompt.ask_calls == 1

    def test_one_edit_success_omo_config_invalid(
        self, tmp_path: Path,
    ) -> None:
        # Given: OpenCode MESSAGE_READY, OMO DOCTOR_CONFIG_INVALID.
        prompt = _ScriptedPrompt(["edit"])
        edit_omo = _EditPort(_omo_result(committed=True))

        # When: edit fixes OMO; revalidate returns VALIDATED.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.MESSAGE_READY,
                OmoValidationFact.DOCTOR_CONFIG_INVALID),
            prompt=prompt, edit_omo=edit_omo,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: READY after one OMO edit round.
        assert outcome.status is RepairStatus.READY
        assert outcome.rounds_used == 1
        assert outcome.edits == 1
        assert edit_omo.calls == 1

    def test_model_not_found_edit_success(self, tmp_path: Path) -> None:
        # Given: OpenCode MODEL_NOT_FOUND.
        prompt = _ScriptedPrompt(["edit"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.MODEL_NOT_FOUND,
                OmoValidationFact.VALIDATED),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then
        assert outcome.status is RepairStatus.READY
        assert outcome.edits == 1

    def test_config_failure_edit_success(self, tmp_path: Path) -> None:
        # Given: OpenCode CONFIG_FAILURE.
        prompt = _ScriptedPrompt(["edit"])

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.CONFIG_FAILURE,
                OmoValidationFact.VALIDATED),
            prompt=prompt,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then
        assert outcome.status is RepairStatus.READY

    def test_both_domains_repairable_two_edits_success(
        self, tmp_path: Path,
    ) -> None:
        # Given: OpenCode AUTH_FAILURE + OMO DOCTOR_CONFIG_INVALID.
        prompt = _ScriptedPrompt(["edit", "edit"])
        edit_oc = _EditPort(_oc_result(committed=True))
        edit_omo = _EditPort(_omo_result(committed=True))

        # When: round 1 edits OpenCode, round 2 edits OMO.
        outcome = run_repair(_request(
            tmp_path,
            initial=RepairValidation(
                opencode=_oc_outcome(ValidationFact.AUTH_FAILURE),
                omo=_omo_outcome(OmoValidationFact.DOCTOR_CONFIG_INVALID),
            ),
            prompt=prompt, edit_oc=edit_oc, edit_omo=edit_omo,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY),
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.DOCTOR_CONFIG_INVALID),
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: READY after two edits.
        assert outcome.status is RepairStatus.READY
        assert outcome.rounds_used == 2
        assert outcome.edits == 2
        assert edit_oc.calls == 1
        assert edit_omo.calls == 1


# ---------------------------------------------------------------------------
# repair loop: stop, exhaustion, invalid, failed edit
# ---------------------------------------------------------------------------


class TestStopExhaustionInvalid:
    def test_stop_before_write(self, tmp_path: Path) -> None:
        # Given: repairable failure.
        prompt = _ScriptedPrompt(["stop"])
        edit_oc = _EditPort(_oc_result())

        # When: user immediately stops.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc))

        # Then: STOPPED, zero rounds, zero edits.
        assert outcome.status is RepairStatus.STOPPED
        assert outcome.rounds_used == 0
        assert outcome.edits == 0
        assert edit_oc.calls == 0
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION

    def test_two_round_exhaustion_no_third_prompt(
        self, tmp_path: Path,
    ) -> None:
        # Given: AUTH_FAILURE that never resolves.
        prompt = _ScriptedPrompt(["edit", "edit", "should-not-ask"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When: two failed edits, then loop must exit without third prompt.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: EXHAUSTED after exactly 2 rounds, 2 prompts.
        assert outcome.status is RepairStatus.EXHAUSTED
        assert outcome.exhausted is True
        assert outcome.rounds_used == 2
        assert outcome.edits == 2
        assert prompt.ask_calls == 2
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION

    def test_failed_edit_round_still_consumes_round(
        self, tmp_path: Path,
    ) -> None:
        # Given: edit port returns committed=False (edit failed).
        prompt = _ScriptedPrompt(["edit", "edit"])
        edit_oc = _EditPort(_oc_result(committed=False))

        # When: two failed edits → exhausted.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: EXHAUSTED, rounds=2, both edits counted.
        assert outcome.status is RepairStatus.EXHAUSTED
        assert outcome.edits == 2
        assert outcome.rounds_used == 2

    def test_invalid_choice_stops_immediately(self, tmp_path: Path) -> None:
        # Given: user enters an invalid choice.
        prompt = _ScriptedPrompt(["banana", "should-not-ask"])
        edit_oc = _EditPort(_oc_result())

        # When: invalid choice — must STOP immediately (one prompt only).
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc))

        # Then: STOPPED with zero rounds, zero edits, exactly 1 prompt.
        assert outcome.status is RepairStatus.STOPPED
        assert outcome.rounds_used == 0
        assert outcome.edits == 0
        assert edit_oc.calls == 0
        assert prompt.ask_calls == 1
        assert outcome.failure_kind is FailureKind.OPENCODE_VALIDATION

    def test_unavailable_restore_stops_immediately(
        self, tmp_path: Path,
    ) -> None:
        # Given: repairable, no prior committed edit (restore unavailable).
        prompt = _ScriptedPrompt(["restore", "should-not-ask"])
        edit_oc = _EditPort(_oc_result())

        # When: restore chosen before any edit — must STOP immediately.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc))

        # Then: STOPPED with zero rounds, one prompt.
        assert outcome.status is RepairStatus.STOPPED
        assert outcome.rounds_used == 0
        assert outcome.restorations == 0
        assert edit_oc.calls == 0
        assert prompt.ask_calls == 1


# ---------------------------------------------------------------------------
# restore scenarios
# ---------------------------------------------------------------------------


def _setup_oc_config(tmp_path: Path) -> Path:
    """Create a minimal OpenCode config file and return its path."""
    cfg = tmp_path / ".opencode" / "opencode.jsonc"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    _ = cfg.write_bytes(b'{\n  "model": "openai/gpt-4"\n}\n')
    return cfg


class TestRestoreScenarios:
    def test_edit_then_restore_exhausted(self, tmp_path: Path) -> None:
        # Given: OpenCode AUTH_FAILURE with a real config file on disk.
        cfg = _setup_oc_config(tmp_path)
        prompt = _ScriptedPrompt(["edit", "restore"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When: R1 edit (committed, snapshot taken); R2 restore (succeeds
        # because original file existed); both rounds still AUTH_FAILURE.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: 2 rounds (edit + restore), exhausted, no third prompt.
        assert outcome.status is RepairStatus.EXHAUSTED
        assert outcome.rounds_used == 2
        assert outcome.edits == 1
        assert outcome.restorations == 1
        assert prompt.ask_calls == 2
        # Dynamic choice advertising: restore absent before edit, present after.
        assert "restore" not in prompt.prompts[0]
        assert "restore" in prompt.prompts[1]

    def test_edit_then_restore_reaches_success(
        self, tmp_path: Path,
    ) -> None:
        # Given: edit makes things worse; restore brings back the original
        # which revalidates as MESSAGE_READY (transient cleared).
        cfg = _setup_oc_config(tmp_path)
        prompt = _ScriptedPrompt(["edit", "restore"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When: R1 edit → still failing; R2 restore → success.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: READY via restore in round 2.
        assert outcome.status is RepairStatus.READY
        assert outcome.rounds_used == 2
        assert outcome.edits == 1
        assert outcome.restorations == 1

    def test_second_edit_success_after_failed_first(
        self, tmp_path: Path,
    ) -> None:
        # Given: R1 edit fails (committed=False); R2 edit succeeds.
        prompt = _ScriptedPrompt(["edit", "edit"])

        # When: R1 edit committed=False → revalidate still failing (rounds=1);
        # R2 edit committed=True → revalidate READY (rounds=2).
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt,
            edit_oc=_SequenceEditPort(
                _oc_result(committed=False), _oc_result(committed=True)),
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: READY after 2 rounds; first edit failed.
        assert outcome.status is RepairStatus.READY
        assert outcome.rounds_used == 2
        assert outcome.edits == 2
        assert outcome.restorations == 0


# ---------------------------------------------------------------------------
# integration: real ConfigTransaction restore with byte-exact verification
# ---------------------------------------------------------------------------


class TestIntegrationRestore:
    def test_restore_exact_bytes_real_transaction(
        self, tmp_path: Path,
    ) -> None:
        # Given: a real OpenCode config file with original bytes.
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        omo = tmp_path / ".omo" / "omo.jsonc"
        omo.parent.mkdir(parents=True, exist_ok=True)
        original_oc = b'{\n  "model": "openai/gpt-4",\n  "provider": {}\n}\n'
        _ = cfg.write_bytes(original_oc)
        _ = omo.write_bytes(b'{"$schema": "test"}\n')
        edited_oc = b'{\n  "model": "openai/gpt-4o",\n  "provider": {}\n}\n'

        def _real_edit_oc() -> OpencodeConfigResult:
            tx = ConfigTransaction(tmp_path)
            tx_id = tx.begin((cfg,))
            _ = tx.commit(tx_id, {cfg: edited_oc})
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit", "restore"])

        # When: round 1 edits (real transaction); round 2 restores.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg, omo_target=omo,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_real_edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: exhausted after 2 rounds, bytes byte-exact restored.
        assert outcome.status is RepairStatus.EXHAUSTED
        assert outcome.rounds_used == 2
        assert outcome.edits == 1
        assert outcome.restorations == 1
        assert cfg.read_bytes() == original_oc

    def test_restore_not_available_when_original_absent(
        self, tmp_path: Path,
    ) -> None:
        # Given: target file does NOT exist (no original to restore to).
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        omo = tmp_path / ".omo" / "omo.jsonc"
        omo.parent.mkdir(parents=True, exist_ok=True)

        def _edit_then_create() -> OpencodeConfigResult:
            _ = cfg.write_bytes(b'{"model": "new"}\n')
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit", "restore", "stop"])

        # When: edit creates file (original absent); restore must fail closed.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg, omo_target=omo,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_edit_then_create,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: restore was unavailable (no original), user stopped.
        assert outcome.status is RepairStatus.STOPPED
        assert outcome.restorations == 0


# ---------------------------------------------------------------------------
# interrupted recovery called before any edit
# ---------------------------------------------------------------------------


class TestRecoveryFirst:
    def test_recover_interrupted_before_edit(self, tmp_path: Path) -> None:
        # Given: an interrupted (COMMITTING) transaction with corrupted file.
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"model": "original"}\n'
        _ = cfg.write_bytes(original)

        tx = ConfigTransaction(tmp_path)
        _ = tx.begin((cfg,))  # state is now COMMITTING
        _ = cfg.write_bytes(b'{"model": "PARTIAL"}')  # simulate crash

        edit_calls: list[bytes] = []

        def _verify_recovery_first() -> OpencodeConfigResult:
            # At edit time, recovery must have restored original bytes.
            edit_calls.append(cfg.read_bytes())
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit"])

        # When
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_verify_recovery_first,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: recovery restored original BEFORE edit was called.
        assert outcome.status is RepairStatus.READY
        assert len(edit_calls) == 1
        assert edit_calls[0] == original  # recovery happened first


# ---------------------------------------------------------------------------
# secret hygiene: canary never appears in outcome/prompt/repr
# ---------------------------------------------------------------------------


class TestSecretHygiene:
    def test_canary_absent_from_outcome_and_prompt(
        self, tmp_path: Path,
    ) -> None:
        # Given: config file contains a canary key; failure_detail is redacted.
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        canary_bytes = (
            b'{"provider":{"openai":{"options":{"apiKey":"'
            + _CANARY.encode()
            + b'"}}}}\n'
        )
        _ = cfg.write_bytes(canary_bytes)
        omo = tmp_path / ".omo" / "omo.jsonc"
        omo.parent.mkdir(parents=True, exist_ok=True)

        safe_detail = SafeDetail(redact_sensitive_text(
            "auth failed; key referenced in provider config"))

        prompt = _ScriptedPrompt(["edit", "stop"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When: repair runs; snapshot reads canary bytes.
        outcome = run_repair(RepairRequest(
            project_root=tmp_path,
            opencode_target=cfg,
            omo_target=omo,
            prompt=prompt,
            initial=RepairValidation(
                opencode=ValidationOutcome(
                    fact=ValidationFact.AUTH_FAILURE,
                    server_url=_URL,
                    failure_kind=FailureKind.OPENCODE_VALIDATION,
                    failure_detail=safe_detail,
                ),
                omo=_omo_outcome(OmoValidationFact.VALIDATED),
            ),
            revalidate_opencode=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED)),
            edit_opencode=edit_oc,
            edit_omo=_EditPort(_omo_result()),
        ))

        # Then: canary appears in ZERO of outcome/prompt/repr.
        outcome_repr = repr(outcome)
        outcome_str = str(outcome)
        all_prompts = " ".join(prompt.prompts)
        for text in (outcome_repr, outcome_str, all_prompts,
                     str(outcome.safe_detail)):
            assert _CANARY not in text, (
                f"canary leaked in: {text[:200]}")

    def test_outcome_does_not_expose_snapshot_bytes(
        self, tmp_path: Path,
    ) -> None:
        # Given: config with sensitive bytes is snapshotted during repair.
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        secret_marker = "SECRET_BLOB_xyz789"
        _ = cfg.write_bytes(
            b'{"key":"' + secret_marker.encode() + b'"}\n')
        omo = tmp_path / ".omo" / "omo.jsonc"
        omo.parent.mkdir(parents=True, exist_ok=True)

        prompt = _ScriptedPrompt(["stop"])

        # When
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg, omo_target=omo,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt))

        # Then: snapshot was never created (stop before edit); no leak.
        assert outcome.status is RepairStatus.STOPPED
        assert secret_marker not in repr(outcome)


# ---------------------------------------------------------------------------
# transaction failure during restore
# ---------------------------------------------------------------------------


class TestTransactionFailure:
    def test_restore_with_nonexistent_backup_directory(
        self, tmp_path: Path,
    ) -> None:
        # Given: a committed edit exists but we simulate a broken restore
        # by ensuring the snapshot target path is valid but the project
        # root has an incomplete transaction from a different source.
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"model": "original"}\n'
        _ = cfg.write_bytes(original)
        omo = tmp_path / ".omo" / "omo.jsonc"
        omo.parent.mkdir(parents=True, exist_ok=True)

        edit_calls: list[bool] = []

        def _edit() -> OpencodeConfigResult:
            edit_calls.append(True)
            _ = cfg.write_bytes(b'{"model": "edited"}\n')
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit", "restore"])

        # When: edit commits (snapshot taken), then restore via real tx.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg, omo_target=omo,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_edit,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: restore succeeded via real ConfigTransaction.
        assert outcome.restorations == 1
        assert cfg.read_bytes() == original


# ---------------------------------------------------------------------------
# all repairable OpenCode facts reach REPAIRABLE category
# ---------------------------------------------------------------------------


class TestRepairableFactsReachRepair:
    @pytest.mark.parametrize("oc_fact", [
        ValidationFact.AUTH_FAILURE,
        ValidationFact.MODEL_NOT_FOUND,
        ValidationFact.CONFIG_FAILURE,
    ])
    def test_opencode_repairable_fact_prompts(
        self, tmp_path: Path, oc_fact: ValidationFact,
    ) -> None:
        # Given: a repairable OpenCode fact paired with OMO VALIDATED.
        prompt = _ScriptedPrompt(["stop"])

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(oc_fact, OmoValidationFact.VALIDATED),
            prompt=prompt))

        # Then: a prompt was issued (category was REPAIRABLE).
        assert prompt.ask_calls == 1
        assert outcome.status is RepairStatus.STOPPED

    def test_omo_repairable_fact_prompts(self, tmp_path: Path) -> None:
        # Given: OMO DOCTOR_CONFIG_INVALID.
        prompt = _ScriptedPrompt(["stop"])

        # When
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(
                ValidationFact.MESSAGE_READY,
                OmoValidationFact.DOCTOR_CONFIG_INVALID),
            prompt=prompt))

        # Then
        assert prompt.ask_calls == 1
        assert outcome.status is RepairStatus.STOPPED


# ---------------------------------------------------------------------------
# stop preserves last user-authorized committed state
# ---------------------------------------------------------------------------


class TestStopAfterEdit:
    def test_stop_after_committed_edit_preserves_state(
        self, tmp_path: Path,
    ) -> None:
        # Given: AUTH_FAILURE; user edits (committed) then stops.
        cfg = _setup_oc_config(tmp_path)
        prompt = _ScriptedPrompt(["edit", "stop"])
        edit_oc = _EditPort(_oc_result(committed=True))

        # When: edit commits, revalidate still failing, user stops.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=edit_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: STOPPED after 1 round; committed edit preserved (not restored).
        assert outcome.status is RepairStatus.STOPPED
        assert outcome.rounds_used == 1
        assert outcome.edits == 1
        assert outcome.restorations == 0
        assert edit_oc.calls == 1


# ---------------------------------------------------------------------------
# closure compatibility: edit ports wrapping real configure_* functions
# ---------------------------------------------------------------------------


class TestClosureCompatibility:
    """Verify edit-port return types match configure_opencode/configure_omo.

    The full workflow closure binding (constructing OpencodeConfigRequest /
    OmoConfigRequest with real sub-ports, wiring them into RepairRequest) is
    owned by Task 15 (workflow.py). These tests lock the return-type
    compatibility so Task 15 only needs the closure binding, not type changes.
    """

    def test_configure_opencode_result_type_matches_edit_port(
        self, tmp_path: Path,
    ) -> None:
        # Given: a real configure_opencode closure wired as the edit port.
        from seam_init.models import ModelId, ProviderId, ProviderSelection
        from seam_init.opencode_config import OpencodeConfigRequest
        from seam_init.opencode_discovery import JsonDict

        @final
        class _FakeRuntime:
            def __init__(self) -> None:
                target = (tmp_path / ".opencode" / "opencode.jsonc").resolve()
                self._config: JsonDict = {
                    "config_files": [str(target)],
                    "provider": {"openai": {}},
                }

            def debug_config(self) -> JsonDict | None:
                return self._config

            def debug_models(
                self, config_bytes: bytes | None = None,
            ) -> tuple[str, ...] | None:
                _ = config_bytes
                return ("openai/gpt-4",)

        @final
        class _FakeSchema:
            def validate(self, config_bytes: bytes) -> bool:
                _ = config_bytes
                return True

        @final
        class _ConfigPrompt:
            def __init__(self) -> None:
                self._confirms = [True]

            def ask(self, prompt: str, *, default: str | None = None) -> str:
                _ = prompt, default
                raise AssertionError("configure_opencode should not ask")

            def secret(self, prompt: str) -> str:
                _ = prompt
                raise AssertionError("configure_opencode should not secret")

            def confirm(self, prompt: str, *, default: bool = False) -> bool:
                _ = prompt, default
                if self._confirms:
                    return self._confirms.pop(0)
                return False

        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        _ = cfg.write_bytes(b'{\n  "provider": {"openai": {}}\n}\n')

        def _real_configure_oc() -> OpencodeConfigResult:
            from seam_init.opencode_config import ConfigTargetPolicy, configure_opencode
            req = OpencodeConfigRequest(
                project_root=tmp_path,
                prompt=_ConfigPrompt(),
                runtime=_FakeRuntime(),
                schema_validator=_FakeSchema(),
                selection=ProviderSelection(
                    provider_id=ProviderId("openai"),
                    model_id=ModelId("gpt-4"),
                ),
                target_policy=ConfigTargetPolicy.PROJECT_DOT_OPENCODE,
            )
            return configure_opencode(req)

        repair_prompt = _ScriptedPrompt(["edit"])

        # When: repair loop calls the real configure_opencode closure.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=repair_prompt,
            edit_oc=_real_configure_oc,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.MESSAGE_READY)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: READY; real configure_opencode committed the config.
        import json
        assert outcome.status is RepairStatus.READY
        assert outcome.edits == 1
        committed = json.loads(cfg.read_bytes())
        assert committed["model"] == "openai/gpt-4"


# ---------------------------------------------------------------------------
# R1: RepairOutcome legal-state invariants
# ---------------------------------------------------------------------------


class TestRepairOutcomeInvariants:
    """RepairOutcome __post_init__ rejects impossible public states."""

    @staticmethod
    def _outcome(
        *,
        status: RepairStatus = RepairStatus.READY,
        rounds_used: int = 0,
        edits: int = 0,
        restorations: int = 0,
        final: RepairValidation | None = None,
        failure_kind: FailureKind | None = None,
        exhausted: bool = False,
    ) -> RepairOutcome:
        return RepairOutcome(
            status=status, rounds_used=rounds_used, edits=edits,
            restorations=restorations,
            final=final or _validation(),
            failure_kind=failure_kind, exhausted=exhausted,
            safe_detail=SafeDetail("test"),
        )

    def test_valid_ready_passes(self) -> None:
        outcome = self._outcome()
        assert outcome.status is RepairStatus.READY

    def test_valid_stopped_passes(self) -> None:
        outcome = self._outcome(
            status=RepairStatus.STOPPED,
            failure_kind=FailureKind.OPENCODE_VALIDATION,
            final=_validation(ValidationFact.AUTH_FAILURE))
        assert outcome.status is RepairStatus.STOPPED

    def test_rounds_above_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="rounds_used"):
            _ = self._outcome(rounds_used=3)

    def test_negative_edits_rejected(self) -> None:
        with pytest.raises(ValueError, match="nonnegative"):
            _ = self._outcome(
                status=RepairStatus.STOPPED,
                rounds_used=1, edits=-1,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_edits_exceed_rounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            _ = self._outcome(
                status=RepairStatus.STOPPED,
                rounds_used=1, edits=2,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_ready_with_failure_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not carry"):
            _ = self._outcome(failure_kind=FailureKind.OPENCODE_VALIDATION)

    def test_stopped_without_failure_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires failure_kind"):
            _ = self._outcome(status=RepairStatus.STOPPED)

    def test_exhausted_flag_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="exhausted"):
            _ = self._outcome(
                status=RepairStatus.STOPPED, exhausted=True,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_edits_plus_restorations_exceed_rounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="edits.*restorations.*rounds"):
            _ = self._outcome(
                status=RepairStatus.EXHAUSTED,
                rounds_used=2, edits=1, restorations=2, exhausted=True,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_exhausted_requires_max_rounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="EXHAUSTED requires"):
            _ = self._outcome(
                status=RepairStatus.EXHAUSTED,
                rounds_used=1, exhausted=True,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_ready_with_non_success_final_rejected(self) -> None:
        with pytest.raises(ValueError, match="READY.*SUCCESS"):
            _ = self._outcome(
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_pending_auth_with_non_pending_final_rejected(self) -> None:
        with pytest.raises(ValueError, match="PENDING_AUTH"):
            _ = self._outcome(
                status=RepairStatus.PENDING_AUTH,
                final=_validation(ValidationFact.AUTH_FAILURE))

    def test_stopped_with_non_repairable_final_rejected(self) -> None:
        with pytest.raises(ValueError, match="STOPPED.*REPAIRABLE"):
            _ = self._outcome(
                status=RepairStatus.STOPPED,
                failure_kind=FailureKind.OPENCODE_VALIDATION)

    def test_valid_edit_plus_restore_outcome_constructs(self) -> None:
        outcome = self._outcome(
            status=RepairStatus.EXHAUSTED,
            rounds_used=2, edits=1, restorations=1, exhausted=True,
            failure_kind=FailureKind.OPENCODE_VALIDATION,
            final=_validation(ValidationFact.AUTH_FAILURE))
        assert outcome.edits == 1
        assert outcome.restorations == 1


# ---------------------------------------------------------------------------
# R1: forged SafeDetail canary regression
# ---------------------------------------------------------------------------


class TestForgedSafeDetailCanary:
    def test_forged_canary_redacted_in_prompt(self, tmp_path: Path) -> None:
        # Given: a forged SafeDetail containing raw canary (not redacted).
        forged = SafeDetail(f"auth failed; apikey={_CANARY}; retry")
        prompt = _ScriptedPrompt(["stop"])
        initial = RepairValidation(
            opencode=ValidationOutcome(
                fact=ValidationFact.AUTH_FAILURE,
                server_url=_URL,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                failure_detail=forged,
            ),
            omo=_omo_outcome(OmoValidationFact.VALIDATED),
        )

        # When: repair prompts — the boundary must re-redact.
        outcome = run_repair(_request(
            tmp_path, initial=initial, prompt=prompt))

        # Then: canary is absent from all prompt text.
        assert _CANARY not in " ".join(prompt.prompts)
        assert outcome.status is RepairStatus.STOPPED


# ---------------------------------------------------------------------------
# R1: transaction failure → TERMINAL (no exception escapes)
# ---------------------------------------------------------------------------


class TestTransactionFailureTerminal:
    def test_conflicting_committing_before_restore(
        self, tmp_path: Path,
    ) -> None:
        # Given: a config file and an edit callback that commits then injects
        # a conflicting COMMITTING state so restore's begin() will throw.
        cfg = _setup_oc_config(tmp_path)
        omo = tmp_path / ".omo" / "omo.jsonc"
        omo.parent.mkdir(parents=True, exist_ok=True)

        def _edit_then_inject_conflict() -> OpencodeConfigResult:
            tx = ConfigTransaction(tmp_path)
            tx_id = tx.begin((cfg,))
            _ = tx.commit(tx_id, {cfg: b'{"model": "edited"}\n'})
            tx2 = ConfigTransaction(tmp_path)
            _ = tx2.begin((cfg,))
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit", "restore"])

        # When: R1 edit commits but leaves COMMITTING; R2 restore fails.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg, omo_target=omo,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_edit_then_inject_conflict,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: TERMINAL — no exception escapes; restore attempted but failed.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.restorations == 0
        assert outcome.rounds_used == 2
        assert outcome.edits == 1

    def test_corrupted_state_recovery_returns_terminal(
        self, tmp_path: Path,
    ) -> None:
        # Given: a corrupted .seam-init/state.json that triggers recovery error.
        state_dir = tmp_path / ".seam-init"
        state_dir.mkdir(parents=True, exist_ok=True)
        _ = (state_dir / "state.json").write_text("INVALID JSON{")

        prompt = _ScriptedPrompt([])

        # When: recovery fails → TERMINAL with zero prompts.
        outcome = run_repair(_request(
            tmp_path,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt))

        # Then
        assert outcome.status is RepairStatus.TERMINAL
        assert prompt.ask_calls == 0


# ---------------------------------------------------------------------------
# R1: on-disk last-authorized byte preservation
# ---------------------------------------------------------------------------


class TestOnDiskLastAuthorized:
    def test_stop_preserves_edited_bytes(self, tmp_path: Path) -> None:
        # Given: a real config file; edit mutates it.
        cfg = _setup_oc_config(tmp_path)
        edited = b'{"model": "edited-by-user"}\n'

        def _real_edit() -> OpencodeConfigResult:
            _ = cfg.write_bytes(edited)
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit", "stop"])

        # When: edit commits then user stops.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_real_edit,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: STOPPED; edited bytes remain on disk (not restored).
        assert outcome.status is RepairStatus.STOPPED
        assert cfg.read_bytes() == edited

    def test_exhaustion_preserves_last_edit_bytes(
        self, tmp_path: Path,
    ) -> None:
        # Given: two real edits that both remain failing.
        cfg = _setup_oc_config(tmp_path)
        first = b'{"model": "first-edit"}\n'
        second = b'{"model": "second-edit"}\n'
        queue = [first, second]

        def _real_edit() -> OpencodeConfigResult:
            _ = cfg.write_bytes(queue.pop(0))
            return _oc_result(committed=True)

        prompt = _ScriptedPrompt(["edit", "edit"])

        # When: two edits exhaust; last authorized edit stays.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_real_edit,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: EXHAUSTED; bytes equal the second (last) edit, not original.
        assert outcome.status is RepairStatus.EXHAUSTED
        assert cfg.read_bytes() == second


# ---------------------------------------------------------------------------
# R3: edit callback OSError → typed TERMINAL
# ---------------------------------------------------------------------------


class TestEditCallbackOSError:
    def test_edit_oserror_returns_terminal(self, tmp_path: Path) -> None:
        # Given: edit callback raises OSError (disk full, permissions, etc.).
        cfg = _setup_oc_config(tmp_path)
        prompt = _ScriptedPrompt(["edit"])

        def _edit_oserror() -> OpencodeConfigResult:
            raise OSError("disk full")

        # When: edit raises OSError → must produce typed TERMINAL.
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_edit_oserror,
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: TERMINAL with bounded counts, no exception escape.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.edits == 1
        assert outcome.rounds_used == 1


# ---------------------------------------------------------------------------
# R3: restore filesystem/path failures → typed TERMINAL
# ---------------------------------------------------------------------------


class TestRestoreFilesystemFailure:
    def test_restore_oserror_from_begin_returns_terminal(
        self, tmp_path: Path,
    ) -> None:
        # Given: .seam-init is a file (not a dir), so begin()'s mkdir fails.
        cfg = _setup_oc_config(tmp_path)
        _ = (tmp_path / ".seam-init").write_text("")
        prompt = _ScriptedPrompt(["edit", "restore"])

        # When: R1 edit commits (fake, no I/O); R2 restore's begin()
        # tries to mkdir .seam-init → FileExistsError (OSError subclass).
        outcome = run_repair(_request(
            tmp_path, opencode_target=cfg,
            initial=_validation(ValidationFact.AUTH_FAILURE),
            prompt=prompt, edit_oc=_EditPort(_oc_result(committed=True)),
            revalidate_oc=_Revalidator(
                _oc_outcome(ValidationFact.AUTH_FAILURE),
                _oc_outcome(ValidationFact.AUTH_FAILURE)),
            revalidate_omo=_Revalidator(
                _omo_outcome(OmoValidationFact.VALIDATED))))

        # Then: TERMINAL — no exception escapes.
        assert outcome.status is RepairStatus.TERMINAL
        assert outcome.restorations == 0
        assert outcome.rounds_used == 2

    def test_restore_path_outside_root_returns_terminal(
        self, tmp_path: Path,
    ) -> None:
        # Given: opencode_target is an absolute path outside project_root.
        outside = (tmp_path / ".." / "repair_outside.jsonc").resolve()
        _ = outside.write_bytes(b'{"model": "old"}\n')
        prompt = _ScriptedPrompt(["edit", "restore"])
        try:
            # When: R1 edit commits (fake); R2 restore's begin() calls
            # _relative_of(outside) → ValueError from relative_to.
            outcome = run_repair(_request(
                tmp_path, opencode_target=outside,
                initial=_validation(ValidationFact.AUTH_FAILURE),
                prompt=prompt,
                edit_oc=_EditPort(_oc_result(committed=True)),
                revalidate_oc=_Revalidator(
                    _oc_outcome(ValidationFact.AUTH_FAILURE),
                    _oc_outcome(ValidationFact.AUTH_FAILURE)),
                revalidate_omo=_Revalidator(
                    _omo_outcome(OmoValidationFact.VALIDATED))))

            # Then: TERMINAL — no exception escapes.
            assert outcome.status is RepairStatus.TERMINAL
            assert outcome.restorations == 0
        finally:
            outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# R3: redact-before-truncate boundary canary
# ---------------------------------------------------------------------------


class TestRedactBeforeTruncate:
    def test_boundary_canary_absent_from_prompt(self, tmp_path: Path) -> None:
        # Given: a canary at the 200-char truncation boundary in failure_detail.
        # 190 chars of padding + space (word boundary) + canary.
        # Old bound-first: raw[:200] = padding + " " + "sk-canary" (9 chars).
        # sk- pattern needs {16,} after sk-; only 7 chars present → no match.
        # Redact-first: full canary seen → <REDACTED_API_KEY>.
        padding = "x" * 190
        canary_detail = SafeDetail(padding + " " + _CANARY)
        prompt = _ScriptedPrompt(["stop"])
        initial = RepairValidation(
            opencode=ValidationOutcome(
                fact=ValidationFact.AUTH_FAILURE,
                server_url=_URL,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                failure_detail=canary_detail,
            ),
            omo=_omo_outcome(OmoValidationFact.VALIDATED),
        )

        # When
        run_repair(_request(
            tmp_path, initial=initial, prompt=prompt))

        # Then: canary fragment absent from prompt text.
        assert "sk-canary" not in " ".join(prompt.prompts)

    def test_boundary_canary_absent_from_outcome_detail(
        self, tmp_path: Path,
    ) -> None:
        # Given: canary at the 300-char truncation boundary in outcome detail.
        padding = "x" * 290
        canary_detail = SafeDetail(padding + " " + _CANARY)
        prompt = _ScriptedPrompt(["stop"])
        initial = RepairValidation(
            opencode=ValidationOutcome(
                fact=ValidationFact.AUTH_FAILURE,
                server_url=_URL,
                failure_kind=FailureKind.OPENCODE_VALIDATION,
                failure_detail=canary_detail,
            ),
            omo=_omo_outcome(OmoValidationFact.VALIDATED),
        )

        # When
        outcome = run_repair(_request(
            tmp_path, initial=initial, prompt=prompt))

        # Then: canary fragment absent from outcome detail and repr.
        assert "sk-canary" not in str(outcome.safe_detail)
        assert "sk-canary" not in repr(outcome)


# ---------------------------------------------------------------------------
# R3: RepairRequest repr excludes prompt/callback internals
# ---------------------------------------------------------------------------


class TestRepairRequestRepr:
    def test_repr_excludes_prompt_and_callbacks(
        self, tmp_path: Path,
    ) -> None:
        # Given: a prompt object with a dangerous repr exposing a canary.
        canary = "sk-repr-canary-key-9999"

        @final
        class _DangerousPrompt:
            def __repr__(self) -> str:
                return f"Danger({canary})"

            def ask(self, prompt: str, *, default: str | None = None) -> str:
                _ = prompt, default
                return "stop"

            def secret(self, prompt: str) -> str:
                _ = prompt
                return ""

            def confirm(self, prompt: str, *, default: bool = False) -> bool:
                _ = prompt, default
                return False

        @final
        class _DangerousCallback:
            def __repr__(self) -> str:
                return f"Call({canary})"

            def __call__(self) -> OpencodeConfigResult:
                return _oc_result()

        @final
        class _DangerousOmoCallback:
            def __repr__(self) -> str:
                return f"OmoCall({canary})"

            def __call__(self) -> OmoConfigResult:
                return _omo_result()

        @final
        class _DangerousOcRevalidator:
            def __repr__(self) -> str:
                return f"OcReval({canary})"

            def __call__(self) -> ValidationOutcome:
                return _oc_outcome(ValidationFact.MESSAGE_READY)

        @final
        class _DangerousOmoRevalidator:
            def __repr__(self) -> str:
                return f"OmoReval({canary})"

            def __call__(self) -> OmoValidationOutcome:
                return _omo_outcome(OmoValidationFact.VALIDATED)

        request = RepairRequest(
            project_root=tmp_path,
            opencode_target=tmp_path / "oc",
            omo_target=tmp_path / "omo",
            prompt=_DangerousPrompt(),
            initial=_validation(ValidationFact.AUTH_FAILURE),
            revalidate_opencode=_DangerousOcRevalidator(),
            revalidate_omo=_DangerousOmoRevalidator(),
            edit_opencode=_DangerousCallback(),
            edit_omo=_DangerousOmoCallback(),
        )

        # When/Then: canary must not appear in repr.
        assert canary not in repr(request)
