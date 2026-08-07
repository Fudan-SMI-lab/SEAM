from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from core.run_outcome import TerminalOutcome
from harness.run import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHook,
    FinalizationHooks,
    FinalizationStage,
    RunArtifactUpdate,
    finalize_run,
)
from .run_finalizer_test_support import (
    FinalizerScenario,
    finalization_request,
)

ExceptionFactory = Callable[[], Exception]


class OrdinaryHookError(Exception):
    pass


def _type_error() -> Exception:
    return TypeError("type failure")


def _key_error() -> Exception:
    return KeyError("key failure")


def _ordinary_error() -> Exception:
    return OrdinaryHookError("ordinary failure")


@pytest.mark.parametrize(
    "failure_factory",
    [_type_error, _key_error, _ordinary_error],
    ids=["type-error", "key-error", "custom-exception"],
)
@pytest.mark.parametrize("failed_stage", FinalizationStage.callback_stages())
def test_ordinary_hook_exception_is_diagnostic_and_cleanup_continues(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failed_stage: FinalizationStage,
    failure_factory: ExceptionFactory,
) -> None:
    # Given every hook records execution and one raises an ordinary Exception.
    calls: list[FinalizationStage] = []

    def callback(stage: FinalizationStage) -> FinalizationHook:
        def hook(_outcome: TerminalOutcome) -> RunArtifactUpdate:
            calls.append(stage)
            if stage is failed_stage:
                raise failure_factory()
            return EMPTY_ARTIFACT_UPDATE

        return hook

    hooks = FinalizationHooks.from_mapping(
        {stage: callback(stage) for stage in FinalizationStage.callback_stages()}
    )

    # When the passing workflow is finalized across the failed sidecar stage.
    result = finalize_run(
        finalization_request(tmp_path, FinalizerScenario(hooks=hooks))
    )

    # Then every later stage runs and the frozen result is unchanged and diagnosed.
    assert calls == list(FinalizationStage.callback_stages())
    assert FinalizationStage.AUTHORIZED_CLEANUP in calls
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    assert result.summary.errors == ()
    assert [item.stage for item in result.diagnostics] == [failed_stage]
    assert type(failure_factory()).__name__ in caplog.text


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("failed_stage", FinalizationStage.callback_stages())
def test_control_flow_exception_propagates_without_becoming_diagnostic(
    tmp_path: Path,
    failed_stage: FinalizationStage,
    signal_type: type[BaseException],
) -> None:
    # Given a hook raises a process control-flow exception.
    calls: list[FinalizationStage] = []

    def callback(stage: FinalizationStage) -> FinalizationHook:
        def hook(_outcome: TerminalOutcome) -> RunArtifactUpdate:
            calls.append(stage)
            if stage is failed_stage:
                raise signal_type()
            return EMPTY_ARTIFACT_UPDATE

        return hook

    hooks = FinalizationHooks.from_mapping(
        {stage: callback(stage) for stage in FinalizationStage.callback_stages()}
    )

    # When finalization reaches that hook, the control-flow signal escapes.
    with pytest.raises(signal_type):
        _ = finalize_run(finalization_request(tmp_path, FinalizerScenario(hooks=hooks)))

    # Then only bookkeeping through the interrupted stage occurred.
    expected = list(FinalizationStage.callback_stages())
    assert calls == expected[: expected.index(failed_stage) + 1]
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.parametrize("invalid_path_kind", ["missing", "directory", "outside"])
def test_invalid_sidecar_path_is_diagnostic_and_never_claimed(
    tmp_path: Path,
    invalid_path_kind: str,
) -> None:
    # Given a hook update names a missing, directory, or out-of-report sidecar.
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    directory = report_dir / "directory"
    directory.mkdir()
    outside = tmp_path / "outside.json"
    _ = outside.write_text("outside", encoding="utf-8")
    candidates = {
        "missing": report_dir / "missing.json",
        "directory": directory,
        "outside": outside,
    }
    calls: list[FinalizationStage] = []

    def callback(stage: FinalizationStage) -> FinalizationHook:
        def hook(_outcome: TerminalOutcome) -> RunArtifactUpdate:
            calls.append(stage)
            if stage is FinalizationStage.EVIDENCE_REPLAY:
                return RunArtifactUpdate(
                    telemetry_paths=(
                        ("invalid_json", str(candidates[invalid_path_kind])),
                    )
                )
            return EMPTY_ARTIFACT_UPDATE

        return hook

    hooks = FinalizationHooks.from_mapping(
        {stage: callback(stage) for stage in FinalizationStage.callback_stages()}
    )

    # When finalization validates the update before merging it.
    result = finalize_run(
        finalization_request(report_dir, FinalizerScenario(hooks=hooks))
    )

    # Then cleanup continues and no invalid path appears as successful evidence.
    assert calls == list(FinalizationStage.callback_stages())
    assert result.outcome is TerminalOutcome.PASSED
    assert result.exit_code == 0
    assert "invalid_json" not in result.summary.telemetry_paths
    assert [item.stage for item in result.diagnostics] == [
        FinalizationStage.EVIDENCE_REPLAY
    ]
