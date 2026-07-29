from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core.agent_io_logger import AgentIOLogger
from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import EnvironmentVariable, ShellInvocation
from core.run_outcome import TerminalOutcome
from core.telemetry_bridge import TelemetryBridge
from harness.run import FinalizationHooks, RunArtifactUpdate, finalize_run
from tests.phase5_receipt_test_support import execution
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request

SECRET = "sk-abcdefghijklmnopqrstuvwxyz"
CONTEXT_SECRET = "ordinary contextual sentinel"


def test_agent_io_error_is_redacted_in_ordinary_index(tmp_path: Path) -> None:
    logger = AgentIOLogger(tmp_path, "run-secret", enabled=True, redact=True)

    _ = logger.record(
        sequence=1,
        phase_id="phase_5_validation",
        session_id="session",
        role="main_engineer",
        agent=None,
        lifecycle="persistent",
        started_at="start",
        ended_at="end",
        duration_seconds=0.1,
        timeout_seconds=5,
        status="failed",
        command="safe",
        response="safe",
        error=f"Authorization: Bearer {SECRET}",
    )

    persisted = (tmp_path / "agent_io" / "agent_io.jsonl").read_text(encoding="utf-8")
    assert SECRET not in persisted


def test_telemetry_recursively_redacts_nested_metadata(tmp_path: Path) -> None:
    bridge = TelemetryBridge(str(tmp_path))
    bridge.set_metadata(
        "nested",
        {"headers": {"Authorization": f"Bearer {SECRET}"}, "token": SECRET},
    )
    bridge.on_event(
        "request_failed",
        nested={"credentials": [{"password": SECRET}]},
    )

    path = Path(bridge.save_metrics()["telemetry_json"])
    persisted = path.read_text(encoding="utf-8")
    assert SECRET not in persisted


def test_phase5_metadata_and_receipt_redact_environment_secrets(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(str(tmp_path), "run-secret")
    attempt = replace(
        execution(store, tmp_path),
        invocation=ShellInvocation(
            argv=("python", "validate.py", f"--token={SECRET}"),
            environment_delta=(
                EnvironmentVariable(name="OPENAI_API_KEY", value=SECRET),
            ),
        ),
    )

    metadata = store.save_shell_attempt_artifacts(
        "run_entry_script",
        command=f"python validate.py --token={SECRET}",
        cwd=str(tmp_path),
        backend_workdir=str(tmp_path),
        exit_code=0,
        duration=0.01,
        stdout="ok",
        stderr="",
        execution=attempt,
    )

    meta_path = Path(str(metadata["meta_path"]))
    receipt_path = Path(attempt.reservation.receipt_path)
    assert SECRET not in meta_path.read_text(encoding="utf-8")
    assert SECRET not in receipt_path.read_text(encoding="utf-8")


def test_finalizer_exception_detail_is_redacted(tmp_path: Path) -> None:
    def fail(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        raise RuntimeError(f"OPENAI_API_KEY={SECRET}")

    hooks = FinalizationHooks(evidence_replay=fail)
    result = finalize_run(
        finalization_request(tmp_path, FinalizerScenario(hooks=hooks))
    )

    persisted = json.dumps([diagnostic.detail for diagnostic in result.diagnostics])
    assert SECRET not in persisted


def test_phase5_metadata_and_receipt_redact_stateful_cli_values(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(str(tmp_path), "run-context-secret")
    attempt = replace(
        execution(store, tmp_path),
        invocation=ShellInvocation(
            argv=(
                "python",
                "validate.py",
                '--Api-Key="assignment sentinel"',
                "--ToKeN",
                CONTEXT_SECRET,
            ),
            environment_delta=(),
        ),
    )

    metadata = store.save_shell_attempt_artifacts(
        "run_entry_script",
        command=(
            'python validate.py --Api-Key="assignment sentinel" '
            f'--ToKeN "{CONTEXT_SECRET}"'
        ),
        cwd=str(tmp_path),
        backend_workdir=str(tmp_path),
        exit_code=0,
        duration=0.01,
        stdout="ok",
        stderr="",
        execution=attempt,
    )

    persisted = "\n".join(
        (
            Path(str(metadata["meta_path"])).read_text(encoding="utf-8"),
            Path(attempt.reservation.receipt_path).read_text(encoding="utf-8"),
        )
    )
    assert "assignment sentinel" not in persisted
    assert CONTEXT_SECRET not in persisted


def test_finalizer_exception_detail_redacts_stateful_cli_sentinel(
    tmp_path: Path,
) -> None:
    def fail(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        raise RuntimeError(f'runner --PaSsWoRd "{CONTEXT_SECRET}"')

    result = finalize_run(
        finalization_request(
            tmp_path,
            FinalizerScenario(hooks=FinalizationHooks(evidence_replay=fail)),
        )
    )

    persisted = json.dumps([diagnostic.detail for diagnostic in result.diagnostics])
    assert CONTEXT_SECRET not in persisted
