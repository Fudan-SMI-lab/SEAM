# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAny=false, reportExplicitAny=false, reportUnusedParameter=false, reportImplicitStringConcatenation=false
"""Format retrieved experiences as compact action cards for prompt injection."""

from typing import Any


class ExperienceInjector:
    """Formats selected experiences into markdown for prompt injection."""

    def inject(self, phase_definition: Any, query_result: dict) -> str:
        """Return markdown text to append to prompt, or '' if empty."""
        cards = self.action_cards(query_result)
        warning = query_result.get("warning", "")
        if not cards and not warning:
            return ""

        lines: list[str] = []
        lines.append("\n## Relevant Past Experiences\n")
        lines.append(
            "These are advisory action cards. Inspect/read applicable files before acting, "
            "decide whether each card applies, and report later whether each was used or ignored. "
            "If you use or ignore any card, return JSON fields `used_experience_ids`, "
            "`experience_actions_taken`, `ignored_experience_ids`, and `ignored_reasons`."
        )
        lines.append("")

        if warning:
            lines.append(f"WARNING: {warning}")
            lines.append("")

        if not cards:
            lines.append("(No relevant experience found.)")
            return "\n".join(lines)

        lines.extend(cards)
        return "\n".join(lines)

    def action_cards(self, query_result: dict) -> list[str]:
        selected = [
            exp for exp in query_result.get("selected_experiences", [])
            if isinstance(exp, dict) and exp.get("title")
        ]
        return [self._format_action_card(exp, index) for index, exp in enumerate(selected, 1)]

    def _format_action_card(self, exp: dict, index: int) -> str:
        exp_id = exp.get("id", "")
        exp_type = exp.get("type", "skill")
        title = exp.get("title", "")
        category = exp.get("category", "")
        relevance = exp.get("relevance_score", 0)
        reasoning = exp.get("reasoning", "")
        target_roles = self._format_list(exp.get("target_roles", []))
        target_phases = self._format_list(exp.get("target_phases", []))
        readable_paths = self._readable_paths(exp)

        lines = [
            f"### Experience Card {index}: {title}",
            f"- title: **{title}**",
            f"- id: `{exp_id}`",
            f"- type: `{exp_type}`",
            f"- target_roles: {target_roles}",
            f"- target_phases: {target_phases}",
            f"- relevance: {relevance}",
            f"- why: {reasoning or '(selector did not provide reasoning)'}",
        ]

        if readable_paths:
            lines.append("- readable_paths:")
            for path in readable_paths:
                lines.append(f"  - `{path}`")
        else:
            lines.append("- readable_paths: []")

        if category:
            lines.append(f"- category: {category}")

        lines.append(
            "- guidance: Read applicable paths first; use only if the contents match this failure, "
            "otherwise ignore and mention why."
        )

        if self._include_full_details(exp):
            lines.append(f"### {title}")
            root_cause = exp.get("root_cause", "")
            fix_steps = exp.get("fix_steps", exp.get("steps", []))
            if root_cause:
                lines.append(f"- Root cause: {root_cause}")
            if fix_steps:
                lines.append("- fix_steps:")
                for step in fix_steps:
                    lines.append(f"  - {step}")

        lines.append("")
        return "\n".join(lines)

    def _include_full_details(self, exp: dict) -> bool:
        if exp.get("critical") is True:
            return True
        if str(exp.get("priority", "")).lower() == "critical":
            return True
        if exp.get("load_full") is True:
            try:
                return float(exp.get("relevance_score", 0)) >= 0.95
            except (TypeError, ValueError):
                return False
        return False

    def _readable_paths(self, exp: dict) -> list[str]:
        paths: list[str] = []
        for key in ("file_path", "path"):
            value = exp.get(key)
            if value:
                paths.append(str(value))
        asset_paths = exp.get("asset_paths", [])
        if isinstance(asset_paths, str):
            asset_paths = [asset_paths]
        if isinstance(asset_paths, list):
            paths.extend(str(path) for path in asset_paths if path)
        deduped: list[str] = []
        for path in paths:
            if path not in deduped:
                deduped.append(path)
        return deduped

    def _format_list(self, value: Any) -> str:
        if value is None or value == "":
            return "any"
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, (list, tuple, set)):
            items = [str(item) for item in value if item]
        else:
            items = [str(value)]
        return ", ".join(items) if items else "any"
