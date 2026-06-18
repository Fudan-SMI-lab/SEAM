# pyright: reportMissingTypeArgument=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnannotatedClassAttribute=false
# pyright: reportUninitializedInstanceVariable=false
# pyright: reportPrivateUsage=false
# pyright: reportAny=false
# pyright: reportUnusedCallResult=false
"""Experience refiner — transforms candidate experiences
into production-ready skills/documents/rules/prompts."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from core.experience_classifier import ExperienceClassifier
from core.experience_solidifier import ExperienceSolidifier
from core.experience_store import ExperienceStore

logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class ExperienceRefiner:
    """Refines a raw candidate experience via LLM into a production-ready asset."""

    def __init__(self, artifact_dir: str, store: ExperienceStore, session_mgr):
        self.artifact_dir = artifact_dir
        self.store = store
        self.session_mgr = session_mgr
        self.solidifier = ExperienceSolidifier()

    def refine(
        self,
        candidate: dict,
        run_id: str,
        artifact_ctx: dict,
        timeout_sec: int = 3600,
        classification: dict | None = None,
    ) -> dict:
        """Refine a single candidate into a production-ready experience.

        Steps:
        1. Build prompt from candidate + artifact_ctx
        2. Create or reuse a session for refinement (run_id is NOT a valid session)
        3. Call LLM via session_mgr.send_command (independent timeout)
        4. Parse JSON response (strip fences, find braces)
        5. Set meta fields: source_run_id, created_at
        6. Generate assets via _generate_assets
        7. Write refined experience + assets via store.write_refined_experience
        8. Return refined experience dict
        """
        classification = classification or ExperienceClassifier().classify(
            candidate, artifact_ctx
        )
        prompt = self._build_refinement_prompt(candidate, artifact_ctx, classification)

        if self.session_mgr is None or not hasattr(self.session_mgr, "send_command"):
            result = self._fallback_refine(
                candidate,
                run_id,
                "LLM session manager unavailable",
                classification,
            )
            self._write_refined_assets(result, artifact_ctx, run_id)
            return result

        session_role = self._solidifier_session_role(classification)
        session_id = self._ensure_refinement_session(session_role)

        logger.info(
            "Calling LLM refiner for candidate %s (timeout=%ds, session=%s)",
            candidate.get("candidate_id", "unknown"),
            timeout_sec,
            session_id,
        )
        try:
            raw_response = self.session_mgr.send_command(
                session_id, prompt, timeout=timeout_sec
            )
        except Exception as exc:
            logger.warning(
                "LLM refiner call failed for candidate %s: %s",
                candidate.get("candidate_id", "unknown"),
                exc,
            )
            result = self._fallback_refine(candidate, run_id, str(exc), classification)
            self._write_refined_assets(result, artifact_ctx, run_id)
            return result

        result = self._parse_json_response(raw_response)
        if "_raw" in result and not result.get("type"):
            logger.warning(
                "Refiner returned non-JSON for candidate %s",
                candidate.get("candidate_id", "unknown"),
            )

        # Set meta fields
        result.setdefault("meta", {})
        result["meta"]["source_run_id"] = run_id
        result["meta"]["created_at"] = datetime.now(timezone.utc).isoformat()
        self._apply_classification(result, classification)

        # Merge candidate-level metadata into result
        for key in (
            "candidate_id",
            "title",
            "problem_description",
            "rough_fix_approach",
            "recommended_type",
            "tags",
            "category",
            "subtype",
            "confidence",
            "project_source_root",
        ):
            if key in candidate and key not in result:
                result[key] = candidate[key]

        self._write_refined_assets(result, artifact_ctx, run_id)

        return result

    def _write_refined_assets(
        self, result: dict, artifact_ctx: dict, run_id: str
    ) -> None:
        assets = self._generate_assets(result, artifact_ctx, run_id)
        result["_asset_contents"] = assets
        result["asset_names"] = sorted(assets)
        try:
            self.store.write_refined_experience(run_id, result, assets)
        except Exception as exc:
            logger.warning(
                "Failed to write refined experience for candidate %s: %s",
                result.get("candidate_id", "unknown"),
                exc,
            )

    def _ensure_refinement_session(self, role: str = "experience_refiner") -> str:
        if not hasattr(self, "_refinement_session_ids"):
            self._refinement_session_ids = {}
        if role not in self._refinement_session_ids:
            mgr = self.session_mgr
            if hasattr(mgr, "get_or_create"):
                self._refinement_session_ids[role] = mgr.get_or_create(
                    role=role, lifecycle="persistent"
                )
            elif hasattr(mgr, "_session_mgr"):
                self._refinement_session_ids[role] = mgr._session_mgr.get_or_create(
                    role=role, lifecycle="persistent"
                )
            else:
                self._refinement_session_ids[role] = role
        return self._refinement_session_ids[role]

    def _solidifier_session_role(self, classification: dict | None) -> str:
        if not classification:
            return "experience_refiner"
        solidifier = str(classification.get("solidifier", "")).strip()
        if not solidifier:
            exp_type = str(classification.get("type", "")).strip()
            solidifier = f"{exp_type}_solidifier" if exp_type else ""
        allowed = {
            "skill_solidifier",
            "document_solidifier",
            "rule_solidifier",
            "prompt_solidifier",
        }
        if solidifier not in allowed:
            return "experience_refiner"
        return f"experience_{solidifier}"

    def _fallback_refine(
        self,
        candidate: dict,
        run_id: str,
        error: str,
        classification: dict | None = None,
    ) -> dict:
        """Return a partial result when LLM call fails."""
        classification = classification or ExperienceClassifier().classify(candidate)
        result = {
            "type": classification.get(
                "type", candidate.get("recommended_type", "skill")
            ),
            "title": candidate.get("title", "Unknown"),
            "problem_description": candidate.get("problem_description", ""),
            "rough_fix_approach": candidate.get("rough_fix_approach", ""),
            "meta": {
                "source_run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "refiner_warning": f"LLM call failed: {error}",
            },
        }
        self._apply_classification(result, classification)
        for key in (
            "candidate_id",
            "tags",
            "category",
            "subtype",
            "confidence",
            "skill_name",
            "references",
            "fix_steps",
            "code_changes",
            "antipatterns",
        ):
            if key in candidate:
                result[key] = candidate[key]
        return result

    def _apply_classification(self, result: dict, classification: dict) -> None:
        result["type"] = classification.get("type", result.get("type", "skill"))
        result["target_roles"] = classification.get(
            "target_roles", result.get("target_roles", [])
        )
        result["target_phases"] = classification.get(
            "target_phases", result.get("target_phases", [])
        )
        result["trigger_fingerprint"] = classification.get(
            "trigger_fingerprint", result.get("trigger_fingerprint", "")
        )
        result["solidifier"] = classification.get(
            "solidifier", result.get("solidifier", "")
        )
        result["classifier"] = classification

    def _build_refinement_prompt(
        self,
        candidate: dict,
        artifact_ctx: dict,
        classification: dict | None = None,
    ) -> str:
        """Load experience_refiner.md template and fill with candidate + context."""
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prompts",
            "experience_refiner.md",
        )
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read()
        except OSError:
            logger.error("Prompt file not found: %s", prompt_path)
            template = ""

        # Build candidate details section
        candidate_lines = [
            f"## Candidate ID: {candidate.get('candidate_id', 'unknown')}",
            f"## Title: {candidate.get('title', 'N/A')}",
            f"## Problem Description: {candidate.get('problem_description', 'N/A')}",
            f"## Rough Fix Approach: {candidate.get('rough_fix_approach', 'N/A')}",
            f"## Recommended Type: {candidate.get('recommended_type', 'skill')}",
            f"## Tags: {json.dumps(candidate.get('tags', []))}",
            f"## Category: {candidate.get('category', 'N/A')}",
            f"## Subtype: {candidate.get('subtype', 'N/A')}",
            f"## Confidence: {candidate.get('confidence', 0.0)}",
        ]
        if classification:
            dumped = json.dumps(classification, indent=2)
            candidate_lines.append(f"## Classification: {dumped}")
        candidate_section = "\n".join(candidate_lines)

        # Build artifact evidence section
        evidence_content = artifact_ctx.get("evidence", "(no evidence available)")
        evidence_section = f"## Artifact Evidence Content\n\n{evidence_content}"

        # Build involved source code section
        source_files = artifact_ctx.get("source_files", "(no source files available)")
        source_section = f"## Involved Source Code Files\n\n{source_files}"

        # Assemble full prompt
        context = "\n\n".join([candidate_section, evidence_section, source_section])

        # Try to inject context into template
        for placeholder in ["{context}", "{{context}}", "{candidate_details}"]:
            if placeholder in template:
                return template.replace(placeholder, context)

        return template + "\n\n---\n\n" + context

    def _generate_assets(self, exp: dict, artifact_ctx: dict, run_id: str) -> dict:
        """Generate asset files based on experience type.

        Returns dict of {asset_filename: content}.
        """
        assets = self.solidifier.solidify(exp, artifact_ctx, run_id, self.store)
        if assets:
            return assets

        exp_type = exp.get("type", "skill")
        assets = {}

        if exp_type == "skill":
            skill_name = exp.get("skill_name", exp.get("title", "unknown_skill"))
            # Generate SKILL.md using store's render pattern
            skill_data = {
                "name": skill_name,
                "title": exp.get("title", ""),
                "description": exp.get("title", ""),
                "tags": exp.get("tags", []),
                "category": exp.get("category", ""),
                "subtype": exp.get("subtype", ""),
                "confidence": exp.get("confidence", 0.0),
                "occurrence_count": 1,
                "when_to_use": exp.get("symptom", ""),
                "root_cause": exp.get("root_cause", ""),
                "fix_steps": exp.get("fix_steps", []),
                "code_changes": exp.get("code_changes", []),
                "antipatterns": exp.get("antipatterns", []),
                "references": exp.get("references", []),
                "merged_from_runs": [run_id],
            }
            # Render SKILL.md using store's internal method
            tmp_path = os.path.join(tempfile.gettempdir(), f"_skill_render_{run_id}.md")
            try:
                self.store._render_skill_md_with_data(skill_data, tmp_path)
                with open(tmp_path, "r", encoding="utf-8") as f:
                    assets["SKILL.md"] = f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            # Generate references.md
            refs = exp.get("references", [])
            if refs:
                ref_lines = ["# References\n"]
                for ref in refs:
                    ref_lines.append(f"- {ref}")
                assets["references.md"] = "\n".join(ref_lines)

        elif exp_type == "document":
            body = exp.get("body", "")
            assets["document.md"] = f"# {exp.get('title', 'Untitled')}\n\n{body}"

        elif exp_type == "rule":
            rule_content = (
                f"title: {exp.get('title', 'Untitled')}\n"
                f"pattern: {exp.get('pattern', '')}\n"
                f"replacement: {exp.get('replacement', '')}\n"
                f"file_patterns: {json.dumps(exp.get('file_patterns', ['*.py']))}\n"
            )
            assets["rule.yaml"] = rule_content

        elif exp_type == "prompt":
            prompt_content = (
                f"title: {exp.get('title', 'Untitled')}\n"
                f"phase_target: {exp.get('phase_target', '')}\n"
                f"current_prompt_issue: {exp.get('current_prompt_issue', '')}\n"
                f"suggested_improvement: {exp.get('suggested_improvement', '')}\n"
            )
            assets["prompt_suggestion.yaml"] = prompt_content

        return assets

    def _parse_json_response(self, raw: str) -> dict:
        """Strip fences, find braces, parse — same pattern as evaluator."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
            logger.warning("No JSON object found in refiner response")
            return {"_raw": raw}

        json_str = text[first_brace : last_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("Refiner JSON parse failed: %s", exc)
            return {"_raw": raw}
