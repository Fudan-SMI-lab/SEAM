from __future__ import annotations

from pathlib import Path
import sys

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from harness.server import lifecycle


class FakeProcess:
    def poll(self) -> None:
        return None


def test_parse_server_url_requires_explicit_base_url() -> None:
    spec = lifecycle.parse_server_url("http://127.0.0.1:5000")

    assert spec.server_url == "http://127.0.0.1:5000"
    assert spec.hostname == "127.0.0.1"
    assert spec.port == 5000

    with pytest.raises(ValueError, match="explicit port"):
        _ = lifecycle.parse_server_url("http://127.0.0.1")
    with pytest.raises(ValueError, match="base URL"):
        _ = lifecycle.parse_server_url("http://127.0.0.1:5000/agent")


def test_probe_server_classifies_url_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def health_matches_agent(url: str) -> bool:
        return url == "http://127.0.0.1:5000/agent"

    def port_open(_hostname: str, _port: int) -> bool:
        return True

    monkeypatch.setattr(lifecycle, "health_check", health_matches_agent)
    monkeypatch.setattr(lifecycle, "is_port_open", port_open)

    matching = lifecycle.probe_server("http://127.0.0.1:5000", "opencode")
    assert matching.state == "matching"

    def health_unavailable(_url: str) -> bool:
        return False

    monkeypatch.setattr(lifecycle, "health_check", health_unavailable)
    conflict = lifecycle.probe_server("http://127.0.0.1:5000", "opencode")
    assert conflict.state == "conflict"

    def port_closed(_hostname: str, _port: int) -> bool:
        return False

    monkeypatch.setattr(lifecycle, "is_port_open", port_closed)
    free = lifecycle.probe_server("http://127.0.0.1:5000", "opencode")
    assert free.state == "free"


def test_ensure_server_starts_from_server_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def fake_probe(server_url: str, _server_type: str) -> lifecycle.ServerProbe:
        return lifecycle.ServerProbe("free", server_url, "available")

    def fake_start(**kwargs: object) -> FakeProcess:
        calls.update(kwargs)
        return FakeProcess()

    def fake_wait(url: str, timeout: int) -> bool:
        calls.update({"wait_url": url, "timeout": timeout})
        return True

    monkeypatch.setattr(lifecycle, "probe_server", fake_probe)
    monkeypatch.setattr(lifecycle, "start_server", fake_start)
    monkeypatch.setattr(lifecycle, "wait_for_server", fake_wait)

    managed = lifecycle.ensure_server(
        work_dir=str(tmp_path),
        server_type="opencode",
        server_url="http://127.0.0.1:5000",
    )

    assert managed.base_url == "http://127.0.0.1:5000"
    assert managed.port == 5000
    assert managed.started is True
    assert calls["server_url"] == "http://127.0.0.1:5000"
    assert calls["wait_url"] == "http://127.0.0.1:5000"
