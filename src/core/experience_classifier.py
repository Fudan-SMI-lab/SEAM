# pyright: reportMissingTypeArgument=false,
# reportUnknownParameterType=false, reportUnknownVariableType=false,
# reportUnknownMemberType=false, reportUnknownArgumentType=false,
# reportExplicitAny=false, reportAny=false,
# reportUnannotatedClassAttribute=false,
# reportUninitializedInstanceVariable=false,
# reportOptionalMemberAccess=false, reportUnnecessaryIsInstance=false
"""Deterministic experience candidate classifier."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ExperienceClassifier:  # pylint: disable=too-few-public-methods; silent
    """Classify raw candidates before refinement or solidification.

    The classifier is deterministic by default so unit tests and offline runs do
    not need a live OpenCode server. If a session manager is provided, callers
    may later layer an LLM result on top of this normalized output.
    """

    VALID_TYPES = {"skill", "document", "rule", "prompt"}
    TYPE_ALIASES = {
        "knowledge": "document",
        "case": "document",
        "case_study": "document",
        "doc": "document",
        "prompt_proposal": "prompt",
        "prompt_improvement": "prompt",
        "mechanical_rule": "rule",
    }

    ROLE_BY_CATEGORY = {
        "dependency": ["dependency_fixer"],
        "environment": ["dependency_fixer"],
        "operator_incompat": ["operator_fixer"],
        "operator": ["operator_fixer"],
        "code": ["code_adapter"],
        "cuda_api": ["code_adapter"],
        "prompt": ["main_engineer"],
        "case_study": ["main_engineer"],
    }

    PHASE_BY_CATEGORY = {
        "dependency": ["phase_2_venv_create", "phase_5_validation"],
        "environment": ["phase_0_env_detect", "phase_2_venv_create"],
        "operator_incompat": ["phase_5_validation"],
        "operator": ["phase_5_validation"],
        "code": ["phase_4_rule_migration", "phase_5_validation"],
        "cuda_api": ["phase_4_rule_migration"],
        "prompt": ["phase_7b_refine"],
        "case_study": ["phase_6_report"],
    }

    def __init__(self, session_mgr: Any | None = None) -> None:
        self.session_mgr = session_mgr

    def classify(
        self, candidate: dict, artifact_ctx: dict | None = None, timeout_sec: int = 300
    ) -> dict:
        base = self._deterministic_classification(candidate)
        llm_result = self._try_llm_classification(candidate, artifact_ctx or {}, timeout_sec)
        if llm_result:
            base.update(
                {key: value for key, value in llm_result.items() if value not in (None, "", [])}
            )
        return self._normalize(base, candidate)

    def _deterministic_classification(self, candidate: dict) -> dict:
        text_fields = " ".join(
            str(candidate.get(key, ""))
            for key in (
                "recommended_type",
                "category",
                "subtype",
                "problem_description",
                "rough_fix_approach",
                "title",
            )
        ).lower()
        tags = [str(tag).lower() for tag in self._as_list(candidate.get("tags", []))]
        combined = " ".join([text_fields, *tags])

        requested_type = self._normalize_type(
            candidate.get("recommended_type") or candidate.get("type")
        )
        if requested_type:
            exp_type = requested_type
            reason = "candidate provided recommended_type/type"
        elif self._matches_any(
            combined, ["prompt", "instruction", "phase prompt", "analyzer prompt"]
        ):
            exp_type = "prompt"
            reason = "candidate describes prompt or instruction improvement"
        elif self._matches_any(
            combined, ["regex", "replacement", "mechanical", "rule", "pattern", "rewrite"]
        ):
            exp_type = "rule"
            reason = "candidate describes a mechanical pattern replacement"
        elif self._matches_any(
            combined, ["case study", "case_study", "narrative", "report", "knowledge", "document"]
        ):
            exp_type = "document"
            reason = "candidate describes knowledge or case documentation"
        elif not self._has_actionable_skill_content(candidate):
            exp_type = "document"
            reason = "candidate lacks actionable skill fields and is better stored as knowledge"
        else:
            exp_type = "skill"
            reason = "candidate has actionable reusable fix content"

        category = str(candidate.get("category", ""))
        return {
            "type": exp_type,
            "target_roles": self._target_roles(candidate, category, exp_type),
            "target_phases": self._target_phases(candidate, category, exp_type),
            "solidifier": f"{exp_type}_solidifier",
            "reasoning": reason,
            "confidence": self._confidence(candidate, exp_type),
            "trigger_fingerprint": self._fingerprint(candidate),
        }

    def _try_llm_classification(
        self, candidate: dict, artifact_ctx: dict, timeout_sec: int
    ) -> dict:
        if not self.session_mgr or not hasattr(self.session_mgr, "send_command"):
            return {}
        try:
            session_id = self._ensure_classifier_session()
            prompt = self._build_classifier_prompt(candidate, artifact_ctx)
            raw = self.session_mgr.send_command(session_id, prompt, timeout=timeout_sec)
            parsed = self._parse_json(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:  # pylint: disable=broad-exception-caught; silent
            logger.warning("Experience classifier LLM call failed: %s", exc)
            return {}

    def _build_classifier_prompt(self, candidate: dict, artifact_ctx: dict) -> str:
        payload = json.dumps({"candidate": candidate, "artifact_context": artifact_ctx}, indent=2)
        return (
    "You are the experience_classifier for the CUDA-to-NPU migration memory pipeline.\n"
    "Classify exactly one reusable experience candidate before solidification.\n"
    "Return exactly one JSON object with these keys: type, target_roles, target_phases, "
    "solidifier, reasoning, confidence, trigger_fingerprint.\n"
    "Allowed type values: skill, document, rule, prompt.\n"
    "Allowed solidifier values: skill_solidifier, document_solidifier, "
    "rule_solidifier, prompt_solidifier.\n"
    "Use skill only for actionable agent procedures; use document for knowledge/case narrative; "
    "use rule for mechanical code transformations; use prompt for prompt-improvement proposals.\n"
    "Do not include markdown fences or explanatory text outside JSON.\n\n"
    f"Input:\n{payload}" )

    def _ensure_classifier_session(self) -> str:
        if hasattr(self, "_classifier_session_id"):
            # pylint: disable-next=access-member-before-definition; silent
            return self._classifier_session_id
        mgr = self.session_mgr
        if hasattr(mgr, "get_or_create"):
            # pylint: disable-next=attribute-defined-outside-init; silent
            self._classifier_session_id = mgr.get_or_create(
                role="experience_classifier", lifecycle="persistent"
            )
        elif hasattr(mgr, "_session_mgr"):
            # pylint: disable-next=attribute-defined-outside-init,protected-access; silent
            self._classifier_session_id = mgr._session_mgr.get_or_create(
                role="experience_classifier", lifecycle="persistent"
            )
        else:
            # pylint: disable-next=attribute-defined-outside-init; silent
            self._classifier_session_id = "experience_classifier"
        return self._classifier_session_id

    def _normalize(self, classification: dict, candidate: dict) -> dict:
        exp_type = self._normalize_type(classification.get("type")) or "skill"
        normalized = {
            "type": exp_type,
            "target_roles": self._as_list(classification.get("target_roles", [])),
            "target_phases": self._as_list(classification.get("target_phases", [])),
            "solidifier": classification.get("solidifier") or f"{exp_type}_solidifier",
            "reasoning": classification.get("reasoning", "deterministic classifier fallback"),
            "confidence": float(
                classification.get("confidence", candidate.get("confidence", 0.5)) or 0.0
            ),
            "trigger_fingerprint": classification.get("trigger_fingerprint")
            or self._fingerprint(candidate),
        }
        if not normalized["target_roles"]:
            normalized["target_roles"] = self._target_roles(
                candidate, str(candidate.get("category", "")), exp_type
            )
        if not normalized["target_phases"]:
            normalized["target_phases"] = self._target_phases(
                candidate, str(candidate.get("category", "")), exp_type
            )
        return normalized

    def _target_roles(self, candidate: dict, category: str, exp_type: str) -> list[str]:
        if candidate.get("target_roles"):
            return self._as_list(candidate.get("target_roles"))
        if exp_type == "rule":
            return ["code_adapter"]
        if exp_type == "prompt":
            return ["main_engineer"]
        return self.ROLE_BY_CATEGORY.get(category, ["main_engineer"])

    def _target_phases(self, candidate: dict, category: str, exp_type: str) -> list[str]:
        if candidate.get("target_phases"):
            return self._as_list(candidate.get("target_phases"))
        if exp_type == "rule":
            return ["phase_4_rule_migration"]
        if exp_type == "prompt":
            return ["phase_7b_refine"]
        return self.PHASE_BY_CATEGORY.get(category, ["phase_5_validation"])

    def _confidence(self, candidate: dict, exp_type: str) -> float:
        confidence = float(candidate.get("confidence", 0.5) or 0.5)
        if exp_type == "document" and confidence < 0.6:
            return 0.6
        return max(0.0, min(1.0, confidence))

    def _fingerprint(self, candidate: dict) -> str:
        parts = [str(candidate.get("category", "")), str(candidate.get("subtype", ""))]
        parts.extend(sorted(str(tag) for tag in self._as_list(candidate.get("tags", []))))
        text = "|".join(part for part in parts if part)
        if not text:
            text = str(candidate.get("title", candidate.get("candidate_id", "experience")))
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        return f"{text}|{digest}"

    def _has_actionable_skill_content(self, candidate: dict) -> bool:
        fields = [
            "rough_fix_approach",
            "fix_steps",
            "code_changes",
            "root_cause",
            "problem_description",
        ]
        return any(candidate.get(field) for field in fields)

    def _normalize_type(self, value: Any) -> str:
        if not value:
            return ""
        text = str(value).strip().lower().replace("-", "_")
        text = self.TYPE_ALIASES.get(text, text)
        return text if text in self.VALID_TYPES else ""

    @staticmethod
    def _matches_any(text: str, needles: list[str]) -> bool:
        return any(needle in text for needle in needles)

    @staticmethod
    def _as_list(value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        # pylint: disable-next=consider-merging-isinstance; silent
        if isinstance(value, tuple) or isinstance(value, set):
            return list(value)
        return [value]

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = str(raw).strip()
        first = text.find("{")
        last = text.rfind("}")
        if first == -1 or last <= first:
            return {}
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            return {}
