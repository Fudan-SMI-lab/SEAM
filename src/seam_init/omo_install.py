"""Install Bun and OMO Ultimate with explicit provider/platform choices."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from core.compat import Self
from core.jsonc import JsonValue, merge_config, parse_config_object
from seam_init.config_transaction import ConfigTransaction
from seam_init.models import FailureKind, InitializerFailure, SafeDetail

OFFICIAL_BUN_INSTALL_URL: Final[str] = "https://bun.sh/install"
OMO_PACKAGE: Final[str] = "oh-my-openagent"
OMO_PLATFORM: Final[str] = "opencode"
CURRENT_PLUGIN: Final[str] = "oh-my-openagent"
LEGACY_PLUGIN: Final[str] = "oh-my-opencode"
OMO_INSTALL_ARGV_PREFIX: Final[tuple[str, ...]] = (
    "bunx", OMO_PACKAGE, "install", "--no-tui", f"--platform={OMO_PLATFORM}",
)
_FORBIDDEN_INSTALL_TOKENS: Final[frozenset[str]] = frozenset(
    {"npm", "npx", "sudo", "apt", "apt-get", "brew", "yum", "dnf", "pacman",
     "zypper", "pip", "pip3"},
)
_FORBIDDEN_OMO_TOKENS: Final[frozenset[str]] = frozenset(
    {"omo", "omo-ai", "--codex-autonomous", "--no-codex-autonomous", "--skip-auth"},
)
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SYSTEM_PREFIXES: Final[tuple[str, ...]] = (
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "/usr", "/bin", "/sbin", "/etc", "/opt", "/var", "/lib",
)
_BOOL_FLAGS: Final[tuple[tuple[str, str], ...]] = (
    # --claude is three-valued (yes/no/max20); see build_omo_argv.
    ("--openai", "openai"), ("--gemini", "gemini"), ("--copilot", "copilot"),
    ("--opencode-zen", "opencode_zen"), ("--zai-coding-plan", "zai_coding_plan"),
    ("--opencode-go", "opencode_go"), ("--kimi-for-coding", "kimi_for_coding"),
    ("--bailian-coding-plan", "bailian_coding_plan"),
    ("--minimax-cn-coding-plan", "minimax_cn_coding_plan"),
    ("--minimax-coding-plan", "minimax_coding_plan"),
    ("--vercel-ai-gateway", "vercel_ai_gateway"),
)


@unique
class TriState(str, Enum):
    YES = "yes"
    NO = "no"
    MAX20 = "max20"


@unique
class InstallAction(str, Enum):
    REUSED = "reused"
    INSTALLED = "installed"


@dataclass(frozen=True, slots=True, order=True)
class BunVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Self | None:
        m = _VERSION_RE.search(text)
        return None if m is None else cls(int(m[1]), int(m[2]), int(m[3]))


MINIMUM_BUN_VERSION: Final[BunVersion] = BunVersion(1, 0, 0)


@dataclass(frozen=True, slots=True)
class BunRuntime:
    path: Path
    version_text: str
    version: BunVersion | None


@dataclass(frozen=True, slots=True)
class SubscriptionSelection:
    claude: TriState
    openai: bool
    gemini: bool
    copilot: bool
    opencode_zen: bool
    zai_coding_plan: bool
    opencode_go: bool
    kimi_for_coding: bool
    bailian_coding_plan: bool
    minimax_cn_coding_plan: bool
    minimax_coding_plan: bool
    vercel_ai_gateway: bool


@dataclass(frozen=True, slots=True)
class OmoInstallRequest:
    subscription: SubscriptionSelection
    bun_install_dir: Path | None = None
    bun_installer_argv: Sequence[str] | None = None
    omo_installer_argv: Sequence[str] | None = None


@dataclass(frozen=True, slots=True)
class OmoInstallMetadata:
    bun: BunRuntime
    bun_action: str
    omo_action: str
    omo_argv: tuple[str, ...]
    legacy_warning: str


@runtime_checkable
class BunInstaller(Protocol):
    def install_bun(self, argv: Sequence[str]) -> None: ...


@runtime_checkable
class OmoInstaller(Protocol):
    def install_omo(self, argv: Sequence[str]) -> str: ...


@runtime_checkable
class PluginRegistrar(Protocol):
    def has_plugin(self, plugin: str) -> bool: ...
    def register_plugin(self, plugin: str) -> str: ...


def _fail(detail: str) -> InitializerFailure:
    return InitializerFailure(
        kind=FailureKind.OMO_INSTALL, safe_detail=SafeDetail(detail),
    )


def _assert_safe_omo_argv(argv: Sequence[str]) -> None:
    for token in argv:
        if token in _FORBIDDEN_OMO_TOKENS or token.split("=", 1)[0] in _FORBIDDEN_OMO_TOKENS:
            raise _fail(f"refused forbidden OMO token: {token}")
    if list(argv[:3]) != ["bunx", OMO_PACKAGE, "install"] \
            or "--no-tui" not in argv or f"--platform={OMO_PLATFORM}" not in argv:
        raise _fail("refused: OMO argv must start with "
                    f"'bunx {OMO_PACKAGE} install --no-tui --platform={OMO_PLATFORM}'")


def build_omo_argv(selection: SubscriptionSelection) -> tuple[str, ...]:
    argv: list[str] = list(OMO_INSTALL_ARGV_PREFIX)
    argv.append(f"--claude={selection.claude.value}")
    for flag_name, attr in _BOOL_FLAGS:
        value = bool(getattr(selection, attr))
        argv.append(f"{flag_name}={'yes' if value else 'no'}")
    _assert_safe_omo_argv(argv)
    return tuple(argv)


def default_bun_dir() -> Path:
    return Path.home() / ".bun"


def _probe_bun_version(binary: Path) -> str:
    needs_cmd = sys.platform == "win32" and binary.suffix.lower() in {".cmd", ".bat", ".exe"}
    argv = ["cmd", "/c", str(binary), "--version"] if needs_cmd else [str(binary), "--version"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{r.stdout}{r.stderr}".strip()


def detect_bun() -> BunRuntime | None:
    resolved = shutil.which("bun")
    if resolved is None:
        return None
    path = Path(resolved)
    text = _probe_bun_version(path)
    return BunRuntime(path=path, version_text=text, version=BunVersion.parse(text))


@dataclass(frozen=True, slots=True)
class SubprocessInstaller:
    bun_install_dir: Path

    def install_bun(self, argv: Sequence[str]) -> None:
        for tok in argv:
            if Path(tok).name.lower() in _FORBIDDEN_INSTALL_TOKENS:
                raise _fail(f"refused forbidden installer token: {Path(tok).name}")
        env = {**os.environ, "BUN_INSTALL": str(self.bun_install_dir)}
        self._run(argv, env, 300, "bun installer")

    def install_omo(self, argv: Sequence[str]) -> str:
        _assert_safe_omo_argv(argv)
        return self._run(argv, None, 600, "omo installer")

    def _run(self, argv: Sequence[str], env: Mapping[str, str] | None, timeout: int, what: str) -> str:
        try:
            r = subprocess.run(list(argv), capture_output=True, text=True,
                               timeout=timeout, check=False, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _fail(f"{what} invocation failed: {exc}") from exc
        if r.returncode != 0:
            raise _fail(f"{what} exited {r.returncode}")
        return f"{r.stdout}{r.stderr}"


@dataclass(frozen=True, slots=True)
class JsoncPluginRegistrar:
    project_root: Path
    config_relative_path: Path = Path(".opencode/opencode.jsonc")

    def _load_existing(self) -> dict[str, JsonValue]:
        path = self.project_root / self.config_relative_path
        if not path.is_file():
            raise _fail(f"missing config: {self.config_relative_path}")
        try:
            parsed = parse_config_object(path.read_text(encoding="utf-8")).value
        except Exception as exc:
            raise _fail(f"config parse failed: {exc}") from exc
        if not isinstance(parsed, dict):
            raise _fail("config root is not a JSON object")
        return parsed

    def has_plugin(self, plugin: str) -> bool:
        try:
            existing = self._load_existing()
        except InitializerFailure:
            return False
        plugins = existing.get("plugin")
        return isinstance(plugins, list) and plugin in plugins

    def register_plugin(self, plugin: str) -> str:
        existing = self._load_existing()
        plugins = existing.get("plugin")
        plugin_list = plugins if isinstance(plugins, list) else []
        legacy = sorted({p for p in plugin_list
                         if isinstance(p, str) and p == LEGACY_PLUGIN})
        warning = (f"retained legacy plugin entry '{', '.join(legacy)}' to "
                   f"preserve the existing setup; '{CURRENT_PLUGIN}' is "
                   "registered alongside it." if legacy else "")
        merged = merge_config(existing, {"plugin": [plugin]})
        new_bytes = json.dumps(merged, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        rel = self.config_relative_path
        tx = ConfigTransaction(self.project_root)
        try:
            tx_id = tx.begin((rel,))
            result = tx.commit(tx_id, {rel: new_bytes})
        except Exception as exc:
            raise _fail(f"plugin registration failed: {exc}") from exc
        if not result.committed:
            raise _fail("plugin registration rolled back")
        return warning


def ensure_omo_install(
    request: OmoInstallRequest,
    *,
    bun_installer: BunInstaller,
    omo_installer: OmoInstaller,
    registrar: PluginRegistrar,
) -> OmoInstallMetadata:
    existing = detect_bun()
    if (existing is not None and existing.version is not None
            and existing.version >= MINIMUM_BUN_VERSION):
        bun = existing
        bun_action = InstallAction.REUSED
    else:
        install_dir = request.bun_install_dir or default_bun_dir()
        resolved_dir = str(install_dir.expanduser().resolve()).lower()
        if any(resolved_dir.startswith(p) for p in _SYSTEM_PREFIXES):
            raise _fail(f"refused: {install_dir} is a system location")
        default_argv = (("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-Command", f"irm {OFFICIAL_BUN_INSTALL_URL}.ps1 | iex")
                        if sys.platform == "win32"
                        else ("bash", "-c", f"curl -fsSL {OFFICIAL_BUN_INSTALL_URL} | bash"))
        argv = tuple(request.bun_installer_argv) if request.bun_installer_argv is not None else default_argv
        bun_installer.install_bun(argv)
        os.environ["PATH"] = (
            f"{install_dir / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
        )
        probe = detect_bun()
        if probe is None:
            raise _fail("bun installer exited 0 but bun not found in install dir")
        bun = probe
        bun_action = InstallAction.INSTALLED

    omo_argv = build_omo_argv(request.subscription)
    if request.omo_installer_argv is not None:
        _assert_safe_omo_argv(request.omo_installer_argv)
        omo_argv = tuple(request.omo_installer_argv)
    if registrar.has_plugin(CURRENT_PLUGIN):
        omo_action = InstallAction.REUSED
    else:
        _ = omo_installer.install_omo(omo_argv)
        omo_action = InstallAction.INSTALLED
    legacy_warning = registrar.register_plugin(CURRENT_PLUGIN)
    return OmoInstallMetadata(bun, bun_action.value, omo_action.value, omo_argv, legacy_warning)
