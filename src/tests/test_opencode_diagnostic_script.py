from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "diagnose_seam_opencode.py"


def load_diag_module():
    spec = importlib.util.spec_from_file_location("diagnose_seam_opencode", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_env_patch_appends_required_values() -> None:
    diag = load_diag_module()
    patch = diag.build_env_patch(
        "http://127.0.0.1:4098",
        {"NO_PROXY": "example.com", "no_proxy": "localhost", "PYTHONUNBUFFERED": "0"},
    )

    assert patch["NO_PROXY"] == "example.com,127.0.0.1,localhost,::1"
    assert patch["no_proxy"] == "localhost,127.0.0.1,::1"
    assert patch["PYTHONUNBUFFERED"] == "1"


def test_remote_env_patch_does_not_change_no_proxy() -> None:
    diag = load_diag_module()
    patch = diag.build_env_patch(
        "http://10.0.0.2:4098",
        {"NO_PROXY": "example.com", "no_proxy": "localhost", "PYTHONUNBUFFERED": "1"},
    )

    assert "NO_PROXY" not in patch
    assert "no_proxy" not in patch
    assert "PYTHONUNBUFFERED" not in patch


def test_readiness_exit_code_mapping() -> None:
    diag = load_diag_module()

    status, exit_code = diag.readiness_status(
        {"open": False}, {}, {}, {},
    )
    assert status == "server_unreachable"
    assert exit_code == diag.EXIT_SERVER_UNREACHABLE

    status, exit_code = diag.readiness_status(
        {"open": True}, {"ok": True}, {"ok": True}, {"enabled": False},
    )
    assert status == "basic_ready"
    assert exit_code == diag.EXIT_BASIC_READY

    status, exit_code = diag.readiness_status(
        {"open": True}, {"ok": True}, {"ok": True}, {"enabled": True, "ok": True},
    )
    assert status == "ready"
    assert exit_code == diag.EXIT_READY
