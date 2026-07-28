from __future__ import annotations

from typing import Literal

from typing_extensions import TypeAlias

TerminalResourceStatus: TypeAlias = Literal[
    "passed", "passed_with_reviews", "failed", "cancelled", "error"
]
