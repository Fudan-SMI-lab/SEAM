"""Isolated subprocess coverage for the typed SQLite provider boundary.

Provider states are exercised via fresh subprocesses so ``sys.modules``
manipulation cannot leak into the parent test run:

    1. stdlib ``sqlite3`` available — proves precedence even when an
       observable fallback is present
    2. stdlib blocked, ``pysqlite3`` present — calls ``connect`` and
       proves the returned connection comes from the fallback
    3. neither backend — typed unavailable state, ``connect`` raises
    4. manager HTTP fallback — a constructed manager receives HTTP
       responses and returns normally when no SQLite backend exists
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _src_root() -> Path:
    import core  # noqa: PLC0415

    return Path(core.__file__).resolve().parent.parent


def _run_probe(setup: str, probe: str) -> subprocess.CompletedProcess[str]:
    src_root = str(_src_root())
    env = {
        "PYTHONPATH": src_root + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    full_code = textwrap.dedent(setup) + "\n" + textwrap.dedent(probe)
    return subprocess.run(
        [sys.executable, "-S", "-c", full_code],
        capture_output=True,
        text=True,
        env=env,
    )


_BLOCK_STDLITE = """
import sys
sys.modules['sqlite3'] = None
sys.modules['_sqlite3'] = None
sys.modules['sqlite3.dbapi2'] = None
"""

_BLOCK_PYSQLITE3 = """
import sys
sys.modules['pysqlite3'] = None
sys.modules['pysqlite3.dbapi2'] = None
"""

_INJECT_OBSERVABLE_PYSQLITE3 = """
import sys
from types import ModuleType

class _FallbackError(Exception):
    pass

class _FallbackConnection:
    _from_fallback = True
    row_factory = None
    def execute(self, sql, parameters=()):
        return self
    def fetchall(self):
        return []
    def fetchone(self):
        return None
    def close(self):
        pass
    def commit(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None

_fake = ModuleType('pysqlite3.dbapi2')
_fake.Error = _FallbackError
_fake.Row = type('Row', (), {})
_fake.Connection = _FallbackConnection
_fake.connect = lambda *a, **kw: _FallbackConnection()
_pkg = ModuleType('pysqlite3')
_pkg.dbapi2 = _fake
sys.modules['pysqlite3'] = _pkg
sys.modules['pysqlite3.dbapi2'] = _fake
"""


def test_stdlib_wins_over_observable_fallback() -> None:
    """When both stdlib and a fallback are present, stdlib wins.

    Given a subprocess with stdlib sqlite3 available AND a fake
    pysqlite3 injected with a distinct ``_FallbackError``.
    When the provider module is imported.
    Then ``Error`` is ``sqlite3.Error`` (from stdlib), NOT the
    fallback's ``_FallbackError`` — proving stdlib precedence.
    """
    probe = """
    from core.sqlite_provider import Error, connect
    import sqlite3 as _real
    print('error_is_stdlib:', Error is _real.Error)
    conn = connect(':memory:')
    print('connect_works:', type(conn).__name__)
    conn.close()
    """
    result = _run_probe(_INJECT_OBSERVABLE_PYSQLITE3, probe)
    assert result.returncode == 0, result.stderr
    assert "error_is_stdlib: True" in result.stdout


def test_fallback_connect_returns_fallback_connection() -> None:
    """When stdlib is blocked, the fallback's connect is actually called.

    Given a subprocess with stdlib sqlite3 blocked and a fake pysqlite3
    whose ``connect`` returns a sentinel ``_FallbackConnection``.
    When the provider module is imported and ``connect`` is called.
    Then the returned connection carries the ``_from_fallback`` marker,
    proving it came from the fallback, not a stale stdlib handle.
    """
    setup = _BLOCK_STDLITE + _INJECT_OBSERVABLE_PYSQLITE3
    probe = """
    from core.sqlite_provider import available, connect
    print('available:', available)
    conn = connect(':memory:')
    print('from_fallback:', getattr(conn, '_from_fallback', False))
    conn.close()
    """
    result = _run_probe(setup, probe)
    assert result.returncode == 0, result.stderr
    assert "available: True" in result.stdout
    assert "from_fallback: True" in result.stdout


def test_neither_available_yields_typed_unavailable_state() -> None:
    """When neither backend is importable, provider returns typed unavailable.

    Given a subprocess with both sqlite3 and pysqlite3 blocked.
    When the provider module is imported.
    Then ``available`` is False, Error is ProviderUnavailableError, and
    connect raises ProviderUnavailableError (not ImportError or crash).
    """
    setup = _BLOCK_STDLITE + _BLOCK_PYSQLITE3
    probe = """
    from core.sqlite_provider import (
        available,
        Error,
        connect,
        ProviderUnavailableError,
    )
    print('available:', available)
    print('error_is_typed:', Error is ProviderUnavailableError)
    try:
        connect('probe.db')
        print('connect_raises: False')
    except ProviderUnavailableError:
        print('connect_raises_unavailable: True')
    except Exception as exc:
        print('connect_raises_other:', type(exc).__name__)
    """
    result = _run_probe(setup, probe)
    assert result.returncode == 0, result.stderr
    assert "available: False" in result.stdout
    assert "error_is_typed: True" in result.stdout
    assert "connect_raises_unavailable: True" in result.stdout


def test_manager_http_fallback_when_no_sqlite_backend() -> None:
    """Session manager returns normal HTTP responses with no SQLite backend.

    Given a subprocess with both sqlite3 and pysqlite3 blocked.
    When a real ``MigrationSessionManager`` subclass with HTTP routes
    receives a ``send_command`` call.
    Then the manager skips unavailable SQLite evidence, consults HTTP,
    and returns the phase-complete text without import or backend errors.
    """
    setup = _BLOCK_STDLITE + _BLOCK_PYSQLITE3
    probe = """
    from harness.session.manager import MigrationSessionManager

    class _HttpManager(MigrationSessionManager):
        def __init__(self):
            super().__init__(
                base_url='http://test', auto_detect_agent=False
            )
            self.http_calls = []

        def _http(self, method, path, query=None, body=None, timeout=None):
            self.http_calls.append((method, path))
            if (method, path) == ('POST', '/session/ses-1/message'):
                return {
                    'ok': True,
                    'data': {
                        'info': {'finish': 'stop'},
                        'parts': [{'type': 'text', 'text': 'phase complete'}],
                    },
                }
            if (method, path) == ('GET', '/session/status'):
                return {'ok': True, 'data': {'ses-1': {'type': 'idle'}}}
            if (method, path) == ('GET', '/session/ses-1/message'):
                return {'ok': True, 'data': [{'todos': [{'status': 'completed'}]}]}
            return {'ok': False}

    mgr = _HttpManager()
    result = mgr.send_command('ses-1', 'do work', retries=0)
    print('send_command_result:', result)
    print('http_call_count:', len(mgr.http_calls))
    """
    result = _run_probe(setup, probe)
    assert result.returncode == 0, result.stderr
    assert "send_command_result: phase complete" in result.stdout
    assert "http_call_count:" in result.stdout
