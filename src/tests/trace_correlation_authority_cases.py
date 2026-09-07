from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import harness.run as run
from core.artifact_store import ArtifactStore
from core.continuation import resolve_terminal_parent
from core.run_manifest import RunId
from core.run_outcome import PhaseId
from core.runtime_observability_models import ObservabilitySummary, TimeoutDetails
from core.terminal_continuation_models import ContinuationPromptFacts
from core.trace_correlation_models import (
    FrameworkInvocationId,
    RunCorrelationScope,
    SessionId,
    make_phase_execution_id,
    make_transport_attempt_id,
)
from harness.run.trace_correlation_transports import (
    TransportCorrelationRequest,
    build_transport_correlations,
)
from harness.session.trace_correlation_models import FrameworkInvocationCorrelation
from harness.session.trace_exporter import TraceExportRequest, TraceExporter
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request
from tests.terminal_run_continuation_hydration_support import (
    PHASE_ORDER,
    create_hydration_parent,
    hydrate,
)
from tests.trace_correlation_test_support import (
    correlation_context,
    parent_trace_reference,
)
from tests.trace_export_assertions import CAPTURED_AT
from tests.trace_export_part_fixtures import task_part
from tests.trace_export_test_support import FakeTraceClient, graph, seed


def test_transport_reused_invocation_id_with_conflicting_session_is_incomplete() -> (
    None
):
    # Given a known framework invocation and a timeout that rebinds it to another session.
    run_id = RunId("run-001")
    phase_id = PhaseId("phase_5_validation")
    execution_id = make_phase_execution_id(run_id, phase_id)
    invocation_id = FrameworkInvocationId("framework-review-000001")
    existing = FrameworkInvocationCorrelation(
        run_id,
        execution_id,
        invocation_id,
        SessionId("ses_root"),
    )
    transport_id = "transport-000001"
    attempt_id = make_transport_attempt_id(transport_id, 1)
    timeout = TimeoutDetails(
        record_id="run-001:transport:transport-000001:1:timeout",
        run_id=str(run_id),
        phase_execution_id=str(execution_id),
        framework_invocation_id=str(invocation_id),
        transport_invocation_id=transport_id,
        transport_attempt_id=str(attempt_id),
        event_phase="timeout",
        agent="reviewer",
        sub_phase=str(phase_id),
        session_id="ses_other",
        command_id=transport_id,
        attempt=1,
        max_attempts=2,
        configured_timeout_seconds=30.0,
        elapsed_seconds=30.0,
        retry_decision="retry_same_session",
        reason="request_timeout",
        exhausted=False,
    )

    # When transport correlation projects the timeout.
    result = build_transport_correlations(
        TransportCorrelationRequest(
            scope=RunCorrelationScope(run_id, None, run_id, None),
            phase_ids=frozenset({str(phase_id)}),
            observability=ObservabilitySummary(timeouts=(timeout,)),
            invocations=(existing,),
        )
    )

    # Then the contradictory invocation cannot contribute to a complete graph.
    assert [item.code for item in result.diagnostics] == ["contradictory_invocation"]


