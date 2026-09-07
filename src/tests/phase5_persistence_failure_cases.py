from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

from core.artifact_store import ArtifactStore
from core.phase5_attempt_runtime import accept_phase5_receipt
from core.phase5_attempt_receipt import load_attempt_receipt
from core.run_outcome import ReviewOutcome
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.v3_outcome_mapping import Phase5Decision, PhaseDisposition
from core.workflow_executor import WorkflowExecutor
from tests.phase5_receipt_test_support import execution


def test_successful_rerun_with_receipt_failure_cannot_reuse_prior_attempt(
    tmp_path: Path,
) -> None:
    # Given a fail-then-success command whose second receipt persistence fails.
    store = ArtifactStore(str(tmp_path), "run-persistence-failure")
    marker = tmp_path / "ran.marker"
    script = tmp_path / "validate_once.py"
    _ = script.write_text(
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        "if marker.exists(): raise SystemExit(0)\n"
        "marker.write_text('failed', encoding='utf-8')\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}"'
    phase5 = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        sub_workflow="repair_loop",
        input_mapping={"entry_script": command},
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="task-18-persistence-failure",
        version="1.0",
        globals={"review_gate_enabled": False, "review_fail_closed": True},
        phases=[phase5],
        terminals=["complete"],
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                type="loop",
                max_iterations=2,
                stop_conditions=[
                    {"condition": "$.script_exit_code == 0", "status": "success"}
                ],
                phases=[
                    {
                        "id": "run_entry_script",
                        "type": "shell",
                        "command": "${loop_vars.entry_script}",
                        "on_failure": "continue",
                    }
                ],
            )
        },
    )
    original_writer = store.save_shell_attempt_artifacts
    writes = 0

    def fail_second_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated receipt failure")
        return original_writer(*args, **kwargs)

    store.save_shell_attempt_artifacts = fail_second_write
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.hook_manager = MagicMock()

    # When Phase 5 reaches shell success without current receipt authority.
    result = executor.execute({})

    # Then the run fails closed and the previous failed receipt stays rejected.
    outcome = result["run_outcome"]
    assert outcome.validation_succeeded is False
    assert outcome.accepted_attempt_id is None
    first = load_attempt_receipt(
        Path(store.artifact_dir)
        / "shell_attempts"
        / "run_entry_script_attempt0001.receipt.json"
    )
    assert first.accepted is False


def test_internal_receipt_failure_rolls_back_attempt_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given shell artifacts are written before a simulated receipt failure.
    store = ArtifactStore(str(tmp_path), "run-rollback")
    attempt = execution(store, tmp_path)

    def fail_receipt(*_args, **_kwargs) -> None:
        raise OSError("simulated receipt write failure")

    monkeypatch.setattr(
        "core.phase5_artifact_store.write_attempt_receipt", fail_receipt
    )

    # When persistence reaches the receipt write.
    with pytest.raises(OSError, match="simulated receipt write failure"):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="ok",
            stderr="",
            execution=attempt,
        )

    # Then no partial attempt artifact remains.
    prefix = Path(attempt.reservation.prefix)
    assert not Path(f"{prefix}.stdout.log").exists()
    assert not Path(f"{prefix}.stderr.log").exists()
    assert not Path(f"{prefix}.meta.json").exists()
    assert not Path(attempt.reservation.receipt_path).exists()


def test_compatibility_exhaustion_requires_finalized_receipt_authority(
    tmp_path: Path,
) -> None:
    # Given compatibility review exhaustion after successful shell validation.
    decision = Phase5Decision(
        validation_succeeded=True,
        review_outcome=ReviewOutcome.REJECT_EXHAUSTED,
        review_rounds=(),
        review_fail_closed=False,
        accepted_attempt_id=None,
        parent_disposition=PhaseDisposition.SUCCEEDED,
    )
    store = ArtifactStore(str(tmp_path), "run-compatibility-persistence-failure")

    # When current receipt persistence produced no finalized authority.
    revised = accept_phase5_receipt({"loop_state": {}}, decision, store)

    # Then compatibility cannot turn unpersisted validation into PASS authority.
    assert revised.validation_succeeded is False
    assert revised.accepted_attempt_id is None
    assert revised.parent_disposition is PhaseDisposition.FAILED


def test_metadata_hash_failure_rolls_back_shell_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given both shell artifacts exist before metadata hashing fails.
    store = ArtifactStore(str(tmp_path), "run-hash-rollback")
    attempt = execution(store, tmp_path)

    def fail_hash(_content: bytes):
        raise OSError("simulated hash failure")

    monkeypatch.setattr("core.phase5_artifact_store._sha256_content", fail_hash)

    # When persistence computes metadata hashes.
    with pytest.raises(OSError, match="simulated hash failure"):
        _ = store.save_shell_attempt_artifacts(
            "run_entry_script",
            command="python validate.py",
            cwd=str(tmp_path),
            backend_workdir=str(tmp_path),
            exit_code=0,
            duration=0.01,
            stdout="ok",
            stderr="",
            execution=attempt,
        )

    # Then metadata failure leaves no partial shell files.
    prefix = Path(attempt.reservation.prefix)
    assert not Path(f"{prefix}.stdout.log").exists()
    assert not Path(f"{prefix}.stderr.log").exists()
    assert not Path(f"{prefix}.meta.json").exists()
