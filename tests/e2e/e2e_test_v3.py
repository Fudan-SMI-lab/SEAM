#!/usr/bin/env python3
"""Root-level wrapper for the src E2E harness (V3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    real_script = repo_root / "src" / "tests" / "e2e" / "e2e_test_v3.py"
    completed = subprocess.run(
        [sys.executable, str(real_script), *sys.argv[1:]],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
