from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core.execution_env_context import (
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
)
from core.execution_env_records import build_phase2_environment
from core.resource_retention import ContainerCleanupStatus, ContainerRetention
from core.resource_retention_finalizer import (
    RetentionLifecycleRecord,
    retention_manifest_update,
)
from core.run_outcome import TerminalOutcome
from core.v3_runtime_report import RuntimeReportRequest, build_runtime_report
from core.v3_runtime_report_integration import (
    RuntimeReportingInputs,
    prepare_runtime_report_request,
)
from harness.run import (
    FinalizationHooks,
    FinalizationStage,
    RunArtifactUpdate,
    finalize_run,
)
from harness.run.v3_runtime_reporting import V3RuntimeReportRecorder
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request
from tests.v3_environment_output_test_support import (
    RUN_ID,
    runtime_store,
    seal_lifecycle,
)


class _TelemetryUnavailable(RuntimeError):
    pass


def test_phase2_base_environment_accepts_bounded_path_executable() -> None:
    # Given prompt-valid Phase 2 output that names a PATH executable.
    report = Phase2EnvironmentReport.model_validate(
        {
            "env_type": "base_env",
            "venv_path": "/usr/local",
            "python_path": "python3",
            "installed_packages": [],
            "vendor_stack": {"api_mode": "cuda_compatible"},
        }
    )

    # When the report becomes a qualified environment record.
    environment = build_phase2_environment(
        Phase2EnvironmentRequest(
            environment_id="phase2-path-base",
            namespace="host",
            report=report,
        )
    )

    # Then its base type and exact bounded executable remain visible.
    values = {fact.name: fact.value for fact in environment.facts}
    assert values["environment.type"] == "base"
    assert values["interpreter.sys_executable"] == "python3"


def test_partial_runtime_telemetry_failure_keeps_one_projection(tmp_path: Path) -> None:
    # Given a sink that stores metadata before event publication fails.
    store = runtime_store(tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    class PartialTelemetry:
        def __init__(self) -> None:
            self.runtime = None

        def set_metadata(self, key, value) -> None:
            del key
            self.runtime = value

        def record_event(self, event_type, **details) -> None:
            del event_type, details
            raise _TelemetryUnavailable

    telemetry = PartialTelemetry()
    recorder = V3RuntimeReportRecorder(
        RuntimeReportRequest(store, None, RUN_ID, None),
        telemetry,
        lambda _outcome: RunArtifactUpdate(),
    )

    # When the recorder publishes the optional report.
    _ = recorder(TerminalOutcome.PASSED)

    # Then summary source and already-published metadata remain byte-equivalent.
    captured = recorder.read()
    assert captured is not None
    assert captured.model_dump(mode="json") == telemetry.runtime


def test_throwing_existing_telemetry_hook_is_outcome_neutral(tmp_path: Path) -> None:
    # Given the wrapped production telemetry persistence hook throws.
    store = runtime_store(tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    class WorkingTelemetry:
        def set_metadata(self, key, value) -> None:
            del key, value

        def record_event(self, event_type, **details) -> None:
            del event_type, details

    def fail_existing(_outcome) -> RunArtifactUpdate:
        raise _TelemetryUnavailable

    recorder = V3RuntimeReportRecorder(
        RuntimeReportRequest(store, None, RUN_ID, None),
        WorkingTelemetry(),
        fail_existing,
    )
    request = finalization_request(
        store.path.parent,
        FinalizerScenario(hooks=FinalizationHooks(post_cleanup_manifest=recorder)),
    )
    request = replace(
        request,
        required_stages=frozenset({FinalizationStage.POST_CLEANUP_MANIFEST}),
        runtime_report_source=recorder.read,
    )

    # When finalization executes the required composite stage.
    result = finalize_run(request)

    # Then telemetry persistence remains optional and cannot change PASS.
    assert result.finalization_failed is False
    assert result.exit_code == 0


def test_projection_exception_cannot_skip_required_existing_hook(
    tmp_path: Path,
) -> None:
    # Given optional projection that raises before the required existing hook.
    store = runtime_store(tmp_path)
    existing_called = False

    class WorkingTelemetry:
        def set_metadata(self, key, value) -> None:
            del key, value

        def record_event(self, event_type, **details) -> None:
            del event_type, details

    def existing(_outcome) -> RunArtifactUpdate:
        nonlocal existing_called
        existing_called = True
        return RunArtifactUpdate()

    recorder = V3RuntimeReportRecorder(
        RuntimeReportRequest(store, None, RUN_ID, None),
        WorkingTelemetry(),
        existing,
    )
    request = finalization_request(
        store.path.parent,
        FinalizerScenario(hooks=FinalizationHooks(post_cleanup_manifest=recorder)),
    )
    request = replace(
        request,
        required_stages=frozenset({FinalizationStage.POST_CLEANUP_MANIFEST}),
        runtime_report_source=recorder.read,
    )

    # When projection raises an ordinary data error.
    with patch(
        "harness.run.v3_runtime_reporting.build_runtime_report",
        side_effect=ValueError("invalid optional projection"),
    ):
        result = finalize_run(request)

    # Then the required hook still runs and the frozen PASS remains neutral.
    assert existing_called is True
    assert result.finalization_failed is False
    assert result.exit_code == 0


def test_mismatched_manifest_store_is_rejected_before_projection_or_write(
    tmp_path: Path,
) -> None:
    # Given a manifest store from another run and a Phase 2 update request.
    store = runtime_store(tmp_path)
    revision = store.read().revision
    phase2 = Phase2EnvironmentRequest(
        environment_id="phase2-other-run",
        namespace="host",
        report=Phase2EnvironmentReport(
            env_type="base_env",
            venv_path="/usr/local",
            python_path="python3",
            installed_packages=(),
        ),
    )
    outcome = finalization_request(tmp_path, FinalizerScenario()).authoritative_outcome
    assert outcome is not None

    # When integration and projection receive a different expected run ID.
    prepared = prepare_runtime_report_request(
        RuntimeReportingInputs(
            manifest_store=store,
            artifact_store=None,
            outcome=outcome,
            expected_run_id="different-run",
            phase2_environment=phase2,
        )
    )
    projected = build_runtime_report(
        RuntimeReportRequest(store, outcome, "different-run", None)
    )

    # Then the foreign store is not mutated or exposed as current-run authority.
    assert prepared.manifest_store is None
    assert store.read().revision == revision
    assert projected.manifest_path is None


def test_generic_manifest_write_cannot_attest_caller_lifecycle(
    tmp_path: Path,
) -> None:
    # Given a real container manifest and caller-supplied retained lifecycle values.
    store = runtime_store(tmp_path, effective_backend="container")
    current = store.read()
    attacker_record = RetentionLifecycleRecord(
        requested=ContainerRetention.RETAIN,
        effective=ContainerRetention.RETAIN,
        owner_kind="framework",
        entry_command="docker exec -it cid-123 bash",
        pre_state="running",
        post_state="running",
        cleanup_status=ContainerCleanupStatus.RETAINED,
        continuation_available=True,
    )

    # When the ordinary public update path writes and seals those values.
    revised = store.write(retention_manifest_update(attacker_record, current.revision))
    sealed = store.seal(revised.revision, "passed")
    report = build_runtime_report(RuntimeReportRequest(store, None, RUN_ID, None))

    # Then caller data gains no lifecycle authority or retained access guidance.
    post_state = next(
        fact for fact in sealed.facts if fact.name == "retention.post_state"
    )
    assert post_state.authority_tag is None
    assert report.access.available is False
    assert report.access.entry_command is None
