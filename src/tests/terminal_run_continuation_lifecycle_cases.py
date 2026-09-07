from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from harness.run import ContinuationRunSummary, finalize_run
from harness.run import FinalizationHooks, FinalizationStage, RunArtifactUpdate
from tests.run_finalizer_test_support import (
    FinalizerScenario,
    failed_finalizer_outcome,
    finalization_request,
    passing_finalizer_outcome,
)

from core.terminal_continuation_models import ContinuationPromptFacts
from core.continuation_models import RunSummaryDocument
from core.continuation_environment import (
    ExistingContainerAttachment,
    Phase2EstablishmentEligible,
)
from core.terminal_continuation_environment import (
    apply_verified_continuation_backend,
)
from core.types import ExecutionBackendConfig
from tests.terminal_run_continuation_hydration_support import (
    create_hydration_parent,
)
from core.continuation import resolve_terminal_parent


class _RequiredSealFailure(RuntimeError):
    pass


def test_continuation_prompt_context_is_bounded_and_path_free() -> None:
    # Given
    facts = ContinuationPromptFacts(
        parent_run_id="parent-run-001",
        child_run_id="child-run-001",
        anchor_phase_id="phase_5_validation",
        inherited_phase_ids=("phase_0_detect", "phase_2_prepare"),
        resource_eligibility="retained_environment_verified",
        attachment_mode="existing_container",
    )

    # When
    rendered = facts.render()

    # Then
    assert len(rendered.encode("utf-8")) <= 8192
    assert "parent-run-001" in rendered
    assert "phase_2_prepare" in rendered
    assert ".sm-artifacts" not in rendered
    assert "sealed-artifacts" not in rendered
    assert "raw_trace" not in rendered


@pytest.mark.parametrize(
    ("passes", "expected_status", "expected_exit"),
    ((True, "PASS", 0), (False, "FAIL", 1)),
)
def test_child_summary_records_lineage_with_independent_outcome(
    tmp_path: Path,
    passes: bool,
    expected_status: str,
    expected_exit: int,
) -> None:
    # Given
    output_dir = tmp_path / ("passing-child" if passes else "failing-child")
    output_dir.mkdir()
    outcome = passing_finalizer_outcome() if passes else failed_finalizer_outcome()
    request = finalization_request(
        output_dir,
        FinalizerScenario(authoritative_outcome=outcome),
    )
    request = replace(
        request,
        continuation=ContinuationRunSummary(
            parent_run_id="parent-run-001",
            anchor_phase_id="phase_5_validation",
            inherited_phase_ids=("phase_0_detect",),
            resource_eligibility="retained_environment_verified",
            attachment_mode="none",
        ),
    )

    # When
    result = finalize_run(request)

    # Then
    payload = RunSummaryDocument.model_validate_json(
        (output_dir / "summary.json").read_bytes()
    )
    continuation = payload.continuation
    assert continuation is not None
    assert result.exit_code == expected_exit
    assert payload.overall_status == expected_status
    assert continuation.parent_run_id == "parent-run-001"
    assert continuation.anchor_phase_id == "phase_5_validation"
    assert continuation.inherited_phase_ids == ("phase_0_detect",)
    assert continuation.resource_eligibility == "retained_environment_verified"
    assert continuation.attachment_mode == "none"


def test_phase2_eligible_attachment_pins_verified_container_id(
    tmp_path: Path,
) -> None:
    # Given
    parent_run = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_2_prepare",
        phase_statuses=("passed", "failed", "skipped", "skipped", "skipped"),
        canonical_phase_ids=("phase_0_detect",),
    )
    parent = resolve_terminal_parent(parent_run.summary_path)
    workflow = parent_run.workflow_path
    from core.continuation_workflow_snapshot import (
        load_workflow_snapshot,
        read_workflow_snapshot,
    )

    definition = load_workflow_snapshot(read_workflow_snapshot(workflow), str(workflow))
    definition.execution_backend = ExecutionBackendConfig(
        mode="container",
        source="image",
        image="sha256:image-id",
        container_name_prefix="seam-parent",
    )
    attachment = ExistingContainerAttachment(
        mode="existing_container",
        runtime="docker",
        container_id="immutable-id",
        container_name="seam-parent-123",
        owner_kind="user",
        original_owner_run_id=None,
        lineage_root_run_id=None,
        ownership_token=None,
        ownership_label=None,
    )

    # When
    apply_verified_continuation_backend(
        parent,
        definition,
        Phase2EstablishmentEligible(attachment=attachment),
    )

    # Then
    config = definition.execution_backend
    assert config is not None
    assert config.source == "existing_container"
    assert config.container_name == "immutable-id"


def test_required_child_seal_failure_is_not_published_as_pass(
    tmp_path: Path,
) -> None:
    # Given
    output_dir = tmp_path / "child"
    output_dir.mkdir()

    def fail_seal(_outcome) -> RunArtifactUpdate:
        raise _RequiredSealFailure

    request = finalization_request(
        output_dir,
        FinalizerScenario(
            hooks=FinalizationHooks(trace_export=fail_seal),
            authoritative_outcome=passing_finalizer_outcome(),
        ),
    )
    request = replace(
        request,
        continuation=ContinuationRunSummary(
            parent_run_id="parent-run-001",
            anchor_phase_id="phase_5_validation",
            inherited_phase_ids=(),
            resource_eligibility="retained_environment_verified",
            attachment_mode="none",
        ),
        required_stages=frozenset({FinalizationStage.TRACE_EXPORT}),
        summary_required=True,
    )

    # When
    result = finalize_run(request)

    # Then
    assert result.exit_code == 1
    assert result.summary_path is None
    assert not (output_dir / "summary.json").exists()


def test_required_child_seal_rejected_artifact_is_not_published_as_pass(
    tmp_path: Path,
) -> None:
    # Given
    output_dir = tmp_path / "child"
    output_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    def invalid_seal(_outcome) -> RunArtifactUpdate:
        return RunArtifactUpdate(directory_paths=(("sealed", str(outside)),))

    request = finalization_request(
        output_dir,
        FinalizerScenario(
            hooks=FinalizationHooks(trace_export=invalid_seal),
            authoritative_outcome=passing_finalizer_outcome(),
        ),
    )
    request = replace(
        request,
        continuation=ContinuationRunSummary(
            parent_run_id="parent-run-001",
            anchor_phase_id="phase_5_validation",
            inherited_phase_ids=(),
            resource_eligibility="retained_environment_verified",
            attachment_mode="none",
        ),
        required_stages=frozenset({FinalizationStage.TRACE_EXPORT}),
        summary_required=True,
    )

    # When
    result = finalize_run(request)

    # Then
    assert result.exit_code == 1
    assert result.summary_path is None
    assert not (output_dir / "summary.json").exists()
