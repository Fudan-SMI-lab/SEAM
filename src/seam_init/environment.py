"""Selection and creation of a safe Python interpreter for SEAM.

Side effects beyond read-only detection are delegated through protocols
(VenvCreator, InterpreterProbe) so tests inject fakes; the only real
filesystem reads in detection are the EXTERNALLY-MANAGED marker probe and
``os.access`` on the prefix.
"""
from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final, runtime_checkable

from seam_init.models import EnvironmentChoice, EnvironmentKind, SafeDetail

__all__ = [
    "EnvironmentSelectionError", "ExistingVenvReport", "InterpreterInfo",
    "InterpreterProbe", "InterpreterSubprocessProbe", "PromptPort",
    "SafetyReport", "SubprocessVenvCreator", "VenvCreator",
    "inspect_current_interpreter", "is_safe_base", "is_system_executable",
    "select_environment", "validate_existing_venv",
]

_PYTHON_FLOOR: tuple[int, int] = (3, 10)
_VENV_TIMEOUT_SECONDS: float = 120.0
_PROBE_TIMEOUT_SECONDS: float = 15.0
_SYSTEM_PREFIXES = ("/usr/", "/system/", "/library/system/")


@final
@dataclass(frozen=True, slots=True)
class InterpreterInfo:
    """Snapshot of one Python interpreter's initializer-relevant properties."""

    executable: str
    version_str: str
    version_tuple: tuple[int, int, int]
    in_venv: bool
    prefix: str
    base_prefix: str
    prefix_writable: bool
    externally_managed: bool
    has_pip: bool
    is_root_or_system: bool


@final
@dataclass(frozen=True, slots=True)
class SafetyReport:
    """Reasons a base interpreter cannot receive pip installs (empty = safe)."""

    safe: bool
    reasons: tuple[SafeDetail, ...]


@final
@dataclass(frozen=True, slots=True)
class ExistingVenvReport:
    """Reasons an existing venv cannot be selected (empty = usable)."""

    usable: bool
    reasons: tuple[SafeDetail, ...]


class EnvironmentSelectionError(Exception):
    """Typed selection error; carries only secret-free SafeDetail context."""

    safe_detail: SafeDetail

    def __init__(self, *, safe_detail: SafeDetail) -> None:
        super().__init__(str(safe_detail))
        self.safe_detail = safe_detail


@runtime_checkable
class PromptPort(Protocol):
    """Subset of ``seam_init.cli.PromptPort`` used by environment selection."""

    def ask(self, prompt: str, *, default: str | None = None) -> str: ...
    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...


@runtime_checkable
class InterpreterProbe(Protocol):
    """Boundary for inspecting an arbitrary interpreter path."""

    def probe(self, python_path: str) -> InterpreterInfo: ...


@runtime_checkable
class VenvCreator(Protocol):
    """Boundary for ``python -m venv``; returns the new venv python path."""

    def create(self, python: str, target: Path) -> str: ...


class _RetryMenu(Exception):
    """Internal signal: re-display the outer selection menu."""


def is_system_executable(executable: str, *, euid: int | None) -> bool:
    """True when euid==0 (POSIX root) or executable under canonical system paths."""
    if euid == 0:
        return True
    return executable.lower().startswith(_SYSTEM_PREFIXES)


def is_safe_base(info: InterpreterInfo) -> SafetyReport:
    """Safe only when version >= 3.10, prefix writable, NOT PEP-668, NOT root."""
    reasons: list[SafeDetail] = []
    if info.version_tuple[:2] < _PYTHON_FLOOR:
        reasons.append(SafeDetail(f"Python {info.version_str} below 3.10 floor"))
    if not info.prefix_writable:
        reasons.append(SafeDetail(f"prefix not writable: {info.prefix}"))
    if info.externally_managed:
        reasons.append(SafeDetail("EXTERNALLY-MANAGED marker present (PEP-668 blocks system pip)"))
    if info.is_root_or_system:
        reasons.append(SafeDetail("interpreter is root- or system-owned"))
    return SafetyReport(safe=not reasons, reasons=tuple(reasons))


