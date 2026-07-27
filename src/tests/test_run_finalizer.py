from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.run_manifest import RunId
from core.run_outcome import TerminalOutcome
from harness.run import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHook,
    FinalizationHookError,
    FinalizationHooks,
    FinalizationStage,
    ReportAllocationError,
    ReportAllocationErrorKind,
    RunArtifactUpdate,
    allocate_report_directory,
    finalize_run,
)
from .run_finalizer_oracle_cases import (
    test_control_flow_exception_propagates_without_becoming_diagnostic as test_control_flow_exception_propagates_without_becoming_diagnostic,
    test_invalid_sidecar_path_is_diagnostic_and_never_claimed as test_invalid_sidecar_path_is_diagnostic_and_never_claimed,
    test_ordinary_hook_exception_is_diagnostic_and_cleanup_continues as test_ordinary_hook_exception_is_diagnostic_and_cleanup_continues,
)
from .run_cleanup_oracle_cases import (
    test_resource_cleanup_continues_after_ordinary_session_failure as test_resource_cleanup_continues_after_ordinary_session_failure,
    test_resource_cleanup_continues_after_ordinary_server_failure as test_resource_cleanup_continues_after_ordinary_server_failure,
    test_resource_cleanup_propagates_control_flow_from_session as test_resource_cleanup_propagates_control_flow_from_session,
    test_resource_cleanup_propagates_control_flow_from_later_resources as test_resource_cleanup_propagates_control_flow_from_later_resources,
    test_resource_cleanup_records_ordinary_temp_failure as test_resource_cleanup_records_ordinary_temp_failure,
)
from .run_cleanup_bookkeeping_cases import (
    test_cleanup_bookkeeping_control_flow_propagates_immediately as test_cleanup_bookkeeping_control_flow_propagates_immediately,
    test_cleanup_bookkeeping_ordinary_failure_preserves_later_operations as test_cleanup_bookkeeping_ordinary_failure_preserves_later_operations,
    test_cleanup_successful_operation_order_is_unchanged as test_cleanup_successful_operation_order_is_unchanged,
)
from .run_artifact_receipt_cases import (
    test_artifact_link_retarget_is_rejected_at_freeze as test_artifact_link_retarget_is_rejected_at_freeze,
    test_contained_directory_junction_is_never_accepted as test_contained_directory_junction_is_never_accepted,
    test_contained_file_symlink_is_never_accepted as test_contained_file_symlink_is_never_accepted,
    test_hook_artifact_is_revalidated_at_final_freeze as test_hook_artifact_is_revalidated_at_final_freeze,
    test_hook_cannot_claim_preexisting_unchanged_artifact as test_hook_cannot_claim_preexisting_unchanged_artifact,
    test_invalid_initial_artifacts_use_the_same_trust_boundary as test_invalid_initial_artifacts_use_the_same_trust_boundary,
    test_initial_directory_link_uses_the_same_trust_boundary as test_initial_directory_link_uses_the_same_trust_boundary,
    test_initial_artifact_is_revalidated_at_final_freeze as test_initial_artifact_is_revalidated_at_final_freeze,
    test_valid_initial_artifact_remains_claimed_when_unchanged as test_valid_initial_artifact_remains_claimed_when_unchanged,
    test_windows_short_path_alias_remains_contained as test_windows_short_path_alias_remains_contained,
)
from .run_finalizer_test_support import (
    FinalizerScenario,
    finalization_request,
)


def test_report_directory_is_run_qualified_exclusive_and_safe(tmp_path: Path) -> None:
    # Given an empty report root and a safe Task 2 run identifier.
    run_id = RunId("e2e-v3-safe")

    # When the report namespace is allocated once.
    report_dir = allocate_report_directory(tmp_path, run_id)

    # Then the path is run-qualified and stale reuse is deterministically refused.
    assert report_dir == tmp_path / "e2e-v3-safe"
    assert report_dir.is_dir()
    with pytest.raises(ReportAllocationError) as duplicate:
        _ = allocate_report_directory(tmp_path, run_id)
    assert duplicate.value.kind is ReportAllocationErrorKind.DUPLICATE_RUN


@pytest.mark.parametrize(
    "unsafe_run_id", ["../escape", "nested/run", "nested\\run", ""]
)
def test_report_directory_rejects_unsafe_run_ids(
    tmp_path: Path,
    unsafe_run_id: str,
) -> None:
    # Given an identifier that cannot be a Task 2 run namespace.
    # When allocation parses the external identifier.
    with pytest.raises(ReportAllocationError) as refusal:
        _ = allocate_report_directory(tmp_path, RunId(unsafe_run_id))

    # Then no path escapes or appears below the report root.
    assert refusal.value.kind is ReportAllocationErrorKind.UNSAFE_RUN_ID
    assert list(tmp_path.iterdir()) == []


