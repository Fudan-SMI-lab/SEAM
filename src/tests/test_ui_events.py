from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import core.ui_events as ui_events
from core.ui_events import (
    PHASE_DISPLAY,
    UIEventSink,
    dashboard_enabled,
    summarize_text,
)
from core.dashboard import DashboardState, _apply_event, visible_phase_rows
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor
from tests.e2e.e2e_observer import TelemetryObserver
from tests.e2e.e2e_test_v3 import (
    _prepare_dashboard_wiring,
    build_parser,
    write_usage_guide,
)


class _FakeSessionManager:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def get_or_create(
        self,
        role: str,
        agent: str = "",
        lifecycle: str = "persistent",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        del agent, lifecycle, title, working_dir, initial_prompt
        return f"ses-{role}"

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str:
        del agent, timeout, retries
        self.sent.append((session_id, command))
        return "phase complete"

    def cleanup_all(self) -> int:
        return 1


def test_ui_event_sink_appends_schema_complete_jsonl(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-1")

    sink.emit(
        "phase_started",
        phase_id="phase_0_env_detect",
        agent_role="main_engineer",
        session_id="ses-1",
        status="running",
        message="Detecting environment",
        details={"platform": "ppu"},
        artifact_path="artifacts/phase0.json",
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "ui_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "schema_version",
        "timestamp",
        "run_id",
        "event_type",
        "phase_id",
        "subphase_id",
        "agent_role",
        "session_id",
        "status",
        "message",
        "details",
        "artifact_path",
    }
    assert record["schema_version"] == "1.0"
    assert record["run_id"] == "run-1"
    assert record["event_type"] == "phase_started"
    assert record["details"] == {"platform": "ppu"}


def test_ui_event_sink_is_non_critical_when_path_is_unwritable(tmp_path: Path) -> None:
    sink = UIEventSink(
        tmp_path / "missing" / "events", run_id="run-1", create_dir=False
    )

    sink.emit("runner_notice", message="this should not raise")


def test_ui_event_sink_baseline_safe_schema_and_message_behavior(
    tmp_path: Path,
) -> None:
    """Baseline characterization: safe-input behavior must not change."""
    sink = UIEventSink(tmp_path, run_id="run-base")
    long_safe_message = "  intro   " + ("payload " * 100)

    sink.emit(
        "phase_started",
        phase_id="phase_0_env_detect",
        agent_role="main_engineer",
        session_id="ses-1",
        status="running",
        message=long_safe_message,
        details={"platform": "ppu", "attempt": 1, "ok": True, "note": "plain"},
        artifact_path="artifacts/phase0.json",
    )
    sink.emit("runner_notice", message="second")

    lines = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert set(record) == {
        "schema_version",
        "timestamp",
        "run_id",
        "event_type",
        "phase_id",
        "subphase_id",
        "agent_role",
        "session_id",
        "status",
        "message",
        "details",
        "artifact_path",
    }
    assert record["schema_version"] == "1.0"
    assert record["run_id"] == "run-base"
    assert record["event_type"] == "phase_started"
    assert record["phase_id"] == "phase_0_env_detect"
    assert record["subphase_id"] is None
    assert record["agent_role"] == "main_engineer"
    assert record["session_id"] == "ses-1"
    assert record["status"] == "running"
    assert record["message"].startswith("intro payload")
    assert record["message"].endswith("...")
    assert len(record["message"]) <= 503
    assert "  " not in record["message"]
    assert record["details"] == {
        "platform": "ppu",
        "attempt": 1,
        "ok": True,
        "note": "plain",
    }
    assert record["artifact_path"] == "artifacts/phase0.json"

    second = json.loads(lines[1])
    assert second["message"] == "second"
    assert second["details"] == {}
    assert second["artifact_path"] is None


def _read_records(path: Path) -> list[Any]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _measure_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_measure_depth(child) for child in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_measure_depth(child) for child in value), default=0)
    return 0


