"""Targeted tests for V3 E2E workflow selector integration.

Uses mocked session managers and prompt loaders — no real OpenCode server calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e.e2e_test_v3 import _build_project_context, _format_selector_result_log


class TestBuildProjectContext:
    """Verify the lightweight project context builder used for selector resolution."""

    def test_builds_from_python_project(self, tmp_path: Path) -> None:
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / "src" / "utils.py").write_text("def f(): pass")

        ctx = _build_project_context(tmp_path)
        assert ctx["project_path"] == str(tmp_path)
        assert ctx["project_name"] == tmp_path.name
        assert ctx["language"] == "Python"
        assert ctx["build_system"] == "setuptools"
        assert ctx["file_count"] == 3  # setup.py + src/main.py + src/utils.py
        assert "file_hints" not in ctx

    def test_detects_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        ctx = _build_project_context(tmp_path)
        assert ctx["build_system"] == "pyproject"

    def test_no_build_system_detected(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        ctx = _build_project_context(tmp_path)
        assert ctx["build_system"] == ""

    def test_project_context_does_not_emit_file_hints(self, tmp_path: Path) -> None:
        for i in range(15):
            (tmp_path / f"file_{i}.py").write_text("pass")
        ctx = _build_project_context(tmp_path)
        assert ctx["file_count"] == 15
        assert "file_hints" not in ctx


class TestSelectorIntegrationFlow:
    """Verify the selector resolution flow used in e2e_test_v3.run_e2e_v3."""

    def test_selector_resolution_materializes_correctly(self, tmp_path: Path) -> None:
        """Simulate what run_e2e_v3 does: detect selector, resolve, update path."""
        from core.workflow_selector import is_selector_file, resolve_workflow_from_selector

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")

        selector = tmp_path / "selector.yaml"
        selector.write_text(yaml.dump({
            "kind": "workflow_selector",
            "name": "test-sel",
            "candidate_workflows": [{"path": str(wf)}],
            "fallback": str(wf),
        }), encoding="utf-8")

        class _FakeSM:
            def get_or_create(self, role: str, lifecycle: str = "ephemeral", agent: str = "", **kw: object) -> str:
                return "fake-sid"
            def send_command(self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object) -> str:
                return json.dumps({"selected_workflow": str(wf)})

        class _FakePL:
            def load_prompt(self, template_name: str, context: dict) -> str:
                return "prompt"

        assert is_selector_file(str(selector))
        assert not is_selector_file(str(wf))

        materialized = resolve_workflow_from_selector(
            str(selector), _FakeSM(), _FakePL(),
            project_context={"language": "Python"},
            output_dir=tmp_path / "output",
        )
        assert materialized.exists()
        loaded = yaml.safe_load(materialized.read_text())
        assert loaded["name"] == "test_wf"

    def test_selector_result_log_includes_all_paths(self, tmp_path: Path) -> None:
        selector = tmp_path / "selector.yaml"
        selected = tmp_path / "workflow.yaml"
        materialized = tmp_path / "out" / "resolved.yaml"

        line = _format_selector_result_log(
            str(selector),
            str(selected),
            str(materialized),
        )

        assert line.startswith("Workflow selector result: ")
        assert f"selector={selector}" in line
        assert f"selected={selected}" in line
        assert f"materialized={materialized}" in line

    def test_selector_override_preserved_in_output(self, tmp_path: Path) -> None:
        """Overrides from selector are merged into materialized workflow."""
        from core.workflow_selector import resolve_workflow_from_selector

        wf = tmp_path / "base.yaml"
        wf.write_text(yaml.dump({
            "name": "base", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
            "globals": {"max_repair_iterations": 5},
            "experience": {"enabled": False},
        }), encoding="utf-8")

        selector = tmp_path / "sel.yaml"
        selector.write_text(yaml.dump({
            "kind": "workflow_selector",
            "name": "override-test",
            "candidate_workflows": [{"path": str(wf)}],
            "overrides": {
                "experience": {"enabled": True},
                "globals": {"review_gate_enabled": True},
            },
        }), encoding="utf-8")

        class _FakeSM:
            def get_or_create(self, role: str, lifecycle: str = "ephemeral", agent: str = "", **kw: object) -> str:
                return "sid"
            def send_command(self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object) -> str:
                return json.dumps({"selected_workflow": str(wf)})

        class _FakePL:
            def load_prompt(self, template_name: str, context: dict) -> str:
                return "prompt"

        materialized = resolve_workflow_from_selector(
            str(selector), _FakeSM(), _FakePL(),
            output_dir=tmp_path / "output",
        )
        loaded = yaml.safe_load(materialized.read_text())
        assert loaded["experience"]["enabled"] is True
        assert loaded["globals"]["review_gate_enabled"] is True
        assert loaded["globals"]["max_repair_iterations"] == 5


class TestSmokeSelectorYaml:
    """Verify the smoke selector YAML is well-formed and loadable."""

    def test_smoke_selector_exists_and_has_valid_kind(self) -> None:
        from core.workflow_selector import is_selector_file
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        assert smoke_path.exists(), f"Expected smoke selector at {smoke_path}"
        assert is_selector_file(str(smoke_path))

    def test_smoke_selector_candidates_exist(self) -> None:
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        candidates = raw["candidate_workflows"]
        assert len(candidates) >= 2
        for entry in candidates:
            candidate_path = wf_dir / entry["path"]
            assert candidate_path.exists(), f"Candidate workflow not found: {entry['path']}"

    def test_smoke_selector_has_overrides_disabled(self) -> None:
        """Smoke selector overrides must disable experience, disable review gate,
        and set max_repair_iterations to 8."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        assert "overrides" in raw
        assert "experience" in raw["overrides"]
        assert "globals" in raw["overrides"]
        assert raw["overrides"]["experience"].get("enabled") is False
        assert raw["overrides"]["experience"].get("phase7_enabled") is False
        assert raw["overrides"]["experience"].get("memory_enabled") is False
        assert raw["overrides"]["globals"].get("max_repair_iterations") == 8
        assert raw["overrides"]["globals"].get("review_gate_enabled") is False

    def test_smoke_selector_has_fallback(self) -> None:
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        assert raw.get("fallback") is not None

    def test_smoke_selector_fallback_is_not_npu(self) -> None:
        """Smoke selector fallback must not be NPU migration."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        top_fallback = raw.get("fallback", "")
        assert top_fallback != "npu_migration_v2.yaml", (
            f"Smoke selector fallback should not be NPU, got {top_fallback!r}"
        )
        selector_cfg = raw.get("selector", {})
        if isinstance(selector_cfg, dict) and "fallback" in selector_cfg:
            sel_fallback = selector_cfg["fallback"]
            assert sel_fallback != "npu_migration_v2.yaml", (
                f"Selector-level fallback should not be NPU, got {sel_fallback!r}"
            )

    def test_smoke_selector_has_agent_config(self) -> None:
        """Smoke selector should specify a stable agent for selection."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        selector_cfg = raw.get("selector", {})
        assert isinstance(selector_cfg, dict), "Smoke selector should have a 'selector' config block"
        assert "agent" in selector_cfg, "Smoke selector should specify selector.agent"
        assert selector_cfg["agent"], "Selector agent should not be empty"

    # ── Content assertions: candidates, platform hints, fallback ─────────

    def test_smoke_selector_includes_muxi_musa_candidates(self) -> None:
        """Smoke selector MUST include at least one Muxi/MUSA candidate workflow."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        candidates = raw["candidate_workflows"]
        musa_candidates = [
            c for c in candidates
            if "musa" in c.get("path", "").lower()
            or "muxi" in c.get("path", "").lower()
            or "musa" in c.get("description", "").lower()
            or "muxi" in c.get("description", "").lower()
        ]
        assert len(musa_candidates) >= 1, (
            f"Expected at least 1 Muxi/MUSA candidate, found {len(musa_candidates)}. "
            f"Full candidates: {[c.get('path') for c in candidates]}"
        )

    def test_smoke_selector_has_no_target_platform_hint(self) -> None:
        """Smoke selector MUST NOT contain target_platform_hint, accelerator_family,
        or any deterministic platform forcing keys in selector or root."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())

        forbidden_keys_root = [
            "target_platform_hint",
            "accelerator_family",
            "platform_priority",
            "target_platform",
        ]
        for key in forbidden_keys_root:
            assert key not in raw, (
                f"Smoke selector root MUST NOT contain '{key}' (found in YAML). "
                f"Agent selection must be driven by device exploration, not meta hints."
            )

        selector_block = raw.get("selector", {})
        if isinstance(selector_block, dict):
            for key in forbidden_keys_root:
                assert key not in selector_block, (
                    f"Smoke selector.selector MUST NOT contain '{key}'. "
                    f"Agent selection must be driven by device exploration, not meta hints."
                )

    def test_smoke_selector_has_no_platform_priority_field(self) -> None:
        """Neither the selector root nor overrides must encode a priority list
        of platforms (e.g. ['muxi', 'npu', 'ppu']) that would force ordering."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())

        overrides = raw.get("overrides", {})
        if isinstance(overrides, dict):
            assert "platform_priority" not in overrides, (
                "Smoke selector overrides MUST NOT contain 'platform_priority'."
            )

    def test_smoke_selector_fallback_is_neutral(self) -> None:
        """Fallback must be experience_memory_test.yaml — a neutral, non-platform workflow."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())

        fallback = raw.get("fallback", "")
        assert fallback == "experience_memory_test.yaml", (
            f"Smoke selector fallback must be 'experience_memory_test.yaml' (neutral), "
            f"got {fallback!r}"
        )