def test_child_trace_references_parent_identity_without_copying_raw_payload(
    tmp_path: Path,
) -> None:
    # Given an immutable parent trace containing bytes forbidden in child trace/prompt.
    parent_dir = tmp_path / "parent-run-001" / "trace"
    parent_dir.mkdir(parents=True)
    parent_manifest = parent_dir / "manifest.json"
    parent_bytes = b'{"raw_parent_payload":"PARENT-RAW-MUST-NOT-COPY"}'
    _ = parent_manifest.write_bytes(parent_bytes)
    root = graph(
        "ses_root",
        child_ids=("ses_child",),
        parts=(task_part("ses_root", "ses_child"),),
    )
    client = FakeTraceClient(
        {"ses_root": root.retrieval, "ses_child": graph("ses_child").retrieval}
    )
    context = correlation_context(parent_trace=parent_trace_reference(parent_manifest))
    child_report_dir = tmp_path / "child-run-001"
    child_report_dir.mkdir()

    # When the child trace and bounded continuation prompt are produced.
    result = TraceExporter(client).export(
        TraceExportRequest(
            destination=child_report_dir / "trace",
            seeds=(seed("ses_root"),),
            captured_at=CAPTURED_AT,
            correlation=context,
        )
    )
    prompt = ContinuationPromptFacts(
        parent_run_id="parent-run-001",
        child_run_id="child-run-001",
        anchor_phase_id="phase_5_validation",
        inherited_phase_ids=("phase_0_detect",),
        resource_eligibility="retained_environment_verified",
        attachment_mode="none",
    ).render()

    # Then only immutable path/digest identity crosses into child correlation.
    assert parent_manifest.read_bytes() == parent_bytes
    child_bytes = b"".join(
        path.read_bytes()
        for path in result.manifest_path.parent.rglob("*")
        if path.is_file()
    )
    assert b"PARENT-RAW-MUST-NOT-COPY" not in child_bytes
    assert "PARENT-RAW-MUST-NOT-COPY" not in prompt
    assert str(parent_manifest) not in prompt
    assert "raw_trace" not in prompt


def test_trace_deletion_and_corruption_do_not_change_continuation_authority(
    tmp_path: Path,
) -> None:
    # Given one authoritative PASS parent and a non-authoritative trace sidecar.
    parent = create_hydration_parent(
        tmp_path,
        status="PASS",
        anchor_phase="phase_6_report",
        phase_statuses=("passed",) * 5,
        canonical_phase_ids=PHASE_ORDER,
    )
    trace_dir = parent.summary_path.parent / "trace"
    trace_dir.mkdir()
    trace_path = trace_dir / "manifest.json"
    _ = trace_path.write_bytes(b'{"diagnostic":"original"}')
    checkpoint_store = ArtifactStore(str(parent.project_dir), "checkpoint-proof")
    checkpoint = {"phase": "phase_5_validation", "complete": True}
    _ = checkpoint_store.save_checkpoint(checkpoint)
    resolved_before = resolve_terminal_parent(parent.summary_path)
    hydration_before = hydrate(parent, resolved=resolved_before)

    # When trace bytes are corrupted and then deleted.
    _ = trace_path.write_bytes(b"corrupt trace bytes")
    resolved_corrupt = resolve_terminal_parent(parent.summary_path)
    hydration_corrupt = hydrate(parent, resolved=resolved_corrupt)
    trace_path.unlink()
    resolved_deleted = resolve_terminal_parent(parent.summary_path)
    hydration_deleted = hydrate(parent, resolved=resolved_deleted)

    # Then parent resolution, anchor selection, and hydration remain byte-authoritative.
    assert resolved_corrupt == resolved_before == resolved_deleted
    assert hydration_corrupt == hydration_before == hydration_deleted
    assert str(hydration_deleted.start_phase_id) == "phase_5_validation"
    assert checkpoint_store.load_checkpoint() == checkpoint


def test_missing_or_corrupt_trace_status_cannot_change_run_outcome_or_exit(
    tmp_path: Path,
) -> None:
    # Given two frozen PASS runs whose trace paths are missing or corrupt.
    statuses = (
        run.TraceLifecycleStatus(True, True, False, str(tmp_path / "missing"), ()),
        run.TraceLifecycleStatus(True, True, False, str(tmp_path / "corrupt"), ()),
    )
    exits: list[int] = []

    # When each observational status is finalized.
    for index, status in enumerate(statuses):
        output = tmp_path / f"run-{index}"
        output.mkdir()
        request = finalization_request(output, FinalizerScenario())
        result = run.finalize_run(
            replace(request, trace_status_source=lambda status=status: status)
        )
        exits.append(result.exit_code)
        assert result.outcome.value == "passed"

    # Then neither missing nor corrupt trace changes RunOutcome/exit mapping.
    assert exits == [0, 0]
