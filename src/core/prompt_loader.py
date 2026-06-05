"""Load and render phase prompt templates from the prompts directory."""

import re
from pathlib import Path
from typing import ClassVar

from core.routes import DEFAULT_PROMPT_FALLBACK_SUFFIXES


class PromptLoader:
    """Loads prompt templates from .md files and substitutes {placeholder} variables."""

    prompts_dir: Path
    prompt_fallback_suffixes: tuple[str, ...]

    _OPTIONAL_SECTION_PATTERNS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("constraint_summary", r"\n## Migration Constraints(?: \(from Phase 1\.5\))?\n.*?(?=\n## |\Z)"),
        ("user_constraints", r"\n## User-Provided Constraints \(for awareness\)\n.*?(?=\n## |\Z)"),
        ("user_constraints", r"\n## User-Provided Migration Constraints\n.*?(?=\n## |\Z)"),
    )

    def __init__(
        self,
        prompts_dir: str | Path | None = None,
        prompt_fallback_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        """Initialize with the directory containing prompt templates.

        Args:
            prompts_dir: Path to the prompts directory.
                         Defaults to `prompts/` relative to this package.
            prompt_fallback_suffixes: Suffixes to try when a prompt file
                         is missing.  When None, uses
                         ``DEFAULT_PROMPT_FALLBACK_SUFFIXES``.  Callers
                         with an active PlatformPolicy can pass
                         ``policy.prompt_fallback_suffixes``.
        """
        if prompts_dir is None:
            prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        self.prompts_dir = Path(prompts_dir)
        if prompt_fallback_suffixes is None:
            prompt_fallback_suffixes = DEFAULT_PROMPT_FALLBACK_SUFFIXES
        self.prompt_fallback_suffixes = prompt_fallback_suffixes

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
            # Fallback: try platform-specific suffixes for renamed prompts
            for suffix in self.prompt_fallback_suffixes:
                fallback_path = self.prompts_dir / f"{phase_id}{suffix}.md"
                if fallback_path.exists():
                    prompt_path = fallback_path
                    phase_id = f"{phase_id}{suffix}"
                    break
            else:
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
