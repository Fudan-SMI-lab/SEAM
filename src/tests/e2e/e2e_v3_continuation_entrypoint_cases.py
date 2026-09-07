from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness.run import (
    FinalizationHooks,
    FinalizationStage,
    RunArtifactUpdate,
    finalize_run,
)
from tests.run_finalizer_test_support import (
    FinalizerScenario,
    finalization_request,
    passing_finalizer_outcome,
)

from core.terminal_continuation_models import (
    TerminalContinuationRunRequest,
    V3InvocationOptions,
    V3OpenCodeOptions,
    V3ReviewRunOptions,
    V3ServerRunOptions,
)

from . import e2e_test_v3 as target


def test_v3_parser_requires_exactly_one_terminal_run_mode() -> None:
    # Given
    parser = target.build_parser()
    summary = Path("parent-summary.json")
    project = Path("project")

    # When / Then
    continuation = parser.parse_args(["--continue-from", str(summary)])
    assert continuation.continue_from == summary
    assert continuation.project_dir is None
    with pytest.raises(SystemExit) as conflict:
        parser.parse_args(
            ["--project-dir", str(project), "--continue-from", str(summary)]
        )
    assert conflict.value.code == 2
    with pytest.raises(SystemExit) as missing:
        parser.parse_args([])
    assert missing.value.code == 2


def test_v3_main_dispatches_only_to_terminal_continuation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def continuation(request: TerminalContinuationRunRequest, **_values: str) -> int:
        calls.append(request.summary_path)
        return 1

    def normal(**_values: str) -> int:
        pytest.fail("normal V3 coordinator must not run in continuation mode")

    monkeypatch.setattr(target, "run_terminal_continuation", continuation)
    monkeypatch.setattr(target, "run_e2e_v3", normal)
    monkeypatch.setattr(
        sys,
        "argv",
        ["e2e_test_v3", "--continue-from", str(summary)],
    )

    # When
    exit_code = target.main()

    # Then
    assert exit_code == 1
    assert calls == [summary]


def test_v3_main_rejects_workflow_override_in_continuation_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    summary = tmp_path / "summary.json"
    workflow = tmp_path / "workflow.yaml"
    _ = summary.write_text("{}", encoding="utf-8")
    _ = workflow.write_text("name: ignored", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e_test_v3",
            "--continue-from",
            str(summary),
            "--workflow-path",
            str(workflow),
        ],
    )

    # When / Then
    with pytest.raises(SystemExit) as conflict:
        target.main()
    assert conflict.value.code == 2


@pytest.mark.parametrize(
    ("dashboard_argv", "expected"),
    [
        ((), "auto"),
        (("--dashboard",), "on"),
        (("--no-dashboard",), "off"),
        (("--dashboard-mode", "auto"), "auto"),
        (("--dashboard-mode", "on"), "on"),
        (("--dashboard-mode", "off"), "off"),
    ],
)
def test_v3_main_forwards_resolved_dashboard_mode_to_continuation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dashboard_argv: tuple[str, ...],
    expected: str,
) -> None:
    # Given a continuation invocation carrying any public dashboard flag form.
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    captured: list[str] = []

    def continuation(
        request: TerminalContinuationRunRequest, *, dashboard_mode: str = "<missing>"
    ) -> int:
        del request
        captured.append(dashboard_mode)
        return 0

    def normal(**_values: str) -> int:
        pytest.fail("normal V3 coordinator must not run in continuation mode")

    monkeypatch.setattr(target, "run_terminal_continuation", continuation)
    monkeypatch.setattr(target, "run_e2e_v3", normal)
    monkeypatch.setattr(
        sys,
        "argv",
        ["e2e_test_v3", "--continue-from", str(summary), *dashboard_argv],
    )

    # When the Python CLI dispatches.
    exit_code = target.main()

    # Then the resolved dashboard mode reaches the continuation entrypoint.
    assert exit_code == 0
    assert captured == [expected]


def test_v3_main_dashboard_precedence_stays_no_dashboard_over_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given conflicting dashboard switches on a continuation invocation.
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    captured: list[str] = []

    def continuation(
        request: TerminalContinuationRunRequest, *, dashboard_mode: str = "<missing>"
    ) -> int:
        del request
        captured.append(dashboard_mode)
        return 0

    monkeypatch.setattr(target, "run_terminal_continuation", continuation)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e_test_v3",
            "--continue-from",
            str(summary),
            "--dashboard",
            "--no-dashboard",
        ],
    )

    # When the Python CLI resolves the conflict.
    exit_code = target.main()

    # Then the documented --no-dashboard precedence is preserved unchanged.
    assert exit_code == 0
    assert captured == ["off"]


