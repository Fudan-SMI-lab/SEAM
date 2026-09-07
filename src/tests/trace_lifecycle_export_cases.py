from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import pytest

import harness.run as run
from core.run_outcome import TerminalOutcome
from tests.trace_export_test_support import FakeTraceClient, graph, seed
from tests.trace_lifecycle_test_support import (
    RecordingTraceTelemetry,
    ThrowingTraceTelemetry,
)


@pytest.mark.parametrize(
    ("cli_value", "requested"),
    [(None, False), (False, True)],
    ids=("omitted", "explicit-off"),
)
def test_disabled_trace_performs_no_client_or_seed_work(
    tmp_path: Path,
    cli_value: bool | None,
    requested: bool,
) -> None:
    # Given trace capture is omitted or explicitly disabled.
    def forbidden() -> NoReturn:
        pytest.fail("disabled trace must not access exporter inputs")

    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(cli_value),
            destination=tmp_path / "trace",
            client_source=forbidden,
            seeds_source=forbidden,
        )
    )

    # When the trace finalization hook runs.
    update = lifecycle(TerminalOutcome.PASSED)

    # Then no client/exporter work occurs and status remains explicit.
    assert update == run.EMPTY_ARTIFACT_UPDATE
    assert lifecycle.read() == run.TraceLifecycleStatus(
        requested=requested,
        enabled=False,
        complete=False,
        path=None,
        errors=(),
    )


def test_enabled_trace_exports_every_registered_seed(tmp_path: Path) -> None:
    # Given two Task 20 root seeds and a telemetry sink.
    client = FakeTraceClient(
        {
            "ses_first": graph("ses_first").retrieval,
            "ses_second": graph("ses_second").retrieval,
        }
    )
    telemetry = RecordingTraceTelemetry()
    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "run-id" / "trace",
            client_source=lambda: client,
            seeds_source=lambda: (seed("ses_first"), seed("ses_second")),
            telemetry=telemetry,
        )
    )
    lifecycle.request.destination.parent.mkdir()

    # When Task 21 exports through the lifecycle hook.
    update = lifecycle(TerminalOutcome.PASSED)

    # Then every seed is captured and path/completeness are recorded.
    manifest = lifecycle.request.destination / "manifest.json"
    assert client.calls == ["ses_first", "ses_second"]
    assert lifecycle.read() == run.TraceLifecycleStatus(
        requested=True,
        enabled=True,
        complete=True,
        path=str(manifest),
        errors=(),
    )
    assert update.directory_paths == (("agent_trace_dir", str(manifest.parent)),)
    assert update.telemetry_paths == (("agent_trace_manifest", str(manifest)),)
    assert telemetry.metadata["agent_trace"]["complete"] is True


def test_enabled_trace_without_registered_seeds_is_incomplete(tmp_path: Path) -> None:
    # Given enabled capture with no Task 20 root seeds.
    def forbidden_client() -> NoReturn:
        pytest.fail("an empty seed set must not open the trace client")

    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "trace",
            client_source=forbidden_client,
            seeds_source=lambda: (),
        )
    )

    # When the trace hook runs.
    update = lifecycle(TerminalOutcome.PASSED)

    # Then no empty export is mislabeled complete.
    assert update == run.EMPTY_ARTIFACT_UPDATE
    assert lifecycle.read() == run.TraceLifecycleStatus(
        requested=True,
        enabled=True,
        complete=False,
        path=None,
        errors=("no_registered_trace_seeds",),
    )


def test_trace_telemetry_failure_is_outcome_neutral(tmp_path: Path) -> None:
    # Given a complete trace graph and a broken optional telemetry sink.
    client = FakeTraceClient({"ses_root": graph("ses_root").retrieval})
    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "trace",
            client_source=lambda: client,
            seeds_source=lambda: (seed("ses_root"),),
            telemetry=ThrowingTraceTelemetry(),
        )
    )

    # When trace capture publishes its status.
    update = lifecycle(TerminalOutcome.PASSED)

    # Then telemetry cannot erase the complete trace result.
    assert lifecycle.read().complete is True
    assert update.telemetry_paths == (
        ("agent_trace_manifest", str(tmp_path / "trace" / "manifest.json")),
    )


def test_incomplete_child_is_reported_without_claiming_complete(tmp_path: Path) -> None:
    # Given a reachable child whose own child endpoint is incomplete.
    client = FakeTraceClient(
        {
            "ses_root": graph("ses_root", child_ids=("ses_child",)).retrieval,
            "ses_child": graph("ses_child", child_status=500).retrieval,
        }
    )
    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "trace",
            client_source=lambda: client,
            seeds_source=lambda: (seed("ses_root"),),
        )
    )

    # When the graph is exported.
    _ = lifecycle(TerminalOutcome.PASSED)

    # Then the published path remains usable but completeness is false.
    status = lifecycle.read()
    assert status.complete is False
    assert status.path == str(tmp_path / "trace" / "manifest.json")
    assert status.errors == ()
    assert status.path is not None
    manifest = json.loads(Path(status.path).read_text(encoding="utf-8"))
    assert manifest["sessions"][1]["errors"] == ["children:http_500"]
