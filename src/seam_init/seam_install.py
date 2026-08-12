"""Install and verify canonical SEAM dependencies in the selected interpreter.

Side effects beyond in-process string manipulation go through the PipRunner
boundary (so tests inject fakes). The orchestrator displays interpreter/path/
scope, waits for an opt-in confirmation, runs the editable install with
redacted+bounded streaming, then verifies package metadata, importable SEAM
modules, pytest/PyYAML availability, and ``diagnose_seam_opencode.py --help``.
Already-satisfied editable installs skip mutation unless ``force_reinstall``
(repair) is requested. Failures return typed outcomes; nothing raises out of
:func:`install_seam` for ordinary install/verify negatives.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from core.secret_redaction import redact_sensitive_text
from seam_init.environment import PromptPort
from seam_init.models import EnvironmentChoice, FailureKind, SafeDetail

__all__ = [
    "InstallStatus", "PipRunResult", "PipRunner", "SeamInstallOutcome",
    "SeamInstallRequest", "SubprocessPipRunner", "install_seam",
]

_PACKAGE_NAME: Final[str] = "sm-adapt"
_PYTHON_FLOOR: Final[tuple[int, int]] = (3, 10)
_INSTALL_TIMEOUT_SECONDS: Final[float] = 600.0
_PROBE_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_DIAGNOSTIC_BYTES: Final[int] = 8192
_TRUNCATION_SUFFIX: Final[str] = "...[truncated]"
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"\s*(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class PipRunResult:
    """One subprocess invocation result; stdout/stderr already redacted."""

    argv: tuple[str, ...]
    returncode: int
    stdout: SafeDetail
    stderr: SafeDetail


@runtime_checkable
class PipRunner(Protocol):
    """Boundary for any ``python -m ...`` / ``python <script>`` subprocess."""

    def run(self, argv: Sequence[str]) -> PipRunResult: ...


@final
class SubprocessPipRunner:
    """Production PipRunner: ``subprocess.run`` with timeout, env passthrough."""

    __slots__ = ("_install_timeout", "_probe_timeout", "_env")

    def __init__(
        self,
        *,
        install_timeout: float = _INSTALL_TIMEOUT_SECONDS,
        probe_timeout: float = _PROBE_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._install_timeout = install_timeout
        self._probe_timeout = probe_timeout
        self._env = env

    def run(self, argv: Sequence[str]) -> PipRunResult:
        argv_list = list(argv)
        timeout = self._install_timeout if _looks_like_install(argv_list) else self._probe_timeout
        pass_env = self._env if self._env is not None else os.environ
        try:
            result = subprocess.run(
                argv_list, capture_output=True, text=True,
                timeout=timeout, check=False, env=pass_env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return PipRunResult(
                argv=tuple(argv_list), returncode=1,
                stdout=SafeDetail(""), stderr=SafeDetail(str(exc)),
            )
        return PipRunResult(
            argv=tuple(argv_list), returncode=int(result.returncode),
            stdout=SafeDetail(redact_sensitive_text(result.stdout)),
            stderr=SafeDetail(redact_sensitive_text(result.stderr)),
        )


def _looks_like_install(argv: Sequence[str]) -> bool:
    return "install" in argv


@unique
class InstallStatus(str, Enum):
    """Terminal outcome states for the SEAM install stage."""

    INSTALLED = "installed"
    SATISFIED = "satisfied"
    REPAIRED = "repaired"
    DECLINED = "declined"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SeamInstallRequest:
    """All install inputs as one typed value (no >3-param functions)."""

    environment: EnvironmentChoice
    source_path: Path
    extras: str = "dev,dashboard"
    optional_extras: str = ""
    package_name: str = _PACKAGE_NAME
    python_floor: tuple[int, int] = _PYTHON_FLOOR
    diagnose_path: Path | None = None
    force_reinstall: bool = False
    install_timeout: float = _INSTALL_TIMEOUT_SECONDS
    probe_timeout: float = _PROBE_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class SeamInstallOutcome:
    """Frozen terminal outcome; carries only secret-free SafeDetail context."""

    status: InstallStatus
    request: SeamInstallRequest
    diagnostics: tuple[SafeDetail, ...] = field(default_factory=tuple)
    failure_kind: FailureKind | None = None
    failure_detail: SafeDetail = SafeDetail("")

    def __post_init__(self) -> None:
        if self.status is InstallStatus.FAILED and self.failure_kind is None:
            raise ValueError("FAILED requires a failure_kind")
        if self.status is not InstallStatus.FAILED and self.failure_kind is not None:
            raise ValueError(f"{self.status.value} must not carry a failure_kind")

    @property
    def ok(self) -> bool:
        return self.status in {
            InstallStatus.INSTALLED, InstallStatus.SATISFIED, InstallStatus.REPAIRED,
        }


def _redact_and_bound(text: str) -> SafeDetail:
    # Bound the raw input FIRST: redact_sensitive_text has O(n^2) worst-case
    # backtracking on long no-match inputs, so unbounded input can hang.
    truncated = text[:_MAX_DIAGNOSTIC_BYTES]
    was_truncated = len(text) > _MAX_DIAGNOSTIC_BYTES
    redacted = redact_sensitive_text(truncated)
    if was_truncated:
        return SafeDetail(redacted + _TRUNCATION_SUFFIX)
    return SafeDetail(redacted)


def _resolve_diagnose_path(request: SeamInstallRequest) -> Path:
    if request.diagnose_path is not None:
        return request.diagnose_path
    return request.source_path.parent / "scripts" / "diagnose_seam_opencode.py"


def _install_target(request: SeamInstallRequest) -> str:
    return f"{request.source_path.as_posix()}[{request.extras}]"


def _pip_install_argv(request: SeamInstallRequest) -> list[str]:
    return [
        request.environment.python_executable, "-m", "pip", "install",
        "-e", _install_target(request),
    ]


def _pip_show_argv(request: SeamInstallRequest) -> list[str]:
    return [request.environment.python_executable, "-m", "pip", "show", request.package_name]


def _parse_python_major_minor(text: str) -> tuple[int, int] | None:
    match = _VERSION_RE.match(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _check_python_floor(request: SeamInstallRequest) -> SafeDetail | None:
    parsed = _parse_python_major_minor(request.environment.python_version)
    if parsed is None:
        return SafeDetail(f"unparseable Python version: {request.environment.python_version}")
    if parsed < request.python_floor:
        return SafeDetail(
            f"Python {parsed[0]}.{parsed[1]} below "
            f"{request.python_floor[0]}.{request.python_floor[1]} floor",
        )
    return None


def _is_editable_satisfied(show_result: PipRunResult, request: SeamInstallRequest) -> bool:
    if show_result.returncode != 0:
        return False
    output = str(show_result.stdout)
    if "Editable project location:" not in output:
        return False
    return request.source_path.as_posix() in output or str(request.source_path) in output


def _verify_argv(request: SeamInstallRequest) -> list[list[str]]:
    python = request.environment.python_executable
    diagnose = _resolve_diagnose_path(request)
    return [
        [python, "-c", "import seam_init, core.jsonc"],
        [python, "-m", "pytest", "--version"],
        [python, "-c", "import yaml"],
        [python, str(diagnose), "--help"],
    ]


def _verify(request: SeamInstallRequest, runner: PipRunner) -> tuple[bool, tuple[SafeDetail, ...]]:
    diagnostics: list[SafeDetail] = []
    all_ok = True
    for argv in _verify_argv(request):
        result = runner.run(argv)
        diagnostics.append(SafeDetail(f"$ {' '.join(argv)} -> rc={result.returncode}"))
        diagnostics.append(_redact_and_bound(str(result.stdout)))
        if result.returncode != 0:
            all_ok = False
            diagnostics.append(_redact_and_bound(str(result.stderr)))
    return all_ok, tuple(diagnostics)


def _confirm_install(request: SeamInstallRequest, prompt: PromptPort) -> bool:
    target = _install_target(request)
    python = request.environment.python_executable
    version = request.environment.python_version
    msg = f"Install SEAM into {python} (Python {version}) via `pip install -e {target}`?"
    return prompt.confirm(msg, default=False)


def _try_optional_extras(
    request: SeamInstallRequest, runner: PipRunner,
) -> tuple[SafeDetail, ...]:
    """Try installing optional extras; failures produce warnings, not errors."""
    if not request.optional_extras.strip():
        return ()
    target = f"{request.source_path.as_posix()}[{request.optional_extras}]"
    argv = [
        request.environment.python_executable, "-m", "pip", "install", "-e", target,
    ]
    result = runner.run(argv)
    if result.returncode != 0:
        detail = str(result.stderr)[:200] or str(result.stdout)[:200]
        return (SafeDetail(
            f"optional extras '{request.optional_extras}' failed (non-blocking): {detail}"),)
    return (SafeDetail(f"optional extras '{request.optional_extras}' installed successfully"),)


def install_seam(
    request: SeamInstallRequest,
    *,
    prompt: PromptPort,
    runner: PipRunner,
) -> SeamInstallOutcome:
    """Display interpreter info, confirm, install, verify; never raises on
    ordinary install/verify negatives (typed FAILED outcome is returned).
    """
    floor_reason = _check_python_floor(request)
    if floor_reason is not None:
        return SeamInstallOutcome(
            status=InstallStatus.FAILED, request=request,
            failure_kind=FailureKind.SEAM_INSTALL, failure_detail=floor_reason,
        )
    if not _confirm_install(request, prompt):
        return SeamInstallOutcome(status=InstallStatus.DECLINED, request=request)
    if not request.force_reinstall:
        show_result = runner.run(_pip_show_argv(request))
        if _is_editable_satisfied(show_result, request):
            return SeamInstallOutcome(
                status=InstallStatus.SATISFIED, request=request,
                diagnostics=(SafeDetail("editable install already satisfied; skipped"),),
            )
    diagnostics: list[SafeDetail] = []
    install_result = runner.run(_pip_install_argv(request))
    diagnostics.append(SafeDetail(f"$ {' '.join(install_result.argv)} -> rc={install_result.returncode}"))
    diagnostics.append(_redact_and_bound(str(install_result.stdout)))
    diagnostics.append(_redact_and_bound(str(install_result.stderr)))
    if install_result.returncode != 0:
        detail_text = str(install_result.stderr) or str(install_result.stdout)
        return SeamInstallOutcome(
            status=InstallStatus.FAILED, request=request,
            diagnostics=tuple(diagnostics),
            failure_kind=FailureKind.SEAM_INSTALL,
            failure_detail=_redact_and_bound(detail_text),
        )
    verify_ok, verify_diag = _verify(request, runner)
    diagnostics.extend(verify_diag)
    if not verify_ok:
        return SeamInstallOutcome(
            status=InstallStatus.FAILED, request=request,
            diagnostics=tuple(diagnostics),
            failure_kind=FailureKind.SEAM_INSTALL,
            failure_detail=SafeDetail("post-install verification failed"),
        )
    diagnostics.extend(_try_optional_extras(request, runner))
    return SeamInstallOutcome(
        status=InstallStatus.REPAIRED if request.force_reinstall else InstallStatus.INSTALLED,
        request=request, diagnostics=tuple(diagnostics),
    )