class TestSeamAutoDefaultSelectorYaml:
    """Verify seam_auto_default selector enforces fail-fast (no NPU fallback).

    The production selector MUST NOT silently fall back to NPU migration when
    the agent fails.  Instead it must raise a clear ValueError so the caller
    can decide how to proceed.
    """

    def test_seam_auto_default_exists_and_has_valid_kind(self) -> None:
        from core.workflow_selector import is_selector_file
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        assert sel_path.exists(), f"Expected seam_auto_default selector at {sel_path}"
        assert is_selector_file(str(sel_path))

    def test_seam_auto_default_candidates_exist(self) -> None:
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())
        candidates = raw["candidate_workflows"]
        assert len(candidates) >= 2
        for entry in candidates:
            candidate_path = wf_dir / entry["path"]
            assert candidate_path.exists(), f"Candidate workflow not found: {entry['path']}"

    def test_seam_auto_default_has_agent_config(self) -> None:
        """seam_auto_default should specify a stable agent for selection."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())
        selector_cfg = raw.get("selector", {})
        assert isinstance(selector_cfg, dict), "seam_auto_default should have a 'selector' config block"
        assert "agent" in selector_cfg, "seam_auto_default should specify selector.agent"
        assert selector_cfg["agent"], "Selector agent should not be empty"

    def test_seam_auto_default_fallback_is_not_npu(self) -> None:
        """seam_auto_default MUST NOT fallback to NPU migration workflow."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())

        top_fallback = raw.get("fallback", "")
        assert top_fallback != "npu_migration_v2.yaml", (
            f"seam_auto_default top-level fallback should not be NPU, got {top_fallback!r}"
        )
        selector_cfg = raw.get("selector", {})
        if isinstance(selector_cfg, dict) and "fallback" in selector_cfg:
            sel_fallback = selector_cfg["fallback"]
            assert sel_fallback != "npu_migration_v2.yaml", (
                f"seam_auto_default selector.fallback should not be NPU, got {sel_fallback!r}"
            )

    def test_seam_auto_default_has_no_fallback_configured(self) -> None:
        """seam_auto_default production selector MUST NOT configure any fallback.

        This enforces fail-fast behavior: when the agent cannot select, the
        caller gets a clear ValueError instead of silently migrating with a
        wrong-platform workflow.
        """
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())

        assert "fallback" not in raw, (
            f"seam_auto_default MUST NOT have a top-level 'fallback' field. "
            f"Found: {raw.get('fallback')!r}"
        )
        selector_cfg = raw.get("selector", {})
        if isinstance(selector_cfg, dict):
            assert "fallback" not in selector_cfg, (
                f"seam_auto_default selector block MUST NOT have a 'fallback' field. "
                f"Found: {selector_cfg.get('fallback')!r}"
            )

    def test_seam_auto_default_no_duplicate_contradictory_fallback(self) -> None:
        """Neither top-level nor selector block may define fallback.
        Ensures no duplicate/contradictory fallback configuration that could
        cause silent platform-biased migration.
        """
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())

        has_top_fallback = "fallback" in raw
        selector_cfg = raw.get("selector", {})
        has_sel_fallback = isinstance(selector_cfg, dict) and "fallback" in selector_cfg

        assert not has_top_fallback, (
            "seam_auto_default must not have top-level fallback "
            "(had both selector.fallback and top-level fallback before fix)"
        )
        assert not has_sel_fallback, (
            "seam_auto_default must not have selector-level fallback "
            "(had both selector.fallback and top-level fallback before fix)"
        )

    def test_seam_auto_default_has_overrides(self) -> None:
        """seam_auto_default overrides must disable experience, disable review gate,
        and set max_repair_iterations to 8 (same policy as smoke selector)."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())
        assert "overrides" in raw
        assert "experience" in raw["overrides"]
        assert "globals" in raw["overrides"]
        assert raw["overrides"]["experience"].get("enabled") is False
        assert raw["overrides"]["experience"].get("phase7_enabled") is False
        assert raw["overrides"]["experience"].get("memory_enabled") is False
        assert raw["overrides"]["globals"].get("max_repair_iterations") == 8
        assert raw["overrides"]["globals"].get("review_gate_enabled") is False

    def test_seam_auto_default_includes_muxi_musa_candidates(self) -> None:
        """seam_auto_default MUST include at least one Muxi/MUSA candidate workflow
        alongside NPU and PPU candidates for cross-platform coverage."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())
        candidates = raw["candidate_workflows"]
        musa_candidates = [
            c for c in candidates
            if "musa" in c.get("path", "").lower()
            or "muxi" in c.get("path", "").lower()
            or "musa" in c.get("description", "").lower()
            or "muxi" in c.get("description", "").lower()
        ]
        assert len(musa_candidates) >= 1, (
            f"Expected at least 1 Muxi/MUSA candidate, found {len(musa_candidates)}. "
            f"Full candidates: {[c.get('path') for c in candidates]}"
        )

    def test_seam_auto_default_includes_npu_candidate(self) -> None:
        """seam_auto_default MUST include NPU as a candidate workflow
        (agent selects it only when NPU hardware is detected)."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())
        candidates = raw["candidate_workflows"]
        npu_paths = [c["path"] for c in candidates if "npu" in c.get("path", "").lower()]
        assert len(npu_paths) >= 1, (
            f"Expected at least 1 NPU candidate in seam_auto_default, "
            f"got: {[c.get('path') for c in candidates]}"
        )

    def test_seam_auto_default_has_no_platform_priority_field(self) -> None:
        """Neither root nor overrides must encode a platform priority list
        that would force deterministic ordering over agent selection."""
        import yaml as _yaml
        wf_dir = PROJECT_ROOT / "workflows"
        sel_path = wf_dir / "seam_auto_default.yaml"
        raw = _yaml.safe_load(sel_path.read_text())

        forbidden_keys = [
            "target_platform_hint", "accelerator_family",
            "platform_priority", "target_platform",
        ]
        for key in forbidden_keys:
            assert key not in raw, (
                f"seam_auto_default root MUST NOT contain '{key}'"
            )

        selector_block = raw.get("selector", {})
        if isinstance(selector_block, dict):
            for key in forbidden_keys:
                assert key not in selector_block, (
                    f"seam_auto_default selector MUST NOT contain '{key}'"
                )

        overrides = raw.get("overrides", {})
        if isinstance(overrides, dict):
            for key in forbidden_keys:
                assert key not in overrides, (
                    f"seam_auto_default overrides MUST NOT contain '{key}'"
                )


# ═══════════════════════════════════════════════════════════════════════════
#  Telemetry instrumentation regression tests
# ═══════════════════════════════════════════════════════════════════════════


class FakeTelemetryRecorder:
    """Captures ``record_event`` calls for test assertions."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def record_event(self, event_type: str, **details: object) -> None:
        self.events.append({"event_type": event_type, "details": details})

    def events_of_type(self, event_type: str) -> list[dict[str, object]]:
        return [e for e in self.events if e["event_type"] == event_type]

    def has_event(self, event_type: str) -> bool:
        return any(e["event_type"] == event_type for e in self.events)