def test_v3_required_finalization_failure_never_prints_pass(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Given
    def fail_seal(_outcome) -> RunArtifactUpdate:
        raise _RequiredSealFailure

    request = finalization_request(
        tmp_path,
        FinalizerScenario(
            hooks=FinalizationHooks(trace_export=fail_seal),
            authoritative_outcome=passing_finalizer_outcome(),
        ),
    )
    request = replace(
        request,
        required_stages=frozenset({FinalizationStage.TRACE_EXPORT}),
        summary_required=True,
    )
    result = finalize_run(request)

    # When
    target.print_summary(
        result.summary,
        finalization_failed=result.finalization_failed,
    )

    # Then
    output = capsys.readouterr().out
    assert "E2E PASS" not in output
    assert "E2E FINALIZATION FAILED" in output


class _RequiredSealFailure(RuntimeError):
    pass


class _StopCoordinatorProbe(BaseException):
    pass


@pytest.mark.e2e
def test_continuation_coordinator_uses_fresh_sessions_without_copy_or_selector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core import config_loader, terminal_continuation, workflow_selector
    from harness.server import lifecycle as server_lifecycle
    from tests.e2e.e2e_observer import TelemetryObserver
    from tests.terminal_run_continuation_hydration_support import (
        PHASE_ORDER,
        create_hydration_parent,
    )

    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_2_prepare",
        phase_statuses=("passed", "failed", "skipped", "skipped", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:1],
    )
    session_ids: list[str] = []

    class SessionManagerProbe:
        def __init__(self) -> None:
            self.active_agent = "build"
            self.session_id = f"session-{len(session_ids) + 1}"
            session_ids.append(self.session_id)

    class ObserverProbe:
        def set_metadata(self, _key: str, _value) -> None:
            return None

    def observed_session(_factory, _config):
        return SimpleNamespace(
            session_manager=SessionManagerProbe(),
            observer=ObserverProbe(),
        )

    def forbidden(*_args, **_kwargs):
        pytest.fail("continuation mode must bypass project copy and selector")

    def stop_after_guard(_path):
        raise _StopCoordinatorProbe

    monkeypatch.setattr(
        server_lifecycle,
        "resolve_server_url",
        lambda *_args, **_kwargs: ("http://127.0.0.1:4096", None),
    )
    monkeypatch.setattr(target, "check_server_running", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(target, "log_server_diagnostics", lambda *_args: None)
    monkeypatch.setattr(target, "copy_project_light", forbidden)
    monkeypatch.setattr(workflow_selector, "resolve_workflow_from_selector", forbidden)
    monkeypatch.setattr(
        TelemetryObserver,
        "create_observed_session",
        observed_session,
    )
    monkeypatch.setattr(config_loader, "load_framework_config", stop_after_guard)

    # When
    for index in range(2):
        with terminal_continuation.prepare_terminal_continuation(
            parent.summary_path,
            f"child-session-probe-{index}",
        ) as prepared:
            with pytest.raises(_StopCoordinatorProbe):
                target.run_e2e_v3(
                    base_url=None,
                    max_phase5_iter=5,
                    keep_temp_dir=True,
                    agent_name=None,
                    project_dir=None,
                    server_auto_start=False,
                    continuation=prepared,
                    opencode_readiness="off",
                )

    # Then
    assert session_ids == ["session-1", "session-2"]


@pytest.mark.e2e
def test_terminal_continuation_allocates_fresh_child_context_per_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core import terminal_continuation as lifecycle

    # Given
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    child_ids: list[str] = []
    prepared_contexts: list[str] = []

    @contextmanager
    def prepare(_summary: Path, child_run_id: str) -> Iterator[str]:
        child_ids.append(child_run_id)
        yield child_run_id

    def child_runner(**values: str | int | bool | None) -> int:
        assert values["project_dir"] is None
        continuation = values["continuation"]
        assert isinstance(continuation, str)
        prepared_contexts.append(continuation)
        return 0

    monkeypatch.setattr(lifecycle, "prepare_terminal_continuation", prepare)
    monkeypatch.setattr(target, "run_e2e_v3", child_runner)

    def invoke() -> int:
        return target.run_terminal_continuation(
            TerminalContinuationRunRequest(
                summary_path=summary,
                server=V3ServerRunOptions(None, False, 0),
                review=V3ReviewRunOptions(5, False, None),
                invocation=V3InvocationOptions(True, None, "", None),
                opencode=V3OpenCodeOptions("off", 1),
            )
        )

    # When
    first = invoke()
    second = invoke()

    # Then
    assert first == second == 0
    assert len(set(child_ids)) == 2
    assert prepared_contexts == child_ids


@pytest.mark.e2e
@pytest.mark.parametrize("dashboard_mode", ["on", "off", "auto"])
def test_terminal_continuation_forwards_dashboard_mode_to_child_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dashboard_mode: str,
) -> None:
    from core import terminal_continuation as lifecycle

    # Given a prepared child and an explicitly resolved dashboard mode.
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    captured: list[str] = []

    @contextmanager
    def prepare(_summary: Path, _child_run_id: str) -> Iterator[str]:
        yield "prepared-child"

    def child_runner(**values: bool | Path | str | None) -> int:
        value = values["dashboard_mode"]
        assert isinstance(value, str)
        captured.append(value)
        return 0

    monkeypatch.setattr(lifecycle, "prepare_terminal_continuation", prepare)
    monkeypatch.setattr(target, "run_e2e_v3", child_runner)
    request = TerminalContinuationRunRequest(
        summary_path=summary,
        server=V3ServerRunOptions(None, False, 0),
        review=V3ReviewRunOptions(5, False, None),
        invocation=V3InvocationOptions(True, None, "", None),
        opencode=V3OpenCodeOptions("off", 1),
    )

    # When continuation dispatches the child run with the resolved mode.
    exit_code = target.run_terminal_continuation(request, dashboard_mode=dashboard_mode)

    # Then the child receives the same mode unchanged.
    assert exit_code == 0
    assert captured == [dashboard_mode]


