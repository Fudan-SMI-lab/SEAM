"""Idempotent user-space OpenCode standalone installer.

Detects ``opencode`` on PATH, version-checks, and reuses a compatible binary or
installs via the official standalone installer with ``--no-modify-path`` into a
user-owned dir. Never uses npm/Node/sudo/a package manager; records source,
version, path, and SHA-256 (no secrets cross this boundary).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import Final, Sequence

from core.compat import Self
from seam_init.models import FailureKind, InitializerFailure, SafeDetail

__all__ = [
    "InstallAction",
    "InstallOutcome",
    "InstallRequest",
    "MINIMUM_VERSION",
    "OFFICIAL_INSTALL_URL",
    "OpencodeBinary",
    "OpencodeInstallMetadata",
    "OpencodeVersion",
    "default_install_dir",
    "detect_opencode",
    "ensure_opencode",
]

OFFICIAL_INSTALL_URL: Final[str] = "https://opencode.ai/install"

_FORBIDDEN_INSTALL_TOKENS: Final[frozenset[str]] = frozenset(
    {"npm", "npx", "sudo", "apt", "apt-get", "brew", "yum", "dnf", "pacman", "zypper"},
)

# Verified against https://opencode.ai/install (2026-08-10): the standalone
# install.sh is piped to bash with --no-modify-path; binary lands in ~/.opencode/bin.
_DEFAULT_INSTALLER_ARGV: Final[tuple[str, ...]] = (
    "bash", "-c",
    f"curl -fsSL {OFFICIAL_INSTALL_URL} | bash -s -- --no-modify-path",
)

_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _binary_basename() -> str:
    """The binary name the installer places / we look for on this platform."""
    return "opencode.cmd" if sys.platform == "win32" else "opencode"


@unique
class InstallAction(str, Enum):
    REUSED = "reused"
    INSTALLED = "installed"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True, order=True)
class OpencodeVersion:
    """Semantic major.minor.patch; ordered lexicographically by field."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Self | None:
        match = _VERSION_RE.search(text)
        if match is None:
            return None
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))


MINIMUM_VERSION: Final[OpencodeVersion] = OpencodeVersion(1, 0, 0)


@dataclass(frozen=True, slots=True)
class OpencodeBinary:
    path: Path
    version_text: str
    version: OpencodeVersion | None

    def is_compatible(self, minimum: OpencodeVersion) -> bool:
        return self.version is not None and self.version >= minimum


@dataclass(frozen=True, slots=True)
class OpencodeInstallMetadata:
    """Secret-free provenance record for the opencode binary in use."""

    source: str
    version: str
    binary_path: str
    sha256: str
    install_method: str


@dataclass(frozen=True, slots=True)
class InstallRequest:
    """All installer inputs as one typed value (no >3-param functions)."""

    minimum_version: OpencodeVersion = field(default=MINIMUM_VERSION)
    install_dir: Path | None = None
    custom_bin_dir: Path | None = None
    installer_argv: Sequence[str] | None = None
    installer_source: str = OFFICIAL_INSTALL_URL
    overwrite_existing: bool = False


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    action: InstallAction
    binary: OpencodeBinary | None = None
    metadata: OpencodeInstallMetadata | None = None
    refusal_reason: SafeDetail = SafeDetail("")

    def __post_init__(self) -> None:
        if self.action is InstallAction.REFUSED:
            if self.binary is not None:
                raise ValueError("REFUSED must not carry a binary")
        elif self.binary is None:
            raise ValueError(f"{self.action.value} requires a binary")


def default_install_dir() -> Path:
    """The official installer's user-owned target: ~/.opencode/bin."""
    return Path.home() / ".opencode" / "bin"


def detect_opencode() -> OpencodeBinary | None:
    """Find opencode on PATH and probe its version; None if absent."""
    resolved = shutil.which("opencode")
    if resolved is None:
        return None
    path = Path(resolved)
    text = _probe_version(path)
    return OpencodeBinary(path=path, version_text=text, version=OpencodeVersion.parse(text))


def _probe_version(binary: Path) -> str:
    # .cmd/.bat cannot be CreateProcess'd directly on Windows; route via cmd.
    if sys.platform == "win32" and binary.suffix.lower() in {".cmd", ".bat"}:
        argv: list[str] = ["cmd", "/c", str(binary), "--version"]
    else:
        argv = [str(binary), "--version"]
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{result.stdout}{result.stderr}".strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepend_path(directory: Path) -> None:
    current = os.environ.get("PATH", "")
    entry = str(directory)
    os.environ["PATH"] = f"{entry}{os.pathsep}{current}" if current else entry


