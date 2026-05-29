# pyright: reportMissingTypeArgument=false,
# reportUnknownParameterType=false, reportUnknownVariableType=false,
# reportUnknownMemberType=false, reportUnknownArgumentType=false,
# reportUnannotatedClassAttribute=false,
# reportUninitializedInstanceVariable=false,
# reportMissingParameterType=false, reportUnusedCallResult=false
"""Experience evaluator — scans migration artifacts and produces candidate experiences."""

import glob
import json
import logging
import os
import re

from core.experience_store import ExperienceStore

logger = logging.getLogger(__name__)

EVALUATOR_TIMEOUT = 2400

_CANONICAL_PHASES = [
    ("phase_0_env_detect_canonical.json", "env"),
    ("phase_1_project_analysis_canonical.json", "project_analysis"),
    ("phase_2_venv_create_canonical.json", "venv"),
    ("phase_3_entry_script_canonical.json", "entry_script"),
    ("phase_35_static_validate_canonical.json", "static_validate"),
    ("phase_4_rule_migration_canonical.json", "rule_migration"),
    ("phase_5_validation_canonical.json", "validation_summary"),
    ("phase_6_report_canonical.json", "report"),
]


class ExperienceEvaluator:  # pylint: disable=too-few-public-methods; silent
    """Assessment agent that scans migration artifacts and produces candidate experiences."""

    def __init__(self, artifact_dir: str, store: ExperienceStore, session_mgr):
        self.artifact_dir = artifact_dir
        self.store = store
        self.session_mgr = session_mgr

    def _get_evaluator_session(self) -> str:
        if not hasattr(self, "_evaluator_session_id"):
            mgr = self.session_mgr
            if hasattr(mgr, "get_or_create"):
                # pylint: disable-next=attribute-defined-outside-init; silent
                self._evaluator_session_id = mgr.get_or_create(
                    role="experience_evaluator", lifecycle="persistent"
                )
            elif hasattr(mgr, "_session_mgr"):
                # pylint: disable-next=attribute-defined-outside-init,protected-access; silent
                self._evaluator_session_id = mgr._session_mgr.get_or_create(
                    role="experience_evaluator", lifecycle="persistent"
                )
            else:
                # pylint: disable-next=attribute-defined-outside-init; silent
                self._evaluator_session_id = "experience_evaluator"
        return self._evaluator_session_id

    def evaluate(self, run_id: str) -> list[dict]:
        """Run the full evaluation pipeline and return list of candidates."""
        artifacts = self._load_all_artifacts()
        project_source_root = self._resolve_project_source_root(artifacts)
        context = self._build_evaluation_context(artifacts, run_id, project_source_root)
        full_prompt = self._load_prompt(context)

        logger.info("Calling LLM evaluator for run_id=%s (timeout=%ds)", run_id, EVALUATOR_TIMEOUT)
        session_id = self._get_evaluator_session()
        raw_response = self.session_mgr.send_command(
            session_id, full_prompt, timeout=EVALUATOR_TIMEOUT
        )

        parsed = self._parse_json_response(raw_response)
        candidates = self._normalize_candidates(
            parsed.get("candidates", []),
            parsed.get("project_source_root") or project_source_root,
            run_id,
        )
        summary_text = parsed.get("evaluation_summary", "")

        self.store.write_evaluation_summary(run_id, summary_text)
        for c in candidates:
            cid = c["candidate_id"]
            self.store.write_candidate(run_id, cid, c)

        logger.info("Evaluation complete for run_id=%s: %d candidates", run_id, len(candidates))
        return candidates

    def _normalize_candidates(
        self, raw_candidates: object, project_source_root: str, run_id: str
    ) -> list[dict]:
        if not isinstance(raw_candidates, list):
            return []

        candidates: list[dict] = []
        seen_ids: set[str] = set()
        for index, raw_candidate in enumerate(raw_candidates, start=1):
            if not isinstance(raw_candidate, dict):
                continue
            candidate = dict(raw_candidate)
            candidate_id = self._stable_candidate_id(candidate, index, seen_ids)
            seen_ids.add(candidate_id)
            candidate["candidate_id"] = candidate_id
            candidate.setdefault("source_run_id", run_id)
            candidate.setdefault("project_source_root", project_source_root)
            candidates.append(candidate)
        return candidates

    @staticmethod
    def _stable_candidate_id(candidate: dict, index: int, seen_ids: set[str]) -> str:
        raw_id = str(candidate.get("candidate_id") or "").strip()
        if raw_id:
            candidate_id = (
                re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_id).strip("-") or f"candidate-{index:03d}"
            )
        else:
            candidate_id = f"candidate-{index:03d}"
        if candidate_id not in seen_ids:
            return candidate_id
        suffix = 2
        while f"{candidate_id}-{suffix}" in seen_ids:
            suffix += 1
        return f"{candidate_id}-{suffix}"

    def _load_all_artifacts(self) -> dict:
        validated_dir = os.path.join(self.artifact_dir, "validated")
        raw_dir = os.path.join(self.artifact_dir, "raw")
        result: dict = {}

        for filename, key in _CANONICAL_PHASES:
            filepath = os.path.join(validated_dir, filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        result[key] = json.load(f)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("Failed to read %s: %s", filename, exc)
                    result[key] = {}
            else:
                result[key] = {}

        # V2 saves as phase_run_entry_script_attempt*.json; V1 saves as
        # phase_5_validation_attempt*.json
        attempts = []
        for attempt_pattern in [
            os.path.join(raw_dir, "phase_run_entry_script_attempt*.json"),
            os.path.join(raw_dir, "phase_5_validation_attempt*.json"),
        ]:
            found = sorted(glob.glob(attempt_pattern))
            if found:
                for fp in found:
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            attempts.append(json.load(f))
                    except (json.JSONDecodeError, OSError) as exc:
                        logger.warning("Failed to read attempt %s: %s", fp, exc)
                break
        result["attempts"] = attempts

        journal_path = os.path.join(self.artifact_dir, "execution_journal.jsonl")
        journal_lines = []
        if os.path.isfile(journal_path):
            try:
                with open(journal_path, "r", encoding="utf-8") as f:
                    journal_lines = f.readlines()
            except OSError as exc:
                logger.warning("Failed to read journal: %s", exc)
        result["journal_lines"] = journal_lines

        return result

    def _resolve_project_source_root(self, artifacts: dict) -> str:
        phase1 = artifacts.get("project_analysis", {})
        if isinstance(phase1, dict):
            pd = phase1.get("project_dir", "")
            if pd:
                return pd
        entry = artifacts.get("entry_script", {})
        if isinstance(entry, dict):
            ep = entry.get("entry_script_path", "")
            if ep:
                d = os.path.dirname(ep)
                if d and os.path.isdir(d):
                    return d
        return ""

    def _build_evaluation_context(
        self, artifacts: dict, run_id: str, project_source_root: str
    ) -> str:
        lines: list[str] = [
            f"## Run ID: {run_id}",
            f"## Project Source Root: {project_source_root or '(unknown)'}",
        ]

        for key, label in [
            ("env", "Phase 0 — Environment Detection"),
            ("project_analysis", "Phase 1 — Project Analysis"),
            ("venv", "Phase 2 — Venv Create"),
            ("entry_script", "Phase 3 — Entry Script"),
            ("static_validate", "Phase 3.5 — Static Validation"),
            ("rule_migration", "Phase 4 — Rule Migration"),
            ("validation_summary", "Phase 5 — Validation Summary"),
            ("report", "Phase 6 — Report"),
        ]:
            data = artifacts.get(key, {})
            if data:
                snippet = json.dumps(data, indent=2, ensure_ascii=False)[:2000]
                lines.append(f"\n### {label}\n```json\n{snippet}\n```")
            else:
                lines.append(f"\n### {label}\n*(not available)*")

        attempts = artifacts.get("attempts", [])
        lines.append(f"\n### Phase 5 Repair Attempts ({len(attempts)} attempts)\n")
        for i, att in enumerate(attempts):
            snippet = json.dumps(att, indent=2, ensure_ascii=False)[:1500]
            lines.append(f"#### Attempt {i + 1}\n```json\n{snippet}\n```")

        journal_lines = artifacts.get("journal_lines", [])
        if journal_lines:
            journal_text = "".join(journal_lines)[-3000:]
            lines.append(f"\n### Execution Journal (last 3000 chars)\n```\n{journal_text}\n```")

        return "\n".join(lines)

    def _load_prompt(self, context: str) -> str:
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prompts",
            "experience_evaluator.md",
        )
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read()
        except OSError:
            logger.error("Prompt file not found: %s", prompt_path)
            template = ""

        for placeholder in ["{context}", "{{context}}", "{artifacts}"]:
            if placeholder in template:
                return template.replace(placeholder, context)

        return template + "\n\n---\n\n" + context

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
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
            logger.warning("No JSON object found in response")
            return {"_raw": raw}

        json_str = text[first_brace : last_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("JSON parse failed: %s", exc)
            return {"_raw": raw}
