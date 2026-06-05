# pyright: reportMissingTypeArgument=false, reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportUninitializedInstanceVariable=false, reportAny=false, reportExplicitAny=false, reportUnnecessaryIsInstance=false
import glob
import json
import logging
import os
import re
from typing import Any

from collections.abc import Iterable

from core.experience_store import ExperienceStore

logger = logging.getLogger(__name__)

_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "..", "prompts", "experience_query.md")
_INACTIVE_EXPERIENCE_STATUSES = frozenset({"consumed", "rejected", "quarantined", "archived"})
_ATEN_ONLY_CUSTOM_OP_TERMS = frozenset({
    "aten",
    "cpp_extension",
    "cppextension",
    "privateuse1",
    "torch_utils_cpp_extension",
})
# Platform-specific ATen route terms (e.g. "npu_routed_cpp_extension")
# are derived from the policy via ExperienceQuerier.from_policy().
_BASE_ATEN_ONLY_TERMS: frozenset[str] = _ATEN_ONLY_CUSTOM_OP_TERMS
# Platform-agnostic default terms used when no policy is available.
# Platform-specific terms are derived from PlatformPolicy.custom_op_evidence
# via ExperienceQuerier.from_policy() and take precedence.
_CONCRETE_NATIVE_CUSTOM_OP_TERMS = frozenset({
    "op_host",
    "op_kernel",
    "kernel",
    "native_build",
})


