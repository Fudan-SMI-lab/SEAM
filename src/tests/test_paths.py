from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import (  # noqa: E402
    default_output_projects_root,
    execution_root,
    legacy_workspace_root,
    project_search_roots,
    src_root,
    workspace_root,
)


def test_execution_root_is_seam_root() -> None:
    assert src_root().name == "src"
    assert execution_root().name == "SEAM"
    assert workspace_root() == execution_root()


def test_default_outputs_are_under_execution_root() -> None:
    assert default_output_projects_root() == execution_root() / "output_projects"


def test_project_search_roots_are_root_first_with_legacy_fallbacks() -> None:
    roots = project_search_roots()

    assert roots[0] == execution_root() / "original_projects"
    assert roots[1] == execution_root() / "cuda_projects"
    assert legacy_workspace_root() / "original_projects" in roots
    assert legacy_workspace_root() / "cuda_projects" in roots
