"""Failing-first red proofs: runtime dependency / SQLite / typing gaps.

These characterization tests lock the *desired* packaging and typing
contracts for Wave-3 Todo 5 of the v1.2.1 remote-update remediation
workplan:

    * ``typing_extensions`` must be a base runtime dependency (the
      compatibility shim falls back to it on every Python below the
      symbol's introduction), not a dev-only extra.
    * ``pysqlite3-binary`` must be declared as an optional fallback extra
      so the ``harness.session.manager`` import line does not crash when
      the stdlib ``sqlite3`` is unavailable.
    * ``core.compat.SLOTS_KWARG`` must be typed with a ``total=False``
      ``TypedDict`` so ``**SLOTS_KWARG`` is acceptable to ``@dataclass``
      under BasedPyright's strict mode.

At the c6cbed3 baseline every contract above is violated, so each test
below fails for its intended reason rather than for import/setup error.
The assertions read repository files as plain text; the red proof is the
absence of the desired declaration (or presence of the wrong one).
"""

from __future__ import annotations

import re
from pathlib import Path


def _src_root() -> Path:
    import core  # noqa: PLC0415

    return Path(core.__file__).resolve().parent.parent


def _pyproject_path() -> Path:
    return _src_root() / "pyproject.toml"


def _pyproject_text() -> str:
    return _pyproject_path().read_text(encoding="utf-8")


_PROJECT_BLOCK_RE = re.compile(
    r"^\[project\]\s*$.*?(?=^\[)",
    re.MULTILINE | re.DOTALL,
)
_BASE_DEP_LINE_RE = re.compile(
    r"^\s*dependencies\s*=\s*\[(?P<body>[^\]]*)\]",
    re.MULTILINE | re.DOTALL,
)


def _base_dependency_text() -> str:
    text = _pyproject_text()
    project_match = _PROJECT_BLOCK_RE.search(text)
    assert project_match is not None, "pyproject [project] section is missing"
    project_block = project_match.group(0)

    deps_match = _BASE_DEP_LINE_RE.search(project_block)
    if deps_match is None:
        return ""

    return deps_match.group("body")


def test_typing_extensions_is_a_base_runtime_dependency() -> None:
    """``typing_extensions`` must be in ``[project] dependencies``.

    Given the packaging manifest.
    When the base runtime dependency list is read.
    Then ``typing_extensions`` must appear in ``[project] dependencies``
    (not only under ``[project.optional-dependencies.dev]``), because
    ``core.compat`` imports from it on every Python below the version
    that introduced each centralised symbol.
    """
    base_dependencies = _base_dependency_text()

    assert "typing_extensions" in base_dependencies, (
        "typing_extensions must be a base runtime dependency, not a "
        "dev-only extra; core.compat falls back to it on supported floors."
    )


def test_pysqlite3_optional_fallback_is_declared() -> None:
    """``pysqlite3-binary`` must be available as an optional fallback extra.

    Given the packaging manifest.
    When the manifest text is read.
    Then a SQLite-fallback extra (``sqlite`` or similar) must declare
    ``pysqlite3-binary``, because ``harness/session/manager.py`` imports
    it directly when the stdlib ``sqlite3`` extension is unavailable.
    """
    text = _pyproject_text()

    assert "pysqlite3" in text, (
        "pyproject.toml must declare pysqlite3(-binary) as an optional "
        "fallback extra; harness.session.manager imports it directly when "
        "stdlib sqlite3 is unavailable."
    )


def test_slots_kwarg_is_typed_as_total_false_typeddict() -> None:
    """``SLOTS_KWARG`` must be a ``TypedDict(total=False)`` over ``slots: bool``.

    Given ``core/compat.py``.
    When the ``SLOTS_KWARG`` declaration is read.
    Then the annotation must be a ``TypedDict`` with ``total=False`` and a
    single ``slots: bool`` field. The current ``dict[str, object]``
    annotation is rejected by BasedPyright's ``reportArgumentType`` at
    every ``**SLOTS_KWARG`` consumer (``@dataclass(...)``).
    """
    compat_path = _src_root() / "core" / "compat.py"
    text = compat_path.read_text(encoding="utf-8")

    assert "TypedDict" in text, (
        "compat.SLOTS_KWARG must be a TypedDict so **SLOTS_KWARG is "
        "acceptable to @dataclass under BasedPyright strict mode."
    )
    forbidden = re.compile(r"SLOTS_KWARG\s*:\s*dict\[")
    assert forbidden.search(text) is None, (
        "compat.SLOTS_KWARG must not be annotated as dict[str, object]; "
        "BasedPyright rejects **dict[str, object] for @dataclass."
    )


def test_session_manager_uses_typed_sqlite_provider_boundary() -> None:
    """``harness.session.manager`` must not import ``pysqlite3`` directly.

    Given ``harness/session/manager.py``.
    When the SQLite import block is read.
    Then the module must obtain SQLite via a typed provider boundary
    (``core.compat`` or similar) that returns a typed unavailable state
    when neither stdlib ``sqlite3`` nor ``pysqlite3`` is available,
    rather than a bare ``from pysqlite3 import dbapi2 as sqlite3`` that
    raises ``ImportError``.
    """
    manager_path = _src_root() / "harness" / "session" / "manager.py"
    text = manager_path.read_text(encoding="utf-8")

    bare_import = re.compile(r"from\s+pysqlite3\s+import\s+dbapi2\s+as\s+sqlite3")
    assert bare_import.search(text) is None, (
        "harness.session.manager must route SQLite access through a typed "
        "provider boundary; the current bare pysqlite3 import crashes "
        "module load when neither stdlib sqlite3 nor pysqlite3 is present."
    )