class ExperienceQuerier:

    def __init__(
        self,
        store: ExperienceStore,
        session_mgr,
        concrete_native_terms: frozenset[str] | None = None,
        aten_only_terms: frozenset[str] | None = None,
    ) -> None:
        self.store = store
        self.session_mgr = session_mgr
        self._last_query_result: dict | None = None
        self._concrete_native_terms = (
            concrete_native_terms
            if concrete_native_terms is not None
            else _CONCRETE_NATIVE_CUSTOM_OP_TERMS
        )
        self._aten_only_terms = (
            aten_only_terms
            if aten_only_terms is not None
            else _BASE_ATEN_ONLY_TERMS
        )

    @classmethod
    def from_policy(
        cls,
        store: ExperienceStore,
        session_mgr,
        policy: object | None = None,
    ) -> "ExperienceQuerier":
        """Create an ExperienceQuerier, deriving concrete-native and ATen-only terms from *policy*.

        If *policy* (a PlatformPolicy) is None or lacks
        custom_op_evidence, the platform-agnostic
        _CONCRETE_NATIVE_CUSTOM_OP_TERMS and _BASE_ATEN_ONLY_TERMS are used.
        """
        concrete: frozenset[str] | None = None
        aten_only: frozenset[str] | None = None
        if policy is not None:
            try:
                ev_cfg = getattr(policy, "custom_op_evidence", None)
            except AttributeError:
                ev_cfg = None
            if ev_cfg is not None:
                tokens: set[str] = set()
                for attr in ("native_build_log_tokens", "native_source_tokens"):
                    val = getattr(ev_cfg, attr, ())
                    if isinstance(val, Iterable) and not isinstance(val, (str, bytes)):
                        tokens.update(str(t) for t in val if isinstance(t, (str, bytes)))
                concrete = frozenset(tokens) if tokens else None
            try:
                policy_id: str = getattr(policy, "id", "")
            except AttributeError:
                policy_id = ""
            if policy_id:
                prefix = policy_id.split("_")[0]  # "npu_ascend" → "npu"
                route_term = f"{prefix}_routed_cpp_extension"
                aten_only = frozenset(_BASE_ATEN_ONLY_TERMS | {route_term})
        return cls(
            store=store,
            session_mgr=session_mgr,
            concrete_native_terms=concrete,
            aten_only_terms=aten_only,
        )

    def query(self, context: dict, load_full: bool = True) -> dict:
        index = self.store.read_index()

        if not index:
            return {"selected_experiences": [], "summary": "", "warning": ""}

        prefiltered_index = self._prefilter_index(index, context)
        if not prefiltered_index:
            return {
                "selected_experiences": [],
                "summary": "No experiences matched the deterministic role/phase/type/tag filters.",
                "warning": "",
            }

        index_by_id = {str(entry.get("id", "")): entry for entry in prefiltered_index}
        summary_table = self._format_index_summary(prefiltered_index)
        prompt = self._build_query_prompt(context, summary_table)

        try:
            session_id = self._get_query_session()
            raw_response = self.session_mgr.send_command(
                session_id, prompt, timeout=1800,
            )
        except Exception as exc:
            logger.warning("Experience query LLM call failed: %s", exc)
            return {"selected_experiences": [], "summary": "", "warning": f"LLM query failed: {exc}"}

        parsed = self._extract_json(raw_response)

        selected = parsed.get("selected_experiences", [])
        summary_text = parsed.get("summary", "")
        warning_text = parsed.get("warning", "")

        enriched: list[dict] = []
        for item in selected:
            entry_id = item.get("id", "")
            if not entry_id or entry_id not in index_by_id:
                continue

            index_entry = index_by_id[entry_id]
            entry_type = item.get("type") or index_entry.get("type", "skill")
            merged_item = {**index_entry, **item}

            if load_full:
                full_exp = self._load_experience(entry_id, entry_type)
                if full_exp:
                    full_exp = {**index_entry, **full_exp}
                    full_exp["id"] = entry_id
                    full_exp["type"] = full_exp.get("type", entry_type)
                    full_exp["relevance_score"] = item.get("relevance_score", 0.0)
                    full_exp["reasoning"] = item.get("reasoning", "")
                    full_exp["load_full"] = bool(item.get("load_full", False))
                    full_exp["file_path"] = self._derive_experience_file_path(
                        entry_id, entry_type, full_exp
                    )
                    enriched.append(full_exp)
            else:
                file_path = self._derive_experience_file_path(entry_id, entry_type, merged_item)
                enriched.append({
                    "id": entry_id,
                    "type": entry_type,
                    "title": merged_item.get("title", ""),
                    "category": merged_item.get("category", ""),
                    "subtype": merged_item.get("subtype", ""),
                    "tags": merged_item.get("tags", []),
                    "target_roles": merged_item.get("target_roles", []),
                    "target_phases": merged_item.get("target_phases", []),
                    "reasoning": item.get("reasoning", ""),
                    "relevance_score": item.get("relevance_score", 0.0),
                    "symptom": merged_item.get("symptom", ""),
                    "file_path": file_path,
                    "asset_paths": merged_item.get("asset_paths", []),
                    "confidence": merged_item.get("confidence", 0),
                    "load_full": bool(item.get("load_full", False)),
                })

        if self._native_custom_op_gate_required(context):
            enriched = [
                experience
                for experience in enriched
                if not self._is_aten_only_custom_op_entry(experience)
            ]

        result = {
            "selected_experiences": enriched,
            "summary": summary_text,
            "warning": warning_text,
        }
        self._last_query_result = result
        return result


    def _prefilter_index(self, index: list[dict], context: dict) -> list[dict]:
        roles = self._context_values(context, "role", "roles", "target_role", "target_roles", "repair_role")
        phases = self._context_values(context, "phase", "phases", "parent_phase", "target_phase", "target_phases")
        types = self._context_values(context, "type", "types", "experience_type", "experience_types", "allowed_types")
        tags = self._context_values(context, "tags", "query_tags", "experience_tags")
        native_custom_op_gate_required = self._native_custom_op_gate_required(context)

        filtered: list[dict[str, Any]] = []
        for entry in index:
            if str(entry.get("status") or "").strip().lower() in _INACTIVE_EXPERIENCE_STATUSES:
                continue
            if native_custom_op_gate_required and self._is_aten_only_custom_op_entry(entry):
                continue
            if not self._derive_experience_file_path(
                str(entry.get("id", "")), str(entry.get("type", "skill")), entry
            ):
                continue
            if not self._metadata_matches(entry.get("target_roles", []), roles):
                continue
            if not self._metadata_matches(entry.get("target_phases", []), phases):
                continue
            if types and self._normalize_token(entry.get("type", "skill")) not in types:
                continue
            if tags:
                entry_tags = self._context_values(entry, "tags")
                if not entry_tags or entry_tags.isdisjoint(tags):
                    continue
            filtered.append(entry)

        return sorted(
            filtered,
            key=lambda item: (
                -self._safe_float(item.get("confidence", 0.0)),
                str(item.get("type", "")),
                str(item.get("id", "")),
            ),
        )

    def _native_custom_op_gate_required(self, context: dict) -> bool:
        values = self._context_values(
            context,
            "custom_op_native_gate_required",
            "native_custom_op_gate_required",
            "custom_op_evidence_policy",
        )
        if values.intersection({"true", "1", "yes"}):
            return True
        # Platform-agnostic: any "require_real_*" evidence policy string
        # (e.g. require_real_ascend_…, require_real_ppu_…) triggers the
        # native custom-op gate — regardless of which platform is active.
        return any(
            isinstance(v, str) and v.startswith("require_real_")
            for v in values
        )

    def _is_aten_only_custom_op_entry(self, entry: dict) -> bool:
        text = self._entry_filter_text(entry)
        if not any(term in text for term in self._aten_only_terms):
            return False
        return not any(term in text for term in self._concrete_native_terms)

    @staticmethod
    def _entry_filter_text(entry: dict) -> str:
        fields = (
            "id",
            "title",
            "description",
            "category",
            "subtype",
            "trigger_fingerprint",
            "symptom",
            "root_cause",
            "rough_fix_approach",
            "fix_steps",
            "steps",
            "guidance",
            "body",
        )
        pieces = [str(entry.get(field) or "") for field in fields]
        tags = entry.get("tags")
        if isinstance(tags, (list, tuple, set)):
            pieces.extend(str(tag) for tag in tags)
        else:
            pieces.append(str(tags or ""))
        return re.sub(r"[^a-z0-9]+", "_", " ".join(pieces).lower())

    def _metadata_matches(self, entry_values: Any, context_values: set[str]) -> bool:
        normalized_entry = self._normalize_values(entry_values)
        if not normalized_entry or not context_values:
            return True
        for entry_value in normalized_entry:
            for context_value in context_values:
                if entry_value == context_value:
                    return True
                if entry_value in context_value or context_value in entry_value:
                    return True
        return False

    def _context_values(self, source: dict, *keys: str) -> set[str]:
        values: set[str] = set()
        for key in keys:
            if key in source:
                values.update(self._normalize_values(source.get(key)))
        return values

    def _normalize_values(self, raw: Any) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, (list, tuple, set)):
            candidates = list(raw)
        else:
            candidates = [raw]
        return {token for token in (self._normalize_token(value) for value in candidates) if token}

    @staticmethod
    def _normalize_token(value: Any) -> str:
        return str(value).strip().lower().replace("-", "_") if value is not None else ""

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _format_index_summary(self, index: list[dict]) -> str:
        lines: list[str] = []
        for entry in index:
            if entry.get("status") == "consumed":
                continue
            eid = entry.get("id", "")
            etype = entry.get("type", "skill")
            file_path = self._derive_experience_file_path(eid, etype, entry)
            if not file_path:
                continue
            cat = entry.get("category", "")
            sub = entry.get("subtype", "")
            tags = ", ".join(entry.get("tags", []))
            conf = entry.get("confidence", 0.0)
            title = entry.get("title", "")
            symptom = entry.get("symptom", "")
            status = entry.get("status", "")
            target_roles = ", ".join(entry.get("target_roles", []))
            target_phases = ", ".join(entry.get("target_phases", []))
            lines.append(f"- id: {eid}")
            lines.append(f"  type: {etype}  category: {cat}/{sub}  status: {status}  title: {title}")
            lines.append(f"  tags: {tags}  confidence: {conf}")
            lines.append(f"  target_roles: {target_roles}  target_phases: {target_phases}")
            if symptom:
                lines.append(f"  symptom: {symptom[:200]}")
            lines.append(f"  file_path: {file_path}")
            lines.append("")
        return "\n".join(lines)

    def _derive_experience_file_path(self, entry_id: str, entry_type: str, entry: dict) -> str:
        """Return absolute path to the experience file the LLM can read."""
        store = self.store
        asset_paths = entry.get("asset_paths", [])
        if asset_paths:
            for asset_path in asset_paths:
                absolute = asset_path if os.path.isabs(asset_path) else os.path.join(store.repo_root, asset_path)
                if os.path.exists(absolute):
                    return absolute
        if entry_type == "skill":
            skill_name = self._derive_skill_name(entry_id)
            if skill_name:
                md = os.path.join(store.skills_dir, skill_name, "SKILL.md")
                if os.path.isfile(md):
                    return md
                data = os.path.join(store.skills_dir, skill_name, "skill_data.json")
                if os.path.isfile(data):
                    return data
            # Staging fallback: find latest matching JSON via glob
            parts = entry_id.split("-exp-", 1)
            if len(parts) >= 2:
                run_id = parts[0]
                refined_dir = os.path.join(store.staging_dir, run_id, "refined")
                if os.path.isdir(refined_dir):
                    matches = sorted(glob.glob(os.path.join(refined_dir, f"{run_id}-exp-*.json")))
                    if matches:
                        return matches[-1]
        for root in [store.promotions_dir, store.cases_dir]:
            pattern = os.path.join(root, "**", "experience.json")
            for fp in glob.glob(pattern, recursive=True):
                if entry_id.endswith(os.path.basename(os.path.dirname(fp))) or entry_id == f"promoted-{os.path.basename(os.path.dirname(fp))}":
                    return fp
        # Generic: try staging candidates
        parts = entry_id.split("-exp-", 1)
        if len(parts) >= 2:
            run_id = parts[0]
            cand_dir = os.path.join(store.staging_dir, run_id, "candidates")
            if os.path.isdir(cand_dir):
                fp = os.path.join(cand_dir, f"{entry_id}.json")
                if os.path.isfile(fp):
                    return fp
        return ""

    def _build_query_prompt(self, context: dict, index_summary: str) -> str:
        phase = context.get("phase", "unknown")
        
        # Check if we should use a specific prompt for Phase 1
        if "project_analysis" in phase:
            prompt_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                       "prompts", "experience_query_phase1.md")
        else:
            prompt_path = _PROMPT_FILE

        try:
            with open(prompt_path, "r", encoding="utf-8") as fh:
                template = fh.read()
        except FileNotFoundError:
            logger.error("Prompt template not found at %s", prompt_path)
            template = ""

        error_category = context.get("error_category", "unknown")
        error_stderr = context.get("error_stderr", "")
        project_type = context.get("project_type", "unknown")
        target_roles = ", ".join(sorted(self._context_values(context, "role", "roles", "target_roles", "repair_role"))) or "any"
        target_phases = ", ".join(sorted(self._context_values(context, "phase", "phases", "parent_phase", "target_phases"))) or "any"
        deps_raw = context.get("dependencies", [])
        dependencies = ", ".join(deps_raw) if isinstance(deps_raw, list) else str(deps_raw)
        previous_repair_attempts = context.get("previous_repair_attempts", "None recorded")
        root_cause = context.get("root_cause", "") or "(Not yet analyzed)"
        suggested_fix = context.get("suggested_fix", "") or "(Not yet suggested)"

        return (template
                .replace("{{phase}}", phase)
                .replace("{{error_category}}", error_category)
                .replace("{{error_stderr}}", error_stderr)
                .replace("{{project_type}}", project_type)
                .replace("{{target_roles}}", target_roles)
                .replace("{{target_phases}}", target_phases)
                .replace("{{dependencies}}", dependencies)
                .replace("{{previous_repair_attempts}}", str(previous_repair_attempts))
                .replace("{{root_cause}}", root_cause)
                .replace("{{suggested_fix}}", suggested_fix)
                .replace("{{index_summary}}", index_summary))

    def _load_experience(self, entry_id: str, entry_type: str) -> dict:
        try:
            if entry_type in {"skill", "skill-pack"}:
                return self._load_skill_experience(entry_id)
            return self._load_case_experience(entry_id)
        except Exception as exc:
            logger.warning("Failed to load experience %s: %s", entry_id, exc)
            return {"id": entry_id, "_warning": f"Load failed: {exc}"}

    def _load_skill_experience(self, entry_id: str) -> dict:
        store = self.store
        for entry in store.read_index():
            if entry.get("id") != entry_id:
                continue
            loaded = self._load_skill_from_asset_paths(entry, entry_id)
            if loaded:
                return loaded

        # 1. Try promoted skills directory first
        skill_name = self._derive_skill_name(entry_id)
        if skill_name:
            md = os.path.join(store.skills_dir, skill_name, "SKILL.md")
            if os.path.isfile(md):
                data = store.read_skill(skill_name)
                if data.get("title") or data.get("steps") or data.get("body"):
                    data["id"] = entry_id
                    data["type"] = "skill"
                    data["file_path"] = md
                    return data

        # 2. Fallback: Staging refined experience
        staging = self._load_staging_experience(entry_id)
        if staging and staging.get("title"):
            return staging

        return {"id": entry_id, "type": "skill", "_warning": f"Skill not found: {entry_id}"}

    def _load_skill_from_asset_paths(self, entry: dict, entry_id: str) -> dict:
        for asset_path in entry.get("asset_paths", []):
            absolute = asset_path if os.path.isabs(asset_path) else os.path.join(self.store.repo_root, asset_path)
            if not os.path.isfile(absolute):
                continue
            if absolute.endswith(".json"):
                with open(absolute, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data["id"] = entry_id
                    data["type"] = data.get("type", entry.get("type", "skill"))
                    data["file_path"] = absolute
                    return data
            if os.path.basename(absolute) == "SKILL.md":
                with open(absolute, "r", encoding="utf-8") as fh:
                    content = fh.read()
                data = {
                    "id": entry_id,
                    "type": entry.get("type", "skill-pack"),
                    "title": entry.get("title", self._markdown_title(content)),
                    "category": entry.get("category", ""),
                    "subtype": entry.get("subtype", ""),
                    "tags": entry.get("tags", []),
                    "target_roles": entry.get("target_roles", []),
                    "target_phases": entry.get("target_phases", []),
                    "body": content,
                    "file_path": absolute,
                    "asset_paths": entry.get("asset_paths", []),
                }
                return data
        return {}

    @staticmethod
    def _markdown_title(content: str) -> str:
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return ""

    def _load_case_experience(self, entry_id: str) -> dict:
        for entry in self.store.read_index():
            if entry.get("id") == entry_id:
                for asset_path in entry.get("asset_paths", []):
                    absolute = asset_path if os.path.isabs(asset_path) else os.path.join(self.store.repo_root, asset_path)
                    if os.path.isfile(absolute) and absolute.endswith(".json"):
                        with open(absolute, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                        data["id"] = entry_id
                        data["type"] = data.get("type", entry.get("type", "document"))
                        return data
        case_path = os.path.join(self.store.cases_dir, f"{entry_id}.json")
        if os.path.isfile(case_path):
            with open(case_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data["id"] = entry_id
            data["type"] = data.get("type", "document")
            return data

        pattern = os.path.join(self.store.cases_dir, "**", f"{entry_id}.json")
        for fp in glob.glob(pattern, recursive=True):
            with open(fp, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data["id"] = entry_id
            data["type"] = data.get("type", "document")
            return data

        return {"id": entry_id, "type": "document", "_warning": f"Case not found: {entry_id}"}

    @staticmethod
    def _derive_skill_name(entry_id: str) -> str:
        parts = entry_id.split("-exp-")
        if len(parts) >= 2:
            return parts[-1]
        if entry_id.startswith("promoted-"):
            return entry_id[len("promoted-"):]
        return entry_id

    def _load_staging_experience(self, entry_id: str) -> dict:
        parts = entry_id.split("-exp-", 1)
        if len(parts) < 2:
            return {}
        run_id = parts[0]
        refined_dir = os.path.join(self.store.staging_dir, run_id, "refined")
        if not os.path.isdir(refined_dir):
            return {}
        matches = sorted(glob.glob(os.path.join(refined_dir, f"{run_id}-exp-*.json")))
        if not matches:
            return {}
        refined_path = matches[-1]
        with open(refined_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["id"] = entry_id
        data["type"] = data.get("type", "skill")
        data["file_path"] = refined_path
        return data

    @staticmethod
    def _extract_json(text: str) -> dict:
        if not text or not isinstance(text, str):
            return {}

        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 0
            for i, line in enumerate(lines):
                if line.strip().startswith("```"):
                    start = i + 1
                    break
            end = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip().startswith("```"):
                    end = i
                    break
            cleaned = "\n".join(lines[start:end]).strip()

        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
            return {}

        json_str = cleaned[first_brace: last_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("JSON decode failed: %s", exc)
            return {}

    def get_last_result(self) -> dict | None:
        return self._last_query_result

    def _get_query_session(self) -> str:
        """Create or reuse a persistent session for the query LLM call."""
        if not hasattr(self, "_query_session_id"):
            mgr = self.session_mgr
            if hasattr(mgr, "get_or_create"):
                self._query_session_id = mgr.get_or_create(
                    role="experience_querier", lifecycle="persistent"
                )
            elif hasattr(mgr, "_session_mgr"):
                self._query_session_id = mgr._session_mgr.get_or_create(
                    role="experience_querier", lifecycle="persistent"
                )
            else:
                self._query_session_id = "experience_querier"
        return self._query_session_id
