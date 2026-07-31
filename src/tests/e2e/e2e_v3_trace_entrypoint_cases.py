from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.terminal_continuation_models import (
    TerminalContinuationRunRequest,
    V3InvocationOptions,
    V3OpenCodeOptions,
    V3ReviewRunOptions,
    V3ServerRunOptions,
)

from . import e2e_test_v3 as target

SRC_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SRC_ROOT.parent


def test_v3_public_help_exposes_trace_policy() -> None:
    # Given the checked-out root Python entrypoint.
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then both explicit V3 policy forms are public.
    assert completed.returncode == 0, completed.stderr
    assert "--save-agent-trace" in completed.stdout
    assert "--no-save-agent-trace" in completed.stdout


def test_legacy_and_v2_entrypoints_exclude_trace_policy() -> None:
    # Given every legacy and V2 shell/Python entrypoint.
    legacy_paths = (
        SRC_ROOT / "scripts" / "run_e2e.sh",
        SRC_ROOT / "scripts" / "run_e2e_v2.sh",
        SRC_ROOT / "tests" / "e2e" / "e2e_test.py",
        SRC_ROOT / "tests" / "e2e" / "e2e_test_v2.py",
    )

    # Then Task 22's V3 flags are absent from every older surface.
    for legacy_path in legacy_paths:
        content = legacy_path.read_text(encoding="utf-8")
        assert "--save-agent-trace" not in content
        assert "--no-save-agent-trace" not in content


def test_v3_parser_preserves_default_on_and_explicit_off() -> None:
    # Given the public V3 Python parser.
    parser = target.build_parser()

    # When trace policy is omitted or explicitly selected.
    omitted = parser.parse_args(["--project-dir", "."])
    enabled = parser.parse_args(["--project-dir", ".", "--save-agent-trace"])
    disabled = parser.parse_args(["--project-dir", ".", "--no-save-agent-trace"])

    # Then default-off remains distinguishable and contradictions fail.
    assert omitted.save_agent_trace is None
    assert enabled.save_agent_trace is True
    assert disabled.save_agent_trace is False
    with pytest.raises(SystemExit) as conflict:
        _ = parser.parse_args(
            [
                "--project-dir",
                ".",
                "--save-agent-trace",
                "--no-save-agent-trace",
            ]
        )
    assert conflict.value.code == 2


@pytest.mark.parametrize(
    ("mode", "flag", "expected"),
    [
        ("normal", "--save-agent-trace", True),
        ("normal", "--no-save-agent-trace", False),
        ("continuation", "--save-agent-trace", True),
        ("continuation", "--no-save-agent-trace", False),
    ],
)
def test_v3_main_forwards_trace_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    flag: str,
    expected: bool,
) -> None:
    # Given a public normal or continuation invocation with an explicit policy.
    captured: list[bool | None] = []
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")

    def normal(**values: bool | Path | str | None) -> int:
        value = values["save_agent_trace"]
        assert isinstance(value, bool)
        captured.append(value)
        return 0

    def continuation(request: TerminalContinuationRunRequest, **_values: str) -> int:
        captured.append(request.invocation.save_agent_trace)
        return 0

    monkeypatch.setattr(target, "run_e2e_v3", normal)
    monkeypatch.setattr(target, "run_terminal_continuation", continuation)
    run_mode = (
        ["--project-dir", str(tmp_path)]
        if mode == "normal"
        else ["--continue-from", str(summary)]
    )
    monkeypatch.setattr(sys, "argv", ["e2e_test_v3", *run_mode, flag])

    # When the Python CLI dispatches.
    exit_code = target.main()

    # Then the exact tri-state policy reaches the selected coordinator.
    assert exit_code == 0
    assert captured == [expected]


def test_terminal_continuation_forwards_trace_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core import terminal_continuation as lifecycle

    # Given a prepared child and explicit enabled trace policy.
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")
    captured: list[bool | None] = []

    @contextmanager
    def prepare(_summary: Path, _child_run_id: str) -> Iterator[str]:
        yield "prepared-child"

    def child_runner(**values: bool | Path | str | None) -> int:
        value = values["save_agent_trace"]
        assert isinstance(value, bool)
        captured.append(value)
        return 0

    monkeypatch.setattr(lifecycle, "prepare_terminal_continuation", prepare)
    monkeypatch.setattr(target, "run_e2e_v3", child_runner)
    request = TerminalContinuationRunRequest(
        summary_path=summary,
        server=V3ServerRunOptions(None, False, 0),
        review=V3ReviewRunOptions(5, False, None),
        invocation=V3InvocationOptions(True, None, "", None, save_agent_trace=True),
        opencode=V3OpenCodeOptions("off", 1),
    )

    # When continuation dispatches the child run.
    exit_code = target.run_terminal_continuation(request)

    # Then the child receives the same explicit policy.
    assert exit_code == 0
    assert captured == [True]
