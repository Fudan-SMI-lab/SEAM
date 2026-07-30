from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = (
    REPOSITORY_ROOT.parent / ".omo" / "plans" / "seam-runtime-continuation-trace.md"
)
SUPPORT_DOCUMENTS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "README.en.md",
    REPOSITORY_ROOT / "README.zh.md",
    REPOSITORY_ROOT / "docs" / "User_Guide.md",
    REPOSITORY_ROOT / "docs" / "migration_utils_platform_adaptation_guide.md",
    REPOSITORY_ROOT / "src" / "docs" / "E2E_TESTING.md",
)
LINUX_F2_REQUIREMENTS = (
    "POSIX symlink refusal",
    "bounded descriptor reads",
    "exact-object ownership",
    "atomic publication/writes",
    "TOCTOU-safe cleanup",
    "concurrent replacement safety",
    "Linux lock correctness",
)
WINDOWS_NON_GATING_CASES = (
    "NTFS junction/reparse",
    "st_file_attributes",
    "8.3 paths",
    "PowerShell",
    "GBK",
    "CRLF",
    "Windows path-length",
)


def test_user_runtime_documents_require_linux_and_python_310() -> None:
    # Given every active user-facing runtime or installation document.
    contents = {path: path.read_text(encoding="utf-8") for path in SUPPORT_DOCUMENTS}

    # When their production requirements are inspected.
    missing = {
        path: requirement
        for path, text in contents.items()
        for requirement in ("Linux", "Python 3.10+")
        if requirement not in text
    }

    # Then all documents declare the same supported platform and Python floor.
    assert not missing
    e2e_guide = contents[REPOSITORY_ROOT / "src" / "docs" / "E2E_TESTING.md"]
    assert "Real accelerator checks remain optional and non-gating." in e2e_guide


def test_active_f2_plan_targets_linux_without_weakening_linux_safety() -> None:
    # Given the active continuation plan without rewriting its historical notepads.
    plan = PLAN_PATH.read_text(encoding="utf-8")

    # When the production scope, verification environment, and F2 gate are selected.
    f2_line = next(line for line in plan.splitlines() if line.startswith("- [ ] F2."))

    # Then Linux/Python support and every Linux-relevant safety property remain gating.
    assert "Production support target: Linux with Python 3.10+." in plan
    assert "Mandatory environment: Linux with Python 3.10+" in plan
    assert "Linux production target" in f2_line
    assert "Python 3.10+" in f2_line
    assert all(requirement in f2_line for requirement in LINUX_F2_REQUIREMENTS)

    # And Windows-only behavior is explicit, complete, and non-gating for F2.
    assert "Windows-only checks are non-gating" in f2_line
    assert all(case in f2_line for case in WINDOWS_NON_GATING_CASES)
    assert "No mandatory real CUDA/NPU/vendor image/device tests." in plan


def test_active_launcher_enforces_python_310_floor() -> None:
    launcher = (REPOSITORY_ROOT / "src" / "scripts" / "run_e2e_v3.sh").read_text(
        encoding="utf-8"
    )

    assert "sys.version_info < (3, 10)" in launcher
    assert "python3.10 python3 python" in launcher
    assert "python3.9" not in launcher
    assert "python3.8" not in launcher
