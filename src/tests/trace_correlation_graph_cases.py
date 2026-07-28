from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import harness.run as run
from core.run_outcome import TerminalOutcome
from harness.session.trace_exporter import TraceExportRequest, TraceExporter
from tests.opencode_contract_test_helpers import object_list_member, object_member
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request
from tests.trace_correlation_test_support import (
    correlation_context,
    parent_trace_reference,
    with_contradictory_child,
)
from tests.trace_export_assertions import (
    CAPTURED_AT,
    artifact_path,
    read_object,
    string_member,
)
from tests.trace_export_part_fixtures import task_part
from tests.trace_export_test_support import (
    FakeTraceClient,
    graph,
    seed,
    with_duplicate_child,
)


def test_complete_correlation_graph_links_runtime_sessions_and_tool_calls(
    tmp_path: Path,
) -> None:
    # Given one child run with a parent trace, review, timeout, attempt, and Task child.
    parent_manifest = tmp_path / "parent-manifest.json"
    _ = parent_manifest.write_bytes(b'{"raw":"parent-only"}')
    root = graph(
        "ses_root",
        child_ids=("ses_child",),
        parts=(task_part("ses_root", "ses_child"),),
    )
    child = graph("ses_child")
    client = FakeTraceClient({"ses_root": root.retrieval, "ses_child": child.retrieval})
    context = correlation_context(parent_trace=parent_trace_reference(parent_manifest))

    # When Task 21 exports the typed Task 23 projection.
    result = TraceExporter(client).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            captured_at=CAPTURED_AT,
            correlation=context,
        )
    )

    # Then every stable identifier resolves through one child-run scope in BFS order.
    manifest = read_object(result.manifest_path)
    correlation = object_member(manifest, "correlation")
    assert manifest["schema_version"] == 2
    assert correlation["complete"] is True
    assert object_member(correlation, "run_scope") == {
        "run_id": "child-run-001",
        "parent_run_id": "parent-run-001",
        "lineage_root_run_id": "parent-run-001",
        "parent_trace": {
            "run_id": "parent-run-001",
            "manifest_path": str(parent_manifest),
            "sha256": parent_trace_reference(parent_manifest).sha256,
            "size_bytes": parent_manifest.stat().st_size,
        },
    }
    assert len(object_list_member(correlation, "phase_executions")) == 1
    assert object_list_member(correlation, "phase5_attempts")[0]["attempt_id"] == (
        "phase_5_validation-attempt-2"
    )
    assert [
        item["logical_round"]
        for item in object_list_member(correlation, "review_rounds")
    ] == [1, 2]
    assert {
        string_member(item, "invocation_id")
        for item in object_list_member(correlation, "framework_invocations")
    } == {"framework-review-000001", "framework-review-000002"}
    assert (
        object_list_member(correlation, "transport_attempts")[0]["transport_attempt_id"]
        == "transport-000009:attempt-2"
    )
    sessions = object_list_member(correlation, "sessions")
    assert [item["session_id"] for item in sessions] == ["ses_root", "ses_child"]
    assert sessions[1]["root_session_id"] == "ses_root"
    assert sessions[1]["parent_session_id"] == "ses_root"
    assert object_list_member(correlation, "tool_calls")[0]["call_id"] == (
        "call_prt_task"
    )
    root_payload = read_object(
        artifact_path(
            result.manifest_path.parent,
            object_list_member(manifest, "sessions")[0],
        )
    )
    assert root_payload["messages"] == root.messages
    assert root_payload["raw_contract"] == root.retrieval.contract.to_json_value()
    assert client.calls == ["ses_root", "ses_child"]
    assert result.correlation_complete is True


def test_invalid_correlation_edges_are_incomplete_without_duplicate_fetches(
    tmp_path: Path,
) -> None:
    # Given duplicate/cyclic/contradictory session edges and cross-run orphan records.
    root = with_duplicate_child(graph("ses_root"), "ses_root", "ses_child")
    root = with_contradictory_child(root)
    child = graph("ses_child", child_ids=("ses_root",))
    client = FakeTraceClient({"ses_root": root.retrieval, "ses_child": child.retrieval})
    context = correlation_context(
        review_run_id="foreign-run",
        review_session_id="ses_orphan",
        duplicate_review=True,
    )

    # When the exporter correlates all accessible records.
    result = TraceExporter(client).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            captured_at=CAPTURED_AT,
            correlation=context,
        )
    )

    # Then defects are explicit, no record gains authority, and each session is fetched once.
    manifest = read_object(result.manifest_path)
    correlation = object_member(manifest, "correlation")
    codes = {
        string_member(item, "code")
        for item in object_list_member(correlation, "diagnostics")
    }
    assert correlation["complete"] is False
    assert {
        "cross_run_record",
        "duplicate_record",
        "orphan_session",
        "duplicate_child_id",
        "child_parent_mismatch",
        "cycle_detected",
    } <= codes
    assert client.calls == ["ses_root", "ses_child"]
    assert result.complete is False
    assert result.correlation_complete is False


def test_trace_correlation_is_linked_in_summary_without_becoming_outcome(
    tmp_path: Path,
) -> None:
    # Given a frozen PASS finalization with a complete run-scoped trace context.
    parent_manifest = tmp_path / "parent-manifest.json"
    _ = parent_manifest.write_bytes(b'{"parent":"trace"}')
    client = FakeTraceClient({"ses_root": graph("ses_root").retrieval})
    lifecycle = run.TraceLifecycle(
        run.TraceLifecycleRequest(
            policy=run.TraceCapturePolicy.from_cli(True),
            destination=tmp_path / "trace",
            client_source=lambda: client,
            seeds_source=lambda: (seed("ses_root"),),
            correlation_source=lambda: correlation_context(
                parent_trace=parent_trace_reference(parent_manifest)
            ),
        )
    )
    request = finalization_request(
        tmp_path,
        FinalizerScenario(hooks=run.FinalizationHooks(trace_export=lifecycle)),
    )

    # When Task 22 finalizes the run.
    result = run.finalize_run(replace(request, trace_status_source=lifecycle.read))

    # Then summary linkage is additive and cannot change PASS/exit authority.
    correlation = result.summary.trace.correlation
    assert correlation is not None
    assert correlation.run_id == "child-run-001"
    assert correlation.parent_run_id == "parent-run-001"
    assert correlation.complete is True
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    summary = read_object(tmp_path / "summary.json")
    assert (
        object_member(object_member(summary, "trace"), "correlation")["run_id"]
        == "child-run-001"
    )
