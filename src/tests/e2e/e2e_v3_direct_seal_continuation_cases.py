"""Matrix tests for the real direct-run seal-to-continue lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from core.run_manifest_inventory import digest_inventory
from core.run_manifest_io import read_manifest

from .e2e_v3_direct_seal_continuation_support import (
    DirectSealParent,
    DirectSealScenario,
    read_session_id,
    run_direct_seal_continuation,
    run_direct_seal_parent,
)
from .e2e_v3_direct_seal_trace_support import TRACE_SENTINEL_BYTES

_SUMMARY: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_SIDECAR: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_CONT: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_LIST_STR: TypeAdapter[list[str]] = TypeAdapter(list[str])
_RESOURCE: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_LIST_OBJ: TypeAdapter[list[object]] = TypeAdapter(list[object])

_MATRIX = (
    DirectSealScenario("f7" * 16, "pass", "10" * 16),
    DirectSealScenario("f8" * 16, "before5", "11" * 16),
    DirectSealScenario("f9" * 16, "at5", "12" * 16),
    DirectSealScenario("fa" * 16, "after5", "13" * 16),
)
_STATUS = {"pass": "PASS", "before5": "FAIL", "at5": "FAIL", "after5": "FAIL"}
_EXIT = {"pass": 0, "before5": 1, "at5": 1, "after5": 1}
_ANCHOR = {"pass": "phase_5_validation", "before5": "phase_4_migrate",
           "at5": "phase_5_validation", "after5": "phase_6_report"}
_INHERITED = {
    "pass": ("phase_2_venv_create", "phase_4_migrate"),
    "before5": ("phase_2_venv_create",),
    "at5": ("phase_2_venv_create", "phase_4_migrate"),
    "after5": ("phase_2_venv_create", "phase_4_migrate", "phase_5_validation"),
}
_ACCEPTED_ENV = {"pass": "phase2-project-venv", "after5": "phase2-project-venv"}
_TARGET_NAMESPACE = "host"


def tree_hash(root: Path) -> bytes:
    import hashlib as _hl
    h = _hl.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.digest()


def read_sidecar(parent: DirectSealParent) -> dict[str, object]:
    return _SIDECAR.validate_json(parent.sidecar_path.read_bytes())


def read_summary(parent: DirectSealParent) -> dict[str, object]:
    return _SUMMARY.validate_json(parent.summary_path.read_bytes())


def extract_headline(stdout: str) -> str:
    for line in stdout.splitlines():
        s = line.strip()
        if "E2E PASS" in s or "E2E FAIL" in s:
            return s
    return ""


def _resource_manifest(parent: DirectSealParent) -> dict[str, object]:
    return _RESOURCE.validate_json(
        (parent.report_dir / "resource-manifest.v1.json").read_bytes()
    )


def _assert_sealed_and_sidecar(parent: DirectSealParent) -> None:
    manifest = read_manifest(parent.run_manifest_path)
    assert manifest.evidence_sealed
    assert digest_inventory(parent.evidence_dir, parent.report_dir) == manifest.sealed_evidence
    sc = read_sidecar(parent)
    assert sc["status"] == "succeeded"
    assert sc["continuation_eligible"] is True
    projection = read_summary(parent).get("manifest_sealing")
    assert isinstance(projection, dict)
    assert projection["status"] == sc["status"]
    assert projection["continuation_eligible"] == sc["continuation_eligible"]


def _assert_env_authority(parent: DirectSealParent, fp: str) -> None:
    """Exact-identity environment authority: target and accepted refs both
    resolve to real persisted environment records with matching namespace
    and executable.

    The continuation target must identify a Phase-2 venv record whose
    ``interpreter.sys_executable`` equals the actual venv python. For
    pass/after5 the accepted Phase-5 reference must resolve to the
    intended persisted record by exact ID and namespace.
    """
    rm = _resource_manifest(parent)
    target_ref = _RESOURCE.validate_python(rm["continuation_target"])
    target_id_obj = target_ref["environment_id"]
    assert isinstance(target_id_obj, str)
    target_id = target_id_obj
    target_ns_obj = target_ref["namespace"]
    assert isinstance(target_ns_obj, str)
    target_ns = target_ns_obj
    assert target_id == "phase2-project-venv", target_id
    assert target_ns == _TARGET_NAMESPACE, target_ns
    envs = _LIST_OBJ.validate_python(rm["environments"])
    if fp == "at5":
        assert len(envs) >= 2, fp
    env_by_id: dict[str, dict[str, object]] = {}
    for env_obj in envs:
        env = _RESOURCE.validate_python(env_obj)
        env_id_obj = env.get("environment_id")
        assert isinstance(env_id_obj, str)
        env_by_id[env_id_obj] = env
    assert target_id in env_by_id, f"target env {target_id} not persisted"
    target_env = env_by_id[target_id]
    env_namespace = _resolve_env_namespace(target_env)
    assert env_namespace is not None, f"target env {target_id} missing namespace"
    assert env_namespace == target_ns, (env_namespace, target_ns)
    facts = _LIST_OBJ.validate_python(target_env.get("facts", []))
    matched_executable = False
    for fact_obj in facts:
        fact = _RESOURCE.validate_python(fact_obj)
        if fact.get("name") == "interpreter.sys_executable":
            assert fact.get("value") == parent.venv_python
            matched_executable = True
    assert matched_executable, f"target env {target_id} missing sys_executable"
    if fp in ("pass", "after5"):
        refs = _LIST_OBJ.validate_python(rm["phase5_environment_references"])
        assert len(refs) == 1, fp
        ref = _RESOURCE.validate_python(refs[0])
        env_ref = _RESOURCE.validate_python(ref["environment_reference"])
        accepted_id_obj = env_ref["value"]
        assert isinstance(accepted_id_obj, str)
        accepted_id = accepted_id_obj
        assert accepted_id == _ACCEPTED_ENV[fp], accepted_id
        assert accepted_id in env_by_id, (
            f"accepted env {accepted_id} not persisted"
        )
        accepted_env = env_by_id[accepted_id]
        accepted_ns = _resolve_env_namespace(accepted_env)
        assert accepted_ns is not None, f"accepted env {accepted_id} missing namespace"
        assert accepted_ns == _TARGET_NAMESPACE, accepted_ns


def _resolve_env_namespace(env: dict[str, object]) -> str | None:
    """Read the environment's KNOWN ``environment.namespace`` fact.

    Mirrors ``core.resource_manifest_provenance.environment_namespace``:
    collects all KNOWN ``environment.namespace`` values and requires
    exactly one.  Returns ``None`` when the fact is missing or ambiguous
    so the caller fails closed rather than defaulting to an expected value.
    """
    facts = _LIST_OBJ.validate_python(env.get("facts", []))
    namespaces: set[str] = set()
    for fact_obj in facts:
        fact = _RESOURCE.validate_python(fact_obj)
        if fact.get("name") == "environment.namespace":
            status = fact.get("status")
            if status == "known":
                value = fact.get("value")
                if isinstance(value, str):
                    namespaces.add(value)
    if len(namespaces) == 1:
        return next(iter(namespaces))
    return None


@pytest.mark.parametrize("scenario", _MATRIX, ids=[s.fail_point for s in _MATRIX])
def test_direct_seal_matrix_continuation_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: DirectSealScenario,
) -> None:
    parent = run_direct_seal_parent(tmp_path, monkeypatch, scenario)
    assert parent.exit_code == _EXIT[scenario.fail_point]
    assert _STATUS[scenario.fail_point] in extract_headline(parent.stdout)
    assert read_summary(parent)["overall_status"] == _STATUS[scenario.fail_point]
    _assert_sealed_and_sidecar(parent)
    _assert_env_authority(parent, scenario.fail_point)
    parent_session = read_session_id(parent.manager)
    assert parent_session != ""
    parent_hash = tree_hash(parent.report_dir)

    result = run_direct_seal_continuation(
        monkeypatch, scenario, parent.summary_path, parent.report_dir
    )
    assert result.exit_code == 0
    assert result.child_run_id != parent.run_id
    child_session = read_session_id(result.manager)
    assert child_session != ""
    assert child_session != parent_session
    child_summ = _SUMMARY.validate_json(
        (result.child_report_dir / "summary.json").read_bytes()
    )
    assert child_summ["overall_status"] == "PASS"
    cont = _CONT.validate_python(child_summ["continuation"])
    assert cont["parent_run_id"] == parent.run_id
    assert cont["anchor_phase_id"] == _ANCHOR[scenario.fail_point]
    inherited = tuple(_LIST_STR.validate_python(cont["inherited_phase_ids"]))
    assert inherited == _INHERITED[scenario.fail_point]
    assert tree_hash(parent.report_dir) == parent_hash


def test_direct_seal_sidecar_projection_and_digest_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = run_direct_seal_parent(
        tmp_path, monkeypatch, DirectSealScenario("fb" * 16, "pass", "14" * 16)
    )
    _assert_sealed_and_sidecar(parent)
    assert parent.run_manifest_path.exists()
    assert (parent.evidence_dir / "validated").is_dir()


def test_direct_seal_trace_continuation_preserves_lineage_without_raw_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public trace lifecycle through untouched production.

    Verifies two independent authorities: (1) root manifest evidence via
    ``_assert_sealed_and_sidecar`` (production seals only artifact-store
    evidence, not ``trace/manifest.json``), and (2) parent-report trace
    written by the production trace exporter containing the sentinel.
    Child continues publicly with ``--save-agent-trace`` and a trace
    client built from the child's own session ID; raw sentinel bytes
    are absent from every child file.
    """
    scenario = DirectSealScenario("d0" * 16, "pass", "d1" * 16)
    parent = run_direct_seal_parent(
        tmp_path, monkeypatch, scenario, save_trace=True
    )
    assert parent.exit_code == 0
    assert "PASS" in extract_headline(parent.stdout)

    parent_trace_path = parent.report_dir / "trace" / "manifest.json"
    assert parent_trace_path.is_file()
    parent_trace_tree_bytes = b"".join(
        p.read_bytes()
        for p in (parent.report_dir / "trace").rglob("*")
        if p.is_file()
    )
    assert TRACE_SENTINEL_BYTES in parent_trace_tree_bytes

    _assert_sealed_and_sidecar(parent)

    parent_session = read_session_id(parent.manager)
    expected_parent_session = f"ses-{scenario.run_hex[:8]}"
    assert parent_session == expected_parent_session
    parent_hash = tree_hash(parent.report_dir)

    result = run_direct_seal_continuation(
        monkeypatch, scenario, parent.summary_path, parent.report_dir,
        save_trace=True,
    )
    assert result.exit_code == 0
    assert result.child_run_id != parent.run_id

    child_session = read_session_id(result.manager)
    expected_child_session = f"ses-{scenario.child_hex[:8]}"
    assert child_session == expected_child_session
    assert child_session != parent_session

    child_trace_manifest = result.child_report_dir / "trace" / "manifest.json"
    assert child_trace_manifest.is_file()
    child_manifest_obj = _SUMMARY.validate_json(child_trace_manifest.read_bytes())
    correlation = _CONT.validate_python(child_manifest_obj["correlation"])
    run_scope = _CONT.validate_python(correlation["run_scope"])
    assert run_scope["run_id"] == result.child_run_id
    assert run_scope["parent_run_id"] == parent.run_id

    child_bytes = b"".join(
        p.read_bytes()
        for p in result.child_report_dir.rglob("*")
        if p.is_file()
    )
    assert TRACE_SENTINEL_BYTES not in child_bytes
    assert tree_hash(parent.report_dir) == parent_hash