class TestSelectorTelemetryHappyPath:
    """Successful agent selection emits expected diagnostic events."""

    def test_happy_path_emits_all_events(self, tmp_path: Path) -> None:
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            def get_or_create(self, role: str, lifecycle: str = "ephemeral", agent: str = "", **kw: object) -> str:
                return "sid"
            def send_command(self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object) -> str:
                return json.dumps({"selected_workflow": "wf.yaml"})

        class _PL:
            def load_prompt(self, template_name: str, context: dict) -> str:
                return "prompt"

        telemetry = FakeTelemetryRecorder()
        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={"language": "Python"},
            selector_name="test-selector",
            fallback=None,
            selector_path="test.yaml",
            telemetry=telemetry,
        )

        assert result == wf
        assert telemetry.has_event("selector_prompt_sent")
        assert telemetry.has_event("selector_response_received")
        assert telemetry.has_event("selector_workflow_selected")

        prompt_events = telemetry.events_of_type("selector_prompt_sent")
        assert prompt_events[0]["details"]["selector_name"] == "test-selector"
        assert prompt_events[0]["details"]["candidate_count"] == 1
        assert isinstance(prompt_events[0]["details"]["prompt_length"], int)

        resp_events = telemetry.events_of_type("selector_response_received")
        assert resp_events[0]["details"]["response_length"] > 0
        assert isinstance(resp_events[0]["details"]["response_preview"], str)

        sel_events = telemetry.events_of_type("selector_workflow_selected")
        assert sel_events[0]["details"]["selected_path"] == str(wf)

    def test_happy_path_no_telemetry_still_works(self, tmp_path: Path) -> None:
        """Backwards compatibility: telemetry=None must not crash."""
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: json.dumps({"selected_workflow": "wf.yaml"})  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="test",
            fallback=None,
            selector_path="test.yaml",
            # telemetry not passed → default None
        )
        assert result == wf

    def test_telemetry_without_record_event_is_noop(self, tmp_path: Path) -> None:
        """Duck typing: object without record_event should not crash."""
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: json.dumps({"selected_workflow": "wf.yaml"})  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="test",
            fallback=None,
            selector_path="test.yaml",
            telemetry="not_a_real_observer",
        )
        assert result == wf


