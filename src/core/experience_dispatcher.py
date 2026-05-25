# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false
"""Experience dispatcher — orchestrates sequential refinement of candidates with independent timeouts."""

import logging
import os

from core.experience_classifier import ExperienceClassifier
from core.experience_refiner import ExperienceRefiner
from core.experience_store import ExperienceStore

logger = logging.getLogger(__name__)


class ExperienceDispatcher:
    """Sequential dispatcher that refines candidates one at a time with independent timeouts."""

    refine_timeout_sec = 3600

    def __init__(self, artifact_dir: str, store: ExperienceStore, session_mgr):
        self.artifact_dir = artifact_dir
        self.store = store
        self.session_mgr = session_mgr
        self.classifier = ExperienceClassifier(session_mgr)
        self.refiner = ExperienceRefiner(artifact_dir, store, session_mgr)

    def dispatch_and_refine(self, run_id: str, candidates: list[dict]) -> list[dict]:
        """Load artifact contexts, sequentially refine each candidate, auto-promote results.

        Steps:
        1. Load artifact contexts for all candidates
        2. SEQUENTIAL refinement: for each candidate, call refiner.refine()
           - Each has independent timeout_sec
           - Catch Exception, log warning, continue
        3. For each successful result: store.check_and_auto_promote(exp, run_id)
        4. Return list of refined experiences
        """
        artifact_contexts = self._load_artifact_contexts(candidates)
        results: list[dict] = []

        for candidate in candidates:
            cid = candidate.get("candidate_id", "unknown")
            ctx = artifact_contexts.get(cid, {})

            try:
                classification = self.classifier.classify(
                    candidate, ctx, timeout_sec=min(self.refine_timeout_sec, 300)
                )
                candidate["classification"] = classification
                refined = self.refiner.refine(
                    candidate,
                    run_id,
                    ctx,
                    timeout_sec=self.refine_timeout_sec,
                    classification=classification,
                )
                results.append(refined)
                logger.info("Refined candidate %s successfully", cid)
            except Exception as exc:
                logger.warning("Failed to refine candidate %s: %s", cid, exc)
                continue

        for exp in results:
            try:
                self.store.check_and_auto_promote(exp, run_id)
            except Exception as exc:
                logger.warning("Auto-promote failed for experience %s: %s",
                               exp.get("title", "unknown"), exc)

        return results

    def _load_artifact_contexts(self, candidates: list[dict]) -> dict[str, dict]:
        """Load artifact evidence and source file content for each candidate.

        For each candidate:
        - Read artifact_evidence file paths from candidate dict
        - Try to read from artifact_dir/validated/<path>
        - For involved_code_files:
          Try: join(project_source_root, file["path"])
          Fallback: "[File not found: <path>]"
        - Return dict: candidate_id -> {"evidence": "...", "source_files": "..."}
        """
        contexts: dict[str, dict] = {}

        for candidate in candidates:
            cid = candidate.get("candidate_id", "unknown")
            project_source_root = candidate.get("project_source_root", "")

            evidence_parts: list[str] = []
            artifact_evidence = candidate.get("artifact_evidence", [])
            if isinstance(artifact_evidence, list):
                for ev_path in artifact_evidence:
                    content = self._read_file_safe(ev_path, cid)
                    if content:
                        evidence_parts.append(f"### {ev_path}\n```\n{content}\n```")
            elif isinstance(artifact_evidence, str):
                content = self._read_file_safe(artifact_evidence, cid)
                if content:
                    evidence_parts.append(f"### {artifact_evidence}\n```\n{content}\n```")

            source_parts: list[str] = []
            involved_files = candidate.get("involved_code_files", [])
            if isinstance(involved_files, list):
                for file_entry in involved_files:
                    if isinstance(file_entry, dict):
                        file_path = file_entry.get("path", "")
                    else:
                        file_path = str(file_entry)

                    if not file_path:
                        continue

                    if project_source_root:
                        full_path = os.path.join(project_source_root, file_path)
                    else:
                        full_path = os.path.join(
                            os.path.dirname(self.artifact_dir),
                            "project_source",
                            file_path,
                        )

                    if os.path.isfile(full_path):
                        try:
                            with open(full_path, "r", encoding="utf-8") as f:
                                source_parts.append(f"### {file_path}\n```\n{f.read()[:5000]}\n```")
                        except OSError as exc:
                            logger.warning("Cannot read source file %s: %s", full_path, exc)
                            source_parts.append(f"### {file_path}\n\n[Error reading file: {exc}]")
                    else:
                        source_parts.append(f"### {file_path}\n\n[File not found: {file_path}]")

            contexts[cid] = {
                "evidence": "\n\n".join(evidence_parts) or "(no artifact evidence available)",
                "source_files": "\n\n".join(source_parts) or "(no source files available)",
            }

        return contexts

    def _read_file_safe(self, path: str, candidate_id: str) -> str:
        """Safely read a file, trying multiple locations."""
        if not path:
            return ""
        path = str(path).strip()
        if ":lines" in path:
            path = path.split(":lines", 1)[0].strip()

        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                pass

        candidate_paths = []
        if path.startswith(("validated/", "raw/")) or path == "execution_journal.jsonl":
            candidate_paths.append(os.path.join(self.artifact_dir, path))
        candidate_paths.extend([
            os.path.join(self.artifact_dir, "validated", path),
            os.path.join(self.artifact_dir, "raw", path),
            os.path.join(self.artifact_dir, path),
        ])
        for candidate_path in candidate_paths:
            if os.path.isfile(candidate_path):
                try:
                    with open(candidate_path, "r", encoding="utf-8") as f:
                        return f.read()[:3000]
                except OSError:
                    pass

        logger.debug("Artifact file not found for candidate %s: %s", candidate_id, path)
        return f"[Artifact not found: {path}]"