def validate_existing_venv(info: InterpreterInfo) -> ExistingVenvReport:
    """Usable only when version >= 3.10, in a venv, and pip is importable."""
    reasons: list[SafeDetail] = []
    if info.version_tuple[:2] < _PYTHON_FLOOR:
        reasons.append(SafeDetail(f"venv Python {info.version_str} below 3.10 floor"))
    if not info.in_venv:
        reasons.append(SafeDetail(f"not a venv: prefix==base_prefix ({info.prefix})"))
    if not info.has_pip:
        reasons.append(SafeDetail("venv pip is missing"))
    return ExistingVenvReport(usable=not reasons, reasons=tuple(reasons))


def _reasons(reasons: tuple[SafeDetail, ...]) -> str:
    return "; ".join(str(r) for r in reasons)


def inspect_current_interpreter() -> InterpreterInfo:
    """Snapshot of the running interpreter via ``sys``/``os``/``sysconfig``."""
    import importlib.util  # noqa: PLC0415

    executable = os.path.realpath(sys.executable)
    major, minor, micro = sys.version_info[:3]
    euid = os.geteuid() if hasattr(os, "geteuid") else None
    stdlib = sysconfig.get_path("stdlib") or ""
    has_pip = importlib.util.find_spec("pip") is not None
    return InterpreterInfo(
        executable, f"{major}.{minor}.{micro}", (major, minor, micro),
        sys.prefix != sys.base_prefix, sys.prefix, sys.base_prefix,
        os.access(sys.prefix, os.W_OK),
        bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").is_file(),
        has_pip, is_system_executable(executable, euid=euid),
    )


def _venv_python_path(target: Path) -> str:
    """Path to the python executable inside a venv at ``target``."""
    if os.name == "nt":
        return str(target / "Scripts" / "python.exe")
    return str(target / "bin" / "python")


@final
class SubprocessVenvCreator:
    """Production VenvCreator; runs ``python -m venv`` with a bounded timeout."""

    def create(self, python: str, target: Path) -> str:
        try:
            subprocess.run([python, "-m", "venv", str(target)], check=True, capture_output=True, timeout=_VENV_TIMEOUT_SECONDS)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise EnvironmentSelectionError(
                safe_detail=SafeDetail(f"venv creation failed at {target}: {exc}"),
            ) from exc
        return _venv_python_path(target)


_PROBE_SCRIPT = (
    "import json, os, sys, sysconfig; stdlib = sysconfig.get_path('stdlib') or ''; "
    "util = __import__('importlib.util', fromlist=['find_spec']); "
    "print(json.dumps({'executable': os.path.realpath(sys.executable), "
    "'version': list(sys.version_info[:3]), 'in_venv': sys.prefix != sys.base_prefix, "
    "'prefix': sys.prefix, 'base_prefix': sys.base_prefix, "
    "'writable': os.access(sys.prefix, os.W_OK), "
    "'externally_managed': bool(stdlib) and "
    "os.path.isfile(os.path.join(stdlib, 'EXTERNALLY-MANAGED')), "
    "'has_pip': util.find_spec('pip') is not None, "
    "'euid': getattr(os, 'geteuid', lambda: None)()}))"
)


