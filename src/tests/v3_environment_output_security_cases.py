from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.phase5_attempt_receipt import (
    BackendKind,
    EnvironmentVariable,
    phase5_attempt_authority,
)
from core.resource_retention import ContainerCleanupStatus
from core.v3_runtime_report import (
    AcceptedReplaySource,
    RuntimeReportRequest,
    build_runtime_report,
)
from core.v3_runtime_report_integration import _bind_environment
from tests.phase5_receipt_test_support import accepted_receipt, run_outcome
from tests.v3_environment_output_test_support import (
    RUN_ID,
    add_base_environment,
    add_container_environment,
    add_phase5_environment_reference,
    replay_source,
    runtime_store,
    seal_lifecycle,
)


def test_accepted_attempt_requires_exact_executable_environment_binding(
    tmp_path: Path,
) -> None:
    # Given one host environment whose absolute executable differs from receipt argv.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    _receipt, source = replay_source(tmp_path)

    # When integration tries to bind the accepted attempt.
    _bind_environment(store, source)

    # Then namespace uniqueness cannot substitute for executable identity.
    assert store.read().phase5_environment_references == ()


def test_accepted_attempt_requires_same_run_environment_binding(tmp_path: Path) -> None:
    # Given a receipt from another run whose executable otherwise matches exactly.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    receipt = accepted_receipt(tmp_path).model_copy(
        update={
            "run_id": "other-run",
            "invocation": accepted_receipt(tmp_path).invocation.model_copy(
                update={"argv": (sys.executable, "validation.py")}
            ),
        }
    )
    path = tmp_path / "cross-run.receipt.json"
    _ = path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    source = AcceptedReplaySource(path, phase5_attempt_authority(path, receipt))

    # When integration considers the cross-run authority.
    _bind_environment(store, source)

    # Then no current-run manifest reference is created.
    assert store.read().phase5_environment_references == ()


def test_accepted_attempt_without_exact_reference_has_no_active_environment(
    tmp_path: Path,
) -> None:
    # Given one environment but no exact Phase 5 reference for the accepted attempt.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    receipt, source = replay_source(tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    # When the runtime report selects the active environment.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, source)
    )

    # Then it does not guess from a singleton environment.
    assert report.active_environment_id is None


def test_public_replay_loads_once_and_redacts_secrets_and_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an accepted receipt containing environment, argv, and terminal secrets.
    store = runtime_store(tmp_path)
    add_base_environment(store)
    original = accepted_receipt(tmp_path)
    receipt = original.model_copy(
        update={
            "run_id": RUN_ID,
            "invocation": original.invocation.model_copy(
                update={
                    "argv": (
                        "python",
                        "validation.py",
                        "--token",
                        "argv-secret",
                        "\x1b[31mcontrol",
                    ),
                    "environment_delta": (
                        EnvironmentVariable(name="API_TOKEN", value="env-secret"),
                    ),
                }
            ),
        }
    )
    path = tmp_path / "secret.receipt.json"
    _ = path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    source = AcceptedReplaySource(path, phase5_attempt_authority(path, receipt))
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)
    loads = 0

    def load_once(_path: Path):
        nonlocal loads
        loads += 1
        return receipt

    monkeypatch.setattr("core.replay.load_attempt_receipt", load_once)
    monkeypatch.setattr("core.v3_runtime_replay.load_attempt_receipt", load_once)

    # When public runtime output derives replay guidance.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, source)
    )
    serialized = report.model_dump_json()

    # Then one authority-checked object feeds a sanitized public projection.
    assert loads == 1
    assert "env-secret" not in serialized
    assert "argv-secret" not in serialized
    assert "\x1b" not in serialized
    assert "REDACTED" in serialized


def test_public_replay_redacts_compound_credentials_and_unicode_controls(
    tmp_path: Path,
) -> None:
    # Given accepted argv with compound credential flags and C1/format controls.
    store = runtime_store(tmp_path)
    original = accepted_receipt(tmp_path)
    receipt = original.model_copy(
        update={
            "run_id": RUN_ID,
            "invocation": original.invocation.model_copy(
                update={
                    "argv": (
                        "python3",
                        "validation.py",
                        "--access-token",
                        "access-secret",
                        "--client_secret=client-secret",
                        "\u009b31mcontrol",
                        "zero\u200bwidth",
                    )
                }
            ),
        }
    )
    path = tmp_path / "compound-secret.receipt.json"
    _ = path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    source = AcceptedReplaySource(path, phase5_attempt_authority(path, receipt))
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)

    # When public replay guidance is projected.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, source)
    )
    serialized = report.model_dump_json()

    # Then compound values and every Unicode control class are rendered inert.
    assert "access-secret" not in serialized
    assert "client-secret" not in serialized
    assert "\u009b" not in serialized
    assert "\u200b" not in serialized
    assert "REDACTED" in serialized


def test_tampered_retention_entry_is_derived_from_authenticated_identity(
    tmp_path: Path,
) -> None:
    # Given a sealed retained-container manifest whose derived entry text is replaced.
    store = runtime_store(tmp_path, effective_backend="container")
    add_container_environment(store)
    receipt, source = replay_source(tmp_path, backend_kind=BackendKind.CONTAINER)
    add_phase5_environment_reference(store, receipt, "execution-python")
    seal_lifecycle(
        store,
        cleanup=ContainerCleanupStatus.RETAINED,
        entry_command="docker exec -it cid-123 bash",
        post_state="running",
    )
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    for fact in payload["facts"]:
        if fact["name"] == "retention.entry_command":
            fact["value"] = "docker exec cid-123 sh -c 'attacker-command'"
            fact["authority_tag"] = None
    _ = store.path.write_text(json.dumps(payload), encoding="utf-8")

    # When access guidance is projected from the manifest.
    report = build_runtime_report(
        RuntimeReportRequest(store, run_outcome(receipt), RUN_ID, source)
    )

    # Then untrusted command text is ignored in favor of fixed authenticated identity.
    assert report.access.entry_command == "docker exec -it cid-123 bash"
    assert "attacker-command" not in report.model_dump_json()
