"""Test configuration for migration_utils.

Python 3.10 on this system lacks the ``sqlite3`` C extension (``_sqlite3``),
which blocks import of ``harness.session.manager`` during test collection.
Provide a minimal stub only when ``_sqlite3`` is truly missing. On properly
built Python installations this code is never executed.
"""

import sys


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


class _FakeSqliteDbapi2:  # pylint: disable=too-few-public-methods; silent
    """Minimal sqlite3.dbapi2 stub."""

    apilevel = "2.0"
    paramstyle = "qmark"
    threadsafety = 1
    Error = _FakeSqliteError
    Row = type("Row", (), {})
    connect = _FakeSqliteConnection


if "_sqlite3" not in sys.modules:
    try:
        import sqlite3  # noqa: F401  # pylint: disable=unused-import; silent
    except ImportError:
        sys.modules["_sqlite3"] = _FakeSqliteDbapi2
        sys.modules["sqlite3.dbapi2"] = _FakeSqliteDbapi2
        sys.modules["sqlite3"] = _FakeSqliteDbapi2
        _NO_REAL_SQLITE3 = True
    else:
        _NO_REAL_SQLITE3 = False
else:
    _NO_REAL_SQLITE3 = False

# Expose so test files can skip when real sqlite3 is unavailable.
NO_REAL_SQLITE3 = _NO_REAL_SQLITE3

import pytest  # pylint: disable=wrong-import-position; silent


@pytest.fixture
def base_path():
    """Return the base path for test fixtures."""
    return __file__
