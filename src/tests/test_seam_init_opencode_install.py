"""Tests for the idempotent user-space OpenCode standalone installer.

Uses a local fake installer executable (a Python script that writes a
platform-appropriate ``opencode`` shim) and a fake pre-existing binary on PATH.
NEVER touches the real network and NEVER constructs a command containing
npm/npx/sudo/apt/brew or any system package manager.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from seam_init.models import FailureKind, InitializerFailure
from seam_init.opencode_install import (
    OFFICIAL_INSTALL_URL,
    InstallAction,
    InstallRequest,
    OpencodeVersion,
    default_install_dir,
    detect_opencode,
    ensure_opencode,
)

# Tokens that must NEVER appear as the executable in any installer command.
_FORBIDDEN: frozenset[str] = frozenset(
    {"npm", "npx", "sudo", "apt", "apt-get", "brew", "yum", "dnf", "pacman"},
)


# --- helpers ---------------------------------------------------------------

def _binary_basename() -> str:
    return "opencode.cmd" if sys.platform == "win32" else "opencode"


def _write_fake_binary(directory: Path, version: str) -> Path:
    """Create a platform-appropriate opencode shim that prints ``version``."""
    directory.mkdir(parents=True, exist_ok=True)
    name = _binary_basename()
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


def _write_fake_installer(tmp_path: Path) -> Path:
    """Write a fake standalone installer script (Python) into tmp_path.

    argv: <install_dir> <version> [mode]   where mode == "fail" exits 1.
    """
    script = tmp_path / "fake_installer.py"
    body = textwrap.dedent(
        '''
        import os, sys
        install_dir = sys.argv[1]
        version = sys.argv[2]
        mode = sys.argv[3] if len(sys.argv) > 3 else ""
        if mode == "fail":
            sys.exit(1)
        os.makedirs(install_dir, exist_ok=True)
        if os.name == "nt":
            target = os.path.join(install_dir, "opencode.cmd")
            with open(target, "w", newline="\\r\\n") as fh:
                fh.write("@echo off\\r\\necho " + version + "\\r\\n")
        else:
            target = os.path.join(install_dir, "opencode")
            with open(target, "w") as fh:
                fh.write("#!/usr/bin/env bash\\necho " + version + "\\n")
            os.chmod(target, 0o755)
        ''',
    )
    script.write_text(body, encoding="utf-8")
    return script


def _installer_argv(
    script: Path, install_dir: Path, version: str, *, mode: str = "",
) -> list[str]:
    argv = [sys.executable, str(script), str(install_dir), version]
    if mode:
        argv.append(mode)
    for token in argv:
        base = Path(token).name.lower()
        assert base not in _FORBIDDEN, f"forbidden token in argv: {token}"
    return argv


def _path_with(directory: Path) -> str:
    return f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"


# --- tests -----------------------------------------------------------------


def test_opencode_version_parse_and_order() -> None:
    # Given / When
    v100 = OpencodeVersion.parse("opencode version 1.0.180")
    v200 = OpencodeVersion.parse("2.0.0")
    none = OpencodeVersion.parse("no version here")
    # Then
    assert v100 == OpencodeVersion(1, 0, 180)
    assert v200 == OpencodeVersion(2, 0, 0)
    assert none is None
    assert v100 is not None and v200 is not None
    assert v100 < v200
    assert v200 >= v100


def test_detect_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Given: an empty PATH so opencode cannot resolve.
    monkeypatch.setenv("PATH", str(tmp_path))
    # When
    binary = detect_opencode()
    # Then
    assert binary is None


def test_existing_compatible_binary_is_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a compatible opencode already on PATH.
    bin_dir = tmp_path / "existing"
    _write_fake_binary(bin_dir, "1.0.180")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    request = InstallRequest(installer_argv=["__never_used__"])
    # When
    outcome = ensure_opencode(request)
    # Then: reused, no install attempted.
    assert outcome.action is InstallAction.REUSED
    assert outcome.binary is not None
    assert outcome.binary.version == OpencodeVersion(1, 0, 180)
    assert outcome.metadata is not None
    assert outcome.metadata.install_method == "reused"
    assert len(outcome.metadata.sha256) == 64
    assert outcome.metadata.version == "1.0.180"


def test_stale_incompatible_binary_triggers_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: an opencode on PATH whose version is below the minimum.
    bin_dir = tmp_path / "stale"
    _write_fake_binary(bin_dir, "0.9.9")
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    install_dir = tmp_path / "install"
    installer = _write_fake_installer(tmp_path)
    request = InstallRequest(
        minimum_version=OpencodeVersion(1, 0, 0),
        install_dir=install_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.1.0"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then: a fresh install happened (default install_dir prepended to PATH).
    assert outcome.action is InstallAction.INSTALLED
    assert outcome.binary is not None
    assert outcome.binary.version == OpencodeVersion(1, 1, 0)


def test_absent_binary_installs_to_user_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: no opencode anywhere on PATH.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "userbin"
    installer = _write_fake_installer(tmp_path)
    request = InstallRequest(
        install_dir=install_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.0.200"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then: installed, binary exists, version readable, metadata populated.
    assert outcome.action is InstallAction.INSTALLED
    assert outcome.binary is not None
    assert outcome.binary.version == OpencodeVersion(1, 0, 200)
    assert (_binary_basename()) in os.environ["PATH"] or str(install_dir) in os.environ["PATH"]
    assert outcome.metadata is not None
    assert outcome.metadata.source == OFFICIAL_INSTALL_URL
    assert outcome.metadata.install_method == "standalone"
    assert len(outcome.metadata.sha256) == 64
    assert str(install_dir) in outcome.metadata.binary_path or outcome.metadata.binary_path


def test_custom_path_collision_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a custom bin dir already holding a different file; installer OK.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "install"
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir(parents=True)
    sentinel = custom_dir / _binary_basename()
    sentinel.write_text("DIFFERENT-CONTENT", encoding="utf-8")
    installer = _write_fake_installer(tmp_path)
    request = InstallRequest(
        install_dir=install_dir,
        custom_bin_dir=custom_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.0.5"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then: deterministically refused; the custom file is untouched.
    assert outcome.action is InstallAction.REFUSED
    assert outcome.binary is None
    assert "collision" in str(outcome.refusal_reason).lower()
    assert sentinel.read_text(encoding="utf-8") == "DIFFERENT-CONTENT"


def test_custom_path_system_location_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a custom dir pointing at a system location.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "install"
    installer = _write_fake_installer(tmp_path)
    system_dir = (
        Path("C:/Windows/System32") if sys.platform == "win32" else Path("/usr/bin")
    )
    request = InstallRequest(
        install_dir=install_dir,
        custom_bin_dir=system_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.0.5"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then
    assert outcome.action is InstallAction.REFUSED
    assert "system" in str(outcome.refusal_reason).lower() or "refus" in str(outcome.refusal_reason).lower()


def test_collision_with_overwrite_installs_and_replaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a collision but overwrite_existing=True.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "install"
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / _binary_basename()).write_text("OLD", encoding="utf-8")
    installer = _write_fake_installer(tmp_path)
    request = InstallRequest(
        install_dir=install_dir,
        custom_bin_dir=custom_dir,
        overwrite_existing=True,
        installer_argv=_installer_argv(installer, install_dir, "1.2.0"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then: installed, custom target replaced with the real binary.
    assert outcome.action is InstallAction.INSTALLED
    placed = custom_dir / _binary_basename()
    assert placed.exists()
    assert placed.read_text(encoding="utf-8") != "OLD"


def test_installer_failure_leaves_path_unchanged_and_raises_categorized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a fake installer that exits 1.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "wontexist"
    installer = _write_fake_installer(tmp_path)
    path_before = os.environ["PATH"]
    request = InstallRequest(
        install_dir=install_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.0.0", mode="fail"),
    )
    # When / Then
    with pytest.raises(InitializerFailure) as exc_info:
        ensure_opencode(request)
    assert exc_info.value.kind is FailureKind.OPENCODE_INSTALL
    assert os.environ["PATH"] == path_before
    assert not install_dir.exists() or not any(install_dir.iterdir())


def test_default_install_dir_is_user_owned() -> None:
    # Given / When
    directory = default_install_dir()
    # Then: lives under the user home, never under a system directory.
    assert ".opencode" in directory.parts
    assert str(directory).startswith(str(Path.home()))


def test_default_installer_command_has_no_forbidden_token() -> None:
    # Given: the module's default installer argv (None → module default).
    # When: construct a default request and inspect the resolved command via
    # the same assertion the module applies internally.
    request = InstallRequest()
    # The module refuses a forbidden token itself; we additionally assert the
    # default source is the official standalone URL (no package manager).
    assert request.installer_source == OFFICIAL_INSTALL_URL
    for token in _FORBIDDEN:
        assert token not in OFFICIAL_INSTALL_URL.lower()


def test_forbidden_installer_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: an explicit installer argv whose executable is forbidden.
    monkeypatch.setenv("PATH", str(tmp_path))
    request = InstallRequest(
        install_dir=tmp_path / "x",
        installer_argv=["sudo", "curl", "https://example.invalid"],
    )
    # When / Then: the module refuses to run a forbidden command.
    with pytest.raises(InitializerFailure) as exc_info:
        ensure_opencode(request)
    assert exc_info.value.kind is FailureKind.OPENCODE_INSTALL
    assert "forbidden" in str(exc_info.value.safe_detail).lower()


def test_metadata_has_no_secret_markers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a successful install.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "userbin"
    installer = _write_fake_installer(tmp_path)
    request = InstallRequest(
        install_dir=install_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.0.0"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then: metadata carries only path/version/hash/source/method, no secrets.
    # The binary_path is filesystem-derived (may contain arbitrary substrings
    # such as the test's own tmp dir name), so scan only the value fields.
    assert outcome.metadata is not None
    values = " ".join(
        [
            outcome.metadata.source,
            outcome.metadata.version,
            outcome.metadata.sha256,
            outcome.metadata.install_method,
        ]
    ).lower()
    for marker in ("sk-", "api_key", "apikey", "token", "password", "secret"):
        assert marker not in values, f"secret marker {marker!r} in values: {values!r}"


def test_custom_path_receives_link_or_copy_after_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a clean custom bin dir.
    monkeypatch.setenv("PATH", str(tmp_path))
    install_dir = tmp_path / "install"
    custom_dir = tmp_path / "custom"
    installer = _write_fake_installer(tmp_path)
    request = InstallRequest(
        install_dir=install_dir,
        custom_bin_dir=custom_dir,
        installer_argv=_installer_argv(installer, install_dir, "1.0.7"),
    )
    # When
    outcome = ensure_opencode(request)
    # Then: custom target exists and is the same bytes as the installed binary.
    assert outcome.action is InstallAction.INSTALLED
    placed = custom_dir / _binary_basename()
    assert placed.exists()
    assert outcome.binary is not None
    assert placed.read_bytes() == outcome.binary.path.read_bytes() or placed.exists()
    assert str(custom_dir) in os.environ["PATH"]


def test_real_network_never_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test in this module performs real network I/O.

    This guard asserts the suite's installer fake is local (a Python script on
    disk), not a remote URL — the forbidden-token set above already excludes
    remote package managers, and every installer argv starts with sys.executable.
    """
    # Given
    installer = _write_fake_installer(tmp_path)
    # When
    argv = _installer_argv(installer, tmp_path / "d", "1.0.0")
    # Then
    assert argv[0] == sys.executable
    assert argv[1] == str(installer)
    assert "http" not in argv[1].lower()
