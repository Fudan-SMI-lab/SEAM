"""Test configuration for migration_utils.

Python 3.10 on this system lacks the ``sqlite3`` C extension (``_sqlite3``),
which blocks import of ``harness.session.manager`` during test collection.
Provide a minimal stub only when ``_sqlite3`` is truly missing. On properly
built Python installations this code is never executed.
"""

import sys
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest


class _FakeSqliteError(Exception):
    pass


class _FakeSqliteConnection:
    """Minimal context-manager stub for Python without _sqlite3."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        raise _FakeSqliteError("sqlite connect unavailable")

    def cursor(self):
        raise _FakeSqliteError("sqlite connect unavailable")


def _fake_sqlite_module(name: str) -> ModuleType:
    module = ModuleType(name)
    setattr(module, "apilevel", "2.0")
    setattr(module, "paramstyle", "qmark")
    setattr(module, "threadsafety", 1)
    setattr(module, "Error", _FakeSqliteError)
    setattr(module, "Row", type("Row", (), {}))
    setattr(module, "connect", _FakeSqliteConnection)
    return module


_no_real_sqlite3 = False
if "_sqlite3" not in sys.modules:
    try:
        import sqlite3  # noqa: F401
    except ImportError:
        sqlite_stub = _fake_sqlite_module("sqlite3")
        sys.modules["_sqlite3"] = sqlite_stub
        sys.modules["sqlite3.dbapi2"] = sqlite_stub
        sys.modules["sqlite3"] = sqlite_stub
        _no_real_sqlite3 = True

# Expose so test files can skip when real sqlite3 is unavailable.
NO_REAL_SQLITE3 = _no_real_sqlite3


@pytest.fixture
def base_path():
    """Return the base path for test fixtures."""
    return __file__


@pytest.fixture(autouse=True)
def isolate_phase7_fallback_reports(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    isolated = request.node.nodeid.endswith(
        "test_workflow_executor.py::TestPhase7SkipAndReroute::test_phase7_skipped_in_execute_loop"
    )
    if isolated:
        monkeypatch.chdir(request.getfixturevalue("tmp_path"))
    yield
    if isolated:
        repository_root = Path(__file__).resolve().parents[2]
        assert not (repository_root / "MagicMock").exists()
