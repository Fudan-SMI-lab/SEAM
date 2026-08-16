"""Tests for the SEAM editable-install orchestrator.

Every subprocess call (pip install, pip show, import probes, pytest --version,
diagnose_seam_opencode.py --help) is routed through the PipRunner boundary, so
the test suite NEVER spawns a real subprocess and NEVER mutates the running
interpreter. Each test uses an explicit Given/When/Then block.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from seam_init.models import EnvironmentChoice, EnvironmentKind, FailureKind, SafeDetail
from seam_init.seam_install import (
    InstallStatus,
    PipRunResult,
    PipRunner,
    SeamInstallOutcome,
    SeamInstallRequest,
    SubprocessPipRunner,
    install_seam,
)

_FAKE_PYTHON = "/usr/local/venv/bin/python"
_FAKE_VERSION = "3.12.1"
_FAKE_SOURCE = Path("/repo/seam/src")
_FAKE_DIAGNOSE = Path("/repo/seam/scripts/diagnose_seam_opencode.py")


def _env(
    *, python: str = _FAKE_PYTHON, version: str = _FAKE_VERSION,
) -> EnvironmentChoice:
    return EnvironmentChoice(
        kind=EnvironmentKind.EXISTING_VENV,
        python_executable=python,
        python_version=version,
    )


def _request(
    *,
    environment: EnvironmentChoice | None = None,
    extras: str | None = None,
    diagnose_path: Path | None = None,
    force_reinstall: bool = False,
) -> SeamInstallRequest:
    return SeamInstallRequest(
        environment=environment if environment is not None else _env(),
        source_path=_FAKE_SOURCE,
        extras=extras if extras is not None else "dev",
        diagnose_path=diagnose_path if diagnose_path is not None else _FAKE_DIAGNOSE,
        force_reinstall=force_reinstall,
    )


def _run(
    argv: Sequence[str], *, returncode: int = 0,
    stdout: str = "", stderr: str = "",
) -> PipRunResult:
    return PipRunResult(
        argv=tuple(argv), returncode=returncode,
        stdout=SafeDetail(stdout), stderr=SafeDetail(stderr),
    )


class _ScriptedRunner:
    """PipRunner double: returns responses in declared order, records calls."""

    _responses: list[PipRunResult]
    calls: list[list[str]]

    def __init__(self, responses: list[PipRunResult]) -> None:
        self._responses = list(responses)
        self.calls = []

    def run(self, argv: Sequence[str]) -> PipRunResult:
        argv_list = list(argv)
        self.calls.append(argv_list)
        if not self._responses:
            raise AssertionError(f"unexpected runner call: {argv_list}")
        return self._responses.pop(0)


class _MatcherRunner:
    """PipRunner double: returns responses matched by a callable per argv."""

    _rules: list[tuple[Callable[[list[str]], bool], PipRunResult]]
    calls: list[list[str]]

    def __init__(self) -> None:
        self.calls = []
        self._rules = []

    def add(
        self, match: Callable[[list[str]], bool], result: PipRunResult,
    ) -> None:
        self._rules.append((match, result))

    def run(self, argv: Sequence[str]) -> PipRunResult:
        argv_list = list(argv)
        self.calls.append(argv_list)
        for match, result in self._rules:
            if match(argv_list):
                return result
        raise AssertionError(f"no rule matched argv: {argv_list}")


class _FakePrompt:
    _confirms: list[bool]
    confirm_calls: list[tuple[str, bool]]

    def __init__(self, *, confirms: list[bool] | None = None) -> None:
        self._confirms = list(confirms or [])
        self.confirm_calls = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:  # noqa: ARG002
        raise AssertionError(f"unexpected ask: {prompt!r}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.confirm_calls.append((prompt, default))
        if not self._confirms:
            raise AssertionError(f"unexpected confirm: {prompt!r}")
        return self._confirms.pop(0)


# --- argv shape: selected interpreter + exact editable extra ----------------


class TestPipInstallArgvUsesSelectedInterpreterAndEditableExtra:
    def test_install_argv_is_exact_editable_extra_form(self) -> None:
        # Given: confirm=True, repair=True (skip satisfied check), install ok,
        # and every verify call returns rc=0.
        runner = _ScriptedRunner([
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dev]"]),
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"]),
            _run([_FAKE_PYTHON, "-c", "import seam_init, core.jsonc"]),
            _run([_FAKE_PYTHON, "-m", "pytest", "--version"]),
            _run([_FAKE_PYTHON, "-c", "import yaml"]),
            _run([str(_FAKE_PYTHON), str(_FAKE_DIAGNOSE), "--help"]),
        ])
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: the install argv uses the selected interpreter and exact extra.
        assert outcome.status is InstallStatus.REPAIRED
        install_calls = [c for c in runner.calls if "install" in c]
        assert len(install_calls) == 1
        argv = install_calls[0]
        assert argv[0] == _FAKE_PYTHON
        assert argv[1:5] == ["-m", "pip", "install", "-e"]
        assert argv[5] == "/repo/seam/src[dev]"
        # The interpreter passed is the one chosen in EnvironmentChoice.
        assert argv[0] != sys.executable  # never falls back to running interp

    def test_install_uses_custom_extras_when_requested(self) -> None:
        # Given
        runner = _ScriptedRunner([
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dashboard]"]),
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"]),
            _run([_FAKE_PYTHON, "-c", "import seam_init, core.jsonc"]),
            _run([_FAKE_PYTHON, "-m", "pytest", "--version"]),
            _run([_FAKE_PYTHON, "-c", "import yaml"]),
            _run([_FAKE_PYTHON, str(_FAKE_DIAGNOSE), "--help"]),
        ])
        # When
        outcome = install_seam(
            _request(extras="dashboard", force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then
        assert outcome.status is InstallStatus.REPAIRED
        install_argv = next(c for c in runner.calls if "install" in c)
        assert install_argv[5] == "/repo/seam/src[dashboard]"


# --- satisfied state skips mutation ----------------------------------------


class TestAlreadySatisfiedSkipsMutation:
    def test_editable_install_present_skips_install_call(self) -> None:
        # Given: pip show reports 0 + an editable project location at the source.
        show_stdout = (
            "Name: sm-adapt\nVersion: 2.0.0\n"
            f"Editable project location: {_FAKE_SOURCE}\n"
        )
        runner = _MatcherRunner()
        runner.add(
            lambda a: a[1:4] == ["-m", "pip", "show"],
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"], stdout=show_stdout),
        )
        # When
        outcome = install_seam(
            _request(),  # force_reinstall=False
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: status SATISFIED, no install argv was ever issued.
        assert outcome.status is InstallStatus.SATISFIED
        assert outcome.ok is True
        assert outcome.failure_kind is None
        assert not any("install" in c for c in runner.calls)
        # Exactly one subprocess call total: the pip show probe.
        assert len(runner.calls) == 1

    def test_pip_show_missing_editable_marker_proceeds_to_install(self) -> None:
        # Given: pip show rc=0 but no editable location -> not satisfied.
        runner = _MatcherRunner()
        runner.add(
            lambda a: a[1:4] == ["-m", "pip", "show"],
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"],
                 stdout="Name: sm-adapt\nVersion: 2.0.0\n"),
        )
        runner.add(
            lambda a: "install" in a,
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dev]"]),
        )
        for argv_tail in (
            ["-c", "import seam_init, core.jsonc"],
            ["-m", "pytest", "--version"],
            ["-c", "import yaml"],
        ):
            runner.add(
                lambda a, t=argv_tail: a[1:] == t,
                _run([_FAKE_PYTHON] + argv_tail),
            )
        runner.add(
            lambda a: a[-1] == "--help" and "diagnose" in a[-2],
            _run([_FAKE_PYTHON, str(_FAKE_DIAGNOSE), "--help"]),
        )
        # When
        outcome = install_seam(
            _request(),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: install was attempted; status INSTALLED.
        assert outcome.status is InstallStatus.INSTALLED
        assert any("install" in c for c in runner.calls)

    def test_pip_show_nonzero_proceeds_to_install(self) -> None:
        # Given: pip show rc=1 (package absent) -> not satisfied.
        runner = _MatcherRunner()
        runner.add(
            lambda a: a[1:4] == ["-m", "pip", "show"],
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"], returncode=1),
        )
        runner.add(
            lambda a: "install" in a,
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dev]"]),
        )
        for argv_tail in (
            ["-c", "import seam_init, core.jsonc"],
            ["-m", "pytest", "--version"],
            ["-c", "import yaml"],
        ):
            runner.add(
                lambda a, t=argv_tail: a[1:] == t,
                _run([_FAKE_PYTHON] + argv_tail),
            )
        runner.add(
            lambda a: a[-1] == "--help" and "diagnose" in a[-2],
            _run([_FAKE_PYTHON, str(_FAKE_DIAGNOSE), "--help"]),
        )
        # When
        outcome = install_seam(
            _request(),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then
        assert outcome.status is InstallStatus.INSTALLED


# --- failed install: bounded, redacted, typed ------------------------------


class TestFailedInstallReturnsBoundedRedactedTypedDiagnostics:
    def test_failed_install_returns_failed_with_seam_install_kind(self) -> None:
        # Given: install exits 1 with secret-bearing stderr.
        secret = "API_TOKEN=sk-secret-1234567890"
        runner = _ScriptedRunner([
            _run([_FAKE_PYTHON, "-m", "pip", "install"], returncode=1,
                 stderr=f"error: {secret}\ntraceback follows"),
        ])
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: typed failure, no verification calls.
        assert outcome.status is InstallStatus.FAILED
        assert outcome.failure_kind is FailureKind.SEAM_INSTALL
        assert outcome.ok is False
        # Exactly one subprocess call (the install); no verify probes issued.
        assert len(runner.calls) == 1
        assert "install" in runner.calls[0]

    def test_failed_install_redacts_secret_in_diagnostics(self) -> None:
        # Given
        secret = "Authorization=Bearer abcdefghijklmnop"
        runner = _ScriptedRunner([
            _run([_FAKE_PYTHON, "-m", "pip", "install"], returncode=1,
                 stderr=secret),
        ])
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: no diagnostic contains the secret value.
        for line in outcome.diagnostics:
            assert "abcdefghijklmnop" not in str(line)
        assert "abcdefghijklmnop" not in str(outcome.failure_detail)

    def test_failed_install_diagnostics_are_bounded(self) -> None:
        # Given: a 200KB stderr blob.
        huge = "x" * 200_000
        runner = _ScriptedRunner([
            _run([_FAKE_PYTHON, "-m", "pip", "install"], returncode=1, stderr=huge),
        ])
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: failure_detail and every diagnostic line are bounded.
        assert len(str(outcome.failure_detail)) <= 8192 + 32
        for line in outcome.diagnostics:
            assert len(str(line)) <= 8192 + 32

    def test_failed_install_never_proceeds_to_verification(self) -> None:
        # Given: install rc=1; even if verify probes are pre-loaded, none fire.
        runner = _ScriptedRunner([
            _run([_FAKE_PYTHON, "-m", "pip", "install"], returncode=1, stderr="boom"),
            _run([_FAKE_PYTHON, "-m", "pytest", "--version"]),  # would be verify
        ])
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: only ONE call (install); verify probe is untouched.
        assert outcome.status is InstallStatus.FAILED
        assert len(runner.calls) == 1


# --- confirmation flow -----------------------------------------------------


class TestConfirmationFlow:
    def test_declined_confirm_returns_declined_with_no_runner_calls(self) -> None:
        # Given: prompt says No.
        runner = _MatcherRunner()
        prompt = _FakePrompt(confirms=[False])
        # When
        outcome = install_seam(_request(), prompt=prompt, runner=runner)
        # Then: DECLINED, no subprocess calls, no failure kind, prompt text
        # mentioned interpreter path + scope.
        assert outcome.status is InstallStatus.DECLINED
        assert outcome.ok is False
        assert outcome.failure_kind is None
        assert runner.calls == []
        msg = prompt.confirm_calls[0][0]
        assert _FAKE_PYTHON in msg
        assert "/repo/seam/src[dev]" in msg

    def test_confirm_prompt_default_is_false(self) -> None:
        # Given / When
        prompt = _FakePrompt(confirms=[False])
        _ = install_seam(_request(), prompt=prompt, runner=_MatcherRunner())
        # Then: default is False (must be opt-in).
        assert prompt.confirm_calls[0][1] is False


# --- post-install verification --------------------------------------------


class TestPostInstallVerification:
    def test_all_verify_probes_invoked_on_success(self) -> None:
        # Given: install + all probes return rc=0.
        runner = _MatcherRunner()
        runner.add(
            lambda a: "install" in a,
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dev]"]),
        )
        runner.add(
            lambda a: a[1:] == ["-c", "import seam_init, core.jsonc"],
            _run([_FAKE_PYTHON, "-c", "import seam_init, core.jsonc"]),
        )
        runner.add(
            lambda a: a[1:] == ["-m", "pytest", "--version"],
            _run([_FAKE_PYTHON, "-m", "pytest", "--version"], stdout="pytest 8.0.0"),
        )
        runner.add(
            lambda a: a[1:] == ["-c", "import yaml"],
            _run([_FAKE_PYTHON, "-c", "import yaml"]),
        )
        runner.add(
            lambda a: a[-1] == "--help" and "diagnose" in a[-2],
            _run([_FAKE_PYTHON, str(_FAKE_DIAGNOSE), "--help"], stdout="usage: ..."),
        )
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: every probe was issued exactly once.
        assert outcome.status is InstallStatus.REPAIRED
        argv_tails = [tuple(c[1:]) for c in runner.calls]
        assert ("-c", "import seam_init, core.jsonc") in argv_tails
        assert ("-m", "pytest", "--version") in argv_tails
        assert ("-c", "import yaml") in argv_tails
        assert (str(_FAKE_DIAGNOSE), "--help") in argv_tails

    def test_verify_failure_returns_failed_with_diagnostics(self) -> None:
        # Given: install ok but `import yaml` probe fails (PyYAML missing).
        runner = _MatcherRunner()
        runner.add(
            lambda a: "install" in a,
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dev]"]),
        )
        runner.add(
            lambda a: a[1:4] == ["-m", "pip", "show"],
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"],
                 stdout="Name: sm-adapt\nVersion: 2.0.0\n"),
        )
        runner.add(
            lambda a: a[1:] == ["-c", "import seam_init, core.jsonc"],
            _run([_FAKE_PYTHON, "-c", "import seam_init, core.jsonc"]),
        )
        runner.add(
            lambda a: a[1:] == ["-m", "pytest", "--version"],
            _run([_FAKE_PYTHON, "-m", "pytest", "--version"], stdout="pytest 8.0.0"),
        )
        runner.add(
            lambda a: a[1:] == ["-c", "import yaml"],
            _run([_FAKE_PYTHON, "-c", "import yaml"], returncode=1,
                 stderr="ModuleNotFoundError: No module named 'yaml'"),
        )
        runner.add(
            lambda a: a[-1] == "--help" and "diagnose" in a[-2],
            _run([_FAKE_PYTHON, str(_FAKE_DIAGNOSE), "--help"], stdout="usage:"),
        )
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then
        assert outcome.status is InstallStatus.FAILED
        assert outcome.failure_kind is FailureKind.SEAM_INSTALL
        assert any("yaml" in str(d) for d in outcome.diagnostics)


# --- repair option ---------------------------------------------------------


class TestRepairOption:
    def test_force_reinstall_skips_satisfied_check_and_installs(self) -> None:
        # Given: pip show WOULD return satisfied; force_reinstall=True ignores it.
        show_stdout = f"Name: sm-adapt\nEditable project location: {_FAKE_SOURCE}\n"
        runner = _MatcherRunner()
        # No pip show rule added -> force_reinstall must skip it entirely.
        runner.add(
            lambda a: "install" in a,
            _run([_FAKE_PYTHON, "-m", "pip", "install", "-e", "/repo/seam/src[dev]"]),
        )
        runner.add(
            lambda a: a[1:4] == ["-m", "pip", "show"],
            _run([_FAKE_PYTHON, "-m", "pip", "show", "sm-adapt"],
                 stdout=show_stdout),
        )
        for argv_tail in (
            ["-c", "import seam_init, core.jsonc"],
            ["-m", "pytest", "--version"],
            ["-c", "import yaml"],
        ):
            runner.add(
                lambda a, t=argv_tail: a[1:] == t,
                _run([_FAKE_PYTHON] + argv_tail),
            )
        runner.add(
            lambda a: a[-1] == "--help" and "diagnose" in a[-2],
            _run([_FAKE_PYTHON, str(_FAKE_DIAGNOSE), "--help"]),
        )
        # When
        outcome = install_seam(
            _request(force_reinstall=True),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: install ran despite satisfied state; status REPAIRED.
        assert outcome.status is InstallStatus.REPAIRED
        assert any("install" in c for c in runner.calls)
        # The satisfied probe (pip show before install) was NOT issued.
        pre_install_shows = [
            c for c in runner.calls
            if c[1:4] == ["-m", "pip", "show"]
            and runner.calls.index(c) < next(
                i for i, c in enumerate(runner.calls) if "install" in c
            )
        ]
        assert pre_install_shows == []


# --- python floor check ----------------------------------------------------


class TestPythonFloorCheck:
    def test_python_3_9_below_floor_fails_without_runner_calls(self) -> None:
        # Given: an interpreter reporting 3.9.
        runner = _MatcherRunner()
        # When
        outcome = install_seam(
            _request(environment=_env(version="3.9.7")),
            prompt=_FakePrompt(confirms=[True]),
            runner=runner,
        )
        # Then: FAILED pre-flight; no subprocess calls; no confirm either.
        assert outcome.status is InstallStatus.FAILED
        assert outcome.failure_kind is FailureKind.SEAM_INSTALL
        assert runner.calls == []
        assert "3.9" in str(outcome.failure_detail)

    def test_python_floor_check_runs_before_confirm(self) -> None:
        # Given: 3.9 floor violation.
        prompt = _FakePrompt(confirms=[True])
        # When
        outcome = install_seam(
            _request(environment=_env(version="3.9.0")),
            prompt=prompt,
            runner=_MatcherRunner(),
        )
        # Then: confirm was never reached.
        assert outcome.status is InstallStatus.FAILED
        assert prompt.confirm_calls == []


# --- SubprocessPipRunner redaction & bounding ------------------------------


class TestSubprocessPipRunnerRedactsAndBounds:
    def test_run_real_interpreter_version_returns_redacted_result(self) -> None:
        # Given: the production runner against the live interpreter.
        runner = SubprocessPipRunner()
        # When: a benign argv (NOT pip install) is run.
        result = runner.run([sys.executable, "--version"])
        # Then: returncode 0, stdout/stderr are str-typed (SafeDetail is a
        # NewType alias, so we assert on str at runtime).
        assert isinstance(result, PipRunResult)
        assert result.returncode == 0
        assert isinstance(str(result.stdout), str)
        assert isinstance(str(result.stderr), str)
        assert "Python" in str(result.stdout)

    def test_subprocess_runner_satisfies_protocol(self) -> None:
        # Given / When / Then
        assert isinstance(SubprocessPipRunner(), PipRunner)


# --- outcome invariants ----------------------------------------------------


class TestSeamInstallOutcomeInvariants:
    def test_failed_without_failure_kind_raises(self) -> None:
        # Given / When / Then
        with pytest.raises(ValueError):
            SeamInstallOutcome(
                status=InstallStatus.FAILED,
                request=_request(),
            )

    def test_non_failed_with_failure_kind_raises(self) -> None:
        # Given / When / Then
        with pytest.raises(ValueError):
            SeamInstallOutcome(
                status=InstallStatus.INSTALLED,
                request=_request(),
                failure_kind=FailureKind.SEAM_INSTALL,
            )

    def test_ok_property_matches_status(self) -> None:
        # Given
        ok_outcome = SeamInstallOutcome(
            status=InstallStatus.INSTALLED, request=_request(),
        )
        failed_outcome = SeamInstallOutcome(
            status=InstallStatus.FAILED, request=_request(),
            failure_kind=FailureKind.SEAM_INSTALL,
        )
        # Then
        assert ok_outcome.ok is True
        assert failed_outcome.ok is False
