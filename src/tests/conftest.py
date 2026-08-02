"""Test configuration for migration_utils.

SQLite availability is resolved through the typed provider boundary in
``core.sqlite_provider``. No ``sys.modules`` stubs are injected: when the
stdlib ``sqlite3`` C extension is missing, the provider returns a typed
unavailable state and the session manager skips SQLite evidence
gracefully. Test files that require real SQLite create databases through
the provider's ``connect`` function, which is a no-op (raises) when
unavailable, and tests are skipped via ``_sqlite_provider.available``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from core.sqlite_provider import available as SQLITE_AVAILABLE

NO_REAL_SQLITE3: bool = not SQLITE_AVAILABLE


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
