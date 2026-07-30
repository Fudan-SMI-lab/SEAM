from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest

from core.artifact_store import ArtifactStore
from core.continuation_models import PhasePresentationStatus
from core.phase5_attempt_receipt import EnvironmentVariable, ShellInvocation
from core.replay import render_replay
from core.run_outcome import TerminalOutcome
from harness import run
from tests.e2e.e2e_observer import TelemetryObserver
from tests.phase5_receipt_test_support import (
    accepted_receipt,
    execution,
    issued_authority,
    run_outcome,
)
from tests.run_finalizer_test_support import (
    FinalizerScenario,
    failed_finalizer_outcome,
    finalization_request,
)
from tests.test_agent_io_logger import FakeSessionManager
from tests.trace_export_test_support import seed
from tests.trace_lifecycle_test_support import TraceExporterUnavailableError

PHASE5_SECRETS = (
    "aws-phase5-value",
    "database-phase5-value",
    "client-phase5-value",
)
SUMMARY_SECRET = "summary-sensitive-value"
TRACEBACK_SECRET = "traceback-sensitive-value"
TRACE_SECRET = "trace-export-sensitive-value"
METADATA_SECRETS = (
    "nested-aws-value",
    "nested-database-value",
    "nested-client-value",
)


def test_phase5_source_artifacts_sanitize_compound_and_cli_secrets(
    tmp_path: Path,
) -> None:
    # Given shell capture files with env assignments and separated CLI secrets.
    stdout_source = tmp_path / "captured.stdout"
    stderr_source = tmp_path / "captured.stderr"
    _ = stdout_source.write_text(
        "\n".join(
            (
                "validation started",
                f"AWS_SECRET_ACCESS_KEY={PHASE5_SECRETS[0]}",
                f'runner --client_secret "{PHASE5_SECRETS[2]}"',
            )
        ),
        encoding="utf-8",
    )
    _ = stderr_source.write_text(
        f"DaTaBaSe_PaSsWoRd='{PHASE5_SECRETS[1]}'\n",
        encoding="utf-8",
    )
    store = ArtifactStore(str(tmp_path), "run-phase5-sanitized")

    # When Phase 5 persists the ordinary attempt files from their real source paths.
    attempt = execution(store, tmp_path)
    _ = store.save_shell_attempt_artifacts(
        "run_entry_script",
        command=f"runner --client_secret={PHASE5_SECRETS[2]}",
        cwd=str(tmp_path),
        backend_workdir=str(tmp_path),
        exit_code=1,
        duration=0.01,
        stdout_source_path=str(stdout_source),
        stderr_source_path=str(stderr_source),
        execution=attempt,
    )

    # Then no recognized value survives in either persisted output byte stream.
    persisted = (
        Path(f"{attempt.reservation.prefix}.stdout.log").read_bytes()
        + Path(f"{attempt.reservation.prefix}.stderr.log").read_bytes()
    )
    assert b"validation started" in persisted
    for secret in PHASE5_SECRETS:
        assert secret.encode() not in persisted


@pytest.mark.parametrize(
    ("scenario", "expected_outcome", "expected_status"),
    (
        (
            FinalizerScenario(errors=(f"DATABASE_PASSWORD={SUMMARY_SECRET}",)),
            TerminalOutcome.PASSED,
            "PASS",
        ),
        (
            FinalizerScenario(
                status="failed",
                errors=(f"DATABASE_PASSWORD={SUMMARY_SECRET}",),
                authoritative_outcome=failed_finalizer_outcome(),
            ),
            TerminalOutcome.FAILED,
            "FAIL",
        ),
    ),
    ids=("pass", "fail"),
)
def test_pass_and_fail_summaries_sanitize_errors_before_persistence(
    tmp_path: Path,
    scenario: FinalizerScenario,
    expected_outcome: TerminalOutcome,
    expected_status: str,
) -> None:
    # Given a finalization summary error containing a compound sensitive name.
    # When the normal PASS or FAIL finalization writes summary.json.
    result = run.finalize_run(finalization_request(tmp_path, scenario))

    # Then persisted bytes are safe while public outcome tokens stay authoritative.
    persisted = (tmp_path / "summary.json").read_bytes()
    assert SUMMARY_SECRET.encode() not in persisted
    assert f'"overall_status": "{expected_status}"'.encode() in persisted
    assert result.outcome is expected_outcome


