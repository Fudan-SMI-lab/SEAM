"""Verify that early-phase prompts do not contain forward-phase or framework-specific wording."""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PROMPTS_DIR = PROJECT_ROOT / "prompts"

EARLY_PHASE_PATTERNS = (
    "phase_0_",
    "phase_1_",
    "phase_2_",
    "phase_3_",
    "phase_3_entry_",
    "phase_35_",
)

LATER_PHASE_PATTERNS = (
    "phase_5_",
    "phase_6_",
    "phase_4_",
    "repair_",
    "experience_",
    "phase_error_",
    "phase_review_",
    "container_image_",
)


def _prompt_files():
    return sorted(f for f in PROMPTS_DIR.iterdir() if f.suffix == ".md")


def _is_early_phase(filename: str) -> bool:
    name = filename.lower()
    if any(name.startswith(p) for p in LATER_PHASE_PATTERNS):
        return False
    return any(p in name for p in EARLY_PHASE_PATTERNS)


@pytest.mark.parametrize("prompt_file", _prompt_files())
def test_no_forward_phase_5_wording_in_early_prompts(prompt_file: Path) -> None:
    if not _is_early_phase(prompt_file.name):
        pytest.skip(f"{prompt_file.name} is not an early-phase prompt")
    text = prompt_file.read_text(encoding="utf-8")
    assert "Phase 5" not in text, (
        f"{prompt_file.name} contains forward 'Phase 5' wording. "
        f"Replace with 'target runtime' or equivalent neutral language."
    )


@pytest.mark.parametrize("prompt_file", _prompt_files())
def test_no_opencode_wording_in_early_prompts(prompt_file: Path) -> None:
    if not _is_early_phase(prompt_file.name):
        pytest.skip(f"{prompt_file.name} is not an early-phase prompt")
    text = prompt_file.read_text(encoding="utf-8")
    assert "OpenCode" not in text, (
        f"{prompt_file.name} contains explicit 'OpenCode' wording. "
        f"Replace with 'file tools' or equivalent neutral language."
    )
