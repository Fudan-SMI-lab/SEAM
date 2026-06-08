"""Load and render phase prompt templates from the prompts directory."""

import re
from pathlib import Path
from typing import ClassVar


class PromptLoader:
    """Loads prompt templates from .md files and substitutes {placeholder} variables."""

    prompts_dir: Path

    _OPTIONAL_SECTION_PATTERNS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("constraint_summary", r"\n## Migration Constraints(?: \(from Phase 1\.5\))?\n.*?(?=\n## |\Z)"),
        ("user_constraints", r"\n## User-Provided Constraints \(for awareness\)\n.*?(?=\n## |\Z)"),
        ("user_constraints", r"\n## User-Provided Migration Constraints\n.*?(?=\n## |\Z)"),
    )

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        """Initialize with the directory containing prompt templates.

        Args:
            prompts_dir: Path to the prompts directory.
                         Defaults to `prompts/` relative to this package.
        """
        if prompts_dir is None:
            prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        self.prompts_dir = Path(prompts_dir)

    def load_prompt(self, phase_id: str, context: dict[str, str] | None = None) -> str:
        """Load a prompt template and substitute placeholders.

        Args:
            phase_id: Identifier for the phase (e.g. 'analyze').
                      Loads `{phase_id}.md` from the prompts directory.
            context: Dict of placeholder name -> value for substitution.

        Returns:
            The rendered prompt string with all placeholders filled.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
            KeyError: If a placeholder in the template has no matching context key.
        """
        prompt_path = self.prompts_dir / f"{phase_id}.md"

        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_path}. "
                + f"Expected file: '{phase_id}.md' in {self.prompts_dir}"
            )

        template = prompt_path.read_text(encoding="utf-8")

        if context is None:
            context = {}

        for key, pattern in self._OPTIONAL_SECTION_PATTERNS:
            if not str(context.get(key, "")).strip():
                template = re.sub(pattern, "\n", template, flags=re.S)

        if not str(context.get("constraint_summary", "")).strip():
            template = re.sub(r",\s*\{constraint_summary\},\s*", ", ", template)

        # Default repair_role_descriptions to the three basic roles when not provided
        if "repair_role_descriptions" not in context:
            context = dict(context)
            context["repair_role_descriptions"] = (
                "## Repair Roles\n"
                "- `dependency_fixer`: Fix missing/mismatched packages, install commands, version conflicts, mirror configuration.\n"
                "- `code_adapter`: Fix Python-level API/device/tensor migration, device placement, backend strings.\n"
                "- `operator_fixer`: Fix shared-object, native-symbol, compiler, custom-kernel, custom-op final-gate evidence-level issues.\n"
                "\n"
                "## Output Field Semantics\n"
                "- `category`: One of `environment`, `dependency`, `pathing`, `migration logic`, `operator`, `validation`, `unknown`.\n"
                "- `root_cause`: Specific explanation with supporting evidence.\n"
                "- `suggested_fix`: Concrete corrective action for downstream repair agent.\n"
                "- `repair_role`: One of `dependency_fixer`, `code_adapter`, `operator_fixer`.\n"
                "- `entry_script_action.needed`: `true` only to replace the Phase 3 `run_command`, `false` otherwise.\n"
                "- `entry_script_action.action`: `\"none\"`, `\"regenerate\"`, or `\"modify\"`.\n"
                "- `entry_script_action.run_command`: The replacement command; non-empty when `needed=true`. Source edits belong to repair agents."
            )

        placeholders: list[str] = re.findall(r"\{(\w+)\}", template)

        missing_keys = [k for k in placeholders if k not in context]
        if missing_keys:
            raise KeyError(
                f"Missing context key(s) for prompt '{phase_id}': "
                + f"{', '.join(missing_keys)}. "
                + f"Provided keys: {list(context.keys())}"
            )

        result = template
        for key, value in context.items():
            result = result.replace(f"{{{key}}}", str(value))

        return result

    def list_prompts(self) -> list[str]:
        if not self.prompts_dir.exists():
            return []
        return sorted(
            f.name for f in self.prompts_dir.iterdir()
            if f.is_file() and f.name.endswith(".md")
        )
