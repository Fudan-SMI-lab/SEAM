# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportExplicitAny=false, reportAny=false, reportUnannotatedClassAttribute=false, reportImplicitOverride=false, reportArgumentType=false, reportUnusedParameter=false
"""Specialized experience solidifiers."""

from __future__ import annotations

import json
import re
from typing import Any


class BaseSolidifier:
    exp_type = "experience"

    def solidify(self, exp: dict, artifact_ctx: dict, run_id: str, store: Any) -> dict:
        raise NotImplementedError

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
        return slug or "experience"

    def _json(self, data: dict) -> str:
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    def _markdown_list(self, values: list) -> str:
        return "\n".join(f"- {value}" for value in values)


class SkillSolidifier(BaseSolidifier):
    exp_type = "skill"

    def solidify(self, exp: dict, artifact_ctx: dict, run_id: str, store: Any) -> dict:
        skill_name = exp.get("skill_name") or exp.get("name") or self._slug(exp.get("title", "skill"))
        exp["skill_name"] = skill_name
        exp.setdefault("name", skill_name)
        skill_data = {
            "name": skill_name,
            "title": exp.get("title", skill_name),
            "description": exp.get("description", exp.get("title", skill_name)),
            "tags": exp.get("tags", []),
            "category": exp.get("category", ""),
            "subtype": exp.get("subtype", ""),
            "confidence": exp.get("confidence", 0.0),
            "target_roles": exp.get("target_roles", []),
            "target_phases": exp.get("target_phases", []),
            "trigger_fingerprint": exp.get("trigger_fingerprint", ""),
            "classifier": exp.get("classifier", {}),
            "occurrence_count": exp.get("occurrence_count", 1),
            "when_to_use": exp.get("when_to_use", exp.get("symptom", exp.get("problem_description", ""))),
            "root_cause": exp.get("root_cause", ""),
            "fix_steps": exp.get("fix_steps", exp.get("steps", [])),
            "code_changes": exp.get("code_changes", []),
            "antipatterns": exp.get("antipatterns", []),
            "references": exp.get("references", []),
            "merged_from_runs": exp.get("merged_from_runs", [run_id]),
        }
        assets: dict = {"skill_data.json": skill_data}
        assets["SKILL.md"] = self._render_skill_markdown(skill_data)
        assets["skill.yaml"] = self._render_skill_yaml(skill_data)
        assets["verification.md"] = self._render_verification(exp, run_id)

        references = exp.get("references", [])
        if references:
            assets["references/sources.md"] = "# References\n\n" + self._markdown_list(references) + "\n"
        elif exp.get("code_changes"):
            assets["examples/code_changes.json"] = exp.get("code_changes", [])
        else:
            assets["tests/usage.md"] = f"# Usage Check\n\nApply `{skill_name}` when the trigger fingerprint matches.\n"
        return assets

    def _render_skill_markdown(self, data: dict) -> str:
        lines = [
            "---",
            f"name: {data.get('name', 'unknown')}",
            f"description: {data.get('description', '')}",
            f"tags: {json.dumps(data.get('tags', []))}",
            f"category: {data.get('category', '')}",
            f"subtype: {data.get('subtype', '')}",
            f"confidence: {data.get('confidence', 0.0)}",
            f"occurrence_count: {data.get('occurrence_count', 1)}",
            "---",
            "",
            f"# {data.get('title', data.get('name', 'Skill'))}",
            "",
            "## When to Use",
        ]
        when_to_use = str(data.get("when_to_use", "")).strip()
        lines.extend([f"- {line.strip('- ')}" for line in when_to_use.split("\n") if line.strip()])
        if data.get("root_cause"):
            lines.extend(["", "## Root Cause", str(data["root_cause"])])
        if data.get("fix_steps"):
            lines.extend(["", "## How to Use"])
            for index, step in enumerate(data.get("fix_steps", []), 1):
                lines.append(f"{index}. {str(step).lstrip('0123456789. ')}")
        if data.get("code_changes"):
            lines.extend(["", "## Code Examples", json.dumps(data.get("code_changes"), indent=2)])
        if data.get("antipatterns"):
            lines.extend(["", "## Do Not"])
            lines.extend(f"- {item}" for item in data.get("antipatterns", []))
        if data.get("references"):
            lines.extend(["", "## References"])
            lines.extend(f"- {item}" for item in data.get("references", []))
        lines.extend(["", "## Evidence"])
        for run_id in data.get("merged_from_runs", []):
            lines.append(f"- Source runs: {run_id}")
        return "\n".join(lines) + "\n"

    def _render_skill_yaml(self, data: dict) -> str:
        lines = [
            f"name: {data.get('name', 'unknown')}",
            f"title: {data.get('title', '')}",
            f"description: {data.get('description', '')}",
            f"category: {data.get('category', '')}",
            f"subtype: {data.get('subtype', '')}",
            f"target_roles: {json.dumps(data.get('target_roles', []))}",
            f"target_phases: {json.dumps(data.get('target_phases', []))}",
            f"trigger_fingerprint: {data.get('trigger_fingerprint', '')}",
        ]
        return "\n".join(lines) + "\n"

    def _render_verification(self, exp: dict, run_id: str) -> str:
        lines = [f"# Verification for {exp.get('title', 'Skill')}", "", f"- Source run: {run_id}"]
        if exp.get("meta", {}).get("refiner_warning"):
            lines.append(f"- Warning: {exp['meta']['refiner_warning']}")
        lines.append("- Verify by applying the fix steps to a matching migration failure.")
        return "\n".join(lines) + "\n"