class TestSelectorTelemetryInvalidOutput:
    """Agent returns invalid output → fallback with diagnostic events."""

    def test_invalid_output_with_fallback(self, tmp_path: Path) -> None:
        from core.workflow_selector import _select_workflow_via_agent

        wf_a = tmp_path / "npu.yaml"
        wf_a.write_text(yaml.dump({
            "name": "npu", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        wf_b = tmp_path / "ppu.yaml"
        wf_b.write_text(yaml.dump({
            "name": "ppu", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [
            {"path": wf_a, "raw_path": "wf_a.yaml", "description": "A"},
            {"path": wf_b, "raw_path": "wf_b.yaml", "description": "B"},
        ]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: "hello, I pick NPU!"  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="bad-output",
            fallback="wf_a.yaml",
            selector_path="test.yaml",
            telemetry=telemetry,
        )

        assert result == wf_a
        assert telemetry.has_event("selector_prompt_sent")
        assert telemetry.has_event("selector_response_received")
        assert telemetry.has_event("selector_fallback_triggered")

        fallback_events = telemetry.events_of_type("selector_fallback_triggered")
        assert fallback_events[0]["details"]["fallback"] == "wf_a.yaml"
        assert "invalid" in str(fallback_events[0]["details"]["reason"]).lower()
        assert not telemetry.has_event("selector_workflow_selected")

    def test_invalid_output_no_fallback_raises_and_emits(self, tmp_path: Path) -> None:
        """No fallback configured + invalid output → ValueError + telemetry."""
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: "garbage response"  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        with pytest.raises(ValueError, match="no 'fallback' is configured"):
            _select_workflow_via_agent(
                candidates=candidates,
                session_mgr=_SM(),
                prompt_loader=_PL(),
                project_context={},
                selector_name="no-fb",
                fallback=None,
                selector_path="test.yaml",
                telemetry=telemetry,
            )

        assert telemetry.has_event("selector_prompt_sent")
        assert telemetry.has_event("selector_response_received")
        assert telemetry.has_event("selector_no_fallback_configured")

        nofb_events = telemetry.events_of_type("selector_no_fallback_configured")
        assert nofb_events[0]["details"]["selector_name"] == "no-fb"
        assert "invalid" in str(nofb_events[0]["details"]["reason"]).lower()


class TestSelectorTelemetryException:
    """Agent send_command raises an exception → telemetry records it."""

    def test_exception_with_fallback(self, tmp_path: Path) -> None:
        from core.workflow_selector import _select_workflow_via_agent

        wf_a = tmp_path / "npu.yaml"
        wf_a.write_text(yaml.dump({
            "name": "npu", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        wf_b = tmp_path / "ppu.yaml"
        wf_b.write_text(yaml.dump({
            "name": "ppu", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [
            {"path": wf_a, "raw_path": "wf_a.yaml", "description": "A"},
            {"path": wf_b, "raw_path": "wf_b.yaml", "description": "B"},
        ]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]

            def send_command(self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object) -> str:
                raise RuntimeError("Simulated send_command failure")

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="exc-fb",
            fallback="wf_b.yaml",
            selector_path="test.yaml",
            telemetry=telemetry,
        )

        assert result == wf_b
        assert telemetry.has_event("selector_prompt_sent")
        assert telemetry.has_event("selector_agent_exception")
        assert telemetry.has_event("selector_fallback_triggered")

        exc_events = telemetry.events_of_type("selector_agent_exception")
        assert exc_events[0]["details"]["error_type"] == "RuntimeError"
        assert "Simulated" in str(exc_events[0]["details"]["error"])
        assert not telemetry.has_event("selector_response_received")
        assert not telemetry.has_event("selector_workflow_selected")

    def test_exception_no_fallback_raises_and_emits(self, tmp_path: Path) -> None:
        """Exception + no fallback → ValueError with full diagnostic chain."""
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]

            def send_command(self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object) -> str:
                raise RuntimeError("Simulated send_command failure")

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        with pytest.raises(ValueError, match="no 'fallback' is configured"):
            _select_workflow_via_agent(
                candidates=candidates,
                session_mgr=_SM(),
                prompt_loader=_PL(),
                project_context={},
                selector_name="exc-nofb",
                fallback=None,
                selector_path="test.yaml",
                telemetry=telemetry,
            )

        assert telemetry.has_event("selector_prompt_sent")
        assert telemetry.has_event("selector_agent_exception")
        assert telemetry.has_event("selector_no_fallback_configured")

        nofb_events = telemetry.events_of_type("selector_no_fallback_configured")
        assert "Simulated" in str(nofb_events[0]["details"]["reason"])


class TestSelectorTelemetryResponseTruncation:
    """Large responses are truncated in telemetry previews."""

    def test_large_response_truncated(self, tmp_path: Path) -> None:
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        large_response = "x" * 2000 + json.dumps({"selected_workflow": "wf.yaml"})

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: large_response  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="large-resp",
            fallback=None,
            selector_path="test.yaml",
            telemetry=telemetry,
        )

        assert result == wf
        resp_events = telemetry.events_of_type("selector_response_received")
        preview = str(resp_events[0]["details"]["response_preview"])
        assert len(preview) <= 501  # 500 + "…"
        assert preview.endswith("…")
        assert resp_events[0]["details"]["response_length"] == len(large_response)


class TestSelectorTelemetrySelectorConfig:
    """Telemetry captures selector agent config."""

    def test_selector_agent_in_telemetry(self, tmp_path: Path) -> None:
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: json.dumps({"selected_workflow": "wf.yaml"})  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="agent-test",
            fallback=None,
            selector_path="test.yaml",
            selector_config={"agent": "build", "timeout": 45},
            telemetry=telemetry,
        )

        prompt_events = telemetry.events_of_type("selector_prompt_sent")
        assert prompt_events[0]["details"]["selector_agent"] == "build"

    def test_no_selector_agent_reported_as_none(self, tmp_path: Path) -> None:
        from core.workflow_selector import _select_workflow_via_agent

        wf = tmp_path / "wf.yaml"
        wf.write_text(yaml.dump({
            "name": "test_wf", "version": "1.0",
            "phases": [{"id": "p0", "name": "p0", "type": "llm",
                         "prompt_template": "t.md", "agent": "main",
                         "transitions": {"on_success": "complete"}}],
            "terminals": ["complete"],
            "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
        }), encoding="utf-8")
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        class _SM:
            get_or_create = lambda self, role="", lifecycle="ephemeral", agent="", **kw: "sid"  # type: ignore[method-assign]
            send_command = lambda self, sid, cmd, timeout=120, agent="", **kw: json.dumps({"selected_workflow": "wf.yaml"})  # type: ignore[method-assign]

        class _PL:
            load_prompt = lambda self, template_name, context: "prompt"  # type: ignore[method-assign]

        telemetry = FakeTelemetryRecorder()
        _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=_SM(),
            prompt_loader=_PL(),
            project_context={},
            selector_name="agent-test",
            fallback=None,
            selector_path="test.yaml",
            # no selector_config → defaults, no agent override
            telemetry=telemetry,
        )

        prompt_events = telemetry.events_of_type("selector_prompt_sent")
        assert prompt_events[0]["details"]["selector_agent"] is None
