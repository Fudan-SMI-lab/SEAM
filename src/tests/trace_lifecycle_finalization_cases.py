from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest

import harness.run as run
from core.requested_cleanup_error import RequestedContainerCleanupError
from core.run_outcome import TerminalOutcome
from tests.run_finalizer_test_support import (
    FinalizerScenario,
    failed_finalizer_outcome,
    finalization_request,
)
from tests.trace_export_test_support import FakeTraceClient, graph, seed
from tests.trace_lifecycle_test_support import (
    CleanupUnavailableError,
    TraceExporterUnavailableError,
)


@pytest.mark.parametrize(
    ("scenario", "expected_outcome", "expected_exit"),
    [
        (FinalizerScenario(), TerminalOutcome.PASSED, 0),
        (
            FinalizerScenario(
                status="failed",
                authoritative_outcome=failed_finalizer_outcome(),
            ),
            TerminalOutcome.FAILED,
            1,
        ),
    ],
)
def test_exporter_exception_preserves_frozen_outcome_and_continuation_seal(
    tmp_path: Path,
    scenario: FinalizerScenario,
    expected_outcome: TerminalOutcome,
    expected_exit: int,
) -> None:
    # Given an enabled exporter source that fails before a required child seal.
    events: list[str] = []

    def fail_client() -> NoReturn:
        events.append("trace")
        raise TraceExporterUnavailableError("export unavailable")

    def seal(_outcome: TerminalOutcome) -> run.RunArtifactUpdate:
        events.append("seal")
        return run.EMPTY_ARTIFACT_UPDATE

    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "trace",
            client_source=fail_client,
            seeds_source=lambda: (seed("ses_root"),),
        )
    )
    request = finalization_request(
        tmp_path,
        replace(
            scenario,
            hooks=run.FinalizationHooks(
                trace_export=run.compose_trace_hooks(lifecycle, seal)
            ),
        ),
    )
    request = replace(
        request,
        required_stages=frozenset({run.FinalizationStage.TRACE_EXPORT}),
        trace_status_source=lifecycle.read,
    )

    # When finalization isolates the trace failure and runs the child seal.
    result = run.finalize_run(request)

    # Then only frozen migration authority controls ordinary exit mapping.
    assert events == ["trace", "seal"]
    assert result.outcome is expected_outcome
    assert result.exit_code == expected_exit
    assert result.finalization_failed is False
    assert result.summary.errors == scenario.errors
    assert result.summary.trace.errors == (
        "TraceExporterUnavailableError: export unavailable",
    )


@pytest.mark.parametrize("continuation", (False, True), ids=("normal", "continuation"))
def test_trace_runs_before_cleanup_and_final_telemetry(
    tmp_path: Path,
    continuation: bool,
) -> None:
    # Given an available client, cleanup hook, and final telemetry hook.
    events: list[str] = []
    client = FakeTraceClient({"ses_root": graph("ses_root").retrieval})

    def client_source() -> FakeTraceClient:
        events.append("trace")
        return client

    def cleanup(_outcome: TerminalOutcome) -> run.RunArtifactUpdate:
        events.append("cleanup")
        return run.EMPTY_ARTIFACT_UPDATE

    def telemetry(_outcome: TerminalOutcome) -> run.RunArtifactUpdate:
        events.append("telemetry")
        return run.EMPTY_ARTIFACT_UPDATE

    def seal(_outcome: TerminalOutcome) -> run.RunArtifactUpdate:
        events.append("seal")
        return run.EMPTY_ARTIFACT_UPDATE

    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "trace",
            client_source=client_source,
            seeds_source=lambda: (seed("ses_root"),),
        )
    )
    trace_hook = run.compose_trace_hooks(lifecycle, seal) if continuation else lifecycle
    hooks = run.FinalizationHooks(
        trace_export=trace_hook,
        authorized_cleanup=cleanup,
        post_cleanup_manifest=telemetry,
    )
    request = finalization_request(tmp_path, FinalizerScenario(hooks=hooks))

    # When the ordered Task 5 finalizer runs.
    result = run.finalize_run(replace(request, trace_status_source=lifecycle.read))

    # Then capture precedes unavailable sessions and telemetry persists last.
    expected = [
        "trace",
        *(("seal",) if continuation else ()),
        "cleanup",
        "telemetry",
    ]
    assert events == expected
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0


