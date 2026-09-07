"""Central compatibility shim for version-gated typing symbols.

All modules MUST import the following symbols from ``core.compat`` instead of
directly from ``typing`` or ``typing_extensions``.  This keeps the codebase
uniform across Python 3.10+ (main runtime) and 3.8+ (contract submodules)
without per-file version checks.

Centralised symbols and the CPython version that first added them to
:mod:`typing`:

    ==========  ==========
    Symbol      typing in
    ==========  ==========
    Annotated   3.9
    TypeAlias   3.10
    ParamSpec   3.10
    TypeGuard   3.10
    Concatenate 3.10
    assert_never 3.11
    Self        3.11
    Never       3.11
    LiteralString 3.11
    assert_type 3.11
    override    3.12
    ==========  ==========

``typing_extensions`` (a hard dependency) backports every one of them to all
supported versions, so the fallback is always available.
"""

from __future__ import annotations

import sys
from typing import TypedDict

__all__ = [
    "Annotated",
    "TypeAlias",
    "ParamSpec",
    "TypeGuard",
    "Concatenate",
    "assert_never",
    "Self",
    "Never",
    "LiteralString",
    "assert_type",
    "override",
    "SLOTS_KWARG",
]


class _SlotsKwarg(TypedDict, total=False):
    slots: bool


# Conditional ``slots=True`` for ``@dataclass``: active on 3.10+ (identical to
# current behaviour), omitted on 3.8/3.9 where the parameter does not exist.
SLOTS_KWARG: _SlotsKwarg = (
    {"slots": True} if sys.version_info >= (3, 10) else {}
)

# --- Python 3.9+ -----------------------------------------------------------
if sys.version_info >= (3, 9):
    from typing import Annotated
else:  # pragma: no cover -- exercised on 3.8 only
    from typing_extensions import Annotated

# --- Python 3.10+ ----------------------------------------------------------
if sys.version_info >= (3, 10):
    from typing import Concatenate, ParamSpec, TypeAlias, TypeGuard
else:  # pragma: no cover -- exercised on 3.8/3.9 only
    from typing_extensions import (
        Concatenate,
        ParamSpec,
        TypeAlias,
        TypeGuard,
    )

# --- Python 3.11+ ----------------------------------------------------------
if sys.version_info >= (3, 11):
    from typing import LiteralString, Never, Self, assert_never, assert_type
else:
    from typing_extensions import (
        LiteralString,
        Never,
        Self,
        assert_never,
        assert_type,
    )

# --- Python 3.12+ ----------------------------------------------------------
if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override
