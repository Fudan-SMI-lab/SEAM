"""Targeted tests for V3 E2E workflow selector integration.

Uses mocked session managers and prompt loaders — no real OpenCode server calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# pylint: disable-next=wrong-import-position; silent
from tests.e2e.e2e_test_v3 import _build_project_context


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
        assert "src/main.py" in ctx["file_hints"]
        assert "src/utils.py" in ctx["file_hints"]

    def test_detects_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        ctx = _build_project_context(tmp_path)
        assert ctx["build_system"] == "pyproject"

    def test_no_build_system_detected(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        ctx = _build_project_context(tmp_path)
        assert ctx["build_system"] == ""

    def test_file_hints_capped_at_10(self, tmp_path: Path) -> None:
        for i in range(15):
            (tmp_path / f"file_{i}.py").write_text("pass")
        ctx = _build_project_context(tmp_path)
        assert len(ctx["file_hints"]) <= 10


class TestSelectorIntegrationFlow:
    """Verify the selector resolution flow used in e2e_test_v3.run_e2e_v3."""

    def test_selector_resolution_materializes_correctly(self, tmp_path: Path) -> None:
        """Simulate what run_e2e_v3 does: detect selector, resolve, update path."""
        # pylint: disable-next=import-outside-toplevel; silent
        from core.workflow_selector import is_selector_file, resolve_workflow_from_selector

        wf = tmp_path / "wf.yaml"
        wf.write_text(
            yaml.dump(
                {
                    "name": "test_wf",
                    "version": "1.0",
                    "phases": [
                        {
                            "id": "p0",
                            "name": "p0",
                            "type": "llm",
                            "prompt_template": "t.md",
                            "agent": "main",
                            "transitions": {"on_success": "complete"},
                        }
                    ],
                    "terminals": ["complete"],
                    "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
                }
            ),
            encoding="utf-8",
        )

        selector = tmp_path / "selector.yaml"
        selector.write_text(
            yaml.dump(
                {
                    "kind": "workflow_selector",
                    "name": "test-sel",
                    "candidate_workflows": [{"path": str(wf)}],
                    "fallback": str(wf),
                }
            ),
            encoding="utf-8",
        )

        class _FakeSM:
            def get_or_create(
                # pylint: disable-next=unused-argument; silent
                self, role: str, lifecycle: str = "ephemeral", agent: str = "", **kw: object
            ) -> str:
                return "fake-sid"

            def send_command(
                # pylint: disable-next=unused-argument; silent
                self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object
            ) -> str:
                return json.dumps({"selected_workflow": str(wf)})

        class _FakePL:  # pylint: disable=too-few-public-methods; silent
            # pylint: disable-next=unused-argument; silent
            def load_prompt(self, template_name: str, context: dict) -> str:
                return "prompt"

        assert is_selector_file(str(selector))
        assert not is_selector_file(str(wf))

        materialized = resolve_workflow_from_selector(
            str(selector),
            _FakeSM(),
            _FakePL(),
            project_context={"language": "Python"},
            output_dir=tmp_path / "output",
        )
        assert materialized.exists()
        loaded = yaml.safe_load(materialized.read_text())
        assert loaded["name"] == "test_wf"

    def test_selector_override_preserved_in_output(self, tmp_path: Path) -> None:
        """Overrides from selector are merged into materialized workflow."""
        # pylint: disable-next=import-outside-toplevel; silent
        from core.workflow_selector import resolve_workflow_from_selector

        wf = tmp_path / "base.yaml"
        wf.write_text(
            yaml.dump(
                {
                    "name": "base",
                    "version": "1.0",
                    "phases": [
                        {
                            "id": "p0",
                            "name": "p0",
                            "type": "llm",
                            "prompt_template": "t.md",
                            "agent": "main",
                            "transitions": {"on_success": "complete"},
                        }
                    ],
                    "terminals": ["complete"],
                    "agents": {"main": {"role": "main", "lifecycle": "persistent"}},
                    "globals": {"max_repair_iterations": 5},
                    "experience": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )

        selector = tmp_path / "sel.yaml"
        selector.write_text(
            yaml.dump(
                {
                    "kind": "workflow_selector",
                    "name": "override-test",
                    "candidate_workflows": [{"path": str(wf)}],
                    "overrides": {
                        "experience": {"enabled": True},
                        "globals": {"review_gate_enabled": True},
                    },
                }
            ),
            encoding="utf-8",
        )

        class _FakeSM:
            def get_or_create(
                # pylint: disable-next=unused-argument; silent
                self, role: str, lifecycle: str = "ephemeral", agent: str = "", **kw: object
            ) -> str:
                return "sid"

            def send_command(
                # pylint: disable-next=unused-argument; silent
                self, sid: str, cmd: str, timeout: int = 120, agent: str = "", **kw: object
            ) -> str:
                return json.dumps({"selected_workflow": str(wf)})

        class _FakePL:  # pylint: disable=too-few-public-methods; silent
            # pylint: disable-next=unused-argument; silent
            def load_prompt(self, template_name: str, context: dict) -> str:
                return "prompt"

        materialized = resolve_workflow_from_selector(
            str(selector),
            _FakeSM(),
            _FakePL(),
            output_dir=tmp_path / "output",
        )
        loaded = yaml.safe_load(materialized.read_text())
        assert loaded["experience"]["enabled"] is True
        assert loaded["globals"]["review_gate_enabled"] is True
        assert loaded["globals"]["max_repair_iterations"] == 5


class TestSmokeSelectorYaml:
    """Verify the smoke selector YAML is well-formed and loadable."""

    def test_smoke_selector_exists_and_has_valid_kind(self) -> None:
        # pylint: disable-next=import-outside-toplevel; silent
        from core.workflow_selector import is_selector_file

        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        assert smoke_path.exists(), f"Expected smoke selector at {smoke_path}"
        assert is_selector_file(str(smoke_path))

    def test_smoke_selector_candidates_exist(self) -> None:
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

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
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

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
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        assert raw.get("fallback") is not None

    def test_smoke_selector_fallback_is_not_npu(self) -> None:
        """Smoke selector fallback must not be NPU migration."""
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

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
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        selector_cfg = raw.get("selector", {})
        assert isinstance(selector_cfg, dict), (
            "Smoke selector should have a 'selector' config block"
        )
        assert "agent" in selector_cfg, "Smoke selector should specify selector.agent"
        assert selector_cfg["agent"], "Selector agent should not be empty"

    # ── Content assertions: candidates, platform hints, fallback ─────────

    def test_smoke_selector_includes_muxi_musa_candidates(self) -> None:
        """Smoke selector MUST include at least one Muxi/MUSA candidate workflow."""
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())
        candidates = raw["candidate_workflows"]
        musa_candidates = [
            c
            for c in candidates
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
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

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
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

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
        import yaml as _yaml  # pylint: disable=import-outside-toplevel,reimported; silent

        wf_dir = PROJECT_ROOT / "workflows"
        smoke_path = wf_dir / "workflow_selector_smoke.yaml"
        raw = _yaml.safe_load(smoke_path.read_text())

        fallback = raw.get("fallback", "")
        assert fallback == "experience_memory_test.yaml", (
            f"Smoke selector fallback must be 'experience_memory_test.yaml' (neutral), "
            f"got {fallback!r}"
        )