def test_trace_export_failure_sanitizes_summary_and_diagnostic_sidecars(
    tmp_path: Path,
) -> None:
    # Given an explicitly enabled exporter that raises secret-bearing diagnostic text.
    def fail_client() -> NoReturn:
        raise TraceExporterUnavailableError(f"AWS_SECRET_ACCESS_KEY={TRACE_SECRET}")

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
        FinalizerScenario(hooks=run.FinalizationHooks(trace_export=lifecycle)),
    )

    # When exporter failure is finalized into ordinary summary and diagnostics files.
    result = run.finalize_run(replace(request, trace_status_source=lifecycle.read))

    # Then both files omit the value without changing PASS or creating raw trace files.
    persisted = (tmp_path / "summary.json").read_bytes() + (
        tmp_path / "finalization_diagnostics.json"
    ).read_bytes()
    assert TRACE_SECRET.encode() not in persisted
    assert b"TraceExporterUnavailableError" in persisted
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    assert not (tmp_path / "trace").exists()


def test_traceback_and_phase_sidecars_sanitize_exception_values(tmp_path: Path) -> None:
    # Given ordinary traceback and phase error text with sensitive assignments.
    context = run.EvidenceContext(
        output_dir=tmp_path,
        temp_dir=None,
        traceback_text=(
            "Traceback (most recent call last):\n"
            f"RuntimeError: client_secret={TRACEBACK_SECRET}\n"
        ),
        phase_results=(
            run.PhaseStatus(
                phase_number=5,
                phase_id="phase_5_validation",
                label="validation",
                status=PhasePresentationStatus.FAILED,
                error=f"DATABASE_PASSWORD={TRACEBACK_SECRET}",
            ),
        ),
    )
    persister = run.EvidencePersister(context, run.TelemetrySidecars(), lambda _: None)

    # When V3 persists ordinary failure evidence.
    _ = persister(TerminalOutcome.FAILED)

    # Then every ordinary exception sidecar is sanitized and remains recognizable.
    persisted = (tmp_path / "traceback.txt").read_bytes() + (
        tmp_path / "phase_results.json"
    ).read_bytes()
    assert TRACEBACK_SECRET.encode() not in persisted
    assert b"Traceback (most recent call last)" in persisted
    assert b"phase_5_validation" in persisted


def test_observer_recursively_sanitizes_nested_metadata_and_events(
    tmp_path: Path,
) -> None:
    # Given nested observer structures with compound and mixed-case sensitive names.
    observer = TelemetryObserver(FakeSessionManager(), tmp_path)
    observer.set_metadata(
        "nested",
        {
            "AWS_SECRET_ACCESS_KEY": METADATA_SECRETS[0],
            "databasePassword": METADATA_SECRETS[1],
            "token_count": 3,
            "authorization_status": "disabled",
        },
    )
    observer.record_event(
        "trace_export_failed",
        nested={"client_SeCrEt": [METADATA_SECRETS[2]]},
        status="failed",
    )

    # When observer telemetry is persisted.
    telemetry_path = Path(observer.save_metrics()["telemetry_json"])

    # Then no nested sensitive value remains while structural fields survive.
    persisted = telemetry_path.read_bytes()
    for secret in METADATA_SECRETS:
        assert secret.encode() not in persisted
    assert b"trace_export_failed" in persisted
    assert b'"status": "failed"' in persisted
    assert b'"token_count": 3' in persisted
    assert b'"authorization_status": "disabled"' in persisted


def test_replay_is_unavailable_when_sanitized_invocation_is_not_exact(
    tmp_path: Path,
) -> None:
    # Given an accepted receipt whose command and environment were sanitized.
    receipt = accepted_receipt(tmp_path).model_copy(
        update={
            "invocation": ShellInvocation(
                argv=("python", "--client_secret", "<REDACTED>"),
                environment_delta=(
                    EnvironmentVariable(
                        name="AWS_SECRET_ACCESS_KEY",
                        value="<REDACTED>",
                    ),
                ),
            )
        }
    )

    # When replay guidance evaluates whether it can reconstruct exact inputs.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=issued_authority(tmp_path / "attempt.receipt.json", receipt),
    )

    # Then no approximate command is represented as exact replay guidance.
    assert rendered.available is False
    assert rendered.reason is not None
    assert rendered.reason.value == "invocation_sanitized"
    assert rendered.display_command == ""
