"""Shared path helpers for root-aware migration_utils execution."""

from __future__ import annotations

from os import environ
from pathlib import Path


def _parent_workspace_root() -> Path:
    """Return the workspace directory that contains SEAM."""
    return migration_utils_root().parent.parent


def migration_utils_root() -> Path:
    """Return the migration_utils package root."""
    return Path(__file__).resolve().parent.parent


def execution_root() -> Path:
    """Return the unified SEAM execution root."""
    return migration_utils_root().parent


def workspace_root() -> Path:
    """Return the workspace root exposed to prompts and OpenCode sessions."""
    return execution_root()


def default_output_projects_root() -> Path:
    """Return the output-project copy destination for E2E runs.

    Controlled via ``MIGRATION_OUTPUT_PROJECTS_ROOT`` environment variable.
    Falls back to ``<workspace>/output_projects`` (outside the repo) so that
    mutable output directories are not placed inside the controller tree.
    """
    env_override = environ.get("MIGRATION_OUTPUT_PROJECTS_ROOT", "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()
    return _parent_workspace_root() / "output_projects"


def legacy_workspace_root() -> Path:
    """Return the historical parent workspace for compatibility lookups."""
    return execution_root().parent


def project_search_roots() -> list[Path]:
    """Return root-first project lookup directories with legacy fallbacks."""
    candidates = [
        execution_root() / "original_projects",
        execution_root() / "cuda_projects",
        legacy_workspace_root() / "original_projects",
        legacy_workspace_root() / "cuda_projects",
    ]
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            roots.append(candidate)
            seen.add(resolved)
    return roots


def resolve_relative_path(path: str | Path, *, extra_roots: list[Path] | None = None) -> Path:
    """Resolve a relative path from cwd, execution root, then migration_utils root."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate

    search_roots = [Path.cwd(), execution_root(), migration_utils_root()]
    if extra_roots:
        search_roots.extend(extra_roots)

    for root in search_roots:
        resolved = root / candidate
        if resolved.exists():
            return resolved
    return Path.cwd() / candidate
