"""Side-effect-free validation for migration project inputs."""

from __future__ import annotations

import argparse
import os
import stat
from dataclasses import dataclass
from pathlib import Path


IGNORED_DIRECTORY_NAMES = frozenset(
    {".git", ".sm-artifacts", ".venv", "__pycache__", "migration_reports"}
)
IGNORED_FILE_NAMES = frozenset({".DS_Store", ".gitkeep", "Thumbs.db"})


class ProjectPreflightError(ValueError):
    """Raised when a project cannot safely enter the migration workflow."""


@dataclass(frozen=True)
class ProjectPreflightResult:
    project_dir: Path
    regular_file_count: int
    meaningful_file_count: int
    ignored_entry_count: int


def validate_project_input(project_dir: Path) -> ProjectPreflightResult:
    """Validate *project_dir* without following directory symlinks.

    A migration input must contain at least one file-like project entry after
    generated state and common operating-system metadata have been ignored.
    Symlinks to files count as project content, but symlinked directories are
    never traversed.
    """

    resolved = project_dir.expanduser().resolve()
    if not resolved.exists():
        raise ProjectPreflightError(f"Project directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise ProjectPreflightError(f"Project path is not a directory: {resolved}")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ProjectPreflightError(f"Project directory is not readable: {resolved}")

    regular_file_count = 0
    meaningful_file_count = 0
    ignored_entry_count = 0

    def onerror(error: OSError) -> None:
        raise ProjectPreflightError(
            f"Cannot inspect project directory {resolved}: {error}"
        ) from error

    for root, directory_names, file_names in os.walk(
        resolved, topdown=True, followlinks=False, onerror=onerror
    ):
        retained_directories: list[str] = []
        for name in directory_names:
            path = Path(root) / name
            if name in IGNORED_DIRECTORY_NAMES:
                ignored_entry_count += 1
                continue
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise ProjectPreflightError(
                    f"Cannot inspect project entry {path}: {exc}"
                ) from exc
            if stat.S_ISLNK(mode):
                meaningful_file_count += 1
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in file_names:
            path = Path(root) / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise ProjectPreflightError(
                    f"Cannot inspect project entry {path}: {exc}"
                ) from exc
            if stat.S_ISREG(mode):
                regular_file_count += 1
            if name in IGNORED_FILE_NAMES:
                ignored_entry_count += 1
                continue
            if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                meaningful_file_count += 1

    if meaningful_file_count == 0:
        raise ProjectPreflightError(
            "Project directory contains no migratable files after generated "
            f"state and metadata are ignored: {resolved}"
        )

    return ProjectPreflightResult(
        project_dir=resolved,
        regular_file_count=regular_file_count,
        meaningful_file_count=meaningful_file_count,
        ignored_entry_count=ignored_entry_count,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    try:
        result = validate_project_input(args.project_dir)
    except ProjectPreflightError as exc:
        parser.exit(2, f"Project preflight failed: {exc}\n")
    print(
        f"Project preflight passed: {result.project_dir} "
        f"({result.meaningful_file_count} migratable files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
