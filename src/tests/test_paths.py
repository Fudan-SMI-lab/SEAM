import os
from pathlib import Path
from unittest import mock
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import (  # noqa: E402
    default_output_projects_root,
    execution_root,
    legacy_workspace_root,
    project_search_roots,
    migration_utils_root,
    workspace_root,
)


def test_execution_root_is_seam_root() -> None:
    assert migration_utils_root().name == "migration_utils"
    assert execution_root().name == "SEAM"
    assert workspace_root() == execution_root()


def test_default_outputs_are_outside_execution_root() -> None:
    """Default output projects go outside the SEAM repo (parent workspace)."""
    result = default_output_projects_root()
    assert result.name == "output_projects"
    assert result.parent == legacy_workspace_root()


def test_default_outputs_env_override() -> None:
    """MIGRATION_OUTPUT_PROJECTS_ROOT env var overrides the default."""
    with mock.patch.dict(os.environ, {"MIGRATION_OUTPUT_PROJECTS_ROOT": "/custom/output/path"}):
        result = default_output_projects_root()
    assert result == Path("/custom/output/path")


def test_default_outputs_env_override_resolves_home() -> None:
    """Env override resolves ~ and expands user home."""
    with mock.patch.dict(os.environ, {"MIGRATION_OUTPUT_PROJECTS_ROOT": "~/my_outputs"}):
        result = default_output_projects_root()
    assert result == Path.home() / "my_outputs"


def test_default_outputs_env_override_empty_ignored() -> None:
    """Empty or whitespace-only env var is ignored (falls back to default)."""
    with mock.patch.dict(os.environ, {"MIGRATION_OUTPUT_PROJECTS_ROOT": "   "}):
        result = default_output_projects_root()
    # Should fall back to default
    assert result.name == "output_projects"
    assert result.parent == legacy_workspace_root()


def test_project_search_roots_are_root_first_with_legacy_fallbacks() -> None:
    roots = project_search_roots()

    assert roots[0] == execution_root() / "original_projects"
    assert roots[1] == execution_root() / "cuda_projects"
    assert legacy_workspace_root() / "original_projects" in roots
    assert legacy_workspace_root() / "cuda_projects" in roots