def _assert_no_forbidden_token(argv: Sequence[str]) -> None:
    for token in argv:
        if Path(token).name.lower() in _FORBIDDEN_INSTALL_TOKENS:
            raise InitializerFailure(
                kind=FailureKind.OPENCODE_INSTALL,
                safe_detail=SafeDetail(
                    f"refused forbidden installer token: {Path(token).name}",
                ),
            )


def _run_installer(request: InstallRequest) -> None:
    """Run the standalone installer; raises InitializerFailure on any failure."""
    argv = tuple(request.installer_argv) if request.installer_argv is not None else _DEFAULT_INSTALLER_ARGV
    _assert_no_forbidden_token(argv)
    try:
        result = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InitializerFailure(
            kind=FailureKind.OPENCODE_INSTALL,
            safe_detail=SafeDetail(f"installer invocation failed: {exc}"),
        ) from exc
    if result.returncode != 0:
        raise InitializerFailure(
            kind=FailureKind.OPENCODE_INSTALL,
            safe_detail=SafeDetail(f"installer exited {result.returncode}"),
        )


_SYSTEM_PREFIXES_WIN: Final[tuple[str, ...]] = (
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
)
_SYSTEM_PREFIXES_POSIX: Final[tuple[str, ...]] = (
    "/usr", "/bin", "/sbin", "/etc", "/opt", "/var", "/lib",
)


def _is_system_path(directory: Path) -> bool:
    resolved = str(directory.expanduser().resolve()).lower()
    prefixes = _SYSTEM_PREFIXES_WIN if sys.platform == "win32" else _SYSTEM_PREFIXES_POSIX
    return any(resolved.startswith(prefix) for prefix in prefixes)


def _validate_custom_dir(directory: Path) -> SafeDetail | None:
    if _is_system_path(directory):
        return SafeDetail(f"refused: {directory} is a system location")
    return None


def _link_or_copy(source: Path, target: Path) -> None:
    # Symlink preferred (cheap, reflects the real binary); copy is the fallback
    # for platforms without symlink privilege (Windows without Developer Mode).
    try:
        target.unlink(missing_ok=True)
        os.symlink(source, target)
    except (OSError, NotImplementedError):
        shutil.copy2(source, target)


def _place_custom_binary(
    installed: OpencodeBinary,
    custom: Path,
    request: InstallRequest,
) -> SafeDetail | None:
    try:
        custom.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return SafeDetail(f"refused: cannot create {custom}: {exc}")
    target = custom / _binary_basename()
    if target.exists() and not request.overwrite_existing:
        return SafeDetail(f"collision: {target} already exists; refusing to overwrite")
    _link_or_copy(installed.path, target)
    _prepend_path(custom)
    return None


def _metadata(
    binary: OpencodeBinary, *, source: str, method: str,
) -> OpencodeInstallMetadata:
    return OpencodeInstallMetadata(
        source=source,
        version=binary.version_text,
        binary_path=str(binary.path),
        sha256=_sha256(binary.path),
        install_method=method,
    )


def ensure_opencode(request: InstallRequest) -> InstallOutcome:
    """Reuse a compatible on-PATH opencode, or install via the standalone installer.

    Returns REUSED / INSTALLED / REFUSED. Installer/probe failures raise
    InitializerFailure(OPENCODE_INSTALL) and leave PATH/files unchanged.
    """
    existing = detect_opencode()
    if existing is not None and existing.is_compatible(request.minimum_version):
        return InstallOutcome(
            action=InstallAction.REUSED,
            binary=existing,
            metadata=_metadata(existing, source="detected-on-path", method="reused"),
        )

    custom = request.custom_bin_dir
    if custom is not None:
        early_refusal = _validate_custom_dir(custom)
        if early_refusal is not None:
            return InstallOutcome(action=InstallAction.REFUSED, refusal_reason=early_refusal)

    install_dir = request.install_dir or default_install_dir()
    _run_installer(request)
    _prepend_path(install_dir)
    installed = detect_opencode()
    if installed is None:
        raise InitializerFailure(
            kind=FailureKind.OPENCODE_INSTALL,
            safe_detail=SafeDetail(
                "installer exited 0 but opencode not found in install directory",
            ),
        )

    if custom is not None:
        refusal = _place_custom_binary(installed, custom, request)
        if refusal is not None:
            return InstallOutcome(action=InstallAction.REFUSED, refusal_reason=refusal)

    return InstallOutcome(
        action=InstallAction.INSTALLED,
        binary=installed,
        metadata=_metadata(
            installed, source=request.installer_source, method="standalone",
        ),
    )
