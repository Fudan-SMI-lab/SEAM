"""Tests for Python environment selection and PEP-668 safety.

Class style mirroring the repo convention. Every test uses an explicit
Given/When/Then block. Detection tests exercise the real running
interpreter via inspect_current_interpreter; orchestration tests inject
fake VenvCreator and InterpreterProbe doubles so no real subprocess is
spawned and no venv directory is ever materialised on disk.
"""
from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

from seam_init.environment import (
    EnvironmentSelectionError,
    ExistingVenvReport,
    InterpreterInfo,
    InterpreterProbe,
    InterpreterSubprocessProbe,
    PromptPort,
    SafetyReport,
    SubprocessVenvCreator,
    VenvCreator,
    inspect_current_interpreter,
    is_safe_base,
    is_system_executable,
    select_environment,
    validate_existing_venv,
)
from seam_init.models import EnvironmentKind, SafeDetail


def _info(
    *,
    executable: str = "/usr/local/bin/python",
    version: tuple[int, int, int] = (3, 12, 1),
    in_venv: bool = True,
    prefix: str = "/venv",
    base_prefix: str = "/usr/local",
    prefix_writable: bool = True,
    externally_managed: bool = False,
    has_pip: bool = True,
    is_root_or_system: bool = False,
) -> InterpreterInfo:
    """Build an InterpreterInfo with safe defaults for happy-path tests."""
    return InterpreterInfo(
        executable=executable,
        version_str=".".join(str(v) for v in version),
        version_tuple=version,
        in_venv=in_venv,
        prefix=prefix,
        base_prefix=base_prefix,
        prefix_writable=prefix_writable,
        externally_managed=externally_managed,
        has_pip=has_pip,
        is_root_or_system=is_root_or_system,
    )


class _FakePrompt:
    """Scripted prompt port: returns canned answers in order, never stdin."""

    def __init__(
        self,
        *,
        asks: list[str] | None = None,
        confirms: list[bool] | None = None,
    ) -> None:
        self._asks = list(asks or [])
        self._confirms = list(confirms or [])
        self.ask_calls: list[tuple[str, object]] = []
        self.confirm_calls: list[tuple[str, bool]] = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.ask_calls.append((prompt, default))
        if not self._asks:
            raise AssertionError(f"unexpected ask: {prompt!r}")
        return self._asks.pop(0)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.confirm_calls.append((prompt, default))
        if not self._confirms:
            raise AssertionError(f"unexpected confirm: {prompt!r}")
        return self._confirms.pop(0)


class _FakeProbe:
    """InterpreterProbe double: returns canned results keyed by path."""

    def __init__(
        self,
        results: dict[str, InterpreterInfo | Exception] | None = None,
    ) -> None:
        self._results: dict[str, InterpreterInfo | Exception] = dict(results or {})
        self.calls: list[str] = []

    def probe(self, python_path: str) -> InterpreterInfo:
        self.calls.append(python_path)
        if python_path not in self._results:
            raise AssertionError(f"unexpected probe for {python_path!r}")
        result = self._results[python_path]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeCreator:
    """VenvCreator double: records calls; never touches the filesystem."""

    def __init__(self, *, return_template: str = "<target>/bin/python") -> None:
        self.calls: list[tuple[str, Path]] = []
        self._template = return_template

    def create(self, python: str, target: Path) -> str:
        self.calls.append((python, target))
        return self._template.replace("<target>", str(target))


# --- detection: real running interpreter ----------------------------------


class TestInspectCurrentInterpreter:
    def test_returns_real_running_interpreter_properties(self) -> None:
        # Given: the live interpreter running under pytest.
        # When
        info = inspect_current_interpreter()
        # Then: every field reflects the real sys/os/sysconfig values.
        assert info.executable == os.path.realpath(sys.executable)
        assert info.version_tuple == sys.version_info[:3]
        assert info.version_str == ".".join(str(v) for v in sys.version_info[:3])
        assert info.prefix == sys.prefix
        assert info.base_prefix == sys.base_prefix
        assert info.in_venv == (sys.prefix != sys.base_prefix)

    def test_externally_managed_matches_stdlib_marker(self) -> None:
        # Given
        info = inspect_current_interpreter()
        stdlib = sysconfig.get_path("stdlib") or ""
        expected = bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").is_file()
        # Then: the detection reads the same path the test computes.
        assert info.externally_managed is expected

    def test_has_pip_field_is_boolean(self) -> None:
        # Given / When
        info = inspect_current_interpreter()
        # Then
        assert isinstance(info.has_pip, bool)

    def test_is_root_or_system_field_is_boolean(self) -> None:
        info = inspect_current_interpreter()
        assert isinstance(info.is_root_or_system, bool)


