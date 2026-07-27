from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
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

    def continuation(request: TerminalContinuationRunRequest) -> int:
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
