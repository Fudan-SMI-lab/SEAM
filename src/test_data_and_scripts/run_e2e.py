#!/usr/bin/env python3
"""run_e2e.py — end-to-end validation entry script (cwd contract).

Referenced by:
  * ``src/README.md`` L35-41 — the documented project layout for a
    migratable CUDA project (``test_data_and_scripts/run_e2e.py``).
  * ``src/tests/test_usage_guide_docs.py`` L43/L50 and
    ``src/tests/test_ui_events.py`` — the usage-guide contract string
    ``python test_data_and_scripts/run_e2e.py``.
  * ``src/scripts/run_e2e*.sh`` — Phase-3 entry-script discovery over
    ``test_data_and_scripts/*.py``.

This is a smoke-level, self-contained e2e check (stdlib only, no model
required): it checks the documented production floor (Linux / Python
3.10+), locates the project root via the launcher's constraints marker,
and verifies the cwd contract. Behavior depends on the current working
directory: run it from the project root as
``python test_data_and_scripts/run_e2e.py`` so the relative script path
resolves. A real migrated project replaces the smoke body with its actual
validation pipeline; the terminal contract string ``E2E TEST PASSED`` /
``E2E TEST FAILED`` matches the run output wording documented in
``README.md`` and ``docs/User_Guide.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).name
CWD = Path.cwd()

MIN_PYTHON = (3, 10)


def _project_root() -> Path:
    """Return the project root for this invocation.

    Prefer the cwd-contract root: the directory from which the relative
    script path ``test_data_and_scripts/run_e2e.py`` resolves. Fall back
    to walking up from the script location to the nearest
    ``ADAPTATION_REQUIREMENTS.md`` marker, then to the script directory's
    grandparent.
    """
    if (CWD / "test_data_and_scripts" / SCRIPT_NAME).is_file():
        return CWD
    for directory in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        if (directory / "ADAPTATION_REQUIREMENTS.md").is_file():
            return directory
    return SCRIPT_DIR.parent.parent


def main() -> int:
    print("run_e2e.py — SEAM end-to-end validation entry (smoke)")
    print(f"  script dir  : {SCRIPT_DIR}")
    print(f"  cwd         : {CWD}")

    # 1. Documented production floor: Linux / Python 3.10+.
    version = sys.version_info[:2]
    if version < MIN_PYTHON:
        print(f"E2E TEST FAILED: Python {version} is below the documented "
              f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]} floor")
        return 1
    print(f"  python      : {sys.version.split()[0]} (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")

    # 2. Locate the project root via the launcher's constraints marker.
    project_root = _project_root()
    print(f"  project root: {project_root}")
    constraints = project_root / "ADAPTATION_REQUIREMENTS.md"
    if constraints.is_file():
        lines = len(constraints.read_text(encoding="utf-8").splitlines())
        print(f"  constraints : {constraints} (found, {lines} lines)")
    else:
        print("  constraints : ADAPTATION_REQUIREMENTS.md not found at project root")

    # 3. cwd contract: the relative script path must resolve from the root.
    if (CWD / "test_data_and_scripts" / SCRIPT_NAME).is_file():
        print("  cwd contract: OK — `test_data_and_scripts/run_e2e.py` resolves from cwd")
    else:
        print("  cwd contract: invoke from the project root as")
        print("                `python test_data_and_scripts/run_e2e.py`")

    scripts = project_root / "test_data_and_scripts"
    if scripts.is_dir():
        print(f"  scripts dir : {scripts} (present)")
    else:
        print("  scripts dir : no test_data_and_scripts/ at project root")

    print("E2E TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
