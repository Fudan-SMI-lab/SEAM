from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.continuation_environment import (
    ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind,
)
from core.terminal_continuation import prepare_terminal_continuation
from tests.opencode_contract_test_helpers import object_list_member, object_member
from tests.terminal_run_continuation_test_support import tree_bytes
from tests.trace_export_part_fixtures import task_part
from tests.trace_export_test_support import FakeTraceClient, graph, seed

from .e2e_v3_runtime_fakes import SessionScript, concrete_trace_client
from .e2e_v3_runtime_continuation_support import (
    ContinuationParentSpec,
    PHASE_ORDER,
    create_runtime_parent,
    run_prepared_continuation,
)
from .e2e_v3_runtime_fixture import read_json


_ANCHOR_CASES = (
    ContinuationParentSpec(
        "pass",
        "PASS",
        "phase_6_report",
        ("passed", "passed", "passed", "passed", "passed"),
        5,
        phase5_environment_reference=True,
    ),
    ContinuationParentSpec(
        "before5",
        "FAIL",
        "phase_4_migrate",
        ("passed", "passed", "failed", "skipped", "skipped"),
        2,
    ),
    ContinuationParentSpec(
        "at5",
        "FAIL",
        "phase_5_validation",
        ("passed", "passed", "passed", "failed", "skipped"),
        3,
    ),
    ContinuationParentSpec(
        "after5",
        "FAIL",
        "phase_6_report",
        ("passed", "passed", "passed", "passed", "failed"),
        4,
        phase5_environment_reference=True,
    ),
)


@pytest.mark.parametrize("spec", _ANCHOR_CASES, ids=lambda spec: spec.case_id)
def test_v3_runtime_continuation_anchor_matrix_runs_fresh_child_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec: ContinuationParentSpec,
) -> None:
    # Given an authenticated PASS or failure-anchor parent with retained environment.
    parent = create_runtime_parent(tmp_path, monkeypatch, spec)
    parent_before = tree_bytes(parent.report_dir)
    child_id = f"child-{spec.case_id}-24"

    # When public preparation and run_e2e_v3 execute the fresh child lifecycle.
    with prepare_terminal_continuation(parent.summary_path, child_id) as prepared:
        start_phase = str(prepared.hydration.start_phase_id)
        inherited = tuple(
            str(item.phase_id) for item in prepared.hydration.phase_results
        )
        result = run_prepared_continuation(monkeypatch, prepared)

    # Then anchors, summary lineage, receipts, and parent immutability are truthful.
    expected_start = {
        "pass": "phase_5_validation",
        "before5": "phase_4_migrate",
        "at5": "phase_5_validation",
        "after5": "phase_6_report",
    }[spec.case_id]
    expected_inherited = PHASE_ORDER[: PHASE_ORDER.index(expected_start)]
    summary = read_json(result.report_dir / "summary.json")
    continuation = object_member(summary, "continuation")
    replay = object_member(object_member(summary, "runtime"), "replay")
    child_manifest = (result.report_dir / "run-manifest.v1.json").read_text(
        encoding="utf-8"
    )
    child_manifest_json = read_json(result.report_dir / "run-manifest.v1.json")
    parent_manifest_json = read_json(parent.run_manifest_path)
    count_path = parent.project_dir / "continuation-count.txt"
    assert result.exit_code == 0
    assert summary["overall_status"] == "PASS"
    assert start_phase == expected_start
    assert inherited == expected_inherited
    assert continuation["parent_run_id"] == "parent-run-001"
    assert continuation["anchor_phase_id"] == expected_start
    assert (
        child_manifest_json["parent_evidence_digests"]
        == (parent_manifest_json["sealed_evidence"])
    )
    if spec.case_id == "after5":
        assert not count_path.exists()
        assert replay["available"] is False
        assert replay["reason"] == "receipt_missing"
    else:
        assert count_path.read_text(encoding="utf-8") == "1"
        assert ".receipt.json" in child_manifest
    assert result.manager.message_post_count == 0
    assert tree_bytes(parent.report_dir) == parent_before


def test_v3_runtime_missing_environment_refuses_before_child_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a post-Phase-2 parent that claims no retained environment.
    spec = ContinuationParentSpec(
        "missing-env",
        "FAIL",
        "phase_5_validation",
        ("passed", "passed", "passed", "failed", "skipped"),
        3,
        environment_required=False,
    )
    parent = create_runtime_parent(tmp_path, monkeypatch, spec)
    parent_before = tree_bytes(parent.report_dir)
    child_runner = Mock()

    # When public continuation preparation verifies required environment authority.
    with pytest.raises(ContinuationEnvironmentError) as raised:
        with prepare_terminal_continuation(
            parent.summary_path,
            "child-missing-env-24",
        ) as prepared:
            child_runner(prepared)

    # Then refusal is typed and occurs before child/session/backend side effects.
    assert raised.value.kind is ContinuationEnvironmentErrorKind.ENVIRONMENT_MISSING
    child_runner.assert_not_called()
    assert tree_bytes(parent.report_dir) == parent_before
    assert not (parent.reports_root / "child-missing-env-24").exists()


