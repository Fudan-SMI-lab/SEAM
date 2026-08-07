from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run_cpython38(script: str) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None
    environment = dict(os.environ)
    environment.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    return subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--python",
            "3.8",
            "--with",
            "pydantic==2.8.2",
            "--with",
            "typing_extensions>=4.12",
            "python",
            "-c",
            script,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.integration
@pytest.mark.slow
def test_real_task5_finalizer_executes_resource_hook_on_cpython_38() -> None:
    # Given the declared minimum runtime imports the public Task 5 facade.
    uv = shutil.which("uv")
    assert uv is not None
    script = textwrap.dedent(
        """
        import tempfile
        import subprocess
        from pathlib import Path

        import harness.run as run
        from core.resource_manifest import (
            BackendFactRequest,
            OpenCodeFactRequest,
            ResourceManifestContext,
            ResourceManifestIdentity,
            ResourceManifestStore,
            build_backend_facts,
            build_initial_manifest,
            build_opencode_facts,
        )

        assert hasattr(run, "finalize_run")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "run-task5-py38"
            report.mkdir()
            manifest_identity = ResourceManifestIdentity(
                run_id="run-task5-py38",
                workflow_digest="a" * 64,
                workspace_digest="b" * 64,
            )
            context = ResourceManifestContext.bind(report, manifest_identity)
            launcher = context.capture_launcher()
            facts = (
                launcher.facts
                + build_backend_facts(BackendFactRequest(
                    requested_workflow="wf.yaml",
                    effective_workflow="wf.yaml",
                    requested_backend="local",
                    effective_backend="local",
                ))
                + build_opencode_facts(OpenCodeFactRequest(
                    endpoint="http://127.0.0.1:4096",
                    owner_kind="framework",
                ))
            )
            store = ResourceManifestStore.create(
                context,
                build_initial_manifest(
                    manifest_identity, facts, (launcher.receipt,)
                ),
            )
            outcome = run.RunOutcome(run.TerminalOutcome.PASSED)
            request = run.RunFinalizationRequest(
                identity=run.RunIdentity(
                    run_id="run-task5-py38",
                    base_url="http://127.0.0.1:4096",
                    workflow_path="wf.yaml",
                    output_dir=str(report),
                    temp_dir=str(root),
                ),
                execution=run.RunExecution(
                    keep_temp_dir=False,
                    requested_max_phase5_iter=1,
                    effective_max_phase5_iter=1,
                    phases=(),
                    session_count=1,
                    command_count=1,
                    total_duration_seconds=0.1,
                    errors=(),
                ),
                initial_artifacts=run.RunArtifacts(),
                hooks=run.FinalizationHooks(
                    post_cleanup_manifest=run.resource_manifest_finalization_hook(store)
                ),
                authoritative_outcome=outcome,
            )
            result = run.finalize_run(request)
            assert result.outcome is run.TerminalOutcome.PASSED
            assert result.summary.telemetry_paths["resource_manifest_json"] == str(store.path)
            assert store.read().sealed is True
            assert run.RunArtifactUpdate().telemetry_paths == ()
        print("TASK5_REAL_FINALIZER_PY38=PASS")
        """
    )
    # When the real facade finalizes through the Task 16 hook.
    result = _run_cpython38(script)

    # Then no local hook stand-ins or incompatible eager imports remain.
    assert result.returncode == 0, result.stderr
    assert "TASK5_REAL_FINALIZER_PY38=PASS" in result.stdout


@pytest.mark.integration
@pytest.mark.slow
def test_py38_finalizer_isolates_ordinary_exceptions_and_propagates_controls() -> None:
    # Given the real Python 3.8 facade and every finalization hook stage.
    script = textwrap.dedent(
        """
        import tempfile
        from pathlib import Path

        import harness.run as run

        class OrdinaryHookError(Exception):
            pass

        stages = tuple(stage.value for stage, _hook in run.FinalizationHooks().ordered())
        ordinary_types = (TypeError, KeyError, OrdinaryHookError)
        outcomes = (run.TerminalOutcome.PASSED, run.TerminalOutcome.FAILED)

        def request_for(report, outcome, hooks):
            return run.RunFinalizationRequest(
                identity=run.RunIdentity(
                    run_id="callback-matrix",
                    base_url="http://127.0.0.1:4096",
                    workflow_path="wf.yaml",
                    output_dir=str(report),
                    temp_dir=str(report.parent),
                ),
                execution=run.RunExecution(
                    keep_temp_dir=False,
                    requested_max_phase5_iter=1,
                    effective_max_phase5_iter=1,
                    phases=(),
                    session_count=1,
                    command_count=1,
                    total_duration_seconds=0.1,
                    errors=(),
                ),
                initial_artifacts=run.RunArtifacts(),
                hooks=hooks,
                authoritative_outcome=run.RunOutcome(outcome),
            )

        def hooks_for(failing_stage, failure_type, calls):
            def hook(stage):
                def invoke(_outcome):
                    calls.append(stage)
                    if stage == failing_stage:
                        raise failure_type("ordinary failure")
                    return run.RunArtifactUpdate()
                return invoke
            return run.FinalizationHooks(**{stage: hook(stage) for stage in stages})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ordinary_count = 0
            for outcome in outcomes:
                for failing_stage in stages:
                    for failure_type in ordinary_types:
                        report = root / f"ordinary-{ordinary_count}"
                        report.mkdir()
                        calls = []
                        result = run.finalize_run(
                            request_for(
                                report,
                                outcome,
                                hooks_for(failing_stage, failure_type, calls),
                            )
                        )
                        assert calls == list(stages)
                        assert tuple(
                            (item.stage.value, item.error_type)
                            for item in result.diagnostics
                        ) == ((failing_stage, failure_type.__name__),)
                        expected_exit = 1 if outcome is run.TerminalOutcome.FAILED else 0
                        assert result.exit_code == expected_exit
                        assert result.summary.overall_status == (
                            "FAIL" if expected_exit else "PASS"
                        )
                        if stages.index(failing_stage) < stages.index("authorized_cleanup"):
                            assert "authorized_cleanup" in calls
                        ordinary_count += 1

            control_count = 0
            for failing_stage in stages:
                for signal_type in (KeyboardInterrupt, SystemExit):
                    report = root / f"control-{control_count}"
                    report.mkdir()
                    calls = []
                    propagated = False
                    try:
                        run.finalize_run(
                            request_for(
                                report,
                                run.TerminalOutcome.PASSED,
                                hooks_for(failing_stage, signal_type, calls),
                            )
                        )
                    except signal_type:
                        propagated = True
                    assert propagated
                    assert calls[-1] == failing_stage
                    control_count += 1

        print(f"ORDINARY_MATRIX={ordinary_count}")
        print(f"CONTROL_MATRIX={control_count}")
        """
    )

    # When ordinary and control failures execute under actual CPython 3.8.
    result = _run_cpython38(script)

    # Then 24 ordinary cases isolate and 8 BaseException controls propagate.
    assert result.returncode == 0, result.stderr
    assert "ORDINARY_MATRIX=24" in result.stdout
    assert "CONTROL_MATRIX=8" in result.stdout
