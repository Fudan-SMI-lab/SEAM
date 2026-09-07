"""Regression guard: version-gated typing symbols must come from ``core.compat``.

This test scans every ``.py`` file under the runtime packages and asserts that
nobody imports the centralised symbols directly from :mod:`typing` or
:mod:`typing_extensions`.  The only legitimate import site is
``core/compat.py`` itself.

Centralised symbols (added to ``typing`` in 3.9–3.12)::

    Annotated, TypeAlias, ParamSpec, TypeGuard, Concatenate,
    assert_never, Self, Never, LiteralString, assert_type, override

If this test fails, change the offending import to::

    from core.compat import <symbol>
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Symbols that are version-gated in the ``typing`` module and therefore
# centralised in ``core.compat``.
_GATED_SYMBOLS = frozenset(
    {
        "Annotated",
        "TypeAlias",
        "ParamSpec",
        "TypeGuard",
        "Concatenate",
        "TypeVarTuple",
        "assert_never",
        "Self",
        "Never",
        "LiteralString",
        "assert_type",
        "reveal_type",
        "override",
    }
)

# Packages whose source is covered by this guard.
_PACKAGES = ("core", "harness", "migrator", "validators", "scripts")

# ``core/compat.py`` is the sole file allowed to import from
# ``typing_extensions`` / ``typing`` for these symbols.  The path is relative
# to ``core/`` (the source root resolved by ``_source_root``).
_COMPAT_MODULE = Path("compat.py")

# Match ``from typing import ...`` and ``from typing_extensions import ...``
# lines, capturing everything after ``import``.
_IMPORT_RE = re.compile(
    r"^\s*from\s+(typing|typing_extensions)\s+import\s+(.+)$"
)


def _source_root() -> Path:
    # Tests run with ``src`` on sys.path, so ``core`` resolves to the package
    # directory.
    import core  # noqa: PLC0415

    return Path(core.__file__).resolve().parent


def _collect_violations() -> list[tuple[Path, int, str, str]]:
    root = _source_root()
    violations: list[tuple[Path, int, str, str]] = []

    for py_file in sorted(root.rglob("*.py")):
        rel = py_file.relative_to(root)
        # The shim itself is exempt — it is the single dispatch point.
        if rel == _COMPAT_MODULE:
            continue

        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _IMPORT_RE.match(line)
            if match is None:
                continue
            module = match.group(1)
            imported = re.split(r"[,;]", match.group(2))
            for name in imported:
                clean = name.strip().split(" as ")[0].strip()
                if clean in _GATED_SYMBOLS:
                    violations.append((rel, lineno, module, clean))

    return violations


def test_no_version_gated_symbol_imported_from_typing() -> None:
    """No runtime file may import gated symbols from ``typing`` directly."""
    violations = [
        v for v in _collect_violations() if v[2] == "typing"
    ]
    if violations:
        details = "\n".join(
            f"  {rel}:{lineno} — from typing import {name}"
            for rel, lineno, _module, name in violations
        )
        pytest.fail(
            "Version-gated typing symbols must be imported from "
            "'core.compat', not 'typing':\n" + details
        )


def test_no_version_gated_symbol_imported_from_typing_extensions() -> None:
    """Only ``core.compat`` may import gated symbols from ``typing_extensions``."""
    violations = _collect_violations()
    if violations:
        details = "\n".join(
            f"  {rel}:{lineno} — from {module} import {name}"
            for rel, lineno, module, name in violations
        )
        pytest.fail(
            "Version-gated typing symbols must be imported from "
            "'core.compat', not 'typing_extensions':\n" + details
        )


_MATCH_RE = re.compile(r"^\s*match\s+\w")
_SLOTS_RE = re.compile(r"slots\s*=\s*True")


def test_no_match_case_statements() -> None:
    """``match``/``case`` (3.10+ syntax) must not appear in runtime source."""
    root = _source_root()
    violations: list[tuple[Path, int]] = []
    for py_file in sorted(root.rglob("*.py")):
        rel = py_file.relative_to(root)
        if rel == _COMPAT_MODULE:
            continue
        for lineno, line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _MATCH_RE.match(line):
                violations.append((rel, lineno))
    if violations:
        details = "\n".join(f"  {rel}:{ln}" for rel, ln in violations)
        pytest.fail(
            "match/case statements (Python 3.10+ syntax) found — "
            "convert to if/elif for 3.8 compatibility:\n" + details
        )


def test_no_hardcoded_slots_true_in_dataclass() -> None:
    """``slots=True`` must come from ``SLOTS_KWARG``, not be hardcoded."""
    root = _source_root()
    violations: list[tuple[Path, int]] = []
    for py_file in sorted(root.rglob("*.py")):
        rel = py_file.relative_to(root)
        if rel == _COMPAT_MODULE:
            continue
        for lineno, line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "dataclass" in line and _SLOTS_RE.search(line):
                violations.append((rel, lineno))
    if violations:
        details = "\n".join(f"  {rel}:{ln}" for rel, ln in violations)
        pytest.fail(
            "Hardcoded 'slots=True' in @dataclass — use **SLOTS_KWARG "
            "from core.compat for 3.8 compatibility:\n" + details
        )
