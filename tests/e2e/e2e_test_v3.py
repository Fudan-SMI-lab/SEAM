#!/usr/bin/env python3
"""Root-level wrapper for the src E2E harness (V3)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    real_script = repo_root / "src" / "tests" / "e2e" / "e2e_test_v3.py"
    os.execv(sys.executable, [sys.executable, str(real_script), *sys.argv[1:]])


if __name__ == "__main__":
    main()
