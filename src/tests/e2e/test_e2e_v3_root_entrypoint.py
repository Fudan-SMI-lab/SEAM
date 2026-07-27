from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

from harness.run import copy_run_artifacts

from .e2e_v3_summary_cases import (
    test_v3_summary_bytes_and_exit_mapping_are_stable as test_v3_summary_bytes_and_exit_mapping_are_stable,
)


MIGRATION_UTILS_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_ROOT = MIGRATION_UTILS_ROOT.parent


def test_root_module_v3_entrypoint_shows_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--workflow-path" in completed.stdout
    assert "--project-dir" in completed.stdout
    assert "--opencode-readiness" in completed.stdout
    assert "--opencode-message-timeout" in completed.stdout


def test_root_module_v3_entrypoint_propagates_parser_failure() -> None:
    # Given an invalid argument passed through the public root wrapper.
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--unknown-option"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then the wrapper preserves argparse's child-process failure status.
    assert completed.returncode == 2, completed.stderr
    assert "unrecognized arguments: --unknown-option" in completed.stderr


def test_migration_utils_v3_module_shows_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=MIGRATION_UTILS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--workflow-path" in completed.stdout
    assert "V3" in completed.stdout or "custom workflow path" in completed.stdout


def test_v3_parser_accepts_workflow_path() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--workflow-path" in completed.stdout


def test_v2_entrypoint_unaffected() -> None:
    """Verify V2 was NOT modified: no --workflow-path and --project-dir present.

    Checks source files directly since V2 --help subprocess fails due to
    pre-existing sqlite3 absence in this Python 3.10 build (conftest stub
    does not apply to subprocess invocations).
    """
    v2_inner = Path(__file__).resolve().parents[2] / "tests" / "e2e" / "e2e_test_v2.py"
    v2_core = v2_inner.read_text(encoding="utf-8")
    assert "--workflow-path" not in v2_core, "V2 must not have --workflow-path"
    assert "--project-dir" in v2_core, "V2 must still have --project-dir"

    v2_wrapper = (
        Path(__file__).resolve().parents[3] / "tests" / "e2e" / "e2e_test_v2.py"
    )
    assert v2_wrapper.exists(), "V2 root wrapper must exist"

    v2_shell = Path(__file__).resolve().parents[2] / "scripts" / "run_e2e_v2.sh"
    assert v2_shell.exists(), "V2 shell wrapper must exist"
    shell_content = v2_shell.read_text(encoding="utf-8")
    assert "--workflow" not in shell_content, "V2 shell must not have --workflow"


# ── new: --server-no-auto-start and parser regressions ──


def test_v3_parser_accepts_server_no_auto_start() -> None:
    """Verify V3 (e2e_test_v3.py) --server-no-auto-start is a recognized flag."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.e2e.e2e_test_v3",
            "--server-no-auto-start",
            "--help",
        ],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"


def test_v3_parser_server_url_default_is_none() -> None:
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "from tests.e2e.e2e_test_v3 import build_parser; "
        "p = build_parser(); "
        "defaults = {a.dest: a.default for a in p._actions}; "
        "print(defaults.get('server_url'))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"
    assert completed.stdout.strip() == "None", (
        f"Expected None, got {completed.stdout.strip()!r}"
    )


@pytest.mark.parametrize("expected_exit", [0, 1])
def test_v3_main_return_becomes_process_exit(expected_exit: int) -> None:
    # Given a V3 coordinator returning a frozen finalization exit code.
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "from tests.e2e import e2e_test_v3 as target; "
        f"target.run_e2e_v3 = lambda **kwargs: {expected_exit}; "
        "sys.argv = ['e2e_test_v3']; raise SystemExit(target.main())"
    )

    # When Python executes the real module main boundary.
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then the process exit exactly matches the coordinator result.
    assert completed.returncode == expected_exit, completed.stderr


@pytest.mark.parametrize(
    ("authority_passes", "expected_exit", "expected_status"),
    [(True, 0, "PASS"), (False, 1, "FAIL")],
)
def test_frozen_outcome_controls_real_summary_and_process_exit(
    tmp_path: Path,
    authority_passes: bool,
    expected_exit: int,
    expected_status: str,
) -> None:
    # Given
    child = textwrap.dedent(
        """
        import sys
        from pathlib import Path

        sys.path.insert(0, "src")

        from core.run_outcome import (
            PhaseId,
            ReviewOutcome,
            RunOutcome,
            TerminalAnchor,
            WorkflowTerminal,
        )
        from harness.run import finalize_run
        from tests.e2e import e2e_test_v3 as target
        from tests.run_finalizer_test_support import (
            FinalizerScenario,
            finalization_request,
        )

        authority_passes = sys.argv[2] == "PASS"
        outcome = RunOutcome(
            validation_succeeded=authority_passes,
            review_outcome=ReviewOutcome.DISABLED,
            review_fail_closed=True,
            workflow_terminal=WorkflowTerminal("complete"),
            terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_0_env_detect")),
            executed_phases=(PhaseId("phase_0_env_detect"),),
            accepted_attempt_id=None,
            review_rounds=(),
        )
        scenario = FinalizerScenario(
            status="failed" if authority_passes else "passed",
            errors=("contradictory display failure",) if authority_passes else (),
            authoritative_outcome=outcome,
        )
        result = finalize_run(finalization_request(Path(sys.argv[1]), scenario))
        target.print_summary(result.summary)
        raise SystemExit(result.exit_code)
        """
    )

    # When
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(tmp_path),
            "PASS" if authority_passes else "FAIL",
        ],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then
    assert completed.returncode == expected_exit, completed.stderr
    assert f"E2E {expected_status}" in completed.stdout
    summary_text = (tmp_path / "summary.json").read_text(encoding="utf-8")
    assert f'"overall_status": "{expected_status}"' in summary_text


def test_v3_artifact_copy_refuses_stale_destination(tmp_path: Path) -> None:
    # Given source artifacts and an existing report artifact namespace.
    source_dir = tmp_path / "work" / ".sm-artifacts"
    source_dir.mkdir(parents=True)
    _ = (source_dir / "new.txt").write_text("new", encoding="utf-8")
    output_dir = tmp_path / "report"
    destination = output_dir / ".sm-artifacts"
    destination.mkdir(parents=True)
    sentinel = destination / "existing.txt"
    _ = sentinel.write_text("existing", encoding="utf-8")

    # When artifact persistence encounters the stale destination.
    with pytest.raises(FileExistsError):
        _ = copy_run_artifacts(source_dir.parent, output_dir)

    # Then existing evidence is never overwritten.
    assert sentinel.read_text(encoding="utf-8") == "existing"
    assert not (destination / "new.txt").exists()


def test_v3_artifact_copy_cleans_interrupted_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a copy operation interrupted after writing one staged file.
    source_dir = tmp_path / "work" / ".sm-artifacts"
    source_dir.mkdir(parents=True)
    output_dir = tmp_path / "report"
    output_dir.mkdir()

    def interrupt_copy(_source: Path, staging: Path) -> Path:
        staging.mkdir()
        _ = (staging / "partial.txt").write_text("partial", encoding="utf-8")
        raise shutil.Error("copy interrupted")

    monkeypatch.setattr(shutil, "copytree", interrupt_copy)

    # When artifact persistence receives the interruption.
    with pytest.raises(shutil.Error, match="copy interrupted"):
        _ = copy_run_artifacts(source_dir.parent, output_dir)

    # Then neither a claimed destination nor staging debris remains.
    assert not (output_dir / ".sm-artifacts").exists()
    assert list(output_dir.glob(".sm-artifacts.*.tmp")) == []
