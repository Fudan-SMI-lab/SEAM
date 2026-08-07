"""Make the shared V3 harness importable in script and package execution modes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

SCRIPT_DIR: Final = Path(__file__).resolve().parent
PACKAGE_ROOT: Final = Path(__file__).resolve().parents[2]

for root in (SCRIPT_DIR, PACKAGE_ROOT):
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
