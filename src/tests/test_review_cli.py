from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
from typing import ClassVar

from pydantic import BaseModel, ConfigDict
import pytest

from core.config import _read_yaml
from core.review_policy import ReviewCliOverrides

from tests.review_policy_resolution_cases import (
    test_explicit_review_policy_wins_over_workflow_and_framework,
    test_framework_maximum_rejects_coerced_nonintegers,
    test_framework_review_defaults_are_unwrapped,
    test_framework_validation_error_hides_rejected_input,
    test_omitted_subworkflow_maximum_reaches_framework_fallback,
    test_params_selected_subworkflow_takes_executor_precedence,
    test_review_policy_falls_back_to_strict_literals,
    test_review_policy_uses_framework_when_workflow_is_unset,
    test_review_policy_uses_materialized_workflow_before_framework,
    test_review_policy_updates_review_without_changing_phase_five,
    test_selected_subworkflow_maximum_wins_materialized_global,
    test_workflow_defaults_follow_each_materialized_workflow,
    test_workflow_defaults_use_selected_subworkflow_when_global_is_unset,
)


__all__ = (
    "test_explicit_review_policy_wins_over_workflow_and_framework",
    "test_framework_maximum_rejects_coerced_nonintegers",
    "test_framework_review_defaults_are_unwrapped",
    "test_framework_validation_error_hides_rejected_input",
    "test_omitted_subworkflow_maximum_reaches_framework_fallback",
    "test_params_selected_subworkflow_takes_executor_precedence",
    "test_review_policy_falls_back_to_strict_literals",
    "test_review_policy_uses_framework_when_workflow_is_unset",
    "test_review_policy_uses_materialized_workflow_before_framework",
    "test_review_policy_updates_review_without_changing_phase_five",
    "test_selected_subworkflow_maximum_wins_materialized_global",
    "test_workflow_defaults_follow_each_materialized_workflow",
    "test_workflow_defaults_use_selected_subworkflow_when_global_is_unset",
)


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parent


class _ParsedReviewArguments(argparse.Namespace):
    max_review_iter: list[int] | None = None
    review_fail_closed: bool | None = None


