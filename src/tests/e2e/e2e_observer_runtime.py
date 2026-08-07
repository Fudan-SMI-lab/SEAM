from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, NamedTuple, Protocol, TypeVar

from core.agent_io_logger import AgentIOLogger
from core.deferred_transport_observer import DeferredTransportObserver
from core.ui_events import UIEventSink
from harness.session.trace_seeds import SessionLifecycle

SessionBackendT = TypeVar("SessionBackendT")
ObserverT = TypeVar("ObserverT")


class SessionManagerBackend(Protocol):
    def get_or_create(
        self,
        role: str,
        agent: str = "",
        lifecycle: SessionLifecycle = "persistent",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str: ...

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str: ...

    def cleanup_all(self) -> int: ...


def observer_backend_attribute(
    backend: SessionManagerBackend,
    name: str,
) -> Any:
    return getattr(backend, name)


class TelemetryObserverConfig(NamedTuple):
    output_dir: Path
    run_id: str
    agent_io_logger: AgentIOLogger | None
    ui_event_sink: UIEventSink | None = None


@dataclass(frozen=True, slots=True)
class ObservedSessionRuntime(Generic[SessionBackendT, ObserverT]):
    session_manager: SessionBackendT
    observer: ObserverT
    transport_observer: DeferredTransportObserver
