from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, TypeVar, final

from core.atomic_file import atomic_write_bytes
from core.agent_io_logger import AgentIOLogger, redact_sensitive_text
from core.deferred_transport_observer import DeferredTransportObserver
from core.runtime_observability import RuntimeObservability
from core.runtime_observability_models import (
    ObservabilitySummary,
    ReviewCompletion,
    TimeoutScope,
)
from core.ui_events import UIEventSink
from harness.session.events import TransportAttemptEvent, TransportObserver
from harness.session.opencode_contract import JsonValue
from harness.session.opencode_contract_json import load_json
from harness.session.trace_seeds import SessionLifecycle

from .e2e_observer_artifacts import (
    TelemetryArtifactSnapshot,
    persist_telemetry_artifacts,
)
from .e2e_observer_models import CommandMetric as CommandMetric
from .e2e_observer_models import PhaseMetric, PhaseTracker, utc_now
from .e2e_observer_models import SessionMetric as SessionMetric
from .e2e_observer_runtime import ObservedSessionRuntime as ObservedSessionRuntime
from .e2e_observer_runtime import SessionManagerBackend
from .e2e_observer_runtime import TelemetryObserverConfig as TelemetryObserverConfig
from .e2e_observer_runtime import observer_backend_attribute
from .e2e_observer_session import (
    ObservedSessionInstrumentation,
)


SessionBackendT = TypeVar("SessionBackendT", bound=SessionManagerBackend)