class _SelectorGlobals(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    max_repair_iterations: int
    review_gate_enabled: bool
    max_review_iterations: int


class _SelectorOverrides(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    globals: _SelectorGlobals


class _SelectorConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    overrides: _SelectorOverrides


def _load_selector(path: Path) -> _SelectorConfig:
    return _SelectorConfig.model_validate(_read_yaml(path))


def _wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.removesuffix(":").lower()
    suffix = resolved.as_posix().split(":", maxsplit=1)[1]
    return f"/mnt/{drive}{suffix}"


def _run_launcher(
    script_name: str,
    project_dir: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    shell_root = project_dir.parent / "shell-runtime"
    shell_scripts = shell_root / "src" / "scripts"
    shell_scripts.mkdir(parents=True, exist_ok=True)
    for source in (SRC_ROOT / "scripts").glob("run_*.sh"):
        normalized = source.read_text(encoding="utf-8").replace("\r\n", "\n")
        with (shell_scripts / source.name).open(
            "w", encoding="utf-8", newline="\n"
        ) as destination:
            _ = destination.write(normalized)
    diagnostics_dir = shell_root / "scripts"
    diagnostics_dir.mkdir(exist_ok=True)
    _ = shutil.copyfile(
        REPO_ROOT / "scripts" / "diagnose_seam_opencode.py",
        diagnostics_dir / "diagnose_seam_opencode.py",
    )
    return subprocess.run(
        [
            "bash",
            _wsl_path(shell_scripts / script_name),
            _wsl_path(project_dir),
            *arguments,
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )


def _build_review_parser() -> argparse.ArgumentParser:
    from core.review_policy import add_review_policy_arguments

    parser = argparse.ArgumentParser()
    add_review_policy_arguments(parser)
    return parser


def _parse_review_args(*arguments: str) -> ReviewCliOverrides:
    from core.review_policy import review_cli_overrides_from_namespace

    parser = _build_review_parser()
    namespace = _ParsedReviewArguments()
    _ = parser.parse_args(list(arguments), namespace=namespace)
    return review_cli_overrides_from_namespace(namespace, parser)


def test_public_no_review_remains_disabled(tmp_path: Path) -> None:
    # Given a valid project and the compatibility review opt-out.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # When the public V3 launcher renders its effective command.
    completed = _run_launcher("run_seam.sh", project_dir, "--no-review")

    # Then review stays disabled without changing launcher success.
    assert completed.returncode == 0, completed.stderr
    assert "--review-gate" not in completed.stdout


def test_public_max_iter_controls_only_phase_five(tmp_path: Path) -> None:
    # Given a valid project and an explicit Phase 5 repair maximum.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # When the public launcher renders the direct V3 command.
    completed = _run_launcher("run_seam.sh", project_dir, "--max-iter", "11")

    # Then the existing value is forwarded through the Phase 5 option.
    assert completed.returncode == 0, completed.stderr
    assert "--max-phase5-iter 11" in completed.stdout


def test_selector_existing_review_and_repair_defaults_are_preserved() -> None:
    # Given the active production selector before Task 7 policy injection.
    selector_path = SRC_ROOT / "workflows" / "seam_auto_default.yaml"

    # When its explicit global overrides are loaded.
    globals_config = _load_selector(selector_path).overrides.globals

    # Then review remains opt-in and Phase 5 keeps its independent maximum.
    assert globals_config.review_gate_enabled is False
    assert globals_config.max_repair_iterations == 8


def test_v2_no_review_and_phase_five_forwarding_are_unchanged(
    tmp_path: Path,
) -> None:
    # Given a valid project and existing V2 compatibility options.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # When the V2 launcher renders its command.
    completed = _run_launcher(
        "run_e2e_v2.sh",
        project_dir,
        "--no-review",
        "--max-iter",
        "10",
    )

    # Then V2 preserves its own review opt-out and Phase 5 forwarding.
    assert completed.returncode == 0, completed.stderr
    assert "--max-phase5-iter 10" in completed.stdout
    assert "--review-gate" not in completed.stdout
    assert "--max-review-iter" not in completed.stdout
    assert "--review-fail-closed" not in completed.stdout
    assert "--no-review-fail-closed" not in completed.stdout


def test_v3_review_policy_is_unset_until_materialization() -> None:
    # Given no explicit review-policy arguments.
    # When direct V3 argparse parses its defaults.
    arguments = _parse_review_args()

    # Then both policy values retain an explicit unset sentinel.
    assert arguments.max_iterations is None
    assert arguments.fail_closed is None


def test_v3_review_policy_accepts_explicit_overrides() -> None:
    # Given explicit review maximum and compatibility policy arguments.
    # When direct V3 argparse parses the values.
    arguments = _parse_review_args("--max-review-iter", "5", "--no-review-fail-closed")

    # Then the typed boundary retains both explicit values.
    assert arguments.max_iterations == 5
    assert arguments.fail_closed is False


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "not-an-integer", "", "+1", "1_0", "01", " 1"],
)
def test_v3_review_maximum_rejects_invalid_values(value: str) -> None:
    # Given an invalid review maximum.
    # When direct V3 argparse parses the boundary value.
    with pytest.raises(SystemExit) as raised:
        _ = _parse_review_args("--max-review-iter", value)

    # Then argparse rejects it deterministically.
    assert raised.value.code == 2


def test_v3_review_maximum_rejects_missing_value() -> None:
    # Given a review maximum option without its required value.
    # When direct V3 argparse parses the incomplete arguments.
    with pytest.raises(SystemExit) as raised:
        _ = _parse_review_args("--max-review-iter")

    # Then argparse rejects it deterministically.
    assert raised.value.code == 2


def test_v3_review_maximum_rejects_repeated_values() -> None:
    # Given repeated review maximum options, even with matching values.
    # When direct V3 argparse parses the ambiguous repetition.
    with pytest.raises(SystemExit) as raised:
        _ = _parse_review_args("--max-review-iter", "4", "--max-review-iter", "4")

    # Then the chosen one-occurrence policy rejects the invocation.
    assert raised.value.code == 2


def test_v3_review_fail_closed_rejects_conflict() -> None:
    # Given both sides of the fail-closed policy switch.
    # When direct V3 argparse parses the conflict.
    with pytest.raises(SystemExit) as raised:
        _ = _parse_review_args("--review-fail-closed", "--no-review-fail-closed")

    # Then the mutually exclusive policy is rejected.
    assert raised.value.code == 2


def test_v3_review_fail_closed_duplicate_is_idempotent() -> None:
    # Given the same fail-closed policy switch twice.
    # When direct V3 argparse parses the duplicate.
    arguments = _parse_review_args("--review-fail-closed", "--review-fail-closed")

    # Then the repeated identical policy retains its single meaning.
    assert arguments.fail_closed is True


def test_v3_review_maximum_repeat_state_is_per_invocation() -> None:
    from core.review_policy import review_cli_overrides_from_namespace

    # Given one parser reused for two independent valid invocations.
    parser = _build_review_parser()
    first = _ParsedReviewArguments()
    second = _ParsedReviewArguments()

    # When each invocation supplies one review maximum.
    _ = parser.parse_args(["--max-review-iter", "4"], namespace=first)
    _ = parser.parse_args(["--max-review-iter", "5"], namespace=second)
    first_overrides = review_cli_overrides_from_namespace(first, parser)
    second_overrides = review_cli_overrides_from_namespace(second, parser)

    # Then prior parser use does not become a repeated-value error.
    assert first_overrides.max_iterations == 4
    assert second_overrides.max_iterations == 5


def test_public_and_direct_v3_forward_explicit_review_policy(
    tmp_path: Path,
) -> None:
    # Given a valid project and explicit review policy.
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    policy = ("--max-review-iter", "5", "--no-review-fail-closed")

    # When both launcher surfaces render their direct V3 commands.
    public = _run_launcher("run_seam.sh", project_dir, *policy)
    direct = _run_launcher("run_e2e_v3.sh", project_dir, *policy)

    # Then both preserve the maximum and compatibility policy.
    assert public.returncode == direct.returncode == 0
    for output in (public.stdout, direct.stdout):
        assert "--max-review-iter 5" in output
        assert "--no-review-fail-closed" in output


def test_public_v3_rejects_repeated_review_maximum(tmp_path: Path) -> None:
    # Given a valid project and repeated review maximum options.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # When the public launcher parses the ambiguous invocation.
    completed = _run_launcher(
        "run_seam.sh",
        project_dir,
        "--max-review-iter",
        "4",
        "--max-review-iter",
        "4",
    )

    # Then it rejects the invocation before launching V3.
    assert completed.returncode != 0


@pytest.mark.parametrize(
    "policy",
    [
        ("--max-review-iter",),
        ("--max-review-iter", "0"),
        ("--max-review-iter", "-1"),
        ("--max-review-iter", "not-an-integer"),
        ("--max-review-iter", ""),
        ("--review-fail-closed", "--no-review-fail-closed"),
    ],
)
def test_public_v3_rejects_invalid_review_policy(
    tmp_path: Path,
    policy: tuple[str, ...],
) -> None:
    # Given a valid project and malformed or conflicting review policy.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # When the public launcher parses the invocation.
    completed = _run_launcher("run_seam.sh", project_dir, *policy)

    # Then it fails before rendering a V3 command.
    assert completed.returncode != 0
    assert "Would execute:" not in completed.stdout


def test_direct_v3_unset_review_policy_forwards_nothing(tmp_path: Path) -> None:
    # Given a valid project without review policy overrides.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # When the direct launcher renders its V3 command.
    completed = _run_launcher("run_e2e_v3.sh", project_dir)
    rendered_command = completed.stdout.split("Would execute:", maxsplit=1)[1]

    # Then no review policy option masks materialized workflow values.
    assert completed.returncode == 0, completed.stderr
    assert "--max-review-iter" not in rendered_command
    assert "--review-fail-closed" not in rendered_command
    assert "--no-review-fail-closed" not in rendered_command


def test_active_selector_review_maximum_defaults_to_three() -> None:
    # Given the active production selector.
    selector_path = SRC_ROOT / "workflows" / "seam_auto_default.yaml"

    # When its materialized global overrides are inspected.
    selector = _load_selector(selector_path)

    # Then the effective review maximum is three.
    assert selector.overrides.globals.max_review_iterations == 3