class DocumentSolidifier(BaseSolidifier):
    exp_type = "document"

    def solidify(self, exp: dict, artifact_ctx: dict, run_id: str, store: Any) -> dict:
        body = exp.get("body") or exp.get("problem_description") or exp.get("rough_fix_approach") or "No document body provided."
        return {
            "document.md": f"# {exp.get('title', 'Untitled Document')}\n\n{body}\n",
            "metadata.json": {"type": "document", "run_id": run_id, "classifier": exp.get("classifier", {})},
        }


class RuleSolidifier(BaseSolidifier):
    exp_type = "rule"

    def solidify(self, exp: dict, artifact_ctx: dict, run_id: str, store: Any) -> dict:
        rule = {
            "title": exp.get("title", "Untitled Rule"),
            "pattern": exp.get("pattern", exp.get("match", "")),
            "replacement": exp.get("replacement", ""),
            "file_patterns": exp.get("file_patterns", ["*.py"]),
            "target_roles": exp.get("target_roles", []),
            "target_phases": exp.get("target_phases", []),
            "trigger_fingerprint": exp.get("trigger_fingerprint", ""),
        }
        return {"rule.yaml": self._render_yaml(rule), "metadata.json": {"type": "rule", "run_id": run_id}}

    def _render_yaml(self, data: dict) -> str:
        return "\n".join(f"{key}: {json.dumps(value) if isinstance(value, list) else value}" for key, value in data.items()) + "\n"


class PromptSolidifier(BaseSolidifier):
    exp_type = "prompt"

    def solidify(self, exp: dict, artifact_ctx: dict, run_id: str, store: Any) -> dict:
        proposal = {
            "title": exp.get("title", "Untitled Prompt Proposal"),
            "phase_target": exp.get("phase_target", exp.get("target_phases", [""])[0] if exp.get("target_phases") else ""),
            "current_prompt_issue": exp.get("current_prompt_issue", exp.get("problem_description", "")),
            "suggested_improvement": exp.get("suggested_improvement", exp.get("rough_fix_approach", "")),
            "target_roles": exp.get("target_roles", []),
            "trigger_fingerprint": exp.get("trigger_fingerprint", ""),
        }
        return {"proposal.yaml": self._render_yaml(proposal), "metadata.json": {"type": "prompt", "run_id": run_id}}

    def _render_yaml(self, data: dict) -> str:
        return "\n".join(f"{key}: {json.dumps(value) if isinstance(value, list) else value}" for key, value in data.items()) + "\n"


class ExperienceSolidifier:
    def __init__(self) -> None:
        self._solidifiers = {
            "skill": SkillSolidifier(),
            "document": DocumentSolidifier(),
            "rule": RuleSolidifier(),
            "prompt": PromptSolidifier(),
        }

    def solidify(self, exp: dict, artifact_ctx: dict, run_id: str, store: Any) -> dict:
        exp_type = exp.get("type", "skill")
        solidifier = self._solidifiers.get(exp_type, self._solidifiers["skill"])
        return solidifier.solidify(exp, artifact_ctx, run_id, store)
