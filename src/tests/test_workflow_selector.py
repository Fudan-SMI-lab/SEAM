"""Targeted tests for the workflow selector module.

Uses fake session managers and prompt loaders — no real OpenCode server calls.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.workflow_selector import (
    is_selector_yaml,
    is_selector_file,
    read_selector_resolution_metadata,
    resolve_workflow_from_selector,
    _validate_selector_schema,
    _resolve_candidates,
    _select_workflow_via_agent,
    _validate_agent_selection,
    _resolve_fallback,
    _format_project_summary,
    _deep_merge_overrides,
    _materialize_merged_workflow,
)


# ── Helper factories ────────────────────────────────────────────────────


def _make_minimal_workflow_yaml(name: str = "test_workflow") -> dict[str, Any]:
    """Return a minimal valid workflow dict that load_workflow() would accept."""
    return {
        "name": name,
        "version": "1.0",
        "description": "Test workflow",
        "phases": [
            {
                "id": "phase_a",
                "name": "Phase A",
                "type": "llm",
                "agent": "main_engineer",
                "prompt_template": "test.md",
                "transitions": {"on_success": "complete"},
            }
        ],
        "terminals": ["complete", "failed"],
        "agents": {
            "main_engineer": {"role": "main_engineer", "lifecycle": "persistent"},
        },
    }


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False)
    return path


class FakeSessionManager:
    """Session manager that returns pre-configured responses."""

    def __init__(
        self,
        agent_response: str = '{"selected_workflow": "wf_a.yaml"}',
        raise_on_send: bool = False,
    ) -> None:
        self.agent_response = agent_response
        self.raise_on_send = raise_on_send
        self.sessions_created: list[str] = []
        self.commands_sent: list[tuple[str, str]] = []
        self.last_agent: str | None = None

    def get_or_create(self, role: str, lifecycle: str = "ephemeral", agent: str = "", **kwargs: object) -> str:
        self.last_agent = agent or None
        sid = f"fake-session-{role}"
        self.sessions_created.append(sid)
        return sid

    def create_session(
        self,
        role: str = "",
        lifecycle: str = "ephemeral",
        title: str = "",
        agent: str = "",
        **kwargs: object,
    ) -> str:
        self.last_agent = agent or None
        sid = f"fake-session-{role}-{title}"
        self.sessions_created.append(sid)
        return sid

    def send_command(self, session_id: str, command: str, timeout: int = 120, agent: str = "", **kwargs: object) -> str:
        if self.raise_on_send:
            raise RuntimeError("Simulated send_command failure")
        self.last_agent = agent or None
        self.commands_sent.append((session_id, command))
        return self.agent_response


class FakePromptLoader:
    """Prompt loader that returns a pre-set template or captures calls."""

    def __init__(self, template: str = "") -> None:
        self.template = template
        self.loaded: list[tuple[str, dict[str, str]]] = []

    def load_prompt(self, template_name: str, context: dict[str, str]) -> str:
        self.loaded.append((template_name, dict(context)))
        if self.template:
            return self.template
        return f"PROMPT:[{template_name}] ctx={json.dumps(context)}"


# ── Tests: is_selector_yaml ─────────────────────────────────────────────


class TestIsSelectorYaml:
    @pytest.mark.parametrize("kind", ["workflow_selector", "workflow-selector"])
    def test_detects_valid_kinds(self, kind: str) -> None:
        assert is_selector_yaml({"kind": kind, "candidate_workflows": []})

    def test_rejects_normal_workflow(self) -> None:
        assert not is_selector_yaml(_make_minimal_workflow_yaml())

    def test_rejects_missing_kind(self) -> None:
        assert not is_selector_yaml({"candidate_workflows": []})

    def test_rejects_unknown_kind(self) -> None:
        assert not is_selector_yaml({"kind": "normal_workflow", "candidate_workflows": []})


# ── Tests: is_selector_file ──────────────────────────────────────────────


class TestIsSelectorFile:
    def test_detects_selector_file(self, tmp_path: Path) -> None:
        selector = tmp_path / "selector.yaml"
        _write_yaml(selector, {"kind": "workflow_selector", "candidate_workflows": [{"path": "wf.yaml"}]})
        assert is_selector_file(str(selector))

    def test_rejects_normal_workflow_file(self, tmp_path: Path) -> None:
        wf = tmp_path / "workflow.yaml"
        _write_yaml(wf, _make_minimal_workflow_yaml())
        assert not is_selector_file(str(wf))

    def test_detects_workflow_selector_with_dash(self, tmp_path: Path) -> None:
        selector = tmp_path / "selector.yaml"
        _write_yaml(selector, {"kind": "workflow-selector", "candidate_workflows": [{"path": "wf.yaml"}]})
        assert is_selector_file(str(selector))

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            is_selector_file(str(tmp_path / "nonexistent.yaml"))


# ── Tests: _validate_selector_schema ──────────────────────────────────


class TestValidateSelectorSchema:
    def test_passes_for_valid_minimal_selector(self) -> None:
        _validate_selector_schema(
            {"kind": "workflow_selector", "candidate_workflows": [{"path": "wf.yaml"}]},
            "test.yaml",
        )

    def test_raises_on_missing_candidates(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _validate_selector_schema({"kind": "workflow_selector"}, "test.yaml")

    def test_raises_on_empty_candidates(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _validate_selector_schema(
                {"kind": "workflow_selector", "candidate_workflows": []}, "test.yaml"
            )

    def test_raises_on_non_list_candidates(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _validate_selector_schema(
                {"kind": "workflow_selector", "candidate_workflows": "not_a_list"},
                "test.yaml",
            )

    def test_raises_on_non_dict_candidate_entry(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            _validate_selector_schema(
                {"kind": "workflow_selector", "candidate_workflows": ["not_a_dict"]},
                "test.yaml",
            )

    def test_raises_on_missing_path_in_candidate(self) -> None:
        with pytest.raises(ValueError, match="missing required.*path"):
            _validate_selector_schema(
                {"kind": "workflow_selector", "candidate_workflows": [{"description": "x"}]},
                "test.yaml",
            )

    def test_raises_on_empty_path_in_candidate(self) -> None:
        with pytest.raises(ValueError, match="missing required.*path"):
            _validate_selector_schema(
                {"kind": "workflow_selector", "candidate_workflows": [{"path": ""}]},
                "test.yaml",
            )

    def test_raises_on_duplicate_candidate_paths(self) -> None:
        with pytest.raises(ValueError, match="duplicate candidate path"):
            _validate_selector_schema(
                {
                    "kind": "workflow_selector",
                    "candidate_workflows": [{"path": "wf.yaml"}, {"path": "wf.yaml"}],
                },
                "test.yaml",
            )

    def test_accepts_valid_fallback(self) -> None:
        _validate_selector_schema(
            {
                "kind": "workflow_selector",
                "candidate_workflows": [{"path": "wf.yaml"}],
                "fallback": "wf.yaml",
            },
            "test.yaml",
        )

    def test_raises_on_non_string_fallback(self) -> None:
        with pytest.raises(ValueError, match="fallback.*must be a non-empty string"):
            _validate_selector_schema(
                {
                    "kind": "workflow_selector",
                    "candidate_workflows": [{"path": "wf.yaml"}],
                    "fallback": 42,
                },
                "test.yaml",
            )

    def test_raises_on_non_dict_overrides(self) -> None:
        with pytest.raises(ValueError, match="overrides.*must be a mapping"):
            _validate_selector_schema(
                {
                    "kind": "workflow_selector",
                    "candidate_workflows": [{"path": "wf.yaml"}],
                    "overrides": "not_a_dict",
                },
                "test.yaml",
            )


# ── Tests: _resolve_candidates ──────────────────────────────────────────


class TestResolveCandidates:
    def test_resolves_relative_to_selector_dir(self, tmp_path: Path) -> None:
        wf = tmp_path / "workflows" / "npu.yaml"
        _write_yaml(wf, _make_minimal_workflow_yaml("npu"))

        selector_dir = tmp_path / "selectors"
        selector_dir.mkdir(parents=True)

        result = _resolve_candidates(
            [{"path": "../workflows/npu.yaml", "description": "NPU flow"}],
            selector_dir,
            "test_selector.yaml",
        )
        assert len(result) == 1
        assert result[0]["path"] == wf.resolve()
        assert result[0]["description"] == "NPU flow"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        selector_dir = tmp_path
        with pytest.raises(FileNotFoundError, match="not found"):
            _resolve_candidates(
                [{"path": "nonexistent.yaml"}],
                selector_dir,
                "test_selector.yaml",
            )

    def test_defaults_description_to_stem(self, tmp_path: Path) -> None:
        wf = tmp_path / "my_workflow.yaml"
        _write_yaml(wf, _make_minimal_workflow_yaml())

        result = _resolve_candidates(
            [{"path": "my_workflow.yaml"}],
            tmp_path,
            "test_selector.yaml",
        )
        assert result[0]["description"] == "my_workflow"


# ── Tests: _validate_agent_selection ──────────────────────────────────


class TestValidateAgentSelection:
    def test_valid_selection_by_raw_path(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf_a.yaml", _make_minimal_workflow_yaml("a"))
        candidates = [{"path": wf, "raw_path": "wf_a.yaml", "description": "A"}]
        result = _validate_agent_selection(
            {"selected_workflow": "wf_a.yaml"}, candidates, "selector.yaml"
        )
        assert result == wf

    def test_valid_selection_by_abs_path(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf_b.yaml", _make_minimal_workflow_yaml("b"))
        candidates = [{"path": wf, "raw_path": "wf_b.yaml", "description": "B"}]
        result = _validate_agent_selection(
            {"selected_workflow": str(wf)}, candidates, "selector.yaml"
        )
        assert result == wf

    def test_valid_selection_by_stem(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf_c.yaml", _make_minimal_workflow_yaml("c"))
        candidates = [{"path": wf, "raw_path": "wf_c.yaml", "description": "C"}]
        result = _validate_agent_selection(
            {"selected_workflow": "wf_c"}, candidates, "selector.yaml"
        )
        assert result == wf

    def test_non_dict_output_returns_none(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": ""}]
        assert _validate_agent_selection("just a string", candidates, "s.yaml") is None

    def test_missing_selected_workflow_key(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": ""}]
        assert _validate_agent_selection({"other": "val"}, candidates, "s.yaml") is None

    def test_not_in_candidate_list(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": ""}]
        assert _validate_agent_selection(
            {"selected_workflow": "unknown.yaml"}, candidates, "s.yaml"
        ) is None


# ── Tests: _resolve_fallback ────────────────────────────────────────────


class TestResolveFallback:
    def test_resolves_fallback_by_raw_path(self, tmp_path: Path) -> None:
        wf_a = _write_yaml(tmp_path / "wf_a.yaml", _make_minimal_workflow_yaml("a"))
        wf_b = _write_yaml(tmp_path / "wf_b.yaml", _make_minimal_workflow_yaml("b"))
        candidates = [
            {"path": wf_a, "raw_path": "wf_a.yaml", "description": "A"},
            {"path": wf_b, "raw_path": "wf_b.yaml", "description": "B"},
        ]
        result = _resolve_fallback("wf_b.yaml", candidates, "s.yaml")
        assert result == wf_b

    def test_resolves_fallback_by_abs_path(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": ""}]
        result = _resolve_fallback(str(wf), candidates, "s.yaml")
        assert result == wf

    def test_raises_when_no_fallback_configured(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": ""}]
        with pytest.raises(ValueError, match="no 'fallback' is configured"):
            _resolve_fallback(None, candidates, "s.yaml")

    def test_raises_when_fallback_not_in_candidates(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": ""}]
        with pytest.raises(ValueError, match="not among the candidate_workflows"):
            _resolve_fallback("other.yaml", candidates, "s.yaml")


# ── Tests: _format_project_summary ──────────────────────────────────────


class TestFormatProjectSummary:
    def test_empty_context(self) -> None:
        result = _format_project_summary({})
        assert "No project context" in result

    def test_renders_fields(self) -> None:
        ctx = {
            "project_name": "my-project",
            "language": "Python",
            "framework": "PyTorch",
            "file_count": 150,
            "build_system": "setuptools",
            "notes": "has custom CUDA kernels",
        }
        result = _format_project_summary(ctx)
        assert "my-project" in result
        assert "Python" in result
        assert "PyTorch" in result
        assert "150" in result
        assert "setuptools" in result
        assert "custom CUDA kernels" in result

    def test_renders_file_hints_as_list(self) -> None:
        ctx = {"file_hints": ["setup.py", "src/main.cu", "Dockerfile"]}
        result = _format_project_summary(ctx)
        assert "setup.py" in result
        assert "src/main.cu" in result
        assert "Dockerfile" in result

    def test_renders_file_hints_as_comma_separated_string(self) -> None:
        ctx = {"file_hints": "setup.py, src/main.cu, Dockerfile"}
        result = _format_project_summary(ctx)
        assert "setup.py" in result
        assert "src/main.cu" in result

    def test_uses_project_path_fallback(self) -> None:
        ctx = {"project_path": "/tmp/some-dir"}
        result = _format_project_summary(ctx)
        assert "/tmp/some-dir" in result

    def test_caps_file_hints_at_10(self) -> None:
        ctx = {"file_hints": [f"file_{i}.py" for i in range(20)]}
        result = _format_project_summary(ctx)
        # Only 10 files should appear
        count = result.count("file_")
        assert count <= 10


# ── Tests: _deep_merge_overrides ────────────────────────────────────────


class TestDeepMergeOverrides:
    def test_scalar_field_added(self) -> None:
        base = {"a": 1}
        result = _deep_merge_overrides(base, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_scalar_field_replaced(self) -> None:
        base = {"a": 1, "b": 2}
        result = _deep_merge_overrides(base, {"a": 10})
        assert result == {"a": 10, "b": 2}

    def test_nested_dict_merged_recursively(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        overrides = {"x": {"b": 20, "c": 3}}
        result = _deep_merge_overrides(base, overrides)
        assert result == {"x": {"a": 1, "b": 20, "c": 3}}

    def test_list_replaced_not_merged(self) -> None:
        base = {"items": [1, 2, 3]}
        overrides = {"items": [4, 5]}
        result = _deep_merge_overrides(base, overrides)
        assert result == {"items": [4, 5]}

    def test_nested_list_in_dict_replaced(self) -> None:
        base = {"config": {"phases": ["a", "b", "c"]}}
        overrides = {"config": {"phases": ["x", "y"]}}
        result = _deep_merge_overrides(base, overrides)
        assert result == {"config": {"phases": ["x", "y"]}}

    def test_base_not_mutated(self) -> None:
        base = {"a": 1, "nested": {"b": 2}}
        overrides = {"nested": {"c": 3}}
        result = _deep_merge_overrides(base, overrides)
        assert result["nested"] == {"b": 2, "c": 3}
        assert base["nested"] == {"b": 2}  # original untouched

    def test_empty_overrides_noop(self) -> None:
        base = _make_minimal_workflow_yaml()
        result = _deep_merge_overrides(base, {})
        assert result == base

    def test_deeply_nested_merge(self) -> None:
        base = {
            "execution_backend": {
                "mode": "container",
                "runtime": "docker",
                "env_vars": {"FOO": "bar"},
            }
        }
        overrides = {
            "execution_backend": {
                "runtime": "podman",
                "env_vars": {"BAZ": "qux"},
                "timeout": 3600,
            }
        }
        result = _deep_merge_overrides(base, overrides)
        assert result["execution_backend"]["mode"] == "container"  # preserved
        assert result["execution_backend"]["runtime"] == "podman"   # replaced
        assert result["execution_backend"]["timeout"] == 3600        # added
        # env_vars: FOO.was.overwritten because whole dict replaced?
        # Actually, since both values are dicts, they merge recursively.
        assert result["execution_backend"]["env_vars"] == {"FOO": "bar", "BAZ": "qux"}


# ── Tests: _materialize_merged_workflow ─────────────────────────────────


class TestMaterializeMergedWorkflow:
    def test_writes_to_resolved_workflows_dir(self, tmp_path: Path) -> None:
        merged = _make_minimal_workflow_yaml("selected")
        path = _materialize_merged_workflow(merged, "my-selector", tmp_path)
        assert path.exists()
        assert path.parent.name == "resolved_workflows"
        assert path.name == "my-selector.yaml"
        loaded = yaml.safe_load(path.read_text())
        assert loaded["name"] == "selected"

    def test_sanitizes_selector_name(self, tmp_path: Path) -> None:
        merged = _make_minimal_workflow_yaml()
        path = _materialize_merged_workflow(merged, "my selector/name", tmp_path)
        assert " " not in path.name
        assert "/" not in path.name
        assert path.suffix == ".yaml"

    def test_handles_empty_selector_name(self, tmp_path: Path) -> None:
        merged = _make_minimal_workflow_yaml()
        path = _materialize_merged_workflow(merged, "   ", tmp_path)
        assert path.name == "selected_workflow.yaml"


# ── Integration: resolve_workflow_from_selector ────────────────────────


class TestResolveWorkflowFromSelector:
    def test_end_to_end_agent_selects_first_candidate(
        self, tmp_path: Path
    ) -> None:
        """Full pipeline: valid selector → agent picks first → materialize."""
        # Write two candidate workflows
        wf_a = _write_yaml(tmp_path / "workflows" / "npu.yaml", _make_minimal_workflow_yaml("npu"))
        wf_b = _write_yaml(tmp_path / "workflows" / "ppu.yaml", _make_minimal_workflow_yaml("ppu"))

        # Write selector YAML
        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "auto-select",
            "candidate_workflows": [
                {"path": str(wf_a), "description": "NPU migration (Ascend)"},
                {"path": str(wf_b), "description": "PPU migration"},
            ],
            "fallback": str(wf_a),
        }
        _write_yaml(selector, selector_data)

        # Agent returns the first candidate
        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": str(wf_a)})
        )
        prompt_loader = FakePromptLoader()

        result = resolve_workflow_from_selector(
            str(selector),
            session_mgr,
            prompt_loader,
            project_context={"language": "Python", "framework": "PyTorch"},
            output_dir=tmp_path / "output",
        )

        assert result.exists()
        loaded = yaml.safe_load(result.read_text())
        assert loaded["name"] == "npu"
        assert len(prompt_loader.loaded) == 1
        assert prompt_loader.loaded[0][0] == "workflow_select"
        metadata = read_selector_resolution_metadata(result)
        assert metadata == {
            "selector_path": str(selector),
            "selected_path": str(wf_a),
            "materialized_path": str(result),
        }

    def test_end_to_end_with_overrides(self, tmp_path: Path) -> None:
        """Selector with overrides should produce merged workflow."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml("original"))

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "with-overrides",
            "candidate_workflows": [{"path": str(wf)}],
            "overrides": {
                "description": "Overridden description",
                "globals": {"custom_key": "custom_val"},
            },
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": str(wf)})
        )
        prompt_loader = FakePromptLoader()
        result = resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            output_dir=tmp_path / "output",
        )

        loaded = yaml.safe_load(result.read_text())
        assert loaded["name"] == "original"  # preserved
        assert loaded["description"] == "Overridden description"  # replaced
        assert loaded["globals"]["custom_key"] == "custom_val"  # added

    def test_end_to_end_fallback_used_when_agent_fails(self, tmp_path: Path) -> None:
        """When agent raises, fallback is used."""
        wf_a = _write_yaml(tmp_path / "npu.yaml", _make_minimal_workflow_yaml("npu"))
        wf_b = _write_yaml(tmp_path / "ppu.yaml", _make_minimal_workflow_yaml("ppu"))

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "fallback-test",
            "candidate_workflows": [
                {"path": str(wf_a), "description": "NPU"},
                {"path": str(wf_b), "description": "PPU"},
            ],
            "fallback": str(wf_b),
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(raise_on_send=True)
        prompt_loader = FakePromptLoader()
        result = resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            output_dir=tmp_path / "output",
        )

        loaded = yaml.safe_load(result.read_text())
        assert loaded["name"] == "ppu"  # fallback was used

    def test_end_to_end_no_fallback_raises_clear_error(self, tmp_path: Path) -> None:
        """Agent fails and no fallback → ValueError."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "no-fallback",
            "candidate_workflows": [{"path": str(wf)}],
            # no fallback
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(raise_on_send=True)
        prompt_loader = FakePromptLoader()

        with pytest.raises(ValueError, match="no 'fallback' is configured"):
            resolve_workflow_from_selector(
                str(selector), session_mgr, prompt_loader,
                output_dir=tmp_path / "output",
            )

    def test_invalid_agent_output_falls_back_to_fallback(self, tmp_path: Path) -> None:
        """Agent returns non-dict or missing key → fallback."""
        wf_a = _write_yaml(tmp_path / "npu.yaml", _make_minimal_workflow_yaml("npu"))
        wf_b = _write_yaml(tmp_path / "ppu.yaml", _make_minimal_workflow_yaml("ppu"))

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "bad-output",
            "candidate_workflows": [
                {"path": str(wf_a), "description": "NPU"},
                {"path": str(wf_b), "description": "PPU"},
            ],
            "fallback": str(wf_a),
        }
        _write_yaml(selector, selector_data)

        # Agent returns garbage (not valid JSON with selected_workflow)
        session_mgr = FakeSessionManager(agent_response="hello, pick NPU!")
        prompt_loader = FakePromptLoader()
        result = resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            output_dir=tmp_path / "output",
        )

        loaded = yaml.safe_load(result.read_text())
        assert loaded["name"] == "npu"  # fallback

    def test_selects_with_project_context(self, tmp_path: Path) -> None:
        """Verify project context is passed to prompt."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "context-test",
            "candidate_workflows": [{"path": str(wf)}],
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": str(wf)})
        )
        prompt_loader = FakePromptLoader()

        project_ctx = {
            "project_name": "cuda-op-project",
            "language": "C++",
            "framework": "PyTorch",
            "cuda_version": "12.1",
            "file_hints": ["setup.py", "csrc/kernels.cu", "Dockerfile"],
        }

        resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            project_context=project_ctx,
            output_dir=tmp_path / "output",
        )

        # Verify prompt context was passed through
        ctx = prompt_loader.loaded[0][1]
        assert "cuda-op-project" in ctx["project_context"]
        assert "PyTorch" in ctx["project_context"]

    def test_raises_for_non_selector_file(self, tmp_path: Path) -> None:
        """Passing a normal workflow YAML should raise."""
        wf = _write_yaml(tmp_path / "normal.yaml", _make_minimal_workflow_yaml())
        with pytest.raises(ValueError, match="not a workflow selector"):
            resolve_workflow_from_selector(
                str(wf), FakeSessionManager(), FakePromptLoader(),
                output_dir=tmp_path,
            )

    def test_agent_selection_json_fenced_block(self, tmp_path: Path) -> None:
        """Agent response with JSON in a markdown fenced block (extract_json_response handles it)."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "fenced-test",
            "candidate_workflows": [{"path": str(wf)}],
        }
        _write_yaml(selector, selector_data)

        # Simulate what extract_json_response handles: fenced JSON block
        response = f"I think we should use:\n```json\n{{\"selected_workflow\": \"{str(wf)}\"}}\n```"
        session_mgr = FakeSessionManager(agent_response=response)
        prompt_loader = FakePromptLoader()

        result = resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            output_dir=tmp_path / "output",
        )
        assert result.exists()

    def test_deterministic_output_path(self, tmp_path: Path) -> None:
        """Same selector name and output_dir → same output path."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "det-test",
            "candidate_workflows": [{"path": str(wf)}],
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": str(wf)})
        )
        prompt_loader = FakePromptLoader()

        out = tmp_path / "output"
        r1 = resolve_workflow_from_selector(str(selector), session_mgr, prompt_loader, output_dir=out)
        r2 = resolve_workflow_from_selector(str(selector), session_mgr, prompt_loader, output_dir=out)
        assert r1 == r2

    # ── _select_workflow_via_agent unit tests ─────────────────────────

    def test_select_workflow_via_agent_happy_path(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": "wf.yaml"})
        )
        prompt_loader = FakePromptLoader()

        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=session_mgr,
            prompt_loader=prompt_loader,
            project_context={"language": "Python"},
            selector_name="test",
            fallback=None,
            selector_path="test.yaml",
        )
        assert result == wf

    def test_select_workflow_via_agent_falls_back(self, tmp_path: Path) -> None:
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        session_mgr = FakeSessionManager(raise_on_send=True)
        prompt_loader = FakePromptLoader()

        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=session_mgr,
            prompt_loader=prompt_loader,
            project_context={},
            selector_name="test",
            fallback="wf.yaml",
            selector_path="test.yaml",
        )
        assert result == wf

    def test_selector_agent_config_is_passed_to_session_manager(
        self, tmp_path: Path
    ) -> None:
        """selector.agent config is forwarded to get_or_create and send_command."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": "wf.yaml"})
        )
        prompt_loader = FakePromptLoader()

        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=session_mgr,
            prompt_loader=prompt_loader,
            project_context={},
            selector_name="test",
            fallback=None,
            selector_path="test.yaml",
            selector_config={"agent": "build", "timeout": 90},
        )
        assert result == wf
        assert session_mgr.last_agent == "build"

    def test_selector_fallback_overrides_top_level_fallback(
        self, tmp_path: Path
    ) -> None:
        """selector.fallback takes precedence over top-level fallback."""
        wf_a = _write_yaml(tmp_path / "npu.yaml", _make_minimal_workflow_yaml("npu"))
        wf_b = _write_yaml(tmp_path / "ppu.yaml", _make_minimal_workflow_yaml("ppu"))
        wf_smoke = _write_yaml(tmp_path / "smoke.yaml", _make_minimal_workflow_yaml("smoke"))

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "fallback-override-test",
            "candidate_workflows": [
                {"path": str(wf_a), "description": "NPU"},
                {"path": str(wf_b), "description": "PPU"},
                {"path": str(wf_smoke), "description": "Smoke"},
            ],
            "selector": {"fallback": str(wf_smoke)},
            "fallback": str(wf_b),  # top-level fallback (should be ignored)
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(raise_on_send=True)
        prompt_loader = FakePromptLoader()
        result = resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            output_dir=tmp_path / "output",
        )
        loaded = yaml.safe_load(result.read_text())
        assert loaded["name"] == "smoke"  # selector.fallback wins

    def test_selector_without_agent_still_works(self, tmp_path: Path) -> None:
        """When selector.agent is not set, the selection still passes."""
        wf = _write_yaml(tmp_path / "wf.yaml", _make_minimal_workflow_yaml())
        candidates = [{"path": wf, "raw_path": "wf.yaml", "description": "test"}]

        session_mgr = FakeSessionManager(
            agent_response=json.dumps({"selected_workflow": "wf.yaml"})
        )
        prompt_loader = FakePromptLoader()

        result = _select_workflow_via_agent(
            candidates=candidates,
            session_mgr=session_mgr,
            prompt_loader=prompt_loader,
            project_context={},
            selector_name="test",
            fallback=None,
            selector_path="test.yaml",
            # no selector_config → defaults
        )
        assert result == wf
        assert session_mgr.last_agent is None  # No agent override

    def test_top_level_fallback_works_when_selector_block_absent(
        self, tmp_path: Path
    ) -> None:
        """Top-level fallback is used when no selector block is present."""
        wf_a = _write_yaml(tmp_path / "npu.yaml", _make_minimal_workflow_yaml("npu"))
        wf_b = _write_yaml(tmp_path / "ppu.yaml", _make_minimal_workflow_yaml("ppu"))

        selector = tmp_path / "selector.yaml"
        selector_data = {
            "kind": "workflow_selector",
            "name": "legacy-selector",
            "candidate_workflows": [
                {"path": str(wf_a), "description": "NPU"},
                {"path": str(wf_b), "description": "PPU"},
            ],
            "fallback": str(wf_b),  # top-level only — no selector block
        }
        _write_yaml(selector, selector_data)

        session_mgr = FakeSessionManager(raise_on_send=True)
        prompt_loader = FakePromptLoader()
        result = resolve_workflow_from_selector(
            str(selector), session_mgr, prompt_loader,
            output_dir=tmp_path / "output",
        )
        loaded = yaml.safe_load(result.read_text())
        assert loaded["name"] == "ppu"  # top-level fallback used


# ── Tests: workflow_select.md prompt template content ──────────────────


class TestWorkflowSelectPromptContent:
    """Verify the workflow_select.md prompt template instructs device-first
    exploration and includes Muxi/MACA signals."""

    @pytest.fixture(scope="class")
    def _prompt_text(self) -> str:
        prompt_path = PROJECT_ROOT / "prompts" / "workflow_select.md"
        assert prompt_path.exists(), f"Prompt template not found: {prompt_path}"
        return prompt_path.read_text(encoding="utf-8")

    def test_prompt_requires_device_exploration_before_selection(
        self, _prompt_text: str
    ) -> None:
        """The prompt must instruct the agent to explore device/environment FIRST."""
        assert "explore the " in _prompt_text.lower() or "explore" in _prompt_text.lower()
        assert "device" in _prompt_text.lower()
        assert (
            "before" in _prompt_text.lower()
            and ("select" in _prompt_text.lower() or "choos" in _prompt_text.lower())
        ) or "BEFORE" in _prompt_text

    def test_prompt_includes_muxi_device_signals(self, _prompt_text: str) -> None:
        """Prompt must mention Muxi/MACA device discovery commands and paths."""
        must_contain = [
            "mx-smi",
            "/dev/mxcd",
            "/opt/maca",
            "/usr/local/metax",
        ]
        for signal in must_contain:
            assert signal in _prompt_text, (
                f"Prompt missing Muxi device signal: {signal!r}"
            )

    def test_prompt_includes_muxi_env_variable_checks(self, _prompt_text: str) -> None:
        """Prompt must mention MUSA/MACA environment variable checks."""
        assert "MUSA_VISIBLE_DEVICES" in _prompt_text
        assert "MACA_VISIBLE_DEVICES" in _prompt_text

    def test_prompt_includes_generic_npu_signals(self, _prompt_text: str) -> None:
        """Prompt should still cover Ascend NPU/PPU/NVIDIA for cross-platform coverage."""
        assert "npu-smi" in _prompt_text.lower() or "ascend" in _prompt_text.lower()
        assert "/dev/davinci" in _prompt_text or "ascend" in _prompt_text.lower()

    def test_prompt_emphasizes_device_evidence_as_primary(self, _prompt_text: str) -> None:
        """Device evidence must be described as the PRIMARY selection factor."""
        assert "PRIMARY" in _prompt_text or "primary" in _prompt_text.lower()
        assert "device" in _prompt_text.lower()

    def test_prompt_does_not_have_pytorch_to_npu_bias(self, _prompt_text: str) -> None:
        """The prompt must NOT say 'PyTorch -> prefer NPU'. Must frame framework as secondary."""
        lowered = _prompt_text.lower()
        assert "framework presence is secondary" in lowered, (
            "Prompt must state that framework presence is secondary to device evidence"
        )
        assert "DO NOT assume" in _prompt_text and "pytorch" in lowered, (
            "Prompt must warn against assuming platform based on PyTorch usage alone"
        )