def test_finalization_freezes_outcome_and_persists_cleanup_order(
    tmp_path: Path,
) -> None:
    # Given hooks that expose their exact ordering and post-cleanup visibility.
    events: list[str] = []

    def evidence(outcome: TerminalOutcome) -> RunArtifactUpdate:
        events.append(f"evidence:{outcome.value}")
        telemetry_path = tmp_path / "telemetry.json"
        after_snapshot_path = tmp_path / "after_snapshot.json"
        _ = telemetry_path.write_text(json.dumps(events), encoding="utf-8")
        _ = after_snapshot_path.write_text("{}", encoding="utf-8")
        return RunArtifactUpdate(
            telemetry_paths=(("telemetry_json", str(telemetry_path)),),
            after_snapshot_path=str(after_snapshot_path),
        )

    def trace(outcome: TerminalOutcome) -> RunArtifactUpdate:
        events.append(f"trace:{outcome.value}")
        return EMPTY_ARTIFACT_UPDATE

    def cleanup(outcome: TerminalOutcome) -> RunArtifactUpdate:
        events.append(f"cleanup:{outcome.value}")
        return EMPTY_ARTIFACT_UPDATE

    def manifest(outcome: TerminalOutcome) -> RunArtifactUpdate:
        events.append(f"manifest:{outcome.value}:{events[-1]}")
        telemetry_path = tmp_path / "telemetry.json"
        _ = telemetry_path.write_text(json.dumps(events), encoding="utf-8")
        return RunArtifactUpdate(
            telemetry_paths=(("telemetry_json", str(telemetry_path)),),
        )

    hooks = FinalizationHooks(evidence, trace, cleanup, manifest)

    # When a passing workflow is finalized.
    result = finalize_run(
        finalization_request(tmp_path, FinalizerScenario(hooks=hooks))
    )

    # Then outcome precedes sidecars and cleanup is visible before persistence.
    assert events == [
        "evidence:passed",
        "trace:passed",
        "cleanup:passed",
        "manifest:passed:cleanup:passed",
    ]
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    assert result.summary.overall_status == "PASS"
    assert result.summary.telemetry_paths == {
        "telemetry_json": str(tmp_path / "telemetry.json")
    }
    assert json.loads((tmp_path / "telemetry.json").read_text(encoding="utf-8"))[
        -2:
    ] == [
        "cleanup:passed",
        "manifest:passed:cleanup:passed",
    ]
    assert result.summary.errors == ()
    assert result.diagnostics == ()
    assert (tmp_path / "summary.json").is_file()


@pytest.mark.parametrize("failed_stage", list(FinalizationStage.callback_stages()))
def test_callback_failures_are_diagnostic_and_cannot_flip_outcome(
    tmp_path: Path,
    failed_stage: FinalizationStage,
) -> None:
    # Given one failing sidecar stage and otherwise successful hooks.
    calls: list[FinalizationStage] = []

    def callback(stage: FinalizationStage) -> FinalizationHook:
        def hook(outcome: TerminalOutcome) -> RunArtifactUpdate:
            assert outcome is TerminalOutcome.PASSED
            calls.append(stage)
            return EMPTY_ARTIFACT_UPDATE

        return hook

    def fail(outcome: TerminalOutcome) -> RunArtifactUpdate:
        assert outcome is TerminalOutcome.PASSED
        calls.append(failed_stage)
        raise FinalizationHookError(detail=f"{failed_stage.value} interrupted")

    callbacks = {
        stage: callback(stage) for stage in FinalizationStage.callback_stages()
    }
    callbacks[failed_stage] = fail
    hooks = FinalizationHooks.from_mapping(callbacks)

    # When finalization contains the callback failure.
    result = finalize_run(
        finalization_request(tmp_path, FinalizerScenario(hooks=hooks))
    )

    # Then the frozen workflow result remains PASS and the sidecar is diagnostic only.
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    assert result.summary.overall_status == "PASS"
    assert result.summary.errors == ()
    assert [(item.stage, item.detail) for item in result.diagnostics] == [
        (failed_stage, f"{failed_stage.value} interrupted")
    ]
    assert calls == list(FinalizationStage.callback_stages())
    assert result.diagnostics_path == str(tmp_path / "finalization_diagnostics.json")


def test_successful_sidecars_cannot_turn_failed_workflow_into_pass(
    tmp_path: Path,
) -> None:
    # Given a migration failure whose optional sidecars all report success.
    scenario = FinalizerScenario(
        status="failed",
        errors=("RuntimeError: migration failed",),
    )

    # When the run is finalized.
    result = finalize_run(finalization_request(tmp_path, scenario))

    # Then only workflow facts control summary and process outcome.
    assert result.outcome is TerminalOutcome.FAILED
    assert result.exit_code == 1
    assert result.summary.overall_status == "FAIL"
    assert result.summary.errors == ("RuntimeError: migration failed",)


def test_interrupted_summary_rewrite_preserves_previous_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one complete finalization and an interrupted repeated atomic replace.
    cleanup_calls = 0

    def repeated_cleanup(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls > 1:
            raise FinalizationHookError(detail="cleanup already attempted")
        return EMPTY_ARTIFACT_UPDATE

    request = finalization_request(
        tmp_path,
        FinalizerScenario(
            hooks=FinalizationHooks(authorized_cleanup=repeated_cleanup),
        ),
    )
    first = finalize_run(request)
    summary_path = tmp_path / "summary.json"
    baseline = summary_path.read_bytes()

    def interrupt_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace interrupted")

    monkeypatch.setattr("harness.run.sidecars.atomic_replace", interrupt_replace)

    # When the same finalization is attempted again.
    repeated = finalize_run(request)

    # Then no partial summary replaces the prior bytes or changes the outcome.
    assert first.outcome is repeated.outcome is TerminalOutcome.PASSED
    assert repeated.exit_code == 0
    assert cleanup_calls == 2
    assert summary_path.read_bytes() == baseline
    assert repeated.summary_path is None
    assert repeated.diagnostics[0].stage is FinalizationStage.AUTHORIZED_CLEANUP
    assert repeated.diagnostics[-1].stage is FinalizationStage.SUMMARY_WRITE
    assert list(tmp_path.glob(".*.tmp")) == []