# --- pure validation functions --------------------------------------------


class TestIsSafeBase:
    def test_safe_base_returns_no_reasons(self) -> None:
        # Given
        info = _info()
        # When
        report = is_safe_base(info)
        # Then
        assert isinstance(report, SafetyReport)
        assert report.safe is True
        assert report.reasons == ()

    def test_pep_668_marker_makes_base_unsafe(self) -> None:
        # Given
        info = _info(externally_managed=True)
        # When
        report = is_safe_base(info)
        # Then
        assert report.safe is False
        assert any("PEP-668" in str(r) for r in report.reasons)

    def test_python_3_9_below_floor_unsafe(self) -> None:
        # Given
        info = _info(version=(3, 9, 18))
        # When
        report = is_safe_base(info)
        # Then
        assert report.safe is False
        assert any("3.10 floor" in str(r) for r in report.reasons)

    def test_non_writable_prefix_unsafe(self) -> None:
        # Given
        info = _info(prefix_writable=False)
        # When
        report = is_safe_base(info)
        # Then
        assert report.safe is False
        assert any("not writable" in str(r) for r in report.reasons)

    def test_root_owned_unsafe(self) -> None:
        # Given
        info = _info(is_root_or_system=True)
        # When
        report = is_safe_base(info)
        # Then
        assert report.safe is False
        assert any("root" in str(r) for r in report.reasons)

    def test_system_path_unsafe(self) -> None:
        # Given / When / Then
        assert is_system_executable("/usr/bin/python3", euid=1000) is True
        assert is_system_executable("/usr/local/bin/python3", euid=1000) is True
        assert is_system_executable("/home/u/venv/bin/python", euid=1000) is False

    def test_root_euid_unsafe_regardless_of_path(self) -> None:
        assert is_system_executable("/home/u/python", euid=0) is True


class TestValidateExistingVenv:
    def test_usable_venv_returns_no_reasons(self) -> None:
        # Given
        info = _info()
        # When
        report = validate_existing_venv(info)
        # Then
        assert isinstance(report, ExistingVenvReport)
        assert report.usable is True
        assert report.reasons == ()

    def test_python_3_9_venv_rejected(self) -> None:
        info = _info(version=(3, 9, 5))
        report = validate_existing_venv(info)
        assert report.usable is False
        assert any("3.10 floor" in str(r) for r in report.reasons)

    def test_non_venv_rejected(self) -> None:
        info = _info(in_venv=False, prefix="/same", base_prefix="/same")
        report = validate_existing_venv(info)
        assert report.usable is False
        assert any("not a venv" in str(r) for r in report.reasons)

    def test_missing_pip_rejected(self) -> None:
        info = _info(has_pip=False)
        report = validate_existing_venv(info)
        assert report.usable is False
        assert any("pip" in str(r) for r in report.reasons)


# --- orchestration: safe base --------------------------------------------


