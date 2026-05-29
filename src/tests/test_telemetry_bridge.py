from __future__ import annotations

import json
from pathlib import Path

from core.telemetry_bridge import TelemetryBridge


def test_bridge_default_writes_telemetry_json(tmp_path: Path) -> None:
    """Default save_metrics() still writes telemetry.json with key telemetry_json."""
    bridge = TelemetryBridge(str(tmp_path))
    bridge.on_phase_start("phase_1")
    bridge.on_phase_end("phase_1", "success", 1.5)
    bridge.on_command("s1", "phase_1", "cmd", "resp", 0.5, "success")

    paths = bridge.save_metrics()

    assert paths == {"telemetry_json": str(tmp_path / "telemetry.json")}
    assert (tmp_path / "telemetry.json").exists()

    payload = json.loads((tmp_path / "telemetry.json").read_text(encoding="utf-8"))
    assert payload["phases"][0]["phase_id"] == "phase_1"
    assert len(payload["commands"]) == 1


def test_bridge_custom_filename_and_key(tmp_path: Path) -> None:
    """Custom filename and return_key produce separate output without collision."""
    bridge = TelemetryBridge(str(tmp_path))
    bridge.on_phase_start("bridge_phase")
    bridge.on_event("test_event", detail=1)

    paths = bridge.save_metrics(
        filename="telemetry_bridge.json", return_key="telemetry_bridge_json"
    )

    assert paths == {"telemetry_bridge_json": str(tmp_path / "telemetry_bridge.json")}
    assert not (tmp_path / "telemetry.json").exists(), "default telemetry.json must not be created"
    assert (tmp_path / "telemetry_bridge.json").exists()

    payload = json.loads((tmp_path / "telemetry_bridge.json").read_text(encoding="utf-8"))
    assert payload["phases"][0]["phase_id"] == "bridge_phase"


def test_observer_and_bridge_no_collision(tmp_path: Path) -> None:
    """Simulate observer + bridge writing separate files; no overwrite occurs."""
    # pylint: disable-next=import-outside-toplevel; silent
    from tests.e2e.e2e_observer import TelemetryObserver

    class FakeSessionManager:
        def get_or_create(self, **kwargs):  # pylint: disable=unused-argument; silent
            return "fake-session"

        def send_command(self, *args, **kwargs):  # pylint: disable=unused-argument; silent
            return "ok"

        def cleanup_all(self) -> int:
            return 0

    fake_mgr = FakeSessionManager()
    observer = TelemetryObserver(fake_mgr, tmp_path)
    observer.get_or_create("main_engineer", "persistent")

    observer_paths = observer.save_metrics()
    bridge = TelemetryBridge(str(tmp_path))
    bridge.on_phase_start("bridge_phase")
    bridge_paths = bridge.save_metrics(
        filename="telemetry_bridge.json", return_key="telemetry_bridge_json"
    )

    merged = {**observer_paths, **bridge_paths}
    assert "telemetry_json" in merged
    assert "telemetry_bridge_json" in merged

    obs_payload = json.loads(Path(merged["telemetry_json"]).read_text(encoding="utf-8"))
    brg_payload = json.loads(Path(merged["telemetry_bridge_json"]).read_text(encoding="utf-8"))

    assert len(obs_payload["sessions"]) == 1, "observer telemetry must retain real sessions"
    assert len(obs_payload["phases"]) == 0
    assert len(brg_payload["phases"]) == 1, "bridge telemetry must retain its phases"
    assert len(brg_payload["sessions"]) == 0
