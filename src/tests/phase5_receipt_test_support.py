from __future__ import annotations

from pathlib import Path

from core.artifact_store import ArtifactStore
from core.phase5_authority_registry import Phase5AuthorityRegistry
from core.phase5_attempt_receipt import (
    ArtifactFileReceipt,
    BackendExecution,
    BackendKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    EnvironmentVariable,
    Phase5AttemptId,
    Phase5AttemptAuthority,
    Phase5AttemptReceipt,
    ReviewAcceptanceEvidence,
    ShellArtifactsReceipt,
    ShellAttemptExecution,
    ShellInvocation,
    artifact_file_receipt,
    load_attempt_receipt,
)
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    RunOutcome,
    TerminalAnchor,
    WorkflowTerminal,
)


def artifact_file(path: Path) -> ArtifactFileReceipt:
    return artifact_file_receipt(path)


def execution(store: ArtifactStore, tmp_path: Path) -> ShellAttemptExecution:
    reservation = store.reserve_phase5_attempt()
    return ShellAttemptExecution(
        reservation=reservation,
        invocation=ShellInvocation(argv=("python", "validate.py")),
        backend=BackendExecution(
            kind=BackendKind.LOCAL,
            namespace="host",
            host_cwd=str(tmp_path.resolve()),
            backend_cwd=str(tmp_path.resolve()),
        ),
    )


def save_attempt(
    store: ArtifactStore,
    tmp_path: Path,
    *,
    exit_code: int,
) -> Path:
    metadata = store.save_shell_attempt_artifacts(
        "run_entry_script",
        command="ignored display prose",
        cwd=str(tmp_path),
        backend_workdir=str(tmp_path),
        exit_code=exit_code,
        duration=0.01,
        stdout="ok" if exit_code == 0 else "",
        stderr="" if exit_code == 0 else "failed",
        execution=execution(store, tmp_path),
    )
    receipt_path = metadata.get("receipt_path")
    assert isinstance(receipt_path, str)
    return Path(receipt_path)


def review(outcome: ReviewOutcome) -> ReviewAcceptanceEvidence:
    return ReviewAcceptanceEvidence(
        enabled=outcome is not ReviewOutcome.DISABLED,
        outcome=outcome,
    )


def authority(store: ArtifactStore, receipt_path: Path) -> Phase5AttemptAuthority:
    value = store.phase5_attempt_authority(str(receipt_path))
    assert value is not None
    receipt = load_attempt_receipt(receipt_path)
    if receipt.complete and value.finalized_digest is None:
        store.record_finalized_phase5_authority(str(receipt_path), receipt)
        value = store.phase5_attempt_authority(str(receipt_path))
        assert value is not None
    return value


def issued_authority(
    receipt_path: Path,
    receipt: Phase5AttemptReceipt,
) -> Phase5AttemptAuthority:
    return Phase5AuthorityRegistry().register(receipt_path, receipt)


def accepted_receipt(
    tmp_path: Path,
    *,
    backend_kind: BackendKind = BackendKind.LOCAL,
) -> Phase5AttemptReceipt:
    stdout_path = tmp_path / "run_entry_script_attempt0002.stdout.log"
    stderr_path = tmp_path / "run_entry_script_attempt0002.stderr.log"
    metadata_path = tmp_path / "run_entry_script_attempt0002.meta.json"
    _ = stdout_path.write_text("validation passed\n", encoding="utf-8")
    _ = stderr_path.write_text("", encoding="utf-8")
    _ = metadata_path.write_text('{"complete": true}', encoding="utf-8")
    container = backend_kind is BackendKind.CONTAINER
    return Phase5AttemptReceipt(
        run_id="run-1",
        reservation_nonce="0123456789abcdef0123456789abcdef",
        attempt_id=Phase5AttemptId("phase_5_validation-attempt-2"),
        attempt_number=2,
        invocation=ShellInvocation(
            argv=("python", "validation script.py", "--mode", "final"),
            environment_delta=(EnvironmentVariable(name="SEAM_MODE", value="strict"),),
        ),
        backend=BackendExecution(
            kind=backend_kind,
            namespace="container:cid-123" if container else "host",
            host_cwd=str(tmp_path.resolve()),
            backend_cwd="/workspace/project" if container else str(tmp_path.resolve()),
            runtime="docker" if container else None,
            container_id="cid-123" if container else None,
            container_retained=container,
        ),
        artifacts=ShellArtifactsReceipt(
            stdout=artifact_file(stdout_path),
            stderr=artifact_file(stderr_path),
            metadata=artifact_file(metadata_path),
        ),
        shell_exit_code=0,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=ReviewAcceptanceEvidence(enabled=False, outcome=ReviewOutcome.DISABLED),
        complete=True,
        accepted=True,
    )


def run_outcome(
    receipt: Phase5AttemptReceipt,
    *,
    succeeded: bool = True,
) -> RunOutcome:
    anchor = PhaseId("phase_5_validation" if succeeded else "phase_6_report")
    return RunOutcome(
        validation_succeeded=succeeded,
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=anchor),
        executed_phases=(PhaseId("phase_5_validation"), anchor),
        accepted_attempt_id=(
            AcceptedAttemptId(receipt.attempt_id) if succeeded else None
        ),
        review_rounds=(),
    )
