"""Tests for explicit runtime skill resolution."""

import json
from pathlib import Path
from typing import Any

import pytest

from core.runtime_skill_resolver import RuntimeSkillResolver
from core.types import RuntimeSkillsConfig


def write_skill(
    root: Path, name: str, markdown: str | None = None, data: dict[str, Any] | None = None
) -> Path:
    skill_dir = root / ".memory" / "skills" / name
    skill_dir.mkdir(parents=True)
    if markdown is not None:
        (skill_dir / "SKILL.md").write_text(markdown, encoding="utf-8")
    if data is not None:
        (skill_dir / "skill_data.json").write_text(json.dumps(data), encoding="utf-8")
    return skill_dir


def write_local_skill(
    root: Path, name: str, markdown: str | None = None, data: dict[str, Any] | None = None
) -> Path:
    skill_dir = root / ".skills" / name
    skill_dir.mkdir(parents=True)
    if markdown is not None:
        (skill_dir / "SKILL.md").write_text(markdown, encoding="utf-8")
    if data is not None:
        (skill_dir / "skill_data.json").write_text(json.dumps(data), encoding="utf-8")
    return skill_dir


def test_resolves_markdown_skill_before_json_fallback(tmp_path: Path):
    write_skill(
        tmp_path,
        "npu-skill",
        markdown="# Rendered Skill\n\nUse the rendered guidance.",
        data={"title": "JSON Skill", "fix_steps": ["Use JSON"]},
    )

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["npu-skill"])
    )

    assert bundle.names == ["npu-skill"]
    assert bundle.paths == [str(tmp_path / ".memory" / "skills" / "npu-skill" / "SKILL.md")]
    assert "## Explicit Runtime Skills" in bundle.markdown
    assert "# Rendered Skill" not in bundle.markdown
    assert bundle.missing == []  # pylint: disable=use-implicit-booleaness-not-comparison; silent


def test_falls_back_to_skill_data_json(tmp_path: Path):
    write_skill(
        tmp_path,
        "json-only",
        data={
            "title": "JSON Only Skill",
            "when_to_use": "When dependency setup fails",
            "fix_steps": ["Install torch-npu", "Pin CPU torch"],
        },
    )

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["json-only"])
    )

    assert bundle.names == ["json-only"]
    assert bundle.paths == [str(tmp_path / ".memory" / "skills" / "json-only" / "skill_data.json")]
    assert "# JSON Only Skill" not in bundle.markdown
    assert "- Install torch-npu" not in bundle.markdown


def test_inject_full_true_includes_skill_content(tmp_path: Path):
    write_skill(tmp_path, "brief", markdown="# Brief\n\nFull content")

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["brief"], inject_full=True)
    )

    assert bundle.names == ["brief"]
    assert "Full content" in bundle.markdown


def test_resolves_local_skills_markdown_when_canonical_missing(tmp_path: Path):
    write_local_skill(tmp_path, "local-pack", markdown="# Local Pack\n\nLocal guidance")

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["local-pack"], inject_full=True)
    )

    assert bundle.names == ["local-pack"]
    assert bundle.paths == [str(tmp_path / ".skills" / "local-pack" / "SKILL.md")]
    assert bundle.skills[0].source == "local"
    assert "Local guidance" in bundle.markdown


def test_canonical_skill_wins_over_same_named_local_skill(tmp_path: Path):
    write_skill(tmp_path, "same", markdown="# Canonical\n\nCanonical guidance")
    write_local_skill(tmp_path, "same", markdown="# Local\n\nLocal guidance")

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["same"], inject_full=True)
    )

    assert bundle.paths == [str(tmp_path / ".memory" / "skills" / "same" / "SKILL.md")]
    assert bundle.skills[0].source == "canonical"
    assert "Canonical guidance" in bundle.markdown
    assert "Local guidance" not in bundle.markdown


def test_local_skill_data_json_fallback_when_markdown_missing(tmp_path: Path):
    write_local_skill(
        tmp_path,
        "local-json",
        data={"title": "Local JSON", "fix_steps": ["Use local json"]},
    )

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["local-json"], inject_full=True)
    )

    assert bundle.paths == [str(tmp_path / ".skills" / "local-json" / "skill_data.json")]
    assert "# Local JSON" in bundle.markdown


def test_merges_agent_and_phase_runtime_skills_with_excludes(tmp_path: Path):
    for name in ("agent-a", "agent-b", "phase-c"):
        write_skill(tmp_path, name, markdown=f"# {name}")

    resolver = RuntimeSkillResolver(tmp_path)
    bundle = resolver.resolve(
        agent_config=RuntimeSkillsConfig(include=["agent-a", "agent-b"]),
        phase_config=RuntimeSkillsConfig(include=["agent-b", "phase-c"], exclude=["agent-a"]),
    )

    assert bundle.names == ["agent-b", "phase-c"]


def test_phase_replace_merge_ignores_agent_runtime_skills(tmp_path: Path):
    for name in ("agent-a", "phase-a"):
        write_skill(tmp_path, name, markdown=f"# {name}")

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        agent_config=RuntimeSkillsConfig(include=["agent-a"]),
        phase_config=RuntimeSkillsConfig(include=["phase-a"], merge="replace"),
    )

    assert bundle.names == ["phase-a"]


def test_phase_none_merge_disables_runtime_skills(tmp_path: Path):
    write_skill(tmp_path, "agent-a", markdown="# agent-a")

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        agent_config=RuntimeSkillsConfig(include=["agent-a"]),
        phase_config=RuntimeSkillsConfig(merge="none"),
    )

    assert bundle.names == []  # pylint: disable=use-implicit-booleaness-not-comparison; silent
    assert bundle.markdown == ""


def test_missing_policy_warn_records_warning(tmp_path: Path):
    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["missing-skill"])
    )

    assert bundle.missing == ["missing-skill"]
    assert bundle.warnings == ["Missing explicit runtime skills: missing-skill"]


def test_missing_policy_error_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="missing-skill"):
        RuntimeSkillResolver(tmp_path).resolve(
            phase_config=RuntimeSkillsConfig(include=["missing-skill"], missing="error")
        )


def test_missing_policy_ignore_suppresses_warning(tmp_path: Path):
    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["missing-skill"], missing="ignore")
    )

    assert bundle.missing == ["missing-skill"]
    assert bundle.warnings == []  # pylint: disable=use-implicit-booleaness-not-comparison; silent


def test_invalid_merge_policy_rejected_by_resolver(tmp_path: Path):
    with pytest.raises(ValueError, match="merge policy"):
        RuntimeSkillResolver(tmp_path).resolve(
            phase_config=RuntimeSkillsConfig(include=["skill"], merge="prepend")
        )


def test_rejects_path_traversal_skill_names(tmp_path: Path):
    write_local_skill(tmp_path, "safe", markdown="# Safe")

    with pytest.raises(ValueError, match="Invalid runtime skill name"):
        RuntimeSkillResolver(tmp_path).resolve(
            phase_config=RuntimeSkillsConfig(include=["../secret"], missing="ignore")
        )


def test_inject_full_false_keeps_paths_without_content(tmp_path: Path):
    write_skill(tmp_path, "brief", markdown="# Brief\n\nFull content")

    bundle = RuntimeSkillResolver(tmp_path).resolve(
        phase_config=RuntimeSkillsConfig(include=["brief"], inject_full=False)
    )

    assert bundle.names == ["brief"]
    assert "Source:" in bundle.markdown
    assert "Full content" not in bundle.markdown
