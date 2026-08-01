"""Refusal tests: faulted seal, tamper, real environment drift, public smoke."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import TypeAdapter

import harness.server.lifecycle as server_lifecycle
import harness.session.manager as manager_module
from harness.session.events import TransportObserver
from core.continuation import ContinuationError, ContinuationErrorKind
from core.continuation_environment import (
    ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind,
)
from core.terminal_continuation import prepare_terminal_continuation

from .e2e_v3_direct_seal_continuation_cases import (
    extract_headline,
    read_sidecar,
    read_summary,
    tree_hash,
)
from .e2e_v3_direct_seal_continuation_support import (
    DirectSealParent,
    DirectSealScenario,
    resolve_site_packages,
    run_direct_seal_parent,
)
from . import e2e_test_v3 as target
from .e2e_v3_runtime_fakes import ScriptedSessionManager, SessionScript
from . import e2e_v3_runtime_fakes as _fakes

_SUMMARY: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_TAMPER_HEX = {
    "manifest": "fd" * 16,
    "evidence": "fe" * 16,
    "target": "0a" * 16,
    "drift": "0b" * 16,
}


def _assert_authority_refusal(parent: DirectSealParent) -> None:
    parent_hash = tree_hash(parent.report_dir)
    child_id = "refused-child-00"
    with pytest.raises(ContinuationError) as exc_info:
        with prepare_terminal_continuation(parent.summary_path, child_id):
            pass
    assert exc_info.value.kind is ContinuationErrorKind.AUTHORITY_INVALID
    assert tree_hash(parent.report_dir) == parent_hash
    assert not (parent.report_dir.parent / child_id).exists()


def _assert_drift_refusal(parent: DirectSealParent) -> None:
    site_pkg = resolve_site_packages(parent.venv_python)
    dist = site_pkg / "drift_sentinel-9.9.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    _ = (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: drift-sentinel\nVersion: 9.9\n",
        encoding="utf-8",
    )
    parent_hash = tree_hash(parent.report_dir)
    child_id = "refused-child-00"
    with pytest.raises(ContinuationEnvironmentError) as exc_info:
        with prepare_terminal_continuation(parent.summary_path, child_id):
            pass
    assert (
        exc_info.value.kind
        is ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH
    )
    assert tree_hash(parent.report_dir) == parent_hash
    assert not (parent.report_dir.parent / child_id).exists()


def test_direct_seal_faulted_seal_outcome_neutral_and_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc = DirectSealScenario("ee" * 16, "pass", "15" * 16)
    baseline = run_direct_seal_parent(tmp_path, monkeypatch, sc, seal_manifest=False)
    assert baseline.exit_code == 0
    baseline_headline = extract_headline(baseline.stdout)

    import core.manifest_sealing_runner as _srm
    from core.artifact_store import ArtifactStore
    from core.manifest_sealing_models import ManifestSealingFaultHooks
    from core.run_outcome import TerminalAnchor

    def _faulted(
        *,
        report_dir: Path,
        run_id: str,
        project_dir: Path,
        workflow_path: Path,
        artifact_store: ArtifactStore | None,
        terminal_anchor: TerminalAnchor,
        hooks: ManifestSealingFaultHooks | None = None,
    ):
        del hooks
        from core.manifest_sealing import seal_root_manifest as _real
        return _real(
            report_dir=report_dir, run_id=run_id, project_dir=project_dir,
            workflow_path=workflow_path, artifact_store=artifact_store,
            terminal_anchor=terminal_anchor,
            hooks=ManifestSealingFaultHooks(
                before_evidence_publish=_raise_oserror
            ),
        )

    faulted_sc = DirectSealScenario("fc" * 16, "pass", "15" * 16)
    monkeypatch.setattr(_srm, "seal_root_manifest", _faulted)
    faulted = run_direct_seal_parent(tmp_path, monkeypatch, faulted_sc, seal_manifest=True)
    assert faulted.exit_code == baseline.exit_code
    assert extract_headline(faulted.stdout) == baseline_headline
    assert read_summary(faulted)["overall_status"] == "PASS"
    sc = read_sidecar(faulted)
    assert sc["status"] == "failed"
    assert sc["continuation_eligible"] is False
    projection = read_summary(faulted).get("manifest_sealing")
    assert isinstance(projection, dict)
    assert projection["status"] == "failed"
    assert projection["continuation_eligible"] is False
    assert not faulted.run_manifest_path.exists()
    _assert_authority_refusal(faulted)


def _raise_oserror() -> None:
    raise OSError("injected sealing publication fault")


def _tamper(parent: DirectSealParent, kind: str) -> None:
    if kind == "manifest":
        data = bytearray(parent.run_manifest_path.read_bytes())
        data[-1] ^= 0xFF
        _ = parent.run_manifest_path.write_bytes(data)
    elif kind == "evidence":
        for p in parent.evidence_dir.rglob("*.json"):
            data = bytearray(p.read_bytes())
            data[-1] ^= 0xFF
            _ = p.write_bytes(data)
            return
    elif kind == "target":
        mp = parent.report_dir / "resource-manifest.v1.json"
        if not mp.exists():
            pytest.fail("resource manifest missing for target tamper")
        raw = _SUMMARY.validate_json(mp.read_bytes())
        raw["continuation_target"] = {
            "environment_id": "nonexistent-env", "namespace": "host",
        }
        _ = mp.write_bytes(_SUMMARY.dump_json(raw))


@pytest.mark.parametrize("tamper_kind", ("manifest", "evidence", "target", "drift"))
def test_direct_seal_tamper_refuses_before_child_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_kind: str,
) -> None:
    sc = DirectSealScenario(_TAMPER_HEX[tamper_kind], "at5", "16" * 16)
    parent = run_direct_seal_parent(tmp_path, monkeypatch, sc)
    if tamper_kind == "drift":
        _assert_drift_refusal(parent)
    else:
        _tamper(parent, tamper_kind)
        _assert_authority_refusal(parent)


def test_direct_seal_tamper_public_main_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc = DirectSealScenario("0c" * 16, "at5", "17" * 16)
    parent = run_direct_seal_parent(tmp_path, monkeypatch, sc)
    _tamper(parent, "manifest")
    parent_hash = tree_hash(parent.report_dir)

    factory_calls: list[int] = []
    resolve_calls: list[int] = []
    check_calls: list[int] = []
    log_calls: list[int] = []

    def _factory(
        *, work_dir: str, base_url: str,
        transport_observer: TransportObserver | None,
    ) -> ScriptedSessionManager:
        factory_calls.append(1)
        return ScriptedSessionManager(
            work_dir=work_dir, base_url=base_url,
            transport_observer=transport_observer,
            script=SessionScript(),
        )

    def _counting_resolve(*_a: object, **_kw: object) -> tuple[str, None]:
        resolve_calls.append(1)
        return ("http://opencode.test", None)

    def _counting_check(*_a: object, **_kw: object) -> None:
        check_calls.append(1)

    def _counting_log(*_a: object, **_kw: object) -> None:
        log_calls.append(1)

    monkeypatch.setattr(_fakes, "_SESSION_ID", "ses-public-refuse")
    monkeypatch.setattr(server_lifecycle, "resolve_server_url", _counting_resolve)
    monkeypatch.setattr(target, "check_server_running", _counting_check)
    monkeypatch.setattr(target, "log_server_diagnostics", _counting_log)
    monkeypatch.setattr(manager_module, "SessionManager", _factory)
    monkeypatch.setattr(target, "uuid4", lambda: UUID(hex="17" * 16))
    monkeypatch.setattr(
        sys, "argv",
        ["e2e_test_v3", "--continue-from", str(parent.summary_path),
         "--server-no-auto-start", "--opencode-readiness", "off"],
    )
    exit_code = target.main()
    assert exit_code == 1
    assert tree_hash(parent.report_dir) == parent_hash
    assert factory_calls == [], "SessionManager factory must not be called"
    assert resolve_calls == [], "server resolution must not be called"
    assert check_calls == [], "server check must not be called"
    assert log_calls == [], "server diagnostics must not be called"
    assert not (parent.report_dir.parent / "e2e-v3-171717171717").exists()