@final
class InterpreterSubprocessProbe:
    """Production InterpreterProbe; runs the path with a one-shot JSON dump."""

    def probe(self, python_path: str) -> InterpreterInfo:
        import json  # noqa: PLC0415

        try:
            result = subprocess.run([python_path, "-c", _PROBE_SCRIPT], capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EnvironmentSelectionError(
                safe_detail=SafeDetail(f"cannot probe interpreter at {python_path}: {exc}"),
            ) from exc
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise EnvironmentSelectionError(
                safe_detail=SafeDetail(f"probe at {python_path} returned malformed JSON: {exc.msg}"),
            ) from exc
        major, minor, micro = (int(x) for x in data["version"])
        return InterpreterInfo(
            str(data["executable"]), f"{major}.{minor}.{micro}", (major, minor, micro),
            bool(data["in_venv"]), str(data["prefix"]), str(data["base_prefix"]),
            bool(data["writable"]), bool(data["externally_managed"]),
            bool(data["has_pip"]),
            is_system_executable(str(data["executable"]), euid=data["euid"]),
        )


def _confirm_existing(
    info: InterpreterInfo, prompt: PromptPort, fail_msg_prefix: str,
) -> EnvironmentChoice:
    report = validate_existing_venv(info)
    if not report.usable:
        print(f"{fail_msg_prefix}: {_reasons(report.reasons)}.", file=sys.stderr)
        raise _RetryMenu()
    if not prompt.confirm(f"Reuse existing venv at {info.executable} ({info.version_str})?", default=True):
        raise _RetryMenu()
    return EnvironmentChoice(EnvironmentKind.EXISTING_VENV, info.executable, info.version_str)


def _try_base(base_info: InterpreterInfo, prompt: PromptPort) -> EnvironmentChoice | None:
    report = is_safe_base(base_info)
    if not report.safe:
        print(f"Base interpreter is not safe for SEAM install: {_reasons(report.reasons)}.", file=sys.stderr)
        raise _RetryMenu()
    if not prompt.confirm(f"Install into base interpreter {base_info.executable} ({base_info.version_str})?", default=False):
        return None
    return EnvironmentChoice(EnvironmentKind.BASE, base_info.executable, base_info.version_str)


def _try_existing(
    prompt: PromptPort, probe: InterpreterProbe,
) -> EnvironmentChoice | None:
    raw = prompt.ask("Path to existing venv python executable").strip()
    if not raw:
        return None
    try:
        info = probe.probe(raw)
    except EnvironmentSelectionError as exc:
        print(f"Cannot probe {raw}: {exc}", file=sys.stderr)
        raise _RetryMenu() from exc
    return _confirm_existing(info, prompt, "Not a usable venv")


def _try_new(
    base_info: InterpreterInfo, seam_root: Path, prompt: PromptPort,
    creator: VenvCreator, probe: InterpreterProbe,
) -> EnvironmentChoice | None:
    default_target = seam_root / ".venv"
    while True:
        raw = prompt.ask("Target for new venv (blank for default <SEAM>/.venv)", default=str(default_target)).strip()
        target = Path(raw) if raw else default_target
        if not target.exists():
            parent = target.parent
            if not parent.is_dir():
                print(f"Parent directory does not exist: {parent}", file=sys.stderr)
                raise _RetryMenu()
            try:
                python_path = creator.create(base_info.executable, target)
            except EnvironmentSelectionError as exc:
                print(f"venv creation failed: {exc}", file=sys.stderr)
                raise _RetryMenu() from exc
            return EnvironmentChoice(EnvironmentKind.NEW_VENV, python_path, base_info.version_str)
        action = prompt.ask(f"Target {target} already exists: [r]euse, [c]hange target, or cancel?", default="r").strip().lower()
        if action in {"c", "change"}:
            continue
        if action not in {"r", "reuse"}:
            return None
        try:
            info = probe.probe(_venv_python_path(target))
        except EnvironmentSelectionError as exc:
            print(f"Cannot reuse {target}: {exc}", file=sys.stderr)
            raise _RetryMenu() from exc
        return _confirm_existing(info, prompt, "Existing target is not a usable venv")


def select_environment(
    *, base_info: InterpreterInfo, seam_root: Path, prompt: PromptPort,
    venv_creator: VenvCreator, interpreter_probe: InterpreterProbe,
) -> EnvironmentChoice | None:
    """Drive the base/existing/new-venv selection loop; None on user cancel."""
    while True:
        choice = prompt.ask("Use [b]ase interpreter, [e]xisting venv, or create [n]ew venv?", default="n").strip().lower()
        try:
            if choice in {"b", "base"}:
                return _try_base(base_info, prompt)
            if choice in {"e", "existing"}:
                return _try_existing(prompt, interpreter_probe)
            if choice in {"n", "new"}:
                return _try_new(base_info, seam_root, prompt, venv_creator, interpreter_probe)
            if choice in {"c", "cancel", ""}:
                return None
            print(f"Unknown choice {choice!r}; try b/e/n or cancel.", file=sys.stderr)
        except _RetryMenu:
            continue