@final
class TelemetryObserver:
    def __init__(
        self,
        session_mgr: SessionManagerBackend,
        output_dir: str | Path,
        agent_io_logger: AgentIOLogger | None = None,
        ui_event_sink: UIEventSink | None = None,
    ) -> None:
        self._session_mgr = session_mgr
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._run_started_at = utc_now()
        self._run_started_monotonic = time.monotonic()
        self._events: list[dict[str, JsonValue]] = []
        self._metadata: dict[str, JsonValue] = {}
        self._agent_io_logger = agent_io_logger
        self._ui_event_sink = ui_event_sink
        self._runtime_observability = RuntimeObservability(self._output_dir)
        self._phase_tracker = PhaseTracker(self.record_event)
        self._session_instrumentation = ObservedSessionInstrumentation(
            session_mgr,
            self,
            agent_io_logger,
            ui_event_sink=ui_event_sink,
        )

    def __getattr__(self, name: str) -> Any:
        return observer_backend_attribute(self._session_mgr, name)

    @classmethod
    def create_observed_session(
        cls,
        manager_factory: Callable[[TransportObserver], SessionBackendT],
        config: TelemetryObserverConfig,
    ) -> ObservedSessionRuntime[SessionBackendT, TelemetryObserver]:
        transport_observer = DeferredTransportObserver()
        session_manager = manager_factory(transport_observer)
        observer = cls(
            session_manager,
            config.output_dir,
            agent_io_logger=config.agent_io_logger,
            ui_event_sink=config.ui_event_sink,
        )
        observer.set_metadata("run_id", config.run_id)
        transport_observer.bind(observer.record_transport_event)
        return ObservedSessionRuntime(
            session_manager=session_manager,
            observer=observer,
            transport_observer=transport_observer,
        )

    @property
    def active_phase(self) -> str | None:
        return self._phase_tracker.active_phase

    @property
    def run_id(self) -> str | None:
        run_id = self._metadata.get("run_id")
        return run_id if isinstance(run_id, str) else None

    @property
    def session_count(self) -> int:
        return self._session_instrumentation.session_count

    @property
    def command_count(self) -> int:
        return self._session_instrumentation.command_count

    @property
    def phase_metrics(self) -> dict[str, PhaseMetric]:
        return {metric.phase_id: metric for metric in self._phase_tracker.metrics}

    @property
    def observability_summary(self) -> ObservabilitySummary:
        return self._runtime_observability.summary()

    def set_metadata(self, key: str, value: JsonValue) -> None:
        self._metadata[key] = value

    def record_event(self, event_type: str, **details: JsonValue) -> None:
        concise_details: dict[str, JsonValue] = {}
        for key, value in details.items():
            lowered = key.lower()
            sensitive_key = any(
                fragment in lowered
                for fragment in (
                    "prompt",
                    "response",
                    "preview",
                    "body",
                    "auth",
                    "secret",
                    "token",
                    "traceback",
                )
            )
            if sensitive_key and not lowered.endswith("_length"):
                continue
            concise_details[key] = (
                redact_sensitive_text(value) if isinstance(value, str) else value
            )
        self._events.append(
            {
                "event_type": event_type,
                "timestamp": utc_now(),
                "phase_id": self.active_phase,
                "details": concise_details,
            }
        )

    def record_review_completion(self, completion: ReviewCompletion) -> bool:
        details = self._runtime_observability.add_review(completion)
        if details is None:
            return False
        self._events.append(
            {
                "event_type": "review_completed",
                "timestamp": utc_now(),
                "phase_id": completion.scope.phase_id,
                "details": load_json(json.dumps(details)),
            }
        )
        return True

    def record_transport_event(self, event: TransportAttemptEvent) -> None:
        run_id = self._metadata.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return
        session_role = self._session_instrumentation.role_for(event.session_id)
        details = self._runtime_observability.add_transport(
            TimeoutScope(
                run_id=run_id,
                agent=session_role or "unknown_agent",
                sub_phase=self.active_phase or "unknown_sub_phase",
                framework_invocation_id=(
                    self._session_instrumentation.active_framework_invocation_id
                ),
            ),
            event,
        )
        if details is not None:
            self._events.append(
                {
                    "event_type": "transport_timeout",
                    "timestamp": utc_now(),
                    "phase_id": self.active_phase,
                    "details": load_json(json.dumps(details)),
                }
            )

    def set_active_phase(self, phase_id: str | None) -> None:
        self._phase_tracker.set_active(phase_id)

    def timing_phase(self, phase_id: str) -> AbstractContextManager[None]:
        return self._phase_tracker.timing(phase_id)

    def mark_phase_status(
        self, phase_id: str, status: str, error: str | None = None
    ) -> None:
        self._phase_tracker.mark_status(phase_id, status, error)

    def link_session_to_phase(self, session_id: str) -> None:
        self._phase_tracker.link_session(session_id)

    def get_or_create(
        self,
        role: str,
        lifecycle: SessionLifecycle = "persistent",
        agent: str = "",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        return self._session_instrumentation.get_or_create(
            role,
            lifecycle,
            agent,
            title,
            working_dir,
            initial_prompt,
        )

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str:
        return self._session_instrumentation.send_command(
            session_id,
            command,
            agent,
            timeout,
            retries,
        )

    def cleanup_all(self) -> int:
        return self._session_instrumentation.cleanup_all()

    def annotate_trace_seed(
        self,
        session_id: str,
        logical_role: str | None,
        scope: str | None,
    ) -> None:
        self._session_instrumentation.annotate_trace_seed(
            session_id,
            logical_role,
            scope,
        )

    def save_metrics(self) -> dict[str, str]:
        observability = self.observability_summary
        return persist_telemetry_artifacts(
            TelemetryArtifactSnapshot(
                output_dir=self._output_dir,
                run_started_at=self._run_started_at,
                run_started_monotonic=self._run_started_monotonic,
                metadata=self._metadata,
                phases=self._phase_tracker.metrics,
                sessions=self._session_instrumentation.sessions,
                commands=self._session_instrumentation.commands,
                events=tuple(self._events),
                observability=observability,
                agent_io_paths=(
                    self._agent_io_logger.paths()
                    if self._agent_io_logger is not None
                    else None
                ),
            ),
            self._runtime_observability,
            atomic_write_bytes,
        )
