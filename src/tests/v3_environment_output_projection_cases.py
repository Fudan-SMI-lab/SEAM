from __future__ import annotations

from pathlib import Path

from core.phase5_attempt_receipt import BackendKind
from core.resource_manifest import FactProvenance
from core.resource_retention import ContainerCleanupStatus
from core.v3_runtime_report import RuntimeReportRequest, build_runtime_report
from tests.phase5_receipt_test_support import run_outcome
from tests.v3_environment_output_test_support import (
    RUN_ID,
    add_base_environment,
    add_container_environment,
    add_phase5_environment_reference,
    add_project_venv,
    replay_source,
    runtime_store,
    seal_lifecycle,
)


def _fact_value(facts, name: str) -> str | None:
    return next(fact.value for fact in facts if fact.name == name)


def test_successful_local_base_environment_reports_authoritative_replay(
    tmp_path: Path,
) -> None:
    # Given an authenticated local manifest and accepted actual Phase 5 receipt.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    receipt, accepted = replay_source(tmp_path)
    add_phase5_environment_reference(store, receipt, "execution-python")
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    # When the completion report is projected from those authorities.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, accepted)
    )

    # Then base Python and replay come from structured observed/receipt facts.
    assert _fact_value(report.execution, "backend.effective") == "local"
    assert report.active_environment_id == "execution-python"
    assert _fact_value(report.environments[0].facts, "environment.type") == "base"
    executable = next(
        fact
        for fact in report.environments[0].facts
        if fact.name == "interpreter.sys_executable"
    )
    assert executable.provenance is FactProvenance.FRAMEWORK_OBSERVED
    assert report.access.kind == "base_environment"
    assert report.replay.available is True
    assert report.replay.accepted_attempt_id == receipt.attempt_id
    assert (
        report.replay.validation_command == "python 'validation script.py' --mode final"
    )
    assert report.replay.command is not None
    assert report.replay.auto_execute is False


def test_project_venv_activation_preserves_agent_reported_qualification(
    tmp_path: Path,
) -> None:
    # Given a project venv recorded only from the Phase 2 report.
    store = runtime_store(tmp_path)
    add_project_venv(store, tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    # When runtime output is projected without an accepted attempt.
    report = build_runtime_report(RuntimeReportRequest(store, None, RUN_ID, None))

    # Then the activation hint is useful but never promoted to observed truth.
    environment = report.environments[0]
    assert _fact_value(environment.facts, "environment.type") == "project_venv"
    assert report.access.kind == "project_venv"
    assert report.access.activation_command is not None
    assert ".venv" in report.access.activation_command
    assert report.access.provenance is FactProvenance.AGENT_REPORTED
    assert report.replay.available is False
    assert report.replay.reason == "run_outcome_unavailable"


def test_retained_container_reports_exact_access_and_live_replay(
    tmp_path: Path,
) -> None:
    # Given a retained container manifest, observed Python, and accepted receipt.
    store = runtime_store(tmp_path, effective_backend="container")
    add_container_environment(store)
    receipt, accepted = replay_source(tmp_path, backend_kind=BackendKind.CONTAINER)
    add_phase5_environment_reference(store, receipt, "execution-python")
    entry = "docker exec -it cid-123 bash"
    seal_lifecycle(
        store,
        cleanup=ContainerCleanupStatus.RETAINED,
        entry_command=entry,
        post_state="running",
    )

    # When completion output is projected.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, accepted)
    )

    # Then identity, mount, ownership, retention, access, and replay agree.
    expected = {
        "container.runtime": "docker",
        "container.name": "seam-migration-42",
        "container.id": "cid-123",
        "container.image": "python:3.11",
        "container.workdir": "/workspace/project",
        "container.mount_destination": "/workspace/project",
    }
    assert {name: _fact_value(report.container, name) for name in expected} == expected
    container_names = {fact.name for fact in report.container}
    assert "container.framework_ownership_token_sha256" not in container_names
    assert "container.framework_ownership_label" not in container_names
    assert _fact_value(report.retention, "retention.cleanup_result") == "retained"
    assert report.access.entry_command == entry
    assert report.replay.available is True
    assert report.replay.command is not None
    assert report.replay.command.startswith("docker exec -i -w /workspace/project")
    assert str(store.path) == report.manifest_path


