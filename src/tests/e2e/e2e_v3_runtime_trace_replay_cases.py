from __future__ import annotations

from pathlib import Path

import pytest

from tests.opencode_contract_test_helpers import object_list_member, object_member
from tests.trace_export_assertions import artifact_path
from tests.trace_export_part_fixtures import reasoning_part, task_part, terminal_parts
from tests.trace_export_test_support import FakeTraceClient, graph, seed

from .e2e_v3_runtime_fakes import SessionScript, concrete_trace_client
from .e2e_v3_runtime_fixture import RuntimeScenario, read_json, run_runtime_scenario


def test_v3_runtime_replay_uses_accepted_receipt_without_auto_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a successful local Phase 5 with one actual accepted receipt.
    scenario = RuntimeScenario(run_hex="f" * 32)

    # When final runtime reporting projects replay guidance.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then replay is display-only and validation executed exactly once.
    summary = read_json(result.report_dir / "summary.json")
    runtime = object_member(summary, "runtime")
    replay = object_member(runtime, "replay")
    assert result.exit_code == 0
    assert replay["available"] is True
    assert replay["accepted_attempt_id"] == "phase_5_validation-attempt-1"
    assert replay["auto_execute"] is False
    assert "SEAM never executes replay automatically" in str(
        replay["nondeterminism_notice"]
    )
    assert result.validation_count_path.read_text(encoding="utf-8") == "1"
    assert result.manager.message_post_count == 0


def test_v3_runtime_exports_recursive_raw_trace_once_without_summary_duplication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a deterministic root, child, and grandchild raw session graph.
    root = graph(
        "ses_root",
        child_ids=("ses_child",),
        parts=(reasoning_part("ses_root"), task_part("ses_root", "ses_child")),
    )
    child = graph(
        "ses_child",
        child_ids=("ses_grandchild",),
        parts=(task_part("ses_child", "ses_grandchild"),),
    )
    grandchild = graph(
        "ses_grandchild",
        parts=tuple(terminal_parts("ses_grandchild")),
    )
    fake_client = FakeTraceClient(
        {
            "ses_root": root.retrieval,
            "ses_child": child.retrieval,
            "ses_grandchild": grandchild.retrieval,
        }
    )
    client = concrete_trace_client(
        fake_client,
        ("ses_root", "ses_child", "ses_grandchild"),
    )
    script = SessionScript(trace_client=client, trace_seeds=(seed("ses_root"),))
    scenario = RuntimeScenario(
        run_hex="1" * 32,
        save_agent_trace=True,
        session_script=script,
    )

    # When the public V3 finalizer invokes the real transactional exporter.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then traversal and correlation are truthful without concise duplication.
    trace_root = result.report_dir / "trace"
    manifest = read_json(trace_root / "manifest.json")
    counts = object_member(manifest, "counts")
    assert result.exit_code == 0
    assert fake_client.calls == ["ses_root", "ses_child", "ses_grandchild"]
    assert manifest["schema_version"] == 2
    assert manifest["session_ids"] == ["ses_root", "ses_child", "ses_grandchild"]
    assert counts["session_count"] == 3
    assert counts["child_edge_count"] == 2
    root_record = object_list_member(manifest, "sessions")[0]
    root_payload = read_json(artifact_path(trace_root, root_record))
    assert root_payload["messages"] == root.messages
    assert root_payload["raw_contract"] == root.retrieval.contract.to_json_value()
    summary_text = (result.report_dir / "summary.json").read_text(encoding="utf-8")
    assert "persisted accessible reasoning" not in summary_text
    assert result.manager.message_post_count == 0


def test_v3_runtime_exporter_failure_preserves_pass_and_omits_trace_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an enabled trace whose read-only client fails at export time.
    script = SessionScript(
        trace_client_error=RuntimeError("export unavailable"),
        trace_seeds=(seed("ses_root"),),
    )
    scenario = RuntimeScenario(
        run_hex="2" * 32,
        save_agent_trace=True,
        session_script=script,
    )

    # When optional trace export fails inside real finalization.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then frozen PASS/0 survives and no partial trace directory is published.
    summary = read_json(result.report_dir / "summary.json")
    trace = object_member(summary, "trace")
    assert result.exit_code == 0
    assert summary["overall_status"] == "PASS"
    assert trace["complete"] is False
    assert "export unavailable" in str(trace["errors"])
    assert not (result.report_dir / "trace").exists()
