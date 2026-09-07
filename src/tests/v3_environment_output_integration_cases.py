from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from core.resource_retention import ContainerCleanupStatus
from core.v3_runtime_report import RuntimeReportRequest, build_runtime_report
from harness.run import (
    ContinuationRunSummary,
    FinalizationStage,
    FinalizationHooks,
    RunArtifactUpdate,
    finalize_run,
)
from harness.run.v3_runtime_reporting import (
    V3RuntimeReportRecorder,
    print_runtime_report,
    render_runtime_report_lines,
)
from tests.e2e.e2e_observer import TelemetryObserver
from tests.phase5_receipt_test_support import run_outcome
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request
from tests.v3_environment_output_test_support import (
    RUN_ID,
    add_base_environment,
    add_phase5_environment_reference,
    replay_source,
    runtime_store,
    seal_lifecycle,
)


class _SessionBackend:
    def get_or_create(
        self,
        role: str,
        agent: str = "",
        lifecycle: Literal["persistent", "reusable", "ephemeral"] = "persistent",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        del role, agent, lifecycle, title, working_dir, initial_prompt
        return "session-runtime-report"

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str:
        del session_id, command, agent, timeout, retries
        return ""

    def cleanup_all(self) -> int:
        return 0


class _RuntimeProjectionInterrupted(RuntimeError):
    pass


@pytest.mark.parametrize("continuation", [False, True])
def test_summary_telemetry_and_manifest_share_one_runtime_projection(
    tmp_path: Path,
    continuation: bool,
) -> None:
    # Given a sealed manifest and one real observer for normal or continuation output.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    receipt, accepted = replay_source(tmp_path)
    add_phase5_environment_reference(store, receipt, "execution-python")
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)
    outcome = run_outcome(receipt)
    observer = TelemetryObserver(_SessionBackend(), tmp_path)

    def persist_telemetry(_outcome) -> RunArtifactUpdate:
        return RunArtifactUpdate(telemetry_paths=tuple(observer.save_metrics().items()))

    recorder = V3RuntimeReportRecorder(
        RuntimeReportRequest(store, outcome, RUN_ID, accepted),
        observer,
        persist_telemetry,
    )
    request = finalization_request(
        store.path.parent,
        FinalizerScenario(
            authoritative_outcome=outcome,
            hooks=FinalizationHooks(post_cleanup_manifest=recorder),
        ),
    )
    request = replace(
        request,
        initial_artifacts=replace(
            request.initial_artifacts,
            telemetry_paths=(("resource_manifest_json", str(store.path)),),
        ),
        continuation=(
            ContinuationRunSummary(
                parent_run_id="parent-run",
                anchor_phase_id="phase_5_validation",
                inherited_phase_ids=("phase_0_env_detect",),
                resource_eligibility="retained",
                attachment_mode="local",
            )
            if continuation
            else None
        ),
        runtime_report_source=recorder.read,
    )

    # When finalization writes telemetry and summary from the recorder.
    result = finalize_run(request)

    # Then both serialized artifacts contain the exact same typed runtime payload.
    summary = json.loads(
        (store.path.parent / "summary.json").read_text(encoding="utf-8")
    )
    telemetry = json.loads((tmp_path / "telemetry.json").read_text(encoding="utf-8"))
    assert summary["runtime"] == telemetry["metadata"]["runtime"]
    assert telemetry["events"][-1]["details"]["runtime"] == summary["runtime"]
    assert summary["runtime"]["manifest_path"] == str(store.path)
    assert summary["telemetry_paths"]["resource_manifest_json"] == str(store.path)
    assert ("continuation" in summary) is continuation
    assert result.runtime_report is recorder.read()


def test_completion_console_renders_runtime_access_and_replay(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a finalized local run with one projected runtime report.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    receipt, accepted = replay_source(tmp_path)
    add_phase5_environment_reference(store, receipt, "execution-python")
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)
    outcome = run_outcome(receipt)
    report = build_runtime_report(
        RuntimeReportRequest(store, outcome, RUN_ID, accepted)
    )
    request = replace(
        finalization_request(
            store.path.parent,
            FinalizerScenario(authoritative_outcome=outcome),
        ),
        runtime_report_source=lambda: report,
    )
    result = finalize_run(request)

    # When the real V3 completion printer renders the result.
    assert result.runtime_report is not None
    print_runtime_report(result.runtime_report)
    output = capsys.readouterr().out

    # Then user-visible output includes qualified environment and replay guidance.
    assert "Execution mode: local" in output
    assert "Environment: base" in output
    assert "Accepted attempt: phase_5_validation-attempt-2" in output
    assert "Validation command: python 'validation script.py' --mode final" in output
    assert "Replay available: yes" in output
    assert "never executes replay automatically" in output
    assert tuple(render_runtime_report_lines(report))


def test_unavailable_replay_console_never_prints_success_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a failed outcome paired with a stale accepted receipt.
    store = runtime_store(tmp_path)
    receipt, accepted = replay_source(tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)
    report = build_runtime_report(
        RuntimeReportRequest(
            store,
            run_outcome(receipt, succeeded=False),
            RUN_ID,
            accepted,
        )
    )

    # When runtime lines are printed through the real completion renderer.
    for line in render_runtime_report_lines(report):
        print(line)
    output = capsys.readouterr().out

    # Then unavailability is explicit and no replay/validation command is labeled successful.
    assert "Replay available: no (run_not_successful)" in output
    assert "Replay command:" not in output
    assert "Validation command:" not in output


def test_runtime_reporting_failure_is_diagnostic_and_outcome_neutral(
    tmp_path: Path,
) -> None:
    # Given a frozen PASS and a reporting source that fails after all hooks.
    request = finalization_request(tmp_path, FinalizerScenario())

    def fail_reporting():
        raise _RuntimeProjectionInterrupted("runtime projection interrupted")

    request = replace(request, runtime_report_source=fail_reporting)

    # When finalization asks for the optional runtime projection.
    result = finalize_run(request)

    # Then reporting remains diagnostic and cannot change PASS or its exit contract.
    assert result.outcome.value == "passed"
    assert result.summary.overall_status == "PASS"
    assert result.exit_code == 0
    assert result.runtime_report is None
    assert result.diagnostics[-1].stage is FinalizationStage.RUNTIME_REPORT
