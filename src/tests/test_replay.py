from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    BackendKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    Phase5AttemptReceipt,
    artifact_file_receipt,
    load_attempt_receipt,
    phase5_attempt_authority,
)
from core.replay import (
    ContainerObservation,
    ReplayUnavailableReason,
    render_replay,
    render_replay_from_path,
)
from core.run_outcome import AcceptedAttemptId
from tests.phase5_receipt_test_support import accepted_receipt, run_outcome


def replay_authority(tmp_path: Path, receipt: Phase5AttemptReceipt):
    return phase5_attempt_authority(tmp_path / "attempt.receipt.json", receipt)


def test_local_replay_renders_exact_recorded_argv_environment_and_cwd(
    tmp_path: Path,
) -> None:
    # Given one accepted local receipt and a successful frozen outcome.
    receipt = accepted_receipt(tmp_path)

    # When replay is rendered for display.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
    )

    # Then the command is reconstructed only from structured receipt facts.
    assert rendered.available is True
    assert rendered.argv == receipt.invocation.argv
    assert rendered.cwd == str(tmp_path.resolve())
    assert "SEAM_MODE=strict" in rendered.display_command
    assert "'validation script.py'" in rendered.display_command
    assert rendered.auto_execute is False
    assert "does not guarantee deterministic output" in rendered.nondeterminism_notice


def test_retained_container_replay_uses_exact_runtime_identity_and_backend_cwd(
    tmp_path: Path,
) -> None:
    # Given an accepted retained-container receipt and a live exact container.
    receipt = accepted_receipt(tmp_path, backend_kind=BackendKind.CONTAINER)

    # When replay is rendered.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
        container_observation=ContainerObservation(
            runtime="docker", container_id="cid-123", running=True
        ),
    )

    # Then its display command uses the recorded runtime, ID, cwd, env, and argv.
    assert rendered.available is True
    assert rendered.cwd == "/workspace/project"
    assert rendered.display_command.startswith("docker exec -i -w /workspace/project")
    assert "-e SEAM_MODE=strict cid-123 python 'validation script.py'" in (
        rendered.display_command
    )
    assert rendered.auto_execute is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("overall_failure", ReplayUnavailableReason.RUN_NOT_SUCCESSFUL),
        ("unaccepted", ReplayUnavailableReason.ATTEMPT_NOT_ACCEPTED),
        ("incomplete", ReplayUnavailableReason.INCOMPLETE_RECEIPT),
        ("identity", ReplayUnavailableReason.ACCEPTED_ATTEMPT_MISMATCH),
    ],
)
def test_non_authoritative_receipt_never_renders_replay(
    tmp_path: Path,
    mutation: str,
    reason: ReplayUnavailableReason,
) -> None:
    # Given an accepted receipt with one authority condition removed.
    receipt = accepted_receipt(tmp_path)
    outcome = run_outcome(receipt)
    if mutation == "overall_failure":
        outcome = run_outcome(receipt, succeeded=False)
    elif mutation == "unaccepted":
        receipt = receipt.model_copy(update={"accepted": False})
    elif mutation == "incomplete":
        receipt = receipt.model_copy(update={"complete": False})
    else:
        outcome = replace(
            outcome,
            accepted_attempt_id=AcceptedAttemptId("phase_5_validation-attempt-99"),
        )

    # When rendering is requested.
    rendered = render_replay(
        receipt,
        outcome,
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
    )

    # Then no display command is exposed as replay authority.
    assert rendered.available is False
    assert rendered.reason is reason
    assert rendered.display_command == ""
    assert rendered.auto_execute is False


def test_tampered_artifact_hash_makes_replay_unavailable(tmp_path: Path) -> None:
    # Given an accepted receipt whose complete stdout changes afterward.
    receipt = accepted_receipt(tmp_path)
    _ = Path(receipt.artifacts.stdout.path).write_text("tampered", encoding="utf-8")

    # When replay authority is checked.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
    )

    # Then the hash mismatch is truthful and no command is rendered.
    assert rendered.available is False
    assert rendered.reason is ReplayUnavailableReason.ARTIFACT_HASH_MISMATCH
    assert rendered.display_command == ""


