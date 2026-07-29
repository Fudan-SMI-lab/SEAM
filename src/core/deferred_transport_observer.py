from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from harness.session.events import TransportAttemptEvent, TransportObserver


@dataclass(frozen=True)
class TransportObserverBindingError(RuntimeError):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


class DeferredTransportObserver:
    """Buffer transport events until the active telemetry observer is constructed."""

    def __init__(self) -> None:
        self._target: TransportObserver | None = None
        self._pending: list[TransportAttemptEvent] = []

    @property
    def is_bound(self) -> bool:
        return self._target is not None

    def __call__(self, event: TransportAttemptEvent) -> None:
        target = self._target
        if target is None:
            self._pending.append(event)
            return
        target(event)

    def bind(self, target: TransportObserver) -> None:
        if self._target is not None:
            raise TransportObserverBindingError("transport observer is already bound")
        self._target = target
        pending = tuple(self._pending)
        self._pending.clear()
        for event in pending:
            target(event)
