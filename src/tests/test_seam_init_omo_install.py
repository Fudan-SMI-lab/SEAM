"""Given/When/Then tests for the Bun + OMO Ultimate installer.

Uses injectable fakes (BunInstaller / OmoInstaller / PluginRegistrar) plus a
local fake Bun shim on PATH. NEVER touches the real network, NEVER constructs
a command containing npm/npx/sudo/bun-add-g/omo/omo-ai/codex-autonomous, and
NEVER writes outside the test's tmp project root.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Sequence

import pytest

from seam_init.models import FailureKind, InitializerFailure, SafeDetail
from seam_init.omo_install import (
    CURRENT_PLUGIN,
    MINIMUM_BUN_VERSION,
    OMO_INSTALL_ARGV_PREFIX,
    OMO_PACKAGE,
    OMO_PLATFORM,
    BunVersion,
    InstallAction,
    JsoncPluginRegistrar,
    OmoInstallRequest,
    SubscriptionSelection,
    TriState,
    build_omo_argv,
    default_bun_dir,
    detect_bun,
    ensure_omo_install,
)

# Tokens that must NEVER appear as the executable or argv token anywhere.
_FORBIDDEN_INSTALL: frozenset[str] = frozenset(
    {"npm", "npx", "sudo", "apt", "apt-get", "brew", "yum", "dnf", "pacman"}
)
_FORBIDDEN_OMO: frozenset[str] = frozenset(
    {"omo", "omo-ai", "--codex-autonomous", "--no-codex-autonomous", "--skip-auth"}
)


# --- helpers ---------------------------------------------------------------


def _bun_basename() -> str:
    return "bun.cmd" if sys.platform == "win32" else "bun"


def _write_fake_bun(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = _bun_basename()
    if sys.platform == "win32":
        (directory / name).write_text(
            f"@echo off\r\necho {version}\r\n", encoding="ascii"
        )
    else:
        target = directory / name
        target.write_text(
            f"#!/usr/bin/env bash\necho {version}\n", encoding="ascii"
        )
        target.chmod(0o755)
    return directory / name


def _write_fake_bun_installer(tmp_path: Path) -> Path:
    """Fake Bun installer: argv = <install_dir> <version> [fail]."""
    script = tmp_path / "fake_bun_installer.py"
    body = textwrap.dedent(
        '''
        import os, sys
        install_dir = sys.argv[1]
        version = sys.argv[2]
        if len(sys.argv) > 3 and sys.argv[3] == "fail":
            sys.exit(1)
        os.makedirs(install_dir, exist_ok=True)
        bin_dir = os.path.join(install_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        if os.name == "nt":
            target = os.path.join(bin_dir, "bun.cmd")
            with open(target, "w", newline="\\r\\n") as fh:
                fh.write("@echo off\\r\\necho " + version + "\\r\\n")
        else:
            target = os.path.join(bin_dir, "bun")
            with open(target, "w") as fh:
                fh.write("#!/usr/bin/env bash\\necho " + version + "\\n")
            os.chmod(target, 0o755)
        ''',
    )
    script.write_text(body, encoding="utf-8")
    return script


def _bun_installer_argv(
    script: Path, install_dir: Path, version: str, *, fail: bool = False,
) -> list[str]:
    argv = [sys.executable, str(script), str(install_dir), version]
    if fail:
        argv.append("fail")
    for token in argv:
        base = Path(token).name.lower()
        assert base not in _FORBIDDEN_INSTALL, f"forbidden token: {token}"
    return argv


def _path_with(directory: Path) -> str:
    return f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"


def _default_subscription() -> SubscriptionSelection:
    return SubscriptionSelection(
        claude=TriState.YES,
        openai=False,
        gemini=False,
        copilot=False,
        opencode_zen=False,
        zai_coding_plan=False,
        opencode_go=False,
        kimi_for_coding=False,
        bailian_coding_plan=False,
        minimax_cn_coding_plan=False,
        minimax_coding_plan=False,
        vercel_ai_gateway=False,
    )


class _RecordingBunInstaller:
    """Fake BunInstaller that records argv and writes a fake bun shim."""

    def __init__(self, install_dir: Path, version: str) -> None:
        self.install_dir = install_dir
        self.version = version
        self.calls: list[list[str]] = []

    def install_bun(self, argv: Sequence[str]) -> None:
        self.calls.append(list(argv))
        # Reject forbidden install tokens (parity with production guard).
        for token in argv:
            base = Path(token).name.lower()
            assert base not in _FORBIDDEN_INSTALL, f"forbidden token: {token}"
        bin_dir = self.install_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        _write_fake_bun(bin_dir, self.version)


class _RecordingOmoInstaller:
    """Fake OmoInstaller that records argv; optionally fails Nth call."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._fail_first = fail_first

    def install_omo(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        for token in argv:
            assert token not in _FORBIDDEN_OMO, f"forbidden OMO token: {token}"
        if self._fail_first and len(self.calls) <= self._fail_first:
            raise InitializerFailure(
                kind=FailureKind.OMO_INSTALL,
                safe_detail=SafeDetail("fake omo installer failure"),
            )
        return "fake omo installer success"


class _FakeRegistrar:
    """In-memory registrar; preserves existing plugin entries."""

    def __init__(self, existing: list[str] | None = None) -> None:
        self._plugins: list[str] = list(existing or [])
        self.register_calls: list[str] = []

    def has_plugin(self, plugin: str) -> bool:
        return plugin in self._plugins

    def register_plugin(self, plugin: str) -> str:
        self.register_calls.append(plugin)
        if plugin not in self._plugins:
            self._plugins.append(plugin)
        legacy = [p for p in self._plugins if p != plugin and p.startswith("oh-my-")]
        if not legacy:
            return ""
        return f"retained legacy plugin entry {legacy!r}"


def _write_opencode_jsonc(project_root: Path, plugins: list[str]) -> Path:
    cfg_dir = project_root / ".opencode"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "opencode.jsonc"
    cfg.write_text(
        json.dumps({"$schema": "https://opencode.ai/config.json", "plugin": plugins},
                   indent=2)
        + "\n",
        encoding="utf-8",
    )
    return cfg


# --- argv construction tests ----------------------------------------------


def test_build_omo_argv_starts_with_exact_prefix() -> None:
    # Given: any subscription selection.
    selection = _default_subscription()
    # When
    argv = build_omo_argv(selection)
    # Then: argv begins with the exact mandated prefix.
    assert argv[: len(OMO_INSTALL_ARGV_PREFIX)] == OMO_INSTALL_ARGV_PREFIX
    assert argv[0] == "bunx"
    assert argv[1] == OMO_PACKAGE
    assert argv[2] == "install"
    assert argv[3] == "--no-tui"
    assert argv[4] == f"--platform={OMO_PLATFORM}"


def test_build_omo_argv_contains_every_provider_flag() -> None:
    # Given: a fully-off subscription selection.
    selection = SubscriptionSelection(
        claude=TriState.NO,
        openai=False, gemini=False, copilot=False,
        opencode_zen=False, zai_coding_plan=False, opencode_go=False,
        kimi_for_coding=False, bailian_coding_plan=False,
        minimax_cn_coding_plan=False, minimax_coding_plan=False,
        vercel_ai_gateway=False,
    )
    # When
    argv = build_omo_argv(selection)
    # Then: all twelve provider flags are present, each with an explicit value.
    flags = {token.split("=", 1)[0]: token for token in argv if token.startswith("--")}
    expected = {
        "--claude", "--openai", "--gemini", "--copilot", "--opencode-zen",
        "--zai-coding-plan", "--opencode-go", "--kimi-for-coding",
        "--bailian-coding-plan", "--minimax-cn-coding-plan",
        "--minimax-coding-plan", "--vercel-ai-gateway",
    }
    missing = expected - set(flags)
    assert not missing, f"missing flags: {missing}"
    # No flag may carry an implicit value (must be =yes / =no / =max20).
    for token in argv:
        if token.startswith("--") and token not in {"--no-tui"}:
            assert "=" in token, f"flag without value: {token}"


def test_build_omo_argv_emits_max20_for_claude() -> None:
    # Given: max20 Claude selection.
    selection = SubscriptionSelection(
        claude=TriState.MAX20,
        openai=True, gemini=False, copilot=False,
        opencode_zen=False, zai_coding_plan=False, opencode_go=False,
        kimi_for_coding=False, bailian_coding_plan=False,
        minimax_cn_coding_plan=False, minimax_coding_plan=False,
        vercel_ai_gateway=False,
    )
    # When
    argv = build_omo_argv(selection)
    # Then
    assert "--claude=max20" in argv
    assert "--openai=yes" in argv


def test_build_omo_argv_never_contains_forbidden_tokens() -> None:
    # Given: any subscription.
    selection = _default_subscription()
    # When
    argv = build_omo_argv(selection)
    # Then: no forbidden OMO tokens or wrong package names appear.
    for token in argv:
        assert token not in _FORBIDDEN_OMO
        head = token.split("=", 1)[0]
        assert head not in _FORBIDDEN_OMO
        assert token != "omo" and token != "omo-ai"
    # Never includes global-install verbs.
    assert "add" not in argv
    assert "install -g" not in " ".join(argv)


def test_build_omo_argv_rejects_explicit_forbidden_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a hand-crafted forbidden argv injected as an installer override.
    bin_dir = tmp_path / "bun"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    omo_installer = _RecordingOmoInstaller()
    bad_argv: list[str] = [
        "bunx", OMO_PACKAGE, "install", "--no-tui",
        f"--platform={OMO_PLATFORM}", "--codex-autonomous",
    ]
    request = OmoInstallRequest(
        subscription=_default_subscription(),
        omo_installer_argv=bad_argv,
    )
    # When / Then: the production guard refuses the forbidden override before
    # the OMO installer is invoked.
    with pytest.raises(InitializerFailure) as exc_info:
        ensure_omo_install(
            request,
            bun_installer=_RecordingBunInstaller(tmp_path / "x", "1.1.0"),
            omo_installer=omo_installer, registrar=_FakeRegistrar(),
        )
    assert exc_info.value.kind is FailureKind.OMO_INSTALL
    assert omo_installer.calls == []


# --- detection / version --------------------------------------------------


def test_bun_version_parse_and_order() -> None:
    # When / Then
    v100 = BunVersion.parse("1.0.0")
    v110 = BunVersion.parse("bun 1.1.0")
    none = BunVersion.parse("no version")
    assert v100 == BunVersion(1, 0, 0)
    assert v110 == BunVersion(1, 1, 0)
    assert none is None
    assert v100 is not None and v110 is not None
    assert v100 < v110


def test_detect_returns_none_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: an empty PATH so bun cannot resolve.
    monkeypatch.setenv("PATH", str(tmp_path))
    # When
    runtime = detect_bun()
    # Then
    assert runtime is None


def test_default_bun_dir_is_user_owned() -> None:
    # Given / When
    directory = default_bun_dir()
    # Then: lives under user home, never under a system directory.
    assert ".bun" in directory.parts
    assert str(directory).startswith(str(Path.home()))


# --- ensure_omo_install: bun reuse, install, refusal ---------------------


def test_existing_compatible_bun_is_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a compatible bun already on PATH.
    bin_dir = tmp_path / "existing"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    bun_installer = _RecordingBunInstaller(tmp_path / "install", "1.2.0")
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(subscription=_default_subscription())
    # When
    result = ensure_omo_install(
        request, bun_installer=bun_installer, omo_installer=omo_installer,
        registrar=registrar,
    )
    # Then: bun reused, no install attempted.
    assert bun_installer.calls == []
    assert result.bun_action == InstallAction.REUSED.value
    assert result.bun.version == BunVersion(1, 1, 0)


def test_stale_incompatible_bun_triggers_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a stale bun on PATH below the minimum.
    bin_dir = tmp_path / "stale"
    _write_fake_bun(bin_dir, "0.9.9")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    install_dir = tmp_path / "install"
    fake_script = _write_fake_bun_installer(tmp_path)
    from seam_init.omo_install import SubprocessInstaller
    real_bun = SubprocessInstaller(bun_install_dir=install_dir)
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(
        subscription=_default_subscription(),
        bun_install_dir=install_dir,
        bun_installer_argv=_bun_installer_argv(fake_script, install_dir, "1.2.0"),
    )
    # When
    result = ensure_omo_install(
        request, bun_installer=real_bun, omo_installer=omo_installer,
        registrar=registrar,
    )
    # Then: a fresh install happened; new bun is on PATH and reported.
    assert result.bun_action == InstallAction.INSTALLED.value
    assert result.bun.version == BunVersion(1, 2, 0)
    # Stale detection ignored the older 0.9.9.
    assert result.bun.version != BunVersion(0, 9, 9)


def test_absent_bun_installs_to_user_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: no bun anywhere on PATH.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "userbun"
    fake_script = _write_fake_bun_installer(tmp_path)
    from seam_init.omo_install import SubprocessInstaller
    real_bun = SubprocessInstaller(bun_install_dir=install_dir)
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(
        subscription=_default_subscription(),
        bun_install_dir=install_dir,
        bun_installer_argv=_bun_installer_argv(fake_script, install_dir, "1.0.0"),
    )
    # When
    result = ensure_omo_install(
        request, bun_installer=real_bun, omo_installer=omo_installer,
        registrar=registrar,
    )
    # Then: installed, binary exists, version readable.
    assert result.bun_action == InstallAction.INSTALLED.value
    assert result.bun.version == BunVersion(1, 0, 0)
    # User-owned path: install_dir is under tmp_path (a user dir).
    assert str(install_dir) in os.environ["PATH"] or "userbun" in os.environ["PATH"]
    assert result.bun.path.exists()


def test_custom_system_dir_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a custom install dir at a system location.
    monkeypatch.setenv("PATH", str(tmp_path))
    system_dir = (
        Path("C:/Windows/System32") if sys.platform == "win32" else Path("/usr/local")
    )
    fake_script = _write_fake_bun_installer(tmp_path)
    from seam_init.omo_install import SubprocessInstaller
    real_bun = SubprocessInstaller(bun_install_dir=system_dir)
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(
        subscription=_default_subscription(),
        bun_install_dir=system_dir,
        bun_installer_argv=_bun_installer_argv(fake_script, system_dir, "1.0.0"),
    )
    # When / Then
    with pytest.raises(InitializerFailure) as exc_info:
        ensure_omo_install(
            request, bun_installer=real_bun, omo_installer=omo_installer,
            registrar=registrar,
        )
    assert exc_info.value.kind is FailureKind.OMO_INSTALL
    assert "system" in str(exc_info.value.safe_detail).lower() or "refus" in str(exc_info.value.safe_detail).lower()


# --- ensure_omo_install: OMO install invocation --------------------------


def test_omo_installer_receives_canonical_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a compatible bun already present.
    bin_dir = tmp_path / "bun"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(subscription=_default_subscription())
    # When
    ensure_omo_install(
        request,
        bun_installer=_RecordingBunInstaller(tmp_path / "x", "1.1.0"),
        omo_installer=omo_installer, registrar=registrar,
    )
    # Then: exactly one OMO call; argv begins with the canonical prefix and
    # contains every provider flag.
    assert len(omo_installer.calls) == 1
    argv = omo_installer.calls[0]
    assert argv[: len(OMO_INSTALL_ARGV_PREFIX)] == list(OMO_INSTALL_ARGV_PREFIX)
    assert "--claude=yes" in argv  # default selection has claude=YES
    expected_flags = {
        "--claude", "--openai", "--gemini", "--copilot", "--opencode-zen",
        "--zai-coding-plan", "--opencode-go", "--kimi-for-coding",
        "--bailian-coding-plan", "--minimax-cn-coding-plan",
        "--minimax-coding-plan", "--vercel-ai-gateway",
    }
    flag_heads = {t.split("=", 1)[0] for t in argv if t.startswith("--") and "=" in t}
    assert expected_flags <= flag_heads


def test_omo_installer_skipped_when_plugin_already_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: compatible bun and OMO plugin already registered.
    bin_dir = tmp_path / "bun"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar(existing=[CURRENT_PLUGIN])
    request = OmoInstallRequest(subscription=_default_subscription())
    # When
    result = ensure_omo_install(
        request,
        bun_installer=_RecordingBunInstaller(tmp_path / "x", "1.1.0"),
        omo_installer=omo_installer, registrar=registrar,
    )
    # Then: OMO install was not invoked; action is REUSED.
    assert omo_installer.calls == []
    assert result.omo_action == InstallAction.REUSED.value


def test_omo_installer_failure_blocks_plugin_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: compatible bun but OMO installer always fails.
    bin_dir = tmp_path / "bun"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    omo_installer = _RecordingOmoInstaller(fail_first=1)
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(subscription=_default_subscription())
    # When / Then
    with pytest.raises(InitializerFailure) as exc_info:
        ensure_omo_install(
            request,
            bun_installer=_RecordingBunInstaller(tmp_path / "x", "1.1.0"),
            omo_installer=omo_installer, registrar=registrar,
        )
    assert exc_info.value.kind is FailureKind.OMO_INSTALL
    # Plugin registration never ran.
    assert registrar.register_calls == []


# --- plugin registration via JsoncPluginRegistrar -------------------------


def test_registrar_preserves_legacy_plugin_and_adds_current(tmp_path: Path) -> None:
    # Given: an existing config with a legacy plugin entry.
    cfg = _write_opencode_jsonc(tmp_path, ["oh-my-opencode"])
    registrar = JsoncPluginRegistrar(project_root=tmp_path)
    # When
    warning = registrar.register_plugin(CURRENT_PLUGIN)
    # Then: both entries are present, legacy preserved; warning explains it.
    data = json.loads(cfg.read_text(encoding="utf-8"))
    plugins = data["plugin"]
    assert "oh-my-openagent" in plugins
    assert "oh-my-opencode" in plugins
    assert "oh-my-opencode" in warning


def test_registrar_idempotent_when_current_already_present(tmp_path: Path) -> None:
    # Given: an existing config already listing the current plugin.
    cfg = _write_opencode_jsonc(tmp_path, [CURRENT_PLUGIN])
    before = cfg.read_bytes()
    registrar = JsoncPluginRegistrar(project_root=tmp_path)
    # When
    warning = registrar.register_plugin(CURRENT_PLUGIN)
    # Then: no duplicate; no legacy warning.
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["plugin"].count(CURRENT_PLUGIN) == 1
    assert warning == ""
    # Bytes may differ (re-serialization) but plugin list is the same set.
    assert set(json.loads(before)["plugin"]) == set(data["plugin"])


def test_registrar_preserves_unrelated_plugin_entries(tmp_path: Path) -> None:
    # Given: existing config with unrelated plugins plus the schema key.
    cfg = _write_opencode_jsonc(tmp_path, ["other-plugin", "oh-my-opencode"])
    registrar = JsoncPluginRegistrar(project_root=tmp_path)
    # When
    registrar.register_plugin(CURRENT_PLUGIN)
    # Then: $schema and unrelated plugins survive.
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert "other-plugin" in data["plugin"]
    assert CURRENT_PLUGIN in data["plugin"]


def test_registrar_missing_config_raises_typed_failure(tmp_path: Path) -> None:
    # Given: no .opencode/opencode.jsonc exists.
    registrar = JsoncPluginRegistrar(project_root=tmp_path)
    # When / Then
    with pytest.raises(InitializerFailure) as exc_info:
        registrar.register_plugin(CURRENT_PLUGIN)
    assert exc_info.value.kind is FailureKind.OMO_INSTALL


def test_registrar_corrupt_config_raises_typed_failure(tmp_path: Path) -> None:
    # Given: a config that is not valid JSON.
    cfg = _write_opencode_jsonc(tmp_path, [])
    cfg.write_text("{not valid json", encoding="utf-8")
    registrar = JsoncPluginRegistrar(project_root=tmp_path)
    # When / Then
    with pytest.raises(InitializerFailure) as exc_info:
        registrar.register_plugin(CURRENT_PLUGIN)
    assert exc_info.value.kind is FailureKind.OMO_INSTALL
    # Original bytes are intact (transaction never began).
    assert cfg.read_text(encoding="utf-8") == "{not valid json"


# --- metadata sanity -------------------------------------------------------


def test_metadata_has_no_secret_markers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a successful install.
    bin_dir = tmp_path / "bun"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    omo_installer = _RecordingOmoInstaller()
    registrar = _FakeRegistrar()
    request = OmoInstallRequest(subscription=_default_subscription())
    # When
    result = ensure_omo_install(
        request,
        bun_installer=_RecordingBunInstaller(tmp_path / "x", "1.1.0"),
        omo_installer=omo_installer, registrar=registrar,
    )
    # Then: no secret markers in metadata text fields.
    text = " ".join(
        [
            result.bun.version_text,
            result.bun_action,
            result.omo_action,
            result.legacy_warning,
        ]
    ).lower()
    for marker in ("sk-", "api_key", "apikey", "token", "password", "secret"):
        assert marker not in text


def test_user_paths_remain_user_owned_after_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a clean tmp project root.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "userbun"
    fake_script = _write_fake_bun_installer(tmp_path)
    from seam_init.omo_install import SubprocessInstaller
    real_bun = SubprocessInstaller(bun_install_dir=install_dir)
    request = OmoInstallRequest(
        subscription=_default_subscription(),
        bun_install_dir=install_dir,
        bun_installer_argv=_bun_installer_argv(fake_script, install_dir, "1.0.0"),
    )
    # When
    result = ensure_omo_install(
        request, bun_installer=real_bun,
        omo_installer=_RecordingOmoInstaller(), registrar=_FakeRegistrar(),
    )
    # Then: every recorded path lives under tmp_path (a user-owned root).
    for recorded in (str(result.bun.path), str(install_dir)):
        assert str(tmp_path) in recorded or str(Path.home()) in recorded
    # The default minimum (1.0.0) is met by the freshly installed bun.
    assert result.bun.version is not None
    assert result.bun.version >= MINIMUM_BUN_VERSION


def test_no_global_install_command_in_recorded_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a happy install.
    bin_dir = tmp_path / "bun"
    _write_fake_bun(bin_dir, "1.1.0")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    omo_installer = _RecordingOmoInstaller()
    request = OmoInstallRequest(subscription=_default_subscription())
    # When
    result = ensure_omo_install(
        request,
        bun_installer=_RecordingBunInstaller(tmp_path / "x", "1.1.0"),
        omo_installer=omo_installer, registrar=_FakeRegistrar(),
    )
    # Then: no global install / sudo / npm token ever appears.
    blob = " ".join(result.omo_argv).lower()
    for bad in ("npm", "npx", "sudo", "apt", "brew", "bun add", "bun install -g",
                " omo ", "omo-ai", "codex-autonomous", "--skip-auth"):
        assert bad not in blob, f"forbidden token {bad!r} in argv blob"
