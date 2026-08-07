from __future__ import annotations

import textwrap

import pytest

from tests.task5_py38_compat_cases import _run_cpython38


@pytest.mark.integration
@pytest.mark.slow
def test_py38_required_stage_rejected_output_fails_closed() -> None:
    script = textwrap.dedent(
        """
        import tempfile
        import subprocess
        from pathlib import Path

        import harness.run as run

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            outside_file = outside / "snapshot.json"
            outside_file.write_text("outside", encoding="utf-8")
            cases = (
                "directory_paths",
                "artifact_dir",
                "before_snapshot_path",
                "after_snapshot_path",
                "stale_directory",
                "nested_junction",
            )
            for index, kind in enumerate(cases):
                report = root / f"child-{index}"
                report.mkdir()
                stale = report / "stale"
                stale.mkdir()
                claimed = report / "claimed"
                claimed.mkdir()
                if kind == "nested_junction":
                    junction = claimed / "outside-junction"
                    created = subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    assert created.returncode == 0, created.stderr

                def invalid_seal(_outcome, selected=kind):
                    if selected == "directory_paths":
                        return run.RunArtifactUpdate(
                            directory_paths=(("sealed", str(outside)),)
                        )
                    if selected == "artifact_dir":
                        return run.RunArtifactUpdate(artifact_dir=str(outside))
                    if selected == "before_snapshot_path":
                        return run.RunArtifactUpdate(
                            before_snapshot_path=str(outside_file)
                        )
                    if selected == "after_snapshot_path":
                        return run.RunArtifactUpdate(
                            after_snapshot_path=str(outside_file)
                        )
                    if selected == "nested_junction":
                        return run.RunArtifactUpdate(artifact_dir=str(claimed))
                    return run.RunArtifactUpdate(
                        directory_paths=(("sealed", str(stale)),)
                    )

                request = run.RunFinalizationRequest(
                    identity=run.RunIdentity(
                        run_id=f"child-py38-{index}",
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
                        session_count=0,
                        command_count=0,
                        total_duration_seconds=0.0,
                        errors=(),
                    ),
                    initial_artifacts=run.RunArtifacts(),
                    hooks=run.FinalizationHooks(trace_export=invalid_seal),
                    authoritative_outcome=run.RunOutcome(run.TerminalOutcome.PASSED),
                    required_stages=frozenset({run.FinalizationStage.TRACE_EXPORT}),
                    summary_required=True,
                )
                result = run.finalize_run(request)
                assert result.exit_code == 1, kind
                assert result.finalization_failed is True, kind
                assert result.summary_path is None, kind
                assert not (report / "summary.json").exists(), kind
        print("REQUIRED_REJECTED_OUTPUT_PY38=PASS")
        """
    )

    result = _run_cpython38(script)

    assert result.returncode == 0, result.stderr
    assert "REQUIRED_REJECTED_OUTPUT_PY38=PASS" in result.stdout
