"""Make the shared V3 harness importable in script and package execution modes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final


def _package_root() -> Path:
    """Resolve the src/ root (source tree or installed wheel)."""
    try:
        from importlib.resources import as_file, files

        with as_file(files("core")) as core_dir:
            root = Path(core_dir).resolve().parent
        if (root / "prompts").is_dir():
            return root
    except Exception:
        pass
    return Path(__file__).resolve().parents[2]


SCRIPT_DIR: Final = Path(__file__).resolve().parent
PACKAGE_ROOT: Final = _package_root()

for root in (SCRIPT_DIR, PACKAGE_ROOT):
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
