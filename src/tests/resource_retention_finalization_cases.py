from __future__ import annotations

from pathlib import Path

from core.resource_retention import ContainerDeletionError
from core.run_outcome import TerminalOutcome
from harness.run import FinalizationHooks, RunArtifactUpdate, finalize_run
from tests.run_finalizer_test_support import (
    FinalizerScenario,
    failed_finalizer_outcome,
    finalization_request,
)


def _requested_delete_failure(_outcome: TerminalOutcome) -> RunArtifactUpdate:
    raise ContainerDeletionError(
        "immutable-id", "running", "stopped", "runtime remove failed"
    )


def test_requested_owned_cleanup_failure_preserves_pass_and_exits_two(
    tmp_path: Path,
) -> None:
    # Given a frozen PASS and explicitly requested owned-container deletion.
    hooks = FinalizationHooks(authorized_cleanup=_requested_delete_failure)

    # When the authorized cleanup stage fails after evidence stages.
    result = finalize_run(
        finalization_request(tmp_path, FinalizerScenario(hooks=hooks))
    )

    # Then migration authority remains PASS while finalization returns code 2.
    assert result.summary.overall_status == "PASS"
    assert result.outcome.value == "passed"
    assert result.exit_code == 2


def test_requested_owned_cleanup_failure_keeps_failed_migration_exit_one(
    tmp_path: Path,
) -> None:
    # Given a frozen migration FAIL and the same requested cleanup failure.
    hooks = FinalizationHooks(authorized_cleanup=_requested_delete_failure)
    scenario = FinalizerScenario(
        hooks=hooks,
        authoritative_outcome=failed_finalizer_outcome(),
    )

    # When finalization runs.
    result = finalize_run(finalization_request(tmp_path, scenario))

    # Then the migration failure remains authoritative and exits 1.
    assert result.summary.overall_status == "FAIL"
    assert result.exit_code == 1
