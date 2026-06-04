# pyright: reportUnusedCallResult=false

"""Verification test for PromptLoader (T6)."""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.prompt_loader import PromptLoader


def test_load_and_substitute():
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "test_phase.md"
        prompt_file.write_text("Hello {name}, welcome to {place}!", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmpdir)
        result = loader.load_prompt("test_phase", context={"name": "World", "place": "Earth"})
        assert result == "Hello World, welcome to Earth!", f"Got: {result!r}"
        print("PASS: load_prompt substitutes placeholders correctly")


def test_missing_key_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "greet.md"
        prompt_file.write_text("Hello {name} from {city}", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmpdir)
        try:
            loader.load_prompt("greet", context={"name": "Alice"})
            assert False, "Should have raised KeyError"
        except KeyError as e:
            error_msg = str(e)
            assert "city" in error_msg, f"Error should mention missing key 'city': {error_msg}"
            assert "name" in error_msg, f"Error should show provided keys: {error_msg}"
            print(f"PASS: missing key raises informative KeyError: {error_msg}")


def test_missing_file_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = PromptLoader(prompts_dir=tmpdir)
        try:
            loader.load_prompt("nonexistent")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError as e:
            print(f"PASS: missing file raises FileNotFoundError: {e}")


def test_list_prompts():
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "alpha.md").write_text("# Alpha")
        Path(tmpdir, "beta.md").write_text("# Beta")
        Path(tmpdir, "skip.txt").write_text("not a prompt")

        loader = PromptLoader(prompts_dir=tmpdir)
        prompts = loader.list_prompts()
        assert prompts == ["alpha.md", "beta.md"], f"Got: {prompts}"
        print("PASS: list_prompts returns sorted .md files only")


def test_no_context_needed():
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "static.md"
        prompt_file.write_text("No placeholders here.", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmpdir)
        result = loader.load_prompt("static")
        assert result == "No placeholders here.", f"Got: {result!r}"
        print("PASS: load_prompt works with no context")


def test_empty_constraint_summary_strips_optional_blocks():
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "constraints.md"
        prompt_file.write_text(
            """# Example

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding.

## Required Actions
Follow the plan.

Operator note: {first}, {constraint_summary}, {last}
""",
            encoding="utf-8",
        )

        loader = PromptLoader(prompts_dir=tmpdir)

        populated = loader.load_prompt(
            "constraints",
            context={
                "constraint_summary": "Rule 1: No CPU fallback",
                "first": "alpha",
                "last": "omega",
            },
        )
        assert "Migration Constraints (from Phase 1.5)" in populated
        assert "Rule 1: No CPU fallback" in populated
        assert "alpha, Rule 1: No CPU fallback, omega" in populated

        empty = loader.load_prompt(
            "constraints",
            context={"constraint_summary": "", "first": "alpha", "last": "omega"},
        )
        assert "Migration Constraints (from Phase 1.5)" not in empty
        assert "These constraints are binding." not in empty
        assert "Rule 1: No CPU fallback" not in empty
        assert "alpha, omega" in empty
        assert ", ," not in empty


def test_empty_user_constraints_strip_phase_sections():
    loader = PromptLoader()

    phase_1_populated = loader.load_prompt(
        "phase_1_project_analysis_npu",
        context={
            "phase_name": "Phase 1",
            "project_dir": "/tmp/project",
            "user_constraints": "Zero CPU fallback",
        },
    )
    assert "User-Provided Constraints (for awareness)" in phase_1_populated
    assert "Zero CPU fallback" in phase_1_populated

    phase_1_empty = loader.load_prompt(
        "phase_1_project_analysis_npu",
        context={
            "phase_name": "Phase 1",
            "project_dir": "/tmp/project",
            "user_constraints": "",
        },
    )
    assert "User-Provided Constraints (for awareness)" not in phase_1_empty
    assert "Zero CPU fallback" not in phase_1_empty
    assert "{user_constraints}" not in phase_1_empty

    phase_1_5_populated = loader.load_prompt(
        "phase_1_5_constraint_summary_npu",
        context={
            "project_dir": "/tmp/project",
            "phase_1_context": "analysis",
            "user_constraints": "Zero CPU fallback",
        },
    )
    assert "User-Provided Migration Constraints" in phase_1_5_populated
    assert "Zero CPU fallback" in phase_1_5_populated

    phase_1_5_empty = loader.load_prompt(
        "phase_1_5_constraint_summary_npu",
        context={
            "project_dir": "/tmp/project",
            "phase_1_context": "analysis",
            "user_constraints": "",
        },
    )
    assert "User-Provided Migration Constraints" not in phase_1_5_empty
    assert "The user has explicitly provided the following constraints" not in phase_1_5_empty
    assert "Zero CPU fallback" not in phase_1_5_empty
    assert "{user_constraints}" not in phase_1_5_empty


def test_default_prompts_dir():
    loader = PromptLoader()
    assert "prompts" in str(loader.prompts_dir)
    print(f"PASS: default prompts_dir resolves to: {loader.prompts_dir}")


def test_list_prompts_includes_new_templates():
    loader = PromptLoader()
    prompts = loader.list_prompts()
    assert "phase_1_5_constraint_summary_npu.md" in prompts, "Missing phase_1_5_constraint_summary.md"
    assert "phase_5_review_npu.md" in prompts, "Missing phase_5_review.md"


if __name__ == "__main__":
    test_load_and_substitute()
    test_missing_key_raises()
    test_missing_file_raises()
    test_list_prompts()
    test_no_context_needed()
    test_default_prompts_dir()
    print("\nAll T6 tests PASSED")