class TestSelectEnvironmentSafeBase:
    def test_safe_base_choice_returns_base_environment(self) -> None:
        # Given
        base = _info()
        prompt = _FakePrompt(asks=["b"], confirms=[True])
        # When
        choice = select_environment(
            base_info=base,
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=_FakeCreator(),
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is not None
        assert choice.kind is EnvironmentKind.BASE
        assert choice.python_executable == base.executable
        assert choice.python_version == base.version_str


# --- orchestration: PEP-668 refusal --------------------------------------


class TestSelectEnvironmentPEP668Refusal:
    def test_pep_668_base_refused_loops_back_then_cancel(self) -> None:
        # Given: base is externally managed; user tries base, then cancels.
        base = _info(externally_managed=True)
        prompt = _FakePrompt(asks=["b", "c"])
        creator = _FakeCreator()
        # When
        choice = select_environment(
            base_info=base,
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then: refused with guidance, no choice, no venv creation attempted.
        assert choice is None
        assert creator.calls == []
        # First prompt was the menu; second was the cancel. No confirmation
        # was reached because PEP-668 short-circuited before the confirm.
        assert prompt.confirm_calls == []

    def test_pep_668_base_offers_alternatives_via_menu_loop(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: base unsafe; user picks base, then new, accepts default target.
        base = _info(externally_managed=True)
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=["b", "n", ""])
        # When
        choice = select_environment(
            base_info=base,
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then: refusal did not terminate the flow; user chose new venv next.
        assert choice is not None
        assert choice.kind is EnvironmentKind.NEW_VENV
        assert creator.calls == [(base.executable, tmp_path / ".venv")]


# --- orchestration: existing venv ----------------------------------------


class TestSelectEnvironmentExistingVenv:
    def test_valid_existing_venv_choice_returns_existing_environment(self) -> None:
        # Given
        venv_python = "/home/u/venv/bin/python"
        venv_info = _info(
            executable=venv_python,
            prefix="/home/u/venv",
            base_prefix="/usr/local",
        )
        probe = _FakeProbe(results={venv_python: venv_info})
        prompt = _FakePrompt(asks=["e", venv_python], confirms=[True])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=_FakeCreator(),
            interpreter_probe=probe,
        )
        # Then
        assert choice is not None
        assert choice.kind is EnvironmentKind.EXISTING_VENV
        assert choice.python_executable == venv_python
        assert probe.calls == [venv_python]

    def test_invalid_existing_venv_loops_back_to_menu(self) -> None:
        # Given: probe returns a non-venv interpreter; user cancels next round.
        bad_path = "/opt/nonvenv/python"
        bad_info = _info(
            executable=bad_path,
            in_venv=False,
            prefix="/opt",
            base_prefix="/opt",
        )
        probe = _FakeProbe(results={bad_path: bad_info})
        prompt = _FakePrompt(asks=["e", bad_path, "c"])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=_FakeCreator(),
            interpreter_probe=probe,
        )
        # Then
        assert choice is None

    def test_python_3_9_existing_venv_rejected(self) -> None:
        # Given: an old venv with Python 3.9.
        bad_path = "/old/venv/bin/python"
        bad_info = _info(
            executable=bad_path,
            version=(3, 9, 1),
            prefix="/old/venv",
            base_prefix="/usr",
        )
        probe = _FakeProbe(results={bad_path: bad_info})
        prompt = _FakePrompt(asks=["e", bad_path, "c"])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=_FakeCreator(),
            interpreter_probe=probe,
        )
        # Then
        assert choice is None

    def test_probe_failure_loops_back_to_menu(self) -> None:
        # Given: the probe raises EnvironmentSelectionError for the path.
        bad_path = "/broken/python"
        probe = _FakeProbe(
            results={
                bad_path: EnvironmentSelectionError(
                    safe_detail=SafeDetail("no such interpreter"),
                ),
            },
        )
        prompt = _FakePrompt(asks=["e", bad_path, "c"])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=_FakeCreator(),
            interpreter_probe=probe,
        )
        # Then
        assert choice is None

    def test_decline_confirm_loops_back(self) -> None:
        # Given: valid venv but user declines the reuse confirmation.
        venv_python = "/home/u/venv/bin/python"
        venv_info = _info(executable=venv_python, prefix="/home/u/venv")
        probe = _FakeProbe(results={venv_python: venv_info})
        prompt = _FakePrompt(asks=["e", venv_python, "c"], confirms=[False])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=Path("/seam"),
            prompt=prompt,
            venv_creator=_FakeCreator(),
            interpreter_probe=probe,
        )
        # Then
        assert choice is None


# --- orchestration: new venv creation ------------------------------------