@pytest.mark.e2e
def test_terminal_continuation_dashboard_mode_defaults_to_auto_for_legacy_callers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core import terminal_continuation as lifecycle

    # Given a legacy caller that omits the dashboard keyword entirely.
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    captured: list[str] = []

    @contextmanager
    def prepare(_summary: Path, _child_run_id: str) -> Iterator[str]:
        yield "prepared-child"

    def child_runner(**values: bool | Path | str | None) -> int:
        value = values["dashboard_mode"]
        assert isinstance(value, str)
        captured.append(value)
        return 0

    monkeypatch.setattr(lifecycle, "prepare_terminal_continuation", prepare)
    monkeypatch.setattr(target, "run_e2e_v3", child_runner)
    request = TerminalContinuationRunRequest(
        summary_path=summary,
        server=V3ServerRunOptions(None, False, 0),
        review=V3ReviewRunOptions(5, False, None),
        invocation=V3InvocationOptions(True, None, "", None),
        opencode=V3OpenCodeOptions("off", 1),
    )

    # When continuation dispatches without the dashboard keyword.
    exit_code = target.run_terminal_continuation(request)

    # Then the child still receives the historical auto default.
    assert exit_code == 0
    assert captured == ["auto"]


def test_continuation_request_schema_excludes_dashboard_state() -> None:
    # Given the typed continuation request model and its nested option groups.
    model_fields = {field.name for field in fields(TerminalContinuationRunRequest)}
    nested_fields = {
        field.name
        for group in (
            V3ServerRunOptions,
            V3ReviewRunOptions,
            V3InvocationOptions,
            V3OpenCodeOptions,
        )
        for field in fields(group)
    }

    # Then no dashboard/presentation state is serialized into the lineage schema.
    assert model_fields == {"summary_path", "server", "review", "invocation", "opencode"}
    assert nested_fields == {
        "base_url",
        "auto_start",
        "port",
        "max_phase5_iter",
        "enabled",
        "overrides",
        "keep_temp_dir",
        "agent_name",
        "user_constraints",
        "framework_config_path",
        "container_retention",
        "save_agent_trace",
        "readiness",
        "message_timeout",
    }
    assert not any("dashboard" in name for name in model_fields | nested_fields)
