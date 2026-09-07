from __future__ import annotations

from types import TracebackType
from typing import Literal, final
from threading import RLock


@final
class Phase5Transaction:
    def __init__(self) -> None:
        self._lock = RLock()

    def __enter__(self) -> None:
        _ = self._lock.acquire()

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        self._lock.release()
        return False