def test_successful_receipt_with_stopped_container_keeps_replay_unavailable(
    tmp_path: Path,
) -> None:
    # Given an accepted successful receipt whose exact container is no longer live.
    store = runtime_store(tmp_path, effective_backend="container")
    add_container_environment(store)
    receipt, accepted = replay_source(tmp_path, backend_kind=BackendKind.CONTAINER)
    seal_lifecycle(
        store,
        cleanup=ContainerCleanupStatus.RETAINED,
        entry_command="docker exec -it cid-123 bash",
        post_state="stopped",
    )

    # When reporting performs Task 18 liveness checks.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, accepted)
    )

    # Then the accepted validation argv remains qualified but no replay is offered.
    assert report.replay.available is False
    assert report.replay.reason == "container_unavailable"
    assert report.replay.validation_command is not None
    assert report.replay.command is None
    assert report.access.available is False


def test_local_fallback_and_missing_probe_are_reported_without_fabrication(
    tmp_path: Path,
) -> None:
    # Given configured container execution that fell back to local with no probe.
    store = runtime_store(
        tmp_path,
        requested_backend="container",
        effective_backend="local",
    )
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    # When completion reporting reads the sealed manifest.
    report = build_runtime_report(RuntimeReportRequest(store, None, RUN_ID, None))

    # Then requested/effective mode diverge explicitly and environment stays unknown.
    assert _fact_value(report.execution, "backend.requested") == "container"
    assert _fact_value(report.execution, "backend.effective") == "local"
    assert report.environments == ()
    assert report.access.available is False
    assert report.access.detail == "execution environment probe unavailable"
    assert report.replay.available is False


def test_failed_or_unaccepted_attempt_never_becomes_successful_replay(
    tmp_path: Path,
) -> None:
    # Given one accepted-path receipt but a failed run, then an unaccepted receipt.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    receipt, accepted = replay_source(tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    # When each non-authoritative success condition is projected.
    failed = build_runtime_report(
        RuntimeReportRequest(
            store, run_outcome(receipt, succeeded=False), RUN_ID, accepted
        )
    )
    unaccepted_receipt, unaccepted = replay_source(tmp_path / "other", accepted=False)
    unaccepted_report = build_runtime_report(
        RuntimeReportRequest(
            store,
            run_outcome(unaccepted_receipt),
            RUN_ID,
            unaccepted,
        )
    )

    # Then neither stale command is labeled validation success or replay.
    assert failed.replay.reason == "run_not_successful"
    assert failed.replay.validation_command is None
    assert failed.replay.command is None
    assert unaccepted_report.replay.reason == "attempt_not_accepted"
    assert unaccepted_report.replay.validation_command is None
    assert unaccepted_report.replay.command is None


def test_cleanup_failure_is_reported_without_mutating_frozen_outcome(
    tmp_path: Path,
) -> None:
    # Given a frozen PASS and lifecycle authority recording failed cleanup.
    store = runtime_store(tmp_path, effective_backend="container")
    add_container_environment(store)
    receipt, accepted = replay_source(tmp_path, backend_kind=BackendKind.CONTAINER)
    outcome = run_outcome(receipt)
    frozen_outcome = outcome
    seal_lifecycle(
        store,
        cleanup=ContainerCleanupStatus.FAILED,
        entry_command="docker exec -it cid-123 bash",
        post_state="running",
    )

    # When reporting projects the cleanup failure.
    report = build_runtime_report(
        RuntimeReportRequest(store, outcome, RUN_ID, accepted)
    )

    # Then cleanup truth is visible while the exact RunOutcome object is untouched.
    assert _fact_value(report.retention, "retention.cleanup_result") == "failed"
    assert report.outcome_status == "passed"
    assert outcome is frozen_outcome
