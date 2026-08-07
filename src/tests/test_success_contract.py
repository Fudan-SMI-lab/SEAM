"""RED tests for #17 boolean success contract (T10).

#17 contract (user decision ②) — success is a 5-term boolean AND::

    success = entry_exit_ok
              AND required_artifacts_valid
              AND platform_policy_satisfied
              AND required_gates_passed
              AND review_policy_satisfied

``max_phase5_iter`` reached is ONLY a safety cap: it must NOT be treated as
failure (current flowchart documents it as ❌ 失败 at
``docs/framework-architecture-flowchart.md`` L144/555/645/704) and NOT as
success — it is simply not a success/failure determinant.

Current-behavior divergences these RED tests lock:

1. ``orchestrator.py:479-483`` ``Orchestrator._phase_5_succeeded`` decides
   success with a 2-field legacy shortcut — ``phase_5_output["success"] is True``
   plus a status whitelist ``{"success", "succeeded", "pass", "passed"}``. It
   NEVER consults the five contract terms, so any of them being ``False`` is
   silently ignored.

2. Dual definition of phase-5 success: ``repair_loop.py:2453``
   ``RepairLoopEngine._build_result`` derives ``success = status in
   {"success", "passed_with_reviews"}``, but ``_phase_5_succeeded``'s whitelist
   omits ``"passed_with_reviews"`` — the two components disagree on the SAME
   phase5 outcome dict.

3. ``max_iterations`` is hard-coded as failure (``repair_loop.py:1237/1926``
   default ``status = "max_iterations"`` → ``_build_result`` ``success=False``;
   flowchart L144/555/645/704 → ``false`` / ❌ 失败). Under #17 the safety cap
   must not participate in the success boolean at all.

4. ``platform_policy_satisfied`` is never wired into the orchestrator's success
   path, although T8 already provides the predicate
   ``platform_policy.satisfies_platform_requirements`` (``platform_policy.py:979``).

RED semantics: these tests must FAIL against current code for the correct
reason — the boolean contract is violated — and turn GREEN only when T12
implements the 5-term AND in the success-determination path. No source file is
modified here.
"""

from core import orchestrator
from core import platform_policy

# #17 contract terms — success is the AND of exactly these five.
_CONTRACT_TERMS = (
    "entry_exit_ok",
    "required_artifacts_valid",
    "platform_policy_satisfied",
    "required_gates_passed",
    "review_policy_satisfied",
)


def _contract_success(output: dict) -> bool:
    """The #17 boolean contract: success is the 5-term AND."""
    return all(output.get(term) is True for term in _CONTRACT_TERMS)


def _phase5_output(**overrides: object) -> dict:
    """Build a canonical phase5 output with all five contract terms satisfied.

    Callers override individual terms to construct a violating scenario.
    """
    base: dict = {
        "success": True,
        "status": "success",
        "final_exit_code": 0,
        "iteration_count": 1,
        "entry_exit_ok": True,
        "required_artifacts_valid": True,
        "platform_policy_satisfied": True,
        "required_gates_passed": True,
        "review_policy_satisfied": True,
    }
    base.update(overrides)
    return base


class TestSuccessDeterminationIsFiveTermAnd:
    """#17: success must be the 5-term boolean AND.

    Current ``orchestrator.py:479-483`` decides success from the legacy
    ``success``/``status`` shortcut and never reads the five contract terms.
    """

    def test_success_equals_five_term_and(self):
        """RED: required_gates_passed=False must fail even though success/status say success."""
        output = _phase5_output(required_gates_passed=False)
        contract = _contract_success(output)
        actual = orchestrator.Orchestrator._phase_5_succeeded(output)
        assert actual == contract, (
            "#17 boolean contract: success = entry_exit_ok AND "
            "required_artifacts_valid AND platform_policy_satisfied AND "
            "required_gates_passed AND review_policy_satisfied. "
            f"Scenario has required_gates_passed=False → contract success={contract}, "
            f"but orchestrator.py:479-483 _phase_5_succeeded returned {actual} "
            "(it only checks the legacy success flag + status whitelist and ignores "
            "required_gates_passed — the 5-term AND is not implemented)."
        )


class TestExitZeroWithInvalidArtifactsIsFailure:
    """#17 assertion 2: exit0 alone is NOT success — artifacts must be valid too."""

    def test_exit0_but_invalid_artifacts_is_failure(self):
        """RED: final_exit_code == 0 with required_artifacts_valid=False must be failure."""
        output = _phase5_output(final_exit_code=0, required_artifacts_valid=False)
        assert orchestrator.Orchestrator._phase_5_succeeded(output) is False, (
            "#17 boolean contract: exit0 (entry_exit_ok) is ONE term of the AND; "
            "required_artifacts_valid=False forces success=False. "
            "orchestrator.py:479-483 _phase_5_succeeded returned True for "
            "{success=True, status='success', final_exit_code=0, "
            "required_artifacts_valid=False} — it never consults "
            "required_artifacts_valid, so exit0-with-invalid-artifacts is wrongly "
            "reported as success."
        )


