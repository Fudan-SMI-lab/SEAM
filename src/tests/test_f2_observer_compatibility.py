from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_io_logger import AgentIOLogger
from harness.session.opencode_contract_json import load_json
from harness.session.trace_seeds import SessionLifecycle
from tests.e2e.e2e_observer import CommandMetric, SessionMetric, TelemetryObserver


class ObserverProbeError(RuntimeError):
    pass


class ObserverBackend:
    def __init__(self) -> None:
        self.created_agent: str | None = None

    def get_or_create(
        self,
        role: str,
        agent: str = "",
        lifecycle: SessionLifecycle = "persistent",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        del agent, lifecycle, title, working_dir, initial_prompt
        return f"{role}-session"

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str:
        del session_id, command, agent, timeout, retries
        return "full response body"

    def cleanup_all(self) -> int:
        return 1

    def create_session(
        self,
        role: str,
        agent: str = "",
        lifecycle: SessionLifecycle = "ephemeral",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        del role, lifecycle, title, working_dir, initial_prompt
        self.created_agent = agent
        return "created-session"

    def backend_identity(self) -> str:
        return "observer-backend"

    @property
    def active_agent(self) -> str:
        return "observer-agent"

    def list_sessions(self) -> list[str]:
        return ["session-1"]


def test_observer_preserves_original_module_exports_and_proxy(tmp_path: Path) -> None:
    observer = TelemetryObserver(ObserverBackend(), tmp_path)

    assert SessionMetric.__module__.endswith("e2e_observer_models")
    assert CommandMetric.__module__.endswith("e2e_observer_models")
    assert observer.backend_identity() == "observer-backend"


def test_observer_proxy_preserves_create_session_agent(tmp_path: Path) -> None:
    backend = ObserverBackend()
    observer = TelemetryObserver(backend, tmp_path)

    assert observer.create_session("retry", agent="operator_fixer") == "created-session"
    assert backend.created_agent == "operator_fixer"


def test_observer_forwards_arbitrary_backend_method(tmp_path: Path) -> None:
    observer = TelemetryObserver(ObserverBackend(), tmp_path)

    assert observer.list_sessions() == ["session-1"]


def test_observer_forwards_arbitrary_backend_property(tmp_path: Path) -> None:
    observer = TelemetryObserver(ObserverBackend(), tmp_path)

    assert observer.active_agent == "observer-agent"


def test_observer_isolates_runtime_agent_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = AgentIOLogger(tmp_path, "run-runtime-error", enabled=True)
    observer = TelemetryObserver(ObserverBackend(), tmp_path, logger)
    session_id = observer.get_or_create("main_engineer")

    def fail_record(**_details: str | int | float | None) -> dict[str, str]:
        raise ObserverProbeError("optional logger failed")

    monkeypatch.setattr(logger, "record", fail_record)

    assert observer.send_command(session_id, "prompt") == "full response body"
    payload = load_json(
        Path(observer.save_metrics()["telemetry_json"]).read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    events = payload.get("events")
    assert isinstance(events, list)
    assert any(
        isinstance(event, dict) and event.get("event_type") == "agent_io_log_error"
        for event in events
    )
