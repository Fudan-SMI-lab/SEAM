from sqlite3 import Connection as Connection
from sqlite3 import Cursor as Cursor
from sqlite3 import Row as Row


class Error(Exception):
    ...


def connect(
    database: str,
    *,
    uri: bool = ...,
    timeout: float = ...,
) -> Connection:
    ...
