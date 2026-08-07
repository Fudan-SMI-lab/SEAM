"""Typed resolution of a SQLite DB-API 2.0 provider.

The session manager (:mod:`harness.session.manager`) consults this module
instead of importing ``sqlite3`` or ``pysqlite3`` directly.  When neither
backend is importable, the provider exposes a typed *unavailable* state:
the session manager records SQLite completion evidence as unavailable and
continues normal HTTP flow without crashing.

Resolution order: stdlib ``sqlite3`` first, optional ``pysqlite3.dbapi2``
second, typed unavailable third.

The connection/row/cursor type aliases resolve to the real ``sqlite3``
types for annotation.  At runtime they are lightweight ``Protocol``
placeholders that are never used for ``isinstance``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from sqlite3 import Connection as Connection
    from sqlite3 import Cursor as Cursor
    from sqlite3 import Row as Row
else:

    class Row(Protocol):
        def keys(self) -> list[str]: ...

        def __getitem__(self, key: str | int) -> object: ...

    class Cursor(Protocol):
        def execute(
            self, sql: str, parameters: tuple[object, ...] = ()
        ) -> Cursor: ...

        def fetchall(self) -> list[Row]: ...

        def fetchone(self) -> Row | None: ...

    class Connection(Protocol):
        def execute(
            self, sql: str, parameters: tuple[object, ...] = ()
        ) -> Cursor: ...

        def close(self) -> None: ...

        def commit(self) -> None: ...

        def __enter__(self) -> Connection: ...

        def __exit__(self, *args: object) -> bool | None: ...


SQLITE_UNAVAILABLE_MESSAGE: Final = (
    "sqlite3 unavailable: no DB-API 2.0 backend "
    "(stdlib sqlite3 or pysqlite3) is importable"
)


class ProviderUnavailableError(Exception):
    """Raised by :func:`connect` when no SQLite backend was resolved."""


class _UnavailableRow:
    """Row class placeholder used when no backend is importable."""

    def keys(self) -> list[str]:
        raise ProviderUnavailableError(SQLITE_UNAVAILABLE_MESSAGE)

    def __getitem__(self, key: str | int) -> object:
        raise ProviderUnavailableError(SQLITE_UNAVAILABLE_MESSAGE)


available: bool = False
Error: type[Exception] = ProviderUnavailableError
RowFactory: type = _UnavailableRow
_backend_connect: Callable[..., Connection] | None = None

try:
    import sqlite3
except ImportError:
    pass
else:
    available = True
    Error = sqlite3.Error
    RowFactory = sqlite3.Row
    _backend_connect = sqlite3.connect

if not available:
    try:
        from pysqlite3.dbapi2 import Error as _fallback_error
        from pysqlite3.dbapi2 import Row as _fallback_row
        from pysqlite3.dbapi2 import connect as _fallback_connect
    except ImportError:
        pass
    else:
        available = True
        Error = _fallback_error
        RowFactory = _fallback_row
        _backend_connect = _fallback_connect


def connect(database: str, *, uri: bool = False, timeout: float = 5.0) -> Connection:
    """Open a SQLite connection through the resolved backend.

    Raises :class:`ProviderUnavailableError` when no backend was resolved.
    """
    if _backend_connect is None:
        raise ProviderUnavailableError(SQLITE_UNAVAILABLE_MESSAGE)
    return _backend_connect(database, uri=uri, timeout=timeout)
