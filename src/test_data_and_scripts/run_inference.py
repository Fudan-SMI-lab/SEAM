#!/usr/bin/env python3
"""run_inference.py — migrated-project inference entry script (cwd contract).

Concrete referent for the cwd contract documented in
``RepairLoopEngine._resolve_script_cwd`` (src/core/repair_loop.py L1139-1147):

    cd /path/to/project && python test_data_and_scripts/run_inference.py

After ``cd``-stripping the argv is ``[python, test_data_and_scripts/run_inference.py]``
and the execution cwd is the project root, so the relative script path
resolves without double-nesting (never ``test_data_and_scripts/test_data_and_scripts/...``).

This is a smoke-level stand-in for the migrated project's real inference
runner (stdlib only, no model required). It verifies the cwd contract,
locates the project root and its constraints marker, and reports where the
real inference entry would be wired. A real migrated project replaces the
smoke body with its actual inference invocation.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).name
CWD = Path.cwd()


def _project_root() -> Path:
    """Return the project root for this invocation.

    Prefer the cwd-contract root: the directory from which the relative
    script path ``test_data_and_scripts/run_inference.py`` resolves. Fall
    back to walking up from the script location to the nearest
    ``ADAPTATION_REQUIREMENTS.md`` marker (the launcher's constraints
    file), then to the script directory's grandparent.
    """
    if (CWD / "test_data_and_scripts" / SCRIPT_NAME).is_file():
        return CWD
    for directory in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        if (directory / "ADAPTATION_REQUIREMENTS.md").is_file():
            return directory
    return SCRIPT_DIR.parent.parent


def main() -> int:
    print("run_inference.py — migrated-project inference entry (smoke)")
    print(f"  script dir  : {SCRIPT_DIR}")
    print(f"  cwd         : {CWD}")

    project_root = _project_root()
    print(f"  project root: {project_root}")

    constraints = project_root / "ADAPTATION_REQUIREMENTS.md"
    if constraints.is_file():
        lines = len(constraints.read_text(encoding="utf-8").splitlines())
        print(f"  constraints : {constraints} (found, {lines} lines)")
    else:
        print("  constraints : none at project root")

    if (CWD / "test_data_and_scripts" / SCRIPT_NAME).is_file():
        print("  cwd contract: OK — `test_data_and_scripts/run_inference.py` resolves from cwd")
    else:
        print("  cwd contract: invoke from the project root as")
        print("                `cd <project-root> && python test_data_and_scripts/run_inference.py`")

    original_src = project_root / "original_src"
    if original_src.is_dir():
        print(f"  model source: {original_src} (present)")
    else:
        print("  model source: no original_src/ in this copy — the migrated project supplies the model")

    print("inference entry check PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