def _assert_bounded(value: object) -> None:
    if isinstance(value, dict):
        assert len(value) <= 50
        for child in value.values():
            _assert_bounded(child)
        return
    if isinstance(value, list):
        assert len(value) <= 50
        for child in value:
            _assert_bounded(child)
        return
    if isinstance(value, str):
        assert len(value) <= 500


def test_ui_event_message_redacts_secret_vectors(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-msg")
    pat = "ghp_" + "a1" * 20
    api_key = "sk-" + "b" * 24
    bearer = "Bearer " + "eyJ" + "x" * 30
    message = (
        f"auth {bearer} then {api_key} then {pat} "
        "TOKEN=tok-sentinel-1 "
        "--ToKeN split-sentinel-2 "
        '--Api-Key="quoted sentinel 3" '
        '"clientSecret": "named sentinel 4"'
    )

    sink.emit("runner_notice", message=message)

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    for sentinel in (
        pat,
        api_key,
        bearer,
        "tok-sentinel-1",
        "split-sentinel-2",
        "quoted sentinel 3",
        "named sentinel 4",
    ):
        assert sentinel not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert "<REDACTED" in record["message"]


def test_ui_event_details_redact_sensitive_keys_and_nested_structures(
    tmp_path: Path,
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-details")
    pat = "ghp_" + "z9" * 20

    sink.emit(
        "phase_started",
        details={
            "Authorization": "Bearer hdr-sentinel",
            "apiKey": "api-sentinel",
            "clientSecret": "cs-sentinel",
            "token": "tok-sentinel",
            "nested": {"credentials": [{"password": "pw-sentinel"}, pat]},
            "note": "safe",
            "count": 3,
        },
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    for sentinel in (
        "hdr-sentinel",
        "api-sentinel",
        "cs-sentinel",
        "tok-sentinel",
        "pw-sentinel",
        pat,
    ):
        assert sentinel not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    details = record["details"]
    assert details["Authorization"] == "<REDACTED>"
    assert details["apiKey"] == "<REDACTED>"
    assert details["clientSecret"] == "<REDACTED>"
    assert details["token"] == "<REDACTED>"
    assert details["nested"]["credentials"][0]["password"] == "<REDACTED>"
    assert details["nested"]["credentials"][1] == "<REDACTED>"
    assert details["note"] == "safe"
    assert details["count"] == 3


def test_ui_event_details_redact_cli_values_inside_strings(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-cli")

    sink.emit(
        "shell_command_started",
        details={
            "command": (
                "python validate.py --token split-cli-sentinel "
                '--password="quoted cli sentinel"'
            ),
            "argv": ["python", "validate.py", "--Api-Key=argv-cli-sentinel"],
        },
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    for sentinel in ("split-cli-sentinel", "quoted cli sentinel", "argv-cli-sentinel"):
        assert sentinel not in raw


def test_ui_event_artifact_path_is_redacted_and_bounded(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-artifact")
    pat = "ghp_" + "p9" * 20
    artifact_path = f"artifacts/{pat}/" + ("segment/" * 80)

    sink.emit("phase_finished", artifact_path=artifact_path)

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert pat not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert record["artifact_path"] is not None
    assert len(record["artifact_path"]) <= 500
    assert "<REDACTED_GITHUB_TOKEN>" in record["artifact_path"]


def test_ui_event_details_convert_unsupported_values_to_bounded_strings(
    tmp_path: Path,
) -> None:
    class _Gadget:
        def __repr__(self) -> str:
            return "Gadget(token=gadget-repr-sentinel)"

    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    sink = UIEventSink(tmp_path, run_id="run-unsupported")

    sink.emit(
        "runner_notice",
        details={
            "gadget": _Gadget(),
            "tags": {"alpha", "beta"},
            "blob": b"\x00\x01bytes",
            "cyclic": cyclic,
            "missing": None,
            "ratio": 1.5,
            "ok": True,
        },
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "gadget-repr-sentinel" not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    details = record["details"]
    assert isinstance(details["gadget"], str)
    assert isinstance(details["tags"], str)
    assert isinstance(details["blob"], str)
    assert _measure_depth(details["cyclic"]) <= 6
    assert details["missing"] is None
    assert details["ratio"] == 1.5
    assert details["ok"] is True
    _assert_bounded(details)


def test_ui_event_details_bound_depth_breadth_and_text(tmp_path: Path) -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for level in range(12):
        child: dict[str, object] = {"level": level}
        cursor["child"] = child
        cursor = child
    sink = UIEventSink(tmp_path, run_id="run-bounds")

    sink.emit(
        "phase_started",
        details={
            "deep": deep,
            "wide_map": {f"k{i:03d}": f"value-{i}" for i in range(120)},
            "wide_list": [f"item-{i}" for i in range(120)],
            "long_text": "y" * 900,
        },
    )

    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    details = record["details"]
    assert _measure_depth(details) <= 6
    assert len(details["wide_map"]) == 50
    assert len(details["wide_list"]) == 50
    assert len(details["long_text"]) <= 500
    _assert_bounded(details)


def test_ui_event_record_is_capped_below_64kib_with_bounded_preview(
    tmp_path: Path,
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-cap")
    pat = "ghp_" + "q7" * 20
    details: dict[str, object] = {
        f"key{i:02d}": {f"sub{j:02d}": "v" * 400 for j in range(50)}
        for i in range(50)
    }
    details["secret"] = pat

    sink.emit("phase_started", details=details)

    raw_bytes = (tmp_path / "ui_events.jsonl").read_bytes()
    lines = raw_bytes.splitlines()
    assert len(lines) == 1
    assert len(lines[0]) + 1 < 64 * 1024
    raw = raw_bytes.decode("utf-8")
    assert pat not in raw
    record = json.loads(lines[0])
    assert set(record) == {
        "schema_version",
        "timestamp",
        "run_id",
        "event_type",
        "phase_id",
        "subphase_id",
        "agent_role",
        "session_id",
        "status",
        "message",
        "details",
        "artifact_path",
    }
    assert record["details"]["truncated"] is True
    preview = record["details"]["preview"]
    assert isinstance(preview, str)
    assert len(preview) <= 2000


def test_ui_event_sink_appends_with_single_posix_append_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-flags")
    open_calls: list[int] = []
    write_calls: list[int] = []
    real_open = os.open
    real_write = os.write

    def capturing_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        **kwargs: int,
    ) -> int:
        open_calls.append(flags)
        return real_open(path, flags, mode, **kwargs)

    def capturing_write(fd: int, data: bytes) -> int:
        write_calls.append(len(data))
        return real_write(fd, data)

    monkeypatch.setattr("os.open", capturing_open)
    monkeypatch.setattr("os.write", capturing_write)

    sink.emit("runner_notice", message="hello")

    assert len(open_calls) == 1
    flags = open_calls[0]
    assert flags & os.O_APPEND
    assert flags & os.O_CREAT
    assert flags & os.O_WRONLY
    assert len(write_calls) == 1
    assert len(_read_records(tmp_path / "ui_events.jsonl")) == 1


def test_ui_event_sink_disables_when_serialization_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BoomJson:
        @staticmethod
        def dumps(*args: object, **kwargs: object) -> str:
            raise TypeError("boom")

    sink = UIEventSink(tmp_path, run_id="run-ser")
    monkeypatch.setattr(ui_events, "json", _BoomJson)

    sink.emit("runner_notice", message="hello")
    sink.emit("runner_notice", message="again")

    assert sink.enabled is False
    assert not (tmp_path / "ui_events.jsonl").exists()


def test_ui_event_sink_completes_transient_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-short")
    real_write = os.write
    write_calls: list[int] = []

    def short_write(fd: int, data: bytes) -> int:
        written = real_write(fd, data[: max(1, len(data) // 2)])
        write_calls.append(written)
        return written

    monkeypatch.setattr("os.write", short_write)

    sink.emit("runner_notice", message="hello")
    sink.emit("runner_notice", message="again")

    records = _read_records(tmp_path / "ui_events.jsonl")
    assert sink.enabled is True
    assert [record["message"] for record in records] == ["hello", "again"]
    assert len(write_calls) > 2


def test_ui_event_sink_disables_when_write_makes_no_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-zero-write")
    monkeypatch.setattr("os.write", lambda _fd, _data: 0)

    sink.emit("runner_notice", message="hello")

    assert sink.enabled is False
    assert (tmp_path / "ui_events.jsonl").read_bytes() == b""


def test_ui_event_sink_unwritable_path_disables_without_raising(
    tmp_path: Path,
) -> None:
    sink = UIEventSink(
        tmp_path / "missing" / "events", run_id="run-1", create_dir=False
    )

    sink.emit("runner_notice", message="this should not raise")
    sink.emit("runner_notice", message="still no raise")

    assert sink.enabled is False
    assert not (tmp_path / "missing" / "events" / "ui_events.jsonl").exists()


def test_ui_event_message_redacts_authorization_bearer_header(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-hdr")

    sink.emit("runner_notice", message="Authorization: Bearer hdr-sentinel")

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "hdr-sentinel" not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert "<REDACTED>" in record["message"]


def test_ui_event_details_redact_authorization_bearer_in_values_and_lists(
    tmp_path: Path,
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-hdr-details")

    sink.emit(
        "runner_notice",
        details={
            "note": "Authorization: Bearer hdr-v5-sentinel",
            "items": ["Authorization: Bearer hdr-v5b-sentinel"],
        },
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "hdr-v5-sentinel" not in raw
    assert "hdr-v5b-sentinel" not in raw


def test_ui_event_artifact_path_redacts_authorization_bearer(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-hdr-artifact")

    sink.emit(
        "phase_finished", artifact_path="Authorization: Bearer hdr-v6-sentinel"
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "hdr-v6-sentinel" not in raw


def test_ui_event_truncation_preview_redacts_authorization_bearer(
    tmp_path: Path,
) -> None:
    sink = UIEventSink(tmp_path, run_id="run-hdr-preview")
    details: dict[str, object] = {
        "aaa_first": "Authorization: Bearer hdr-p1-sentinel"
    }
    details.update(
        {
            f"key{i:02d}": {f"sub{j:02d}": "v" * 400 for j in range(50)}
            for i in range(50)
        }
    )

    sink.emit("phase_finished", details=details)

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "hdr-p1-sentinel" not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert record["details"]["truncated"] is True
    assert "hdr-p1-sentinel" not in record["details"]["preview"]


def test_ui_event_details_keys_are_redacted_and_bounded(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-keys")
    pat = "ghp_" + "k5" * 20

    sink.emit(
        "runner_notice",
        details={f"Authorization: Bearer {pat}": "v", pat: 1, "k" * 600: 2},
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert pat not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert all(len(key) <= 500 for key in record["details"])


def test_ui_event_structural_fields_are_redacted_and_bounded(
    tmp_path: Path,
) -> None:
    pat = "ghp_" + "s1" * 20
    sink = UIEventSink(tmp_path, run_id=f"run-{pat}")

    sink.emit(
        f"phase_started {pat}",
        phase_id=f"phase {pat}",
        subphase_id=f"sub {pat}",
        agent_role=f"role {pat}",
        session_id=f"ses {pat}",
        status=f"st {pat}",
    )

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert pat not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    for field in (
        "run_id",
        "event_type",
        "phase_id",
        "subphase_id",
        "agent_role",
        "session_id",
        "status",
    ):
        assert "<REDACTED_GITHUB_TOKEN>" in record[field]
        assert len(record[field]) <= 500


def test_ui_event_record_stays_below_cap_with_oversized_structural_fields(
    tmp_path: Path,
) -> None:
    sink = UIEventSink(tmp_path, run_id="x" * 70000)

    sink.emit("e" * 70000, phase_id="p" * 70000, message="m" * 70000)

    raw_bytes = (tmp_path / "ui_events.jsonl").read_bytes()
    lines = raw_bytes.splitlines()
    assert len(lines) == 1
    assert len(lines[0]) + 1 < 64 * 1024
    record = json.loads(lines[0])
    assert set(record) == {
        "schema_version",
        "timestamp",
        "run_id",
        "event_type",
        "phase_id",
        "subphase_id",
        "agent_role",
        "session_id",
        "status",
        "message",
        "details",
        "artifact_path",
    }


def test_ui_event_sink_disables_instead_of_writing_oversize_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui_events, "_MAX_ENCODED_BYTES", 200)
    sink = UIEventSink(tmp_path, run_id="run-tiny-cap")

    sink.emit("runner_notice", message="hello", details={"k": "v"})

    assert sink.enabled is False
    assert not (tmp_path / "ui_events.jsonl").exists()


def test_ui_event_message_redacts_mixed_case_bearer_header(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-lower-hdr")

    sink.emit("runner_notice", message="authorization: bearer hdr-lower-sentinel")

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "hdr-lower-sentinel" not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert "<REDACTED>" in record["message"]


def test_summarize_text_does_not_shift_partial_token_prefix_into_head() -> None:
    secrets = " ".join(
        [
            "sk-" + "a" * 30,
            "sk-" + "b" * 30,
            "sk-" + "c" * 30,
            "sk-" + "d" * 30,
            "ghp_" + "e" * 30,
        ]
    )
    text = secrets + " " + ("f" * 375) + " " + "sk-" + "z" * 30 + " tail"

    summary = summarize_text(text, 500)

    assert "sk-z" not in summary
    assert "zzzzzzzz" not in summary
    assert "sk-" not in summary


def test_ui_event_details_value_does_not_shift_partial_token_prefix(
    tmp_path: Path,
) -> None:
    secrets = " ".join(
        [
            "sk-" + "a" * 30,
            "sk-" + "b" * 30,
            "sk-" + "c" * 30,
            "sk-" + "d" * 30,
            "ghp_" + "e" * 30,
        ]
    )
    shifted = secrets + " " + ("f" * 375) + " " + "sk-" + "z" * 30 + " tail"
    sink = UIEventSink(tmp_path, run_id="run-shift")

    sink.emit("runner_notice", details={"field": shifted})

    raw = (tmp_path / "ui_events.jsonl").read_text(encoding="utf-8")
    assert "sk-z" not in raw
    assert "zzzzzzzz" not in raw
    record = _read_records(tmp_path / "ui_events.jsonl")[0]
    assert "sk-" not in record["details"]["field"]


def test_summarize_text_long_safe_text_characterization() -> None:
    text = "lorem ipsum " * 100

    summary = summarize_text(text, 500)

    assert summary == text[:500].rstrip() + "..."


def test_summarize_text_redacts_full_pat_inside_margin() -> None:
    pat = "ghp_" + "m3" * 20
    text = "a" * 490 + " " + pat + "b" * 100

    summary = summarize_text(text, 500)

    assert pat not in summary
    assert pat[:10] not in summary
    assert pat[:20] not in summary
    assert len(summary) <= 503


def test_phase_display_copy_uses_user_facing_names() -> None:
    assert PHASE_DISPLAY["phase_0_env_detect"].title == "环境检测"
    assert PHASE_DISPLAY["phase_5_validation"].title == "运行验证与自动修复"
    assert "真实" in PHASE_DISPLAY["phase_5_validation"].description


def test_dashboard_enabled_auto_respects_tty_and_ci() -> None:
    assert dashboard_enabled("auto", is_tty=True, environ={}) is True
    assert dashboard_enabled("auto", is_tty=False, environ={}) is False
    assert dashboard_enabled("auto", is_tty=True, environ={"CI": "1"}) is False
    assert dashboard_enabled("on", is_tty=False, environ={"CI": "1"}) is True
    assert dashboard_enabled("off", is_tty=True, environ={}) is False


def test_e2e_v3_parser_accepts_dashboard_flags() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "--project-dir",
            "proj",
            "--dashboard-mode",
            "off",
            "--dashboard",
            "--no-dashboard",
        ]
    )

    assert args.dashboard_mode == "off"
    assert args.dashboard is True
    assert args.no_dashboard is True


def test_summarize_text_redacts_and_truncates_sensitive_values() -> None:
    text = "OPENAI_API_KEY=sk-abc12345678901234567890 " + ("x" * 200)

    summary = summarize_text(text, limit=80)

    assert "sk-abc" not in summary
    assert "OPENAI_API_KEY=<REDACTED>" in summary
    assert len(summary) <= 83


def test_dashboard_event_application_keeps_long_agent_prompts_compact() -> None:
    state = DashboardState()
    long_prompt = " ".join(["dependency_fixer"] * 80)

    for index in range(12):
        _apply_event(
            state,
            {
                "event_type": "agent_command_started",
                "timestamp": f"2026-07-01T09:13:{index:02d}+00:00",
                "agent_role": "dependency_fixer",
                "status": "running",
                "message": long_prompt,
            },
        )

    assert len(state.current_work) <= 140
    assert len(state.activity) <= 8
    assert all(len(line) <= 140 for line in state.activity)


def test_dashboard_translates_workflow_selector_prompt_to_event_language() -> None:
    state = DashboardState()

    _apply_event(
        state,
        {
            "event_type": "agent_command_started",
            "timestamp": "2026-07-01T09:06:53+00:00",
            "agent_role": "workflow_selector",
            "session_id": "ses-workflow_selector",
            "status": "running",
            "message": "# Workflow Selection You are selecting the best SEAM migration workflow",
            "details": {
                "command_preview": "# Workflow Selection You are selecting the best SEAM migration workflow"
            },
        },
    )

    activity_text = "\n".join(state.activity)
    assert "开始：选择迁移工作流" in activity_text
    assert "# Workflow Selection" not in activity_text
    assert "# Workflow Selection" not in state.current_work


def test_dashboard_translates_dependency_fixer_prompt_to_event_language() -> None:
    state = DashboardState()

    _apply_event(
        state,
        {
            "event_type": "agent_command_started",
            "timestamp": "2026-07-01T09:13:39+00:00",
            "phase_id": "phase_5_validation",
            "agent_role": "dependency_fixer",
            "session_id": "ses-dependency_fixer",
            "status": "running",
            "message": "你是dependency_fixer，只处理环境、包、导入、版本、安装和运行依赖问题；不要处理算子。",
        },
    )

    activity_text = "\n".join(state.activity)
    assert "开始：修复依赖和环境问题" in activity_text
    assert "你是dependency_fixer" not in activity_text
    assert "修复依赖和环境问题" in state.current_work


def test_dashboard_tracks_iteration_subsession_subphase_and_error_history() -> None:
    state = DashboardState()

    events = [
        {
            "event_type": "session_ready",
            "timestamp": "2026-07-01T09:13:38+00:00",
            "phase_id": "phase_5_validation",
            "agent_role": "runtime_analyzer",
            "session_id": "ses-runtime-analyzer",
            "status": "ready",
        },
        {
            "event_type": "repair_iteration_started",
            "timestamp": "2026-07-01T09:13:39+00:00",
            "phase_id": "phase_5_validation",
            "status": "running",
            "details": {"attempt": 2, "max_attempts": 8},
        },
        {
            "event_type": "subphase_started",
            "timestamp": "2026-07-01T09:13:40+00:00",
            "phase_id": "phase_5_validation",
            "subphase_id": "analyze_error",
            "status": "running",
            "details": {"subphase_type": "llm", "iteration": 2},
        },
        {
            "event_type": "agent_command_started",
            "timestamp": "2026-07-01T09:13:41+00:00",
            "phase_id": "phase_5_validation",
            "agent_role": "runtime_analyzer",
            "session_id": "ses-runtime-analyzer",
            "status": "running",
            "details": {"command_sequence": 4},
        },
        {
            "event_type": "subphase_finished",
            "timestamp": "2026-07-01T09:13:42+00:00",
            "phase_id": "phase_5_validation",
            "subphase_id": "analyze_error",
            "status": "failure",
            "message": "analysis failed",
            "details": {
                "subphase_type": "llm",
                "iteration": 2,
                "error": "Insufficient Balance",
            },
        },
    ]
    for event in events:
        _apply_event(state, event)

    assert state.current_iteration["attempt"] == 2
    assert state.sessions["ses-runtime-analyzer"]["command_sequence"] == 4
    assert state.subphases["analyze_error"]["iteration"] == 2
    assert state.subphases["analyze_error"]["status"] == "failure"
    assert any("Insufficient Balance" in error for error in state.error_history)


def test_visible_phase_rows_only_show_current_and_next_with_numbers() -> None:
    state = DashboardState()
    for phase_id in (
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_1_5_constraint_summary",
        "phase_2_venv_create",
        "phase_3_entry_script",
        "phase_35_static_validate",
        "phase_4_rule_migration",
    ):
        _apply_event(
            state,
            {
                "event_type": "phase_finished",
                "phase_id": phase_id,
                "status": "success",
                "message": "done",
            },
        )
    _apply_event(
        state,
        {
            "event_type": "phase_started",
            "phase_id": "phase_5_validation",
            "status": "running",
            "message": "validating",
        },
    )

    rows = visible_phase_rows(state)

    assert len(rows) == 2
    assert rows[0].number == "5"
    assert rows[0].title == "运行验证与自动修复"
    assert rows[0].status == "运行中"
    assert rows[1].number == "6"
    assert rows[1].title == "报告与使用说明"
    assert rows[1].status == "待执行"


def test_telemetry_observer_emits_session_and_command_ui_events(tmp_path: Path) -> None:
    sink = UIEventSink(tmp_path, run_id="run-1")
    observer = TelemetryObserver(_FakeSessionManager(), tmp_path, ui_event_sink=sink)
    observer.set_active_phase("phase_1_project_analysis")

    session_id = observer.get_or_create("main_engineer", lifecycle="persistent")
    response = observer.send_command(session_id, "inspect project", timeout=7)

    assert response == "phase complete"
    records = [
        json.loads(line)
        for line in (tmp_path / "ui_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    event_types = [record["event_type"] for record in records]
    assert event_types == [
        "session_ready",
        "agent_command_started",
        "agent_command_finished",
    ]
    assert records[0]["agent_role"] == "main_engineer"
    assert records[1]["phase_id"] == "phase_1_project_analysis"
    assert records[1]["status"] == "running"
    assert records[2]["status"] == "passed"
    assert records[2]["details"]["response_preview"] == "phase complete"


def test_workflow_executor_emits_phase_and_shell_ui_events(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="unused",
        output_schema={},
        type="shell",
        transitions={"on_success": "complete"},
    )
    setattr(phase, "command", "python -c 'print(\"ok\")'")
    setattr(phase, "cwd", str(tmp_path))
    workflow = WorkflowDefinition(
        name="ui-shell",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
    )
    artifact_store = _MemoryArtifactStore()
    sink = UIEventSink(tmp_path, run_id="run-1")
    executor = WorkflowExecutor(
        workflow,
        _FakeSessionManager(),
        artifact_store,
        object(),
        object(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        ui_event_sink=sink,
    )

    executor.execute({"PROJECT_DIR": str(tmp_path)})

    records = [
        json.loads(line)
        for line in (tmp_path / "ui_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    event_types = [record["event_type"] for record in records]
    assert event_types == [
        "phase_started",
        "shell_command_started",
        "shell_command_finished",
        "phase_finished",
        "workflow_finished",
    ]
    assert records[0]["phase_id"] == "run_entry_script"
    assert records[1]["subphase_id"] == "run_entry_script"
    assert records[2]["details"]["exit_code"] == 0
    assert records[3]["status"] == "success"


def test_usage_guide_contains_project_command_and_debug_paths(tmp_path: Path) -> None:
    usage_path = write_usage_guide(
        tmp_path,
        entry_script="python test_data_and_scripts/run_e2e.py",
        overall_status="PASS",
        output_dir=tmp_path / "reports",
    )

    content = Path(usage_path).read_text(encoding="utf-8")
    assert "E2E TEST PASSED" in content
    assert f"cd {tmp_path}" in content
    assert "python test_data_and_scripts/run_e2e.py" in content
    assert ".sm-artifacts/" in content


class _MemoryArtifactStore:
    def save_phase_output(self, phase_id: str, output: dict[str, object]) -> str:
        return phase_id

    def mark_validated(self, phase_id: str, output: dict[str, object]) -> str:
        return phase_id

    def write_journal(self, entry: dict[str, object]) -> str:
        return "journal"

    def save_shell_attempt_artifacts(self, **_: object) -> dict[str, object]:
        return {}


def test_dashboard_wiring_off_mode_is_fully_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEAM_UI_EVENTS_PATH", raising=False)
    monkeypatch.delenv("SEAM_RUN_ID", raising=False)

    wiring = _prepare_dashboard_wiring(
        "off", tmp_path, "run-1", is_tty=True, environ=os.environ
    )

    assert wiring.dashboard_on is False
    assert wiring.ui_event_sink is None
    assert wiring.dashboard_stop is None
    assert wiring.dashboard_thread is None
    assert not (tmp_path / "ui_events.jsonl").exists()
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


def test_dashboard_wiring_auto_without_tty_is_fully_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEAM_UI_EVENTS_PATH", raising=False)
    monkeypatch.delenv("SEAM_RUN_ID", raising=False)

    wiring = _prepare_dashboard_wiring(
        "auto", tmp_path, "run-1", is_tty=False, environ=os.environ
    )

    assert wiring.dashboard_on is False
    assert wiring.ui_event_sink is None
    assert wiring.dashboard_stop is None
    assert wiring.dashboard_thread is None
    assert not (tmp_path / "ui_events.jsonl").exists()
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


def test_dashboard_wiring_on_mode_activates_event_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)

    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-1", is_tty=False, environ=os.environ
    )
    try:
        assert wiring.dashboard_on is True
        assert wiring.ui_event_sink is not None
        assert wiring.dashboard_thread is not None
        assert os.environ["SEAM_UI_EVENTS_PATH"] == str(tmp_path / "ui_events.jsonl")
        assert os.environ["SEAM_RUN_ID"] == "run-1"
        records = [
            json.loads(line)
            for line in (tmp_path / "ui_events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [record["event_type"] for record in records] == ["runner_started"]
    finally:
        if wiring.dashboard_stop is not None:
            wiring.dashboard_stop.set()
        if wiring.dashboard_thread is not None:
            wiring.dashboard_thread.join(timeout=2)
        os.environ.pop("SEAM_UI_EVENTS_PATH", None)
        os.environ.pop("SEAM_RUN_ID", None)


def test_telemetry_observer_without_sink_emits_no_ui_events(tmp_path: Path) -> None:
    observer = TelemetryObserver(_FakeSessionManager(), tmp_path)
    observer.set_active_phase("phase_1_project_analysis")

    session_id = observer.get_or_create("main_engineer", lifecycle="persistent")
    response = observer.send_command(session_id, "inspect project", timeout=7)

    assert response == "phase complete"
    assert not (tmp_path / "ui_events.jsonl").exists()


def test_workflow_executor_without_sink_emits_no_ui_events(tmp_path: Path) -> None:
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run Entry",
        prompt_template="unused",
        output_schema={},
        type="shell",
        transitions={"on_success": "complete"},
    )
    setattr(phase, "command", "python -c 'print(\"ok\")'")
    setattr(phase, "cwd", str(tmp_path))
    workflow = WorkflowDefinition(
        name="ui-shell-off",
        version="1.0",
        phases=[phase],
        terminals=["complete"],
    )
    executor = WorkflowExecutor(
        workflow,
        _FakeSessionManager(),
        _MemoryArtifactStore(),
        object(),
        object(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )

    assert executor.ui_event_sink is None
    executor.execute({"PROJECT_DIR": str(tmp_path)})

    assert not (tmp_path / "ui_events.jsonl").exists()