def test_v3_runtime_observed_package_drift_refuses_before_child_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a post-Phase-2 parent that records a genuine FRAMEWORK_OBSERVED
    # execution-python target via the real probe_retained_environment ->
    # _capture_environment_probe path before sealing.
    spec = ContinuationParentSpec(
        "observed-drift",
        "FAIL",
        "phase_5_validation",
        ("passed", "passed", "passed", "failed", "skipped"),
        3,
    )
    parent = create_runtime_parent(tmp_path, monkeypatch, spec)
    parent_before = tree_bytes(parent.report_dir)
    child_runner = Mock()

    # And the sealed target's packages.inventory_sha256 is FRAMEWORK_OBSERVED.
    manifest = read_json(parent.report_dir / "resource-manifest.v1.json")
    environments = object_list_member(manifest, "environments")
    target_envs = [
        env for env in environments if env.get("environment_id") == "execution-python"
    ]
    assert len(target_envs) == 1
    pkg_facts = [
        fact
        for fact in object_list_member(target_envs[0], "facts")
        if fact.get("name") == "packages.inventory_sha256"
    ]
    assert len(pkg_facts) == 1
    assert pkg_facts[0].get("provenance") == "framework_observed"

    # When drift is exposed AFTER parent creation via PYTHONPATH so only the
    # continuation subprocess's real importlib.metadata probe sees the sentinel.
    drift_root = tmp_path / "drift-sentinel-root"
    dist = drift_root / "drift_sentinel-9.9.dist-info"
    dist.mkdir(parents=True)
    _ = (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: drift-sentinel\nVersion: 9.9\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(drift_root))

    # Then continuation preparation refuses before child/session/backend side effects.
    with pytest.raises(ContinuationEnvironmentError) as raised:
        with prepare_terminal_continuation(
            parent.summary_path,
            "child-observed-drift-24",
        ) as prepared:
            child_runner(prepared)

    assert raised.value.kind is ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH
    child_runner.assert_not_called()
    assert tree_bytes(parent.report_dir) == parent_before
    assert not (parent.reports_root / "child-observed-drift-24").exists()


def test_v3_runtime_child_trace_references_parent_hash_without_copying_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a sealed PASS parent trace and a fresh child session graph.
    parent_bytes = b'{"raw_parent_payload":"PARENT-RAW-MUST-NOT-COPY"}'
    spec = ContinuationParentSpec(
        "tr",
        "PASS",
        "phase_6_report",
        ("passed",) * 5,
        5,
        phase5_environment_reference=True,
        parent_trace_payload=parent_bytes,
    )
    parent = create_runtime_parent(tmp_path, monkeypatch, spec)
    parent_before = tree_bytes(parent.report_dir)
    root = graph(
        "ses_root",
        child_ids=("ses_child",),
        parts=(task_part("ses_root", "ses_child"),),
    )
    child = graph(
        "ses_child",
        child_ids=("ses_grandchild",),
        parts=(task_part("ses_child", "ses_grandchild"),),
    )
    fake_client = FakeTraceClient(
        {
            "ses_root": root.retrieval,
            "ses_child": child.retrieval,
            "ses_grandchild": graph("ses_grandchild").retrieval,
        }
    )
    client = concrete_trace_client(
        fake_client,
        ("ses_root", "ses_child", "ses_grandchild"),
    )
    session = SessionScript(trace_client=client, trace_seeds=(seed("ses_root"),))

    # When the public child lifecycle exports schema-v2 correlation.
    with prepare_terminal_continuation(
        parent.summary_path,
        "child-tr-24",
    ) as prepared:
        inventory_paths = tuple(
            item.relative_path for item in prepared.evidence.parent_report_inventory
        )
        assert "trace/manifest.json" in inventory_paths, inventory_paths
        result = run_prepared_continuation(
            monkeypatch,
            prepared,
            session_script=session,
            save_agent_trace=True,
        )

    # Then only immutable parent identity crosses and raw bytes never duplicate.
    manifest = read_json(result.report_dir / "trace" / "manifest.json")
    correlation = object_member(manifest, "correlation")
    run_scope = object_member(correlation, "run_scope")
    parent_trace = object_member(run_scope, "parent_trace")
    child_bytes = b"".join(
        path.read_bytes()
        for path in (result.report_dir / "trace").rglob("*")
        if path.is_file()
    )
    assert result.exit_code == 0
    assert manifest["errors"] == []
    assert fake_client.calls == ["ses_root", "ses_child", "ses_grandchild"]
    assert parent_trace["run_id"] == "parent-run-001"
    assert parent_trace["sha256"] == hashlib.sha256(parent_bytes).hexdigest()
    assert parent_trace["size_bytes"] == len(parent_bytes)
    assert parent_bytes not in child_bytes
    assert result.manager.message_post_count == 0
    assert tree_bytes(parent.report_dir) == parent_before
