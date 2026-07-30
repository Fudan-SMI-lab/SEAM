from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_DOCUMENTS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "README.en.md",
    REPOSITORY_ROOT / "README.zh.md",
    REPOSITORY_ROOT / "docs" / "User_Guide.md",
    REPOSITORY_ROOT / "docs" / "migration_utils_platform_adaptation_guide.md",
    REPOSITORY_ROOT / "src" / "docs" / "E2E_TESTING.md",
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


def test_active_launcher_enforces_python_310_floor() -> None:
    launcher = (REPOSITORY_ROOT / "src" / "scripts" / "run_e2e_v3.sh").read_text(
        encoding="utf-8"
    )

    assert "sys.version_info < (3, 10)" in launcher
    assert "python3.10 python3 python" in launcher
    assert "python3.9" not in launcher
    assert "python3.8" not in launcher