class TestMaxPhase5IterIsSafetyCapNotDeterminant:
    """#17 assertion 3: max_phase5_iter reached ≠ success/failure.

    The flowchart (L144/555/645/704) and ``repair_loop.py:1237/1926`` treat
    ``status="max_iterations"`` as failure (success=False). Under #17 the cap is
    ONLY a safety upper bound: the success boolean is decided solely by the
    5-term AND.
    """

    def test_cap_reached_with_terms_satisfied_is_not_failure(self):
        """RED: all 5 terms satisfied + safety cap reached → success (cap is not a determinant)."""
        output = _phase5_output(
            success=False,  # legacy field repair_loop._build_result sets for max_iterations
            status="max_iterations",
            iteration_count=5,
            max_phase5_iter=5,
            final_exit_code=1,
        )
        assert _contract_success(output) is True  # sanity: 5-term AND is satisfied
        assert orchestrator.Orchestrator._phase_5_succeeded(output) is True, (
            "#17: reaching max_phase5_iter is a SAFETY CAP, not a failure/success "
            "determinant. flowchart L144/555/645/704 mark status='max_iterations' as "
            "❌ 失败/false, and orchestrator.py:479-483 _phase_5_succeeded hard-codes "
            "success=False whenever phase_5_output['success'] is not True — so a "
            "run whose five contract terms are ALL satisfied is reported failed "
            "solely because the iteration cap was hit. success must be decided "
            "exclusively by the 5-term AND (satisfied here → True)."
        )


class TestPhase5OutcomeConsistency:
    """#17 assertion 4: orchestrator must agree with the phase5 outcome determination.

    Dual-definition divergence: ``repair_loop.py:2453`` derives
    ``success = status in {"success", "passed_with_reviews"}``, while
    ``orchestrator.py:479-483``'s whitelist omits ``passed_with_reviews``.
    """

    def test_passed_with_reviews_agrees_with_repair_loop_outcome(self):
        """RED: passed_with_reviews is success per repair_loop.py:2453 but failure per orchestrator."""
        output = _phase5_output(status="passed_with_reviews")
        assert orchestrator.Orchestrator._phase_5_succeeded(output) is True, (
            "Dual-definition divergence: repair_loop.py:2453 _build_result derives "
            "success = status in {'success', 'passed_with_reviews'} — so the phase5 "
            "outcome for status='passed_with_reviews' is success=True. But "
            "orchestrator.py:479-483 _phase_5_succeeded excludes "
            "'passed_with_reviews' from its whitelist "
            "{success, succeeded, pass, passed} and returns False. The SAME phase5 "
            "outcome dict is success in one place and failure in the other — #17 "
            "requires one consistent boolean success determination."
        )


class TestPlatformPolicySatisfactionWiredToT8:
    """#17 assertion 5: platform_policy_satisfied must gate success via T8's check.

    T8 already implemented ``platform_policy.satisfies_platform_requirements``
    (``platform_policy.py:979``); the orchestrator's success path never calls or
    consults it, so a run whose platform policy is NOT satisfied still reports
    success.
    """

    def test_platform_policy_violation_forces_failure(self):
        """RED: platform_policy_satisfied=False must fail even though success/status say success."""
        output = _phase5_output(platform_policy_satisfied=False)
        assert orchestrator.Orchestrator._phase_5_succeeded(output) is False, (
            "#17 boolean contract: platform_policy_satisfied is one AND term — "
            "False forces success=False. orchestrator.py:479-483 _phase_5_succeeded "
            "returned True for {success=True, status='success', "
            "platform_policy_satisfied=False}: the T8-backed platform check "
            "(platform_policy.satisfies_platform_requirements, platform_policy.py:979) "
            "is never wired into the success-determination code path."
        )

    def test_t8_platform_predicate_is_available_control(self):
        """CONTROL — must PASS on current code (T8 already landed).

        Proves the RED failure above is about the ORCHESTRATOR not consuming T8's
        predicate, not about the predicate being missing.
        """
        policy = platform_policy.BUILTIN_PRESETS["npu_ascend"]
        assert (
            platform_policy.satisfies_platform_requirements(
                policy, {"target_device": "npu"}
            )
            is True
        )
        assert (
            platform_policy.satisfies_platform_requirements(
                policy, {"target_device": "cuda"}
            )
            is False
        )