def test_tampered_custom_op_gate_report_makes_replay_unavailable(
    tmp_path: Path,
) -> None:
    # Given an accepted custom-op attempt with a retained hashed gate report.
    receipt = accepted_receipt(tmp_path)
    gate_path = tmp_path / "custom_op_final_gate.json"
    _ = gate_path.write_text('{"passed": true}', encoding="utf-8")
    receipt = receipt.model_copy(
        update={
            "custom_op_gate": CustomOpGateEvidence(
                status=CustomOpGateStatus.PASSED,
                report=artifact_file_receipt(gate_path),
            )
        }
    )
    _ = gate_path.write_text('{"passed": false}', encoding="utf-8")

    # When replay authority is checked.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
    )

    # Then changed acceptance evidence prevents replay rendering.
    assert rendered.available is False
    assert rendered.reason is ReplayUnavailableReason.ARTIFACT_HASH_MISMATCH


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (
            ContainerObservation(
                runtime="docker", container_id="cid-123", running=False
            ),
            ReplayUnavailableReason.CONTAINER_UNAVAILABLE,
        ),
        (None, ReplayUnavailableReason.CONTAINER_STATUS_UNKNOWN),
        (
            ContainerObservation(
                runtime="docker", container_id="other-container", running=True
            ),
            ReplayUnavailableReason.CONTAINER_IDENTITY_MISMATCH,
        ),
    ],
)
def test_removed_or_unverified_container_is_not_replay_authority(
    tmp_path: Path,
    observation: ContainerObservation | None,
    reason: ReplayUnavailableReason,
) -> None:
    # Given an accepted container receipt without a verified live container.
    receipt = accepted_receipt(tmp_path, backend_kind=BackendKind.CONTAINER)

    # When rendering is requested.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
        container_observation=observation,
    )

    # Then replay is unavailable rather than silently recreating a container.
    assert rendered.available is False
    assert rendered.reason is reason
    assert rendered.display_command == ""


def test_phase3_command_claim_cannot_be_used_as_replay_receipt(tmp_path: Path) -> None:
    # Given only an Agent/Phase 3 command claim and no actual attempt receipt.
    phase3_claim = {"run_command": "python fabricated.py", "passed": True}
    claim_path = tmp_path / "phase3-claim.json"
    _ = claim_path.write_text(json.dumps(phase3_claim), encoding="utf-8")

    # When the claim crosses the actual-attempt receipt parser.
    with pytest.raises(AttemptReceiptError) as raised:
        _ = load_attempt_receipt(claim_path)

    # Then raw prose never becomes executable or display replay authority.
    assert raised.value.kind is AttemptReceiptErrorKind.MALFORMED


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, ReplayUnavailableReason.RECEIPT_MISSING),
        ("not-json", ReplayUnavailableReason.RECEIPT_MALFORMED),
    ],
)
def test_missing_or_malformed_receipt_path_returns_typed_unavailability(
    tmp_path: Path,
    content: str | None,
    reason: ReplayUnavailableReason,
) -> None:
    # Given a missing or malformed durable receipt path.
    receipt_path = tmp_path / "attempt.receipt.json"
    if content is not None:
        _ = receipt_path.write_text(content, encoding="utf-8")
    receipt = accepted_receipt(tmp_path)

    # When replay is requested through the path boundary.
    rendered = render_replay_from_path(
        receipt_path,
        run_outcome(receipt),
        expected_run_id="run-1",
        authority=replay_authority(tmp_path, receipt),
    )

    # Then parsing failures are represented as typed unavailability.
    assert rendered.available is False
    assert rendered.reason is reason


def test_receipt_from_another_run_is_not_replay_authority(tmp_path: Path) -> None:
    # Given an accepted receipt paired with a different live run identity.
    receipt = accepted_receipt(tmp_path)

    # When replay rendering receives the other run ID.
    rendered = render_replay(
        receipt,
        run_outcome(receipt),
        expected_run_id="run-2",
        authority=replay_authority(tmp_path, receipt),
    )

    # Then the repeated attempt number cannot cross the run boundary.
    assert rendered.available is False
    assert rendered.reason is ReplayUnavailableReason.RUN_ID_MISMATCH
