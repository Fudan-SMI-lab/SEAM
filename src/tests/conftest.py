"""Test configuration for migration_utils.

Python 3.10 on this system lacks the ``sqlite3`` C extension (``_sqlite3``),
which blocks import of ``harness.session.manager`` during test collection.
Provide a minimal stub only when ``_sqlite3`` is truly missing. On properly
built Python installations this code is never executed.
"""
import sys
import types

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


class _FakeSqliteDbapi2:
    """Minimal sqlite3.dbapi2 stub."""
    apilevel = "2.0"
    paramstyle = "qmark"
    threadsafety = 1
    Error = _FakeSqliteError
    Row = type("Row", (), {})
    connect = _FakeSqliteConnection


if "_sqlite3" not in sys.modules:
    try:
        import sqlite3  # noqa: F401
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

import pytest
import uuid
from pathlib import Path


OUTPUT_PROJECTS = Path(__file__).resolve().parent.parent.parent / "output_projects"


@pytest.fixture
def base_path():
    """Return the base path for test fixtures."""
    return __file__


@pytest.fixture
def project_root(request: pytest.FixtureRequest) -> Path:
    """Per-test project directory under output_projects/test_artifacts/.

    Creates ``output_projects/test_artifacts/{test_name}_{uuid_short}/``,
    persists after the test (no automatic cleanup), so artifacts are
    inspectable under the target output_projects area.
    """
    test_name = request.node.name
    uid = uuid.uuid4().hex[:8]
    root = OUTPUT_PROJECTS / "test_artifacts" / f"{test_name}_{uid}"
    root.mkdir(parents=True, exist_ok=True)
    return root
