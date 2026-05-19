"""Deterministic resolver for explicit workflow runtime skills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .types import RuntimeSkillsConfig

_INACTIVE_SKILL_STATUSES = frozenset({"archived", "consumed", "quarantined", "rejected"})


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
        self.skills_dir: Path = self.repo_root / "skills"
        self.local_skills_dir: Path = self._resolve_local_skills_dir()
        self._catalog_skill_statuses: dict[str, str] | None = None
        self.skill_roots: tuple[tuple[str, Path], ...] = (
            ("canonical", self.skills_dir),
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
        inactive: list[str] = []
        for name in selected_names:
            self._validate_skill_name(name)
            inactive_status = self._inactive_skill_status(name)
            if inactive_status:
                missing.append(name)
                inactive.append(f"{name} ({inactive_status})")
                continue

            skill = self._load_skill(name, config.inject_full)
            if skill is None:
                missing.append(name)
                continue
            skills.append(skill)

        if inactive:
            message = f"Inactive explicit runtime skills skipped: {', '.join(inactive)}"
            if config.missing == "error":
                raise FileNotFoundError(message)
            if config.missing == "warn":
                warnings.append(message)

        missing_without_inactive = [name for name in missing if not self._inactive_skill_status(name)]
        if missing_without_inactive:
            message = f"Missing explicit runtime skills: {', '.join(missing_without_inactive)}"
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
            data = self._json_object(data_path.read_text(encoding="utf-8"))
            content = self._format_skill_data(name, data) if inject_full else ""
            return RuntimeSkill(
                name=name,
                path=str(data_path),
                content=content,
                source=source,
                metadata=data,
            )

        return None

    def _inactive_skill_status(self, name: str) -> str:
        status = self._catalog_skill_status(name) or self._skill_data_status(name)
        return status if status in _INACTIVE_SKILL_STATUSES else ""

    def _catalog_skill_status(self, name: str) -> str:
        return self._load_catalog_skill_statuses().get(name, "")

    def _load_catalog_skill_statuses(self) -> dict[str, str]:
        if self._catalog_skill_statuses is not None:
            return self._catalog_skill_statuses

        statuses: dict[str, str] = {}
        catalog_path = self.repo_root / "memory" / "index" / "experiences.jsonl"
        if not catalog_path.is_file():
            self._catalog_skill_statuses = statuses
            return statuses

        for line in catalog_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = self._json_object(line)
            except json.JSONDecodeError:
                continue
            if str(entry.get("type") or "skill").strip().lower() != "skill":
                continue
            status = str(entry.get("status") or "").strip().lower()
            for skill_name in self._catalog_skill_names(entry):
                current = statuses.get(skill_name, "")
                if status in _INACTIVE_SKILL_STATUSES or not current:
                    statuses[skill_name] = status

        self._catalog_skill_statuses = statuses
        return statuses

    def _skill_data_status(self, name: str) -> str:
        for _, root in self.skill_roots:
            data_path = root / name / "skill_data.json"
            if not data_path.is_file():
                continue
            try:
                data = self._json_object(data_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            status = str(data.get("status") or "").strip().lower()
            if status:
                return status
        return ""

    def _catalog_skill_names(self, entry: dict[str, object]) -> set[str]:
        names: set[str] = set()
        for key in ("skill_name", "name"):
            value = entry.get(key)
            if isinstance(value, str) and self._is_valid_skill_name(value):
                names.add(value)

        entry_id = str(entry.get("id") or "")
        if entry_id.startswith("promoted-"):
            promoted_name = entry_id.removeprefix("promoted-")
            if self._is_valid_skill_name(promoted_name):
                names.add(promoted_name)

        asset_paths = entry.get("asset_paths")
        if isinstance(asset_paths, list):
            for raw_path in cast(list[object], asset_paths):
                if not isinstance(raw_path, str):
                    continue
                parts = Path(raw_path).parts
                for index, part in enumerate(parts[:-1]):
                    if part == "skills":
                        candidate = parts[index + 1]
                        if self._is_valid_skill_name(candidate):
                            names.add(candidate)
        return names

    def _json_object(self, text: str) -> dict[str, object]:
        loaded = cast(object, json.loads(text))
        if not isinstance(loaded, dict):
            return {}
        mapping = cast(dict[object, object], loaded)
        return {str(key): value for key, value in mapping.items()}

    def _is_valid_skill_name(self, name: str) -> bool:
        return bool(
            name
            and "/" not in name
            and "\\" not in name
            and name not in {".", ".."}
            and ".." not in Path(name).parts
        )

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
            value = data.get(key)
            if value is None or value == "" or value == []:
                continue
            lines.append(f"## {key.replace('_', ' ').title()}")
            if isinstance(value, list):
                lines.extend(f"- {str(item)}" for item in cast(list[object], value))
            else:
                lines.append(str(value))
            lines.append("")
        return "\n".join(lines).rstrip()

    def _validate_skill_name(self, name: str) -> None:
        if not self._is_valid_skill_name(name):
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