class TestSelectEnvironmentNewVenv:
    def test_default_creation_uses_seam_root_venv(self, tmp_path: Path) -> None:
        # Given
        base = _info(executable="/usr/bin/python3.12")
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=["n", ""])  # accept default target
        # When
        choice = select_environment(
            base_info=base,
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is not None
        assert choice.kind is EnvironmentKind.NEW_VENV
        assert creator.calls == [(base.executable, tmp_path / ".venv")]

    def test_custom_target_creates_at_requested_path(self, tmp_path: Path) -> None:
        # Given
        parent = tmp_path / "envs"
        parent.mkdir()
        target = parent / ".venv-custom"
        base = _info(executable="/usr/bin/python3.12")
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=["n", str(target)])
        # When
        choice = select_environment(
            base_info=base,
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is not None
        assert choice.kind is EnvironmentKind.NEW_VENV
        assert creator.calls == [(base.executable, target)]

    def test_custom_target_with_missing_parent_loops_back(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: parent doesn't exist
        target = tmp_path / "nope" / ".venv"
        prompt = _FakePrompt(asks=["n", str(target), "c"])
        creator = _FakeCreator()
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is None
        assert creator.calls == []

    def test_occupied_custom_path_collision_reuse(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: target already exists; probe says it's a valid venv.
        target = tmp_path / ".venv"
        target.mkdir()
        venv_python = _venv_python_rel(target)
        venv_info = _info(executable=venv_python, prefix=str(target), base_prefix="/usr")
        probe = _FakeProbe(results={venv_python: venv_info})
        creator = _FakeCreator()
        prompt = _FakePrompt(
            asks=["n", str(target), "r"],
            confirms=[True],
        )
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=probe,
        )
        # Then: reuse path returns EXISTING_VENV; no creation occurred.
        assert choice is not None
        assert choice.kind is EnvironmentKind.EXISTING_VENV
        assert choice.python_executable == venv_python
        assert creator.calls == []

    def test_occupied_default_collision_change_then_create(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: default target exists; user changes to a fresh target.
        default_target = tmp_path / ".venv"
        default_target.mkdir()
        alt_parent = tmp_path / "alt"
        alt_parent.mkdir()
        new_target = alt_parent / ".venv"
        base = _info(executable="/usr/bin/python3.12")
        creator = _FakeCreator()
        prompt = _FakePrompt(
            asks=["n", "", "c", str(new_target)],
        )
        # When
        choice = select_environment(
            base_info=base,
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then: collision -> change -> create at the new target.
        assert choice is not None
        assert choice.kind is EnvironmentKind.NEW_VENV
        assert creator.calls == [(base.executable, new_target)]

    def test_occupied_collision_cancel_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: target exists; user cancels from the collision prompt.
        target = tmp_path / ".venv"
        target.mkdir()
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=["n", str(target), "x"])  # x = cancel
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is None
        assert creator.calls == []

    def test_creation_failure_loops_back_to_menu(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: creator raises EnvironmentSelectionError.
        base = _info(executable="/usr/bin/python3.12")

        class _FailingCreator:
            def __init__(self) -> None:
                self.calls: list[tuple[str, Path]] = []

            def create(self, python: str, target: Path) -> str:
                self.calls.append((python, target))
                raise EnvironmentSelectionError(
                    safe_detail=SafeDetail("boom"),
                )

        creator = _FailingCreator()
        prompt = _FakePrompt(asks=["n", "", "c"])  # new, default, then cancel
        # When
        choice = select_environment(
            base_info=base,
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is None
        assert len(creator.calls) == 1


# --- orchestration: cancellation -----------------------------------------


class TestSelectEnvironmentCancellation:
    def test_cancel_at_menu_returns_none_no_mutation(
        self,
        tmp_path: Path,
    ) -> None:
        # Given
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=["c"])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then: cancellation produced no choice and no filesystem mutation.
        assert choice is None
        assert creator.calls == []
        assert not (tmp_path / ".venv").exists()

    def test_empty_menu_answer_cancels(self, tmp_path: Path) -> None:
        # Given: blank at the outer menu is treated as cancel.
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=[""])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is None
        assert creator.calls == []

    def test_unknown_choice_loops_then_cancel(self, tmp_path: Path) -> None:
        # Given
        creator = _FakeCreator()
        prompt = _FakePrompt(asks=["zzz", "c"])
        # When
        choice = select_environment(
            base_info=_info(),
            seam_root=tmp_path,
            prompt=prompt,
            venv_creator=creator,
            interpreter_probe=_FakeProbe(),
        )
        # Then
        assert choice is None
        assert creator.calls == []


# --- protocol satisfaction ------------------------------------------------


class TestProtocols:
    def test_fake_prompt_satisfies_prompt_port(self) -> None:
        assert isinstance(_FakePrompt(), PromptPort)

    def test_fake_probe_satisfies_interpreter_probe(self) -> None:
        assert isinstance(_FakeProbe(), InterpreterProbe)

    def test_fake_creator_satisfies_venv_creator(self) -> None:
        assert isinstance(_FakeCreator(), VenvCreator)

    def test_subprocess_creator_satisfies_venv_creator(self) -> None:
        assert isinstance(SubprocessVenvCreator(), VenvCreator)

    def test_subprocess_probe_satisfies_interpreter_probe(self) -> None:
        assert isinstance(InterpreterSubprocessProbe(), InterpreterProbe)


def _venv_python_rel(target: Path) -> str:
    """Return the python path inside a venv at target, matching production."""
    if os.name == "nt":
        return str(target / "Scripts" / "python.exe")
    return str(target / "bin" / "python")
