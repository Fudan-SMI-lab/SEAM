"""Deterministic resolver for explicit workflow runtime skills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .types import RuntimeSkillsConfig


@dataclass
class RuntimeSkill:
    """Loaded promoted skill content."""

    name: str
    path: str
    content: str
    source: str = "canonical"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class RuntimeSkillBundle:
    """Resolved runtime skills and rendered prompt-ready markdown."""

    names: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    skills: list[RuntimeSkill] = field(default_factory=list)
    markdown: str = ""
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exclude_dynamic_duplicates: bool = True


class RuntimeSkillResolver:
    """Load explicit runtime skills from the promoted skills directory."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root: Path = Path(repo_root)
        self.skills_dir: Path = self.repo_root / ".memory" / "skills"
        self.direct_skills_dir: Path = self.repo_root / "skills"
        self.local_skills_dir: Path = self._resolve_local_skills_dir()
        self.skill_roots: tuple[tuple[str, Path], ...] = (
            ("canonical", self.skills_dir),
            ("direct", self.direct_skills_dir),
            ("local", self.local_skills_dir),
        )

    def resolve(
        self,
        agent_config: RuntimeSkillsConfig | None = None,
        phase_config: RuntimeSkillsConfig | None = None,
    ) -> RuntimeSkillBundle:
        config = self.merge_configs(agent_config, phase_config)
        names = self._ordered_unique(config.include)
        exclude = set(config.exclude)
        selected_names = [name for name in names if name not in exclude]

        skills: list[RuntimeSkill] = []
        missing: list[str] = []
        warnings: list[str] = []
        for name in selected_names:
            skill = self._load_skill(name, config.inject_full)
            if skill is None:
                missing.append(name)
                continue
            skills.append(skill)

        if missing:
            message = f"Missing explicit runtime skills: {', '.join(missing)}"
            if config.missing == "error":
                raise FileNotFoundError(message)
            if config.missing == "warn":
                warnings.append(message)

        return RuntimeSkillBundle(
            names=[skill.name for skill in skills],
            paths=[skill.path for skill in skills],
            skills=skills,
            markdown=self._format_markdown(skills),
            missing=missing,
            warnings=warnings,
            exclude_dynamic_duplicates=config.exclude_dynamic_duplicates,
        )

    def merge_configs(
        self,
        agent_config: RuntimeSkillsConfig | None,
        phase_config: RuntimeSkillsConfig | None,
    ) -> RuntimeSkillsConfig:
        if phase_config is None:
            return self._copy_config(agent_config)
        if phase_config.merge == "replace":
            return self._copy_config(phase_config)
        if phase_config.merge == "none":
            return RuntimeSkillsConfig(
                missing=phase_config.missing,
                inject_full=phase_config.inject_full,
                exclude_dynamic_duplicates=phase_config.exclude_dynamic_duplicates,
            )
        if phase_config.merge != "append":
            raise ValueError(f"Unsupported runtime skills merge policy: {phase_config.merge}")

        agent = self._copy_config(agent_config)
        return RuntimeSkillsConfig(
            include=self._ordered_unique(agent.include + phase_config.include),
            exclude=self._ordered_unique(agent.exclude + phase_config.exclude),
            merge="append",
            missing=phase_config.missing,
            inject_full=phase_config.inject_full,
            exclude_dynamic_duplicates=phase_config.exclude_dynamic_duplicates,
        )

    def _load_skill(self, name: str, inject_full: bool) -> RuntimeSkill | None:
        self._validate_skill_name(name)
        for source, root in self.skill_roots:
            skill = self._load_skill_from_root(name, root, source, inject_full)
            if skill is not None:
                return skill

        return None

    def _load_skill_from_root(
        self,
        name: str,
        root: Path,
        source: str,
        inject_full: bool,
    ) -> RuntimeSkill | None:
        skill_dir = root / name
        markdown_path = skill_dir / "SKILL.md"
        data_path = skill_dir / "skill_data.json"

        if markdown_path.is_file():
            content = markdown_path.read_text(encoding="utf-8")
            return RuntimeSkill(
                name=name,
                path=str(markdown_path),
                content=content if inject_full else "",
                source=source,
                metadata={},
            )

        if data_path.is_file():
            loaded_text = data_path.read_text(encoding="utf-8")
            loaded_obj = cast(object, json.loads(loaded_text))
            data = cast(dict[str, object], loaded_obj) if isinstance(loaded_obj, dict) else {}
            content = self._format_skill_data(name, data) if inject_full else ""
            return RuntimeSkill(
                name=name,
                path=str(data_path),
                content=content,
                source=source,
                metadata=data,
            )

        return None

    def _format_markdown(self, skills: list[RuntimeSkill]) -> str:
        if not skills:
            return ""
        lines = ["## Explicit Runtime Skills", ""]
        for skill in skills:
            lines.append(f"### {skill.name}")
            lines.append(f"Source: `{skill.path}`")
            if skill.content:
                lines.append("")
                lines.append(skill.content.strip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _format_skill_data(self, name: str, data: dict[str, object]) -> str:
        title = data.get("title") or data.get("description") or name
        lines = [f"# {str(title)}", ""]
        for key in ("when_to_use", "root_cause", "fix_steps", "steps", "antipatterns", "references"):
            if key not in data or data[key] in (None, "", []):
                continue
            lines.append(f"## {key.replace('_', ' ').title()}")
            value = data[key]
            if isinstance(value, list):
                value_items = cast(list[object], value)
                lines.extend(f"- {str(item)}" for item in value_items)
            else:
                lines.append(str(value))
            lines.append("")
        return "\n".join(lines).rstrip()

    def _validate_skill_name(self, name: str) -> None:
        if not name or "/" in name or "\\" in name or name in {".", ".."} or ".." in Path(name).parts:
            raise ValueError(f"Invalid runtime skill name: {name!r}")

    def _copy_config(self, config: RuntimeSkillsConfig | None) -> RuntimeSkillsConfig:
        if config is None:
            return RuntimeSkillsConfig()
        return RuntimeSkillsConfig(
            include=list(config.include),
            exclude=list(config.exclude),
            merge=config.merge,
            missing=config.missing,
            inject_full=config.inject_full,
            exclude_dynamic_duplicates=config.exclude_dynamic_duplicates,
        )

    def _resolve_local_skills_dir(self) -> Path:
        local_root = self.repo_root / ".skills"
        parent_root = self.repo_root.parent / ".skills"
        if parent_root.is_dir() and not local_root.is_dir():
            return parent_root
        return local_root

    def _ordered_unique(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result
