from __future__ import annotations

from typing import Literal

from core.compat import TypeAlias

TerminalResourceStatus: TypeAlias = Literal[
    "passed", "passed_with_reviews", "failed", "cancelled", "error"
]
