from __future__ import annotations

import json
from pathlib import Path

from core.agent_io_logger import AgentIOLogger
from tests.e2e.e2e_observer import TelemetryObserver


class FakeSessionManager:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, int | float | None, int, int | float | None]] = []

    def get_or_create(
        self,
        role: str,
        lifecycle: str = "persistent",
        agent: str = "",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        return f"{role}-session"

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int | float | None = 600,
        retries: int = 2,
        *,
        recovery_wait_timeout: int | float | None = None,
    ) -> str:
        self.sent.append((session_id, command, agent, timeout, retries, recovery_wait_timeout))
        return "full response body"

    def cleanup_all(self) -> int:
        return 1


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_from_env_disabled_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SM_ADAPT_FULL_AGENT_IO", raising=False)

    assert AgentIOLogger.from_env(tmp_path, "run-1") is None


def test_agent_io_logger_init_defers_directory_creation(tmp_path: Path) -> None:
    logger = AgentIOLogger(tmp_path, "run-1", enabled=True, redact=False)

    assert logger.enabled is True
    assert not (tmp_path / "agent_io").exists()


def test_agent_io_logger_writes_index_and_payloads(tmp_path: Path) -> None:
    logger = AgentIOLogger(tmp_path, "run-1", enabled=True, redact=False)

    paths = logger.record(
        sequence=1,
        phase_id="phase_1",
        session_id="session-1",
        role="main_engineer",
        agent="",
        lifecycle="persistent",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        duration_seconds=1.0,
        timeout_seconds=600,
        status="passed",
        command="full prompt",
        response="full response",
        error=None,
    )

    jsonl_path = Path(paths["agent_io_jsonl"])
    records = _read_jsonl(jsonl_path)
    assert records[0]["run_id"] == "run-1"
    assert records[0]["command_path"] == "agent_io/payloads/000001_prompt.txt"
    assert (tmp_path / str(records[0]["command_path"])).read_text(encoding="utf-8") == "full prompt"
    assert (tmp_path / str(records[0]["response_path"])).read_text(encoding="utf-8") == "full response"


def test_agent_io_logger_redacts_and_truncates(tmp_path: Path) -> None:
    logger = AgentIOLogger(tmp_path, "run-1", enabled=True, max_bytes=24, redact=True)

    logger.record(
        sequence=7,
        phase_id=None,
        session_id="session-1",
        role=None,
        agent=None,
        lifecycle=None,
        started_at="start",
        ended_at="end",
        duration_seconds=0.1,
        timeout_seconds=5,
        status="failed",
        command="OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz prompt tail",
        response="Bearer abcdefghijklmnopqrstuvwxyz response tail",
        error="RuntimeError: boom",
    )

    records = _read_jsonl(tmp_path / "agent_io" / "agent_io.jsonl")
    command_text = (tmp_path / str(records[0]["command_path"])).read_text(encoding="utf-8")
    response_text = (tmp_path / str(records[0]["response_path"])).read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in command_text
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in response_text
    assert records[0]["command_truncated"] is True
    assert records[0]["response_truncated"] is True


def test_agent_io_logger_redacts_quoted_json_secrets(tmp_path: Path) -> None:
    logger = AgentIOLogger(tmp_path, "run-1", enabled=True, redact=True)

    logger.record(
        sequence=8,
        phase_id=None,
        session_id="session-1",
        role=None,
        agent=None,
        lifecycle=None,
        started_at="start",
        ended_at="end",
        duration_seconds=0.1,
        timeout_seconds=5,
        status="passed",
        command='{"password": "secret value", "HF_TOKEN": "hf_secret"}',
        response="TOKEN='quoted-token'",
        error=None,
    )

    records = _read_jsonl(tmp_path / "agent_io" / "agent_io.jsonl")
    command_text = (tmp_path / str(records[0]["command_path"])).read_text(encoding="utf-8")
    response_text = (tmp_path / str(records[0]["response_path"])).read_text(encoding="utf-8")
    assert "secret value" not in command_text
    assert "hf_secret" not in command_text
    assert "quoted-token" not in response_text
    assert '"password": "<REDACTED>"' in command_text


def test_telemetry_observer_keeps_positional_lifecycle_compatibility(tmp_path: Path) -> None:
    fake = FakeSessionManager()
    observer = TelemetryObserver(fake, tmp_path)

    session_id = observer.get_or_create("error_analyzer", "persistent")

    assert session_id == "error_analyzer-session"
    assert observer.send_command(session_id, "prompt") == "full response body"
    assert fake.sent[0][2] == ""


def test_telemetry_observer_forwards_recovery_wait_timeout(tmp_path: Path) -> None:
    fake = FakeSessionManager()
    observer = TelemetryObserver(fake, tmp_path)
    session_id = observer.get_or_create("operator_fixer", "persistent")

    response = observer.send_command(
        session_id,
        "repair",
        timeout=None,
        retries=0,
        recovery_wait_timeout=30,
    )

    assert response == "full response body"
    assert fake.sent[0] == (session_id, "repair", "", None, 0, 30)


def test_telemetry_observer_records_full_agent_io(tmp_path: Path) -> None:
    logger = AgentIOLogger(tmp_path, "run-2", enabled=True, redact=False)
    observer = TelemetryObserver(FakeSessionManager(), tmp_path, agent_io_logger=logger)
    session_id = observer.get_or_create(role="main_engineer", lifecycle="persistent")

    with observer.timing_phase("phase_0"):
        response = observer.send_command(session_id, "complete prompt", agent="main", timeout=123, retries=4)

    telemetry_paths = observer.save_metrics()

    assert response == "full response body"
    telemetry = json.loads(Path(telemetry_paths["telemetry_json"]).read_text(encoding="utf-8"))
    assert telemetry["metadata"]["agent_io_paths"]["jsonl"] == str(tmp_path / "agent_io" / "agent_io.jsonl")
    records = _read_jsonl(tmp_path / "agent_io" / "agent_io.jsonl")
    assert records[0]["phase_id"] == "phase_0"
    assert records[0]["role"] == "main_engineer"
    assert records[0]["agent"] == "main"
    assert (tmp_path / str(records[0]["command_path"])).read_text(encoding="utf-8") == "complete prompt"
    assert (tmp_path / str(records[0]["response_path"])).read_text(encoding="utf-8") == "full response body"
