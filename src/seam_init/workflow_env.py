"""Non-interactive environment selection mirroring Task 5 safety."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from seam_init.environment import (
    inspect_current_interpreter, is_safe_base, validate_existing_venv,
)
from seam_init.models import (
    EnvironmentChoice, EnvironmentKind, FailureKind, InitializerFailure,
    SafeDetail,
)
from seam_init.workflow_types import WorkflowRequest

__all__ = ["is_supported_environment", "select_environment_answers", "unsupported_environment_detail"]

_BASE_ALIASES: Final[frozenset[str]] = frozenset({"b", "base"})
_EXISTING_ALIASES: Final[frozenset[str]] = frozenset({"e", "existing"})
_NEW_ALIASES: Final[frozenset[str]] = frozenset({"n", "new"})
_ENVIRONMENT_ALIASES: Final[frozenset[str]] = _BASE_ALIASES | _EXISTING_ALIASES | _NEW_ALIASES


def is_supported_environment(value: str | None) -> bool:
    """Omitted/blank defaults to new; an explicit nonblank value must be an alias."""
    if value is None or not value.strip():
        return True
    return value.strip().lower() in _ENVIRONMENT_ALIASES


def unsupported_environment_detail(value: str | None) -> SafeDetail:
    return SafeDetail(f"unsupported environment value: {str(value)[:80]!r}")


def _venv_python(target: Path) -> str:
    rel = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    return str(target / rel)


def select_environment_answers(req: WorkflowRequest) -> EnvironmentChoice:
    a = req.answers
    assert a is not None
    if not is_supported_environment(a.environment):
        raise InitializerFailure(
            kind=FailureKind.PYTHON_ENVIRONMENT,
            safe_detail=unsupported_environment_detail(a.environment))
    k = (a.environment or "new").strip().lower()
    base = inspect_current_interpreter()
    if k in _BASE_ALIASES:
        rep = is_safe_base(base)
        if not rep.safe:
            raise InitializerFailure(
                kind=FailureKind.PYTHON_ENVIRONMENT,
                safe_detail=SafeDetail("; ".join(str(r) for r in rep.reasons)))
        return EnvironmentChoice(EnvironmentKind.BASE, base.executable, base.version_str)
    if k in _EXISTING_ALIASES:
        p = a.venv_path or ""
        if not p.strip():
            raise InitializerFailure(
                kind=FailureKind.PYTHON_ENVIRONMENT,
                safe_detail=SafeDetail("existing venv requires venv_path"))
        info = req.ports.interpreter_probe.probe(p)
        rep = validate_existing_venv(info)
        if not rep.usable:
            raise InitializerFailure(
                kind=FailureKind.PYTHON_ENVIRONMENT,
                safe_detail=SafeDetail("; ".join(str(r) for r in rep.reasons)))
        return EnvironmentChoice(EnvironmentKind.EXISTING_VENV, info.executable, info.version_str)
    target = Path(a.venv_path) if a.venv_path else req.project_root / ".venv"
    if target.exists():
        info = req.ports.interpreter_probe.probe(_venv_python(target))
        rep = validate_existing_venv(info)
        if not rep.usable:
            raise InitializerFailure(
                kind=FailureKind.PYTHON_ENVIRONMENT,
                safe_detail=SafeDetail(f"target {target} exists and is not a valid venv"))
        return EnvironmentChoice(EnvironmentKind.EXISTING_VENV, info.executable, info.version_str)
    parent = target.parent
    if not parent.is_dir():
        raise InitializerFailure(
            kind=FailureKind.PYTHON_ENVIRONMENT,
            safe_detail=SafeDetail(f"parent directory does not exist: {parent}"))
    return EnvironmentChoice(
        EnvironmentKind.NEW_VENV,
        req.ports.venv_creator.create(base.executable, target),
        base.version_str)