def test_only_requested_container_cleanup_failure_maps_pass_to_exit_two(
    tmp_path: Path,
) -> None:
    # Given ordinary and explicitly requested cleanup failures.
    ordinary_dir = tmp_path / "ordinary"
    requested_dir = tmp_path / "requested"
    ordinary_dir.mkdir()
    requested_dir.mkdir()

    def ordinary(_outcome: TerminalOutcome) -> run.RunArtifactUpdate:
        raise run.FinalizationHookError("session cleanup failed")

    def requested(_outcome: TerminalOutcome) -> run.RunArtifactUpdate:
        raise RequestedContainerCleanupError("owned container deletion failed")

    # When each cleanup failure finalizes a frozen PASS.
    ordinary_result = run.finalize_run(
        finalization_request(
            ordinary_dir,
            FinalizerScenario(hooks=run.FinalizationHooks(authorized_cleanup=ordinary)),
        )
    )
    requested_result = run.finalize_run(
        finalization_request(
            requested_dir,
            FinalizerScenario(
                hooks=run.FinalizationHooks(authorized_cleanup=requested)
            ),
        )
    )

    # Then only Task 17's typed requested cleanup marker yields exit 2.
    assert ordinary_result.exit_code == 0
    assert requested_result.exit_code == 2


def test_final_telemetry_persists_session_and_server_cleanup_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given session and server cleanup fail before post-cleanup telemetry.
    events: list[str] = []
    telemetry_path = tmp_path / "telemetry.json"
    process = subprocess.Popen([sys.executable, "-c", "pass"])

    def fail_sessions() -> int:
        raise CleanupUnavailableError("session unavailable")

    def fail_server(_process: subprocess.Popen[bytes]) -> None:
        raise OSError("server unavailable")

    def record_failure(resource: str, error_type: str, detail: str) -> None:
        events.append(f"{resource}:{error_type}:{detail}")

    def save_metrics() -> dict[str, str]:
        events.append("telemetry_saved")
        _ = telemetry_path.write_text(json.dumps(events), encoding="utf-8")
        return {"telemetry_json": str(telemetry_path)}

    observer = run.ObserverSidecar(
        save_metrics=save_metrics,
        counts=lambda: run.RunCounts(0, 0),
        record_cleanup_requested=lambda: events.append("cleanup_requested"),
        cleanup_sessions=fail_sessions,
        record_cleaned_sessions=lambda _count: None,
        record_cleanup_failure=record_failure,
    )
    cleanup = run.ResourceCleanup(
        run.CleanupContext(tmp_path, True, False, observer, process)
    )
    monkeypatch.setattr("harness.run.cleanup.stop_server", fail_server)
    request = finalization_request(
        tmp_path,
        FinalizerScenario(
            hooks=run.FinalizationHooks(
                authorized_cleanup=cleanup,
                post_cleanup_manifest=lambda _outcome: run.RunArtifactUpdate(
                    telemetry_paths=tuple(save_metrics().items())
                ),
            )
        ),
    )

    # When Task 5 isolates cleanup and persists final telemetry.
    try:
        result = run.finalize_run(request)
    finally:
        _ = process.wait(timeout=5)

    # Then cleanup diagnostics are present before the last persisted event.
    persisted = json.loads(telemetry_path.read_text(encoding="utf-8"))
    assert persisted == [
        "cleanup_requested",
        "session cleanup:CleanupUnavailableError:session unavailable",
        "server cleanup:OSError:server unavailable",
        "telemetry_saved",
    ]
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    assert result.summary.errors == ()
