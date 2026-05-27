# pyright: reportMissingImports=false, reportMissingTypeArgument=false, reportArgumentType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAny=false, reportExplicitAny=false, reportUnusedCallResult=false
import json
import os
from datetime import datetime, timezone
from typing import Any

from .experience_registry import ExperienceRegistry


class ExperienceStore:

    repo_root: str
    staging_dir: str
    cases_dir: str
    promotions_dir: str
    index_path: str
    catalog_path: str
    manifest_path: str
    skills_dir: str
    local_skills_dir: str
    candidates_subdir: str
    refined_subdir: str
    registry: ExperienceRegistry

    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root
        self.staging_dir = os.path.join(repo_root, "memory", "staging")
        self.cases_dir = os.path.join(repo_root, "memory", "cases")
        self.promotions_dir = os.path.join(repo_root, "memory", "promotions")
        self.index_path = os.path.join(repo_root, "memory", "index", "cases.jsonl")
        self.skills_dir = os.path.join(repo_root, ".memory", "skills")
        self.registry = ExperienceRegistry(repo_root)
        self.local_skills_dir = self.registry.local_skills_dir
        self.catalog_path = self.registry.catalog_path
        self.manifest_path = self.registry.manifest_path
        self.candidates_subdir = "candidates"
        self.refined_subdir = "refined"

        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        os.makedirs(self.staging_dir, exist_ok=True)
        os.makedirs(self.cases_dir, exist_ok=True)
        os.makedirs(self.promotions_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        os.makedirs(self.skills_dir, exist_ok=True)
        for subdir in ("knowledge", "rules", "prompt_proposals"):
            os.makedirs(os.path.join(self.promotions_dir, subdir), exist_ok=True)

    def write_evaluation_summary(self, run_id: str, summary: str) -> str:
        run_dir = os.path.join(self.staging_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        filepath = os.path.join(run_dir, "evaluation_summary.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(summary)
        return filepath

    def write_candidate(self, run_id: str, candidate_id: str, candidate: dict) -> str:
        run_dir = os.path.join(self.staging_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        candidates_dir = os.path.join(run_dir, self.candidates_subdir)
        os.makedirs(candidates_dir, exist_ok=True)
        filepath = os.path.join(candidates_dir, f"{candidate_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(candidate, f, indent=2)
        return filepath

    def read_candidates(self, run_id: str) -> list[dict]:
        candidates_dir = os.path.join(self.staging_dir, run_id, self.candidates_subdir)
        if not os.path.isdir(candidates_dir):
            return []

        candidates: list[dict] = []
        for entry in sorted(os.listdir(candidates_dir)):
            if entry.endswith(".json"):
                filepath = os.path.join(candidates_dir, entry)
                with open(filepath, "r", encoding="utf-8") as f:
                    candidates.append(json.load(f))
        return candidates

    def write_refined_experience(self, run_id: str, experience: dict, assets: dict) -> str:
        run_dir = os.path.join(self.staging_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        refined_dir = os.path.join(run_dir, self.refined_subdir)
        os.makedirs(refined_dir, exist_ok=True)

        exp_id = experience.get("id", f"{run_id}-exp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        experience["id"] = exp_id
        experience["run_id"] = run_id
        experience["created_at"] = datetime.now(timezone.utc).isoformat()
        main_path = os.path.join(refined_dir, f"{exp_id}.json")
        with open(main_path, "w", encoding="utf-8") as f:
            json.dump(experience, f, indent=2)

        for asset_name, asset_content in assets.items():
            if os.path.isabs(asset_name) or ".." in asset_name.split(os.sep):
                raise ValueError(f"Unsafe asset name: {asset_name}")
            asset_path = os.path.join(refined_dir, asset_name)
            os.makedirs(os.path.dirname(asset_path), exist_ok=True)
            self._write_asset_content(asset_path, asset_content)

        return refined_dir

    def upsert_index(self, entry: dict) -> None:
        entries = self._read_persisted_index()

        updated = False
        for i, existing in enumerate(entries):
            if existing.get("id") == entry.get("id"):
                entries[i] = entry
                updated = True
                break

        if not updated:
            entries.append(entry)

        self._rewrite_index(entries)

    def read_index(self) -> list[dict]:
        return self._merge_index_entries(self._read_persisted_index(), self._local_skill_index_entries())

    def _read_persisted_index(self) -> list[dict]:
        if not os.path.isfile(self.index_path):
            return []

        entries: list[dict] = []
        with open(self.index_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def read_catalog(self) -> list[dict[str, Any]]:
        return self.registry.read_catalog()

    def upsert_catalog_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        return self.registry.upsert_catalog_entry(entry)

    def record_experience_usage(
        self,
        *,
        selected_ids: list[str] | None = None,
        used_ids: list[str] | None = None,
        ignored_ids: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        self.registry.record_usage(
            selected_ids=selected_ids,
            used_ids=used_ids,
            ignored_ids=ignored_ids,
            verification=verification,
        )
        entries = self._read_persisted_index()
        changed = self._apply_legacy_usage_updates(
            entries,
            selected_ids=selected_ids,
            used_ids=used_ids,
            ignored_ids=ignored_ids,
            verification=verification,
        )
        if changed:
            self._rewrite_index(entries)

    def write_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return self.registry.write_manifest(manifest)

    def refresh_manifest(self) -> dict[str, Any]:
        return self.registry.refresh_manifest()

    def validate_integrity(self) -> dict[str, Any]:
        return self.registry.validate_integrity()

    def rebuild_catalog(self) -> list[dict[str, Any]]:
        return self.registry.rebuild_catalog()

    def _local_skill_index_entries(self) -> list[dict[str, Any]]:
        entries = [
            entry for entry in self.registry.read_catalog()
            if entry.get("status") == "local" and entry.get("type") == "skill-pack"
        ]
        if entries:
            return entries
        return self.registry.local_skill_entries()

    @staticmethod
    def _merge_index_entries(entries: list[dict], additions: list[dict[str, Any]]) -> list[dict]:
        seen = {str(entry.get("id", "")) for entry in entries}
        merged = list(entries)
        for entry in additions:
            entry_id = str(entry.get("id", ""))
            if entry_id and entry_id not in seen:
                merged.append(entry)
                seen.add(entry_id)
        return merged

    def compact_catalog(self, dry_run: bool = True) -> dict[str, Any]:
        return self.registry.compact_catalog(dry_run=dry_run)

    def cleanup_staging(self, dry_run: bool = True, archive: bool = False) -> dict[str, Any]:
        return self.registry.cleanup_staging(dry_run=dry_run, archive=archive)

    def archive_consumed(self, dry_run: bool = True) -> dict[str, Any]:
        return self.registry.archive_consumed(dry_run=dry_run)

    def prune_orphans(self, dry_run: bool = True, quarantine: bool = True) -> dict[str, Any]:
        return self.registry.prune_orphans(dry_run=dry_run, quarantine=quarantine)


    def _apply_legacy_usage_updates(
        self,
        entries: list[dict],
        *,
        selected_ids: list[str] | None = None,
        used_ids: list[str] | None = None,
        ignored_ids: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        selected = set(str(item) for item in selected_ids or [] if item)
        used = set(str(item) for item in used_ids or [] if item)
        ignored = set(str(item) for item in ignored_ids or [] if item)
        verified_ids = set(str(item) for item in (verification or {}).get("experience_ids", []) if item)
        verification_passed = (verification or {}).get("passed")
        changed = False

        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if entry_id not in selected | used | ignored | verified_ids:
                continue
            usage = self.registry.normalize_usage(entry.get("usage", entry))
            if entry_id in selected:
                usage["selected_count"] += 1
                usage["last_selected_at"] = now
            if entry_id in used:
                usage["used_count"] += 1
                usage["last_used_at"] = now
                entry["last_used_at"] = now
                entry["use_count"] = int(entry.get("use_count", 0) or 0) + 1
            if entry_id in ignored:
                usage["ignored_count"] += 1
                usage["last_ignored_at"] = now
            if entry_id in verified_ids:
                usage["verification_attempt_count"] += 1
                usage["last_verification_at"] = now
                usage["last_verification_passed"] = bool(verification_passed)
                if verification_passed is True:
                    usage["verification_success_count"] += 1
                elif verification_passed is False:
                    usage["verification_failure_count"] += 1
                    entry["failure_count"] = int(entry.get("failure_count", 0) or 0) + 1
            entry["usage"] = usage
            entry["updated_at"] = now
            changed = True
        return changed

    def _write_asset_content(self, asset_path: str, asset_content: Any) -> None:
        with open(asset_path, "w", encoding="utf-8") as f:
            if isinstance(asset_content, (dict, list)):
                json.dump(asset_content, f, indent=2)
                f.write("\n")
                return
            text = str(asset_content)
            f.write(text if text.endswith("\n") else text + "\n")

    def read_skill(self, skill_name: str) -> dict:
        skill_dir = os.path.join(self.skills_dir, skill_name)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        result: dict[str, Any] = {"name": skill_name}

        if not os.path.isfile(skill_md):
            return result

        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()

        meta = self._parse_meta_from_skill(content)
        result.update(meta)

        result["title"] = self._get_skill_title(content)
        result["when_to_use"] = self._get_skill_field(content, "When to Use")
        result["root_cause"] = self._get_skill_field(content, "Root Cause")
        result["steps"] = self._parse_steps_from_skill(content)
        result["code_examples"] = self._get_skill_field(content, "Code Examples")
        result["antipatterns"] = self._parse_antipatterns_from_skill(content)
        result["references"] = self._get_skill_field(content, "References")
        result["evidence"] = self._get_skill_field(content, "Evidence")
        result["body"] = content

        return result

    def promote_from_staging(self, run_id: str, promotion_type: str, exp_data: dict) -> str:
        promotion_type = self._normalize_promotion_type(promotion_type or exp_data.get("type", "skill"))
        skill_name = exp_data.get("skill_name", exp_data.get("name", f"{run_id}-promoted"))
        asset_contents = exp_data.get("_asset_contents", {}) if isinstance(exp_data.get("_asset_contents", {}), dict) else {}

        if promotion_type == "skill":
            skill_dir = os.path.join(self.skills_dir, skill_name)
            os.makedirs(skill_dir, exist_ok=True)

            exp_data.setdefault("name", skill_name)
            data_path = os.path.join(skill_dir, "skill_data.json")
            exp_data["promoted_at"] = datetime.now(timezone.utc).isoformat()
            exp_data["promotion_type"] = promotion_type
            exp_data.pop("_asset_contents", None)
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(exp_data, f, indent=2)

            promoted_asset_paths = [data_path]
            for asset_name, asset_content in asset_contents.items():
                if os.path.isabs(asset_name) or ".." in asset_name.split(os.sep):
                    raise ValueError(f"Unsafe asset name: {asset_name}")
                if asset_name == "skill_data.json":
                    continue
                asset_path = os.path.join(skill_dir, asset_name)
                os.makedirs(os.path.dirname(asset_path), exist_ok=True)
                self._write_asset_content(asset_path, asset_content)
                promoted_asset_paths.append(asset_path)

            skill_md_path = os.path.join(skill_dir, "SKILL.md")
            if "SKILL.md" not in asset_contents:
                self._render_skill_md_with_data(exp_data, skill_md_path)
                promoted_asset_paths.append(skill_md_path)
            elif skill_md_path not in promoted_asset_paths:
                promoted_asset_paths.append(skill_md_path)

            promoted_id = f"promoted-{skill_name}"
            self.upsert_index({
                "id": promoted_id,
                "type": promotion_type,
                "category": exp_data.get("category", ""),
                "subtype": exp_data.get("subtype", ""),
                "title": exp_data.get("title", skill_name),
                "tags": exp_data.get("tags", []),
                "confidence": exp_data.get("confidence", 0.0),
                "target_roles": exp_data.get("target_roles", []),
                "target_phases": exp_data.get("target_phases", []),
                "trigger_fingerprint": exp_data.get("trigger_fingerprint", ""),
                "status": "promoted",
                "run_id": run_id,
                "created_at": exp_data.get("promoted_at", ""),
            })
            self.upsert_catalog_entry(self.registry.catalog_entry_from_promotion(
                run_id,
                promotion_type,
                exp_data,
                [os.path.relpath(path, self.repo_root) for path in promoted_asset_paths],
            ))

            return data_path

        else:
            root_dir = self._promotion_root(promotion_type)
            item_name = exp_data.get("name", exp_data.get("title", skill_name))
            item_slug = self._slug(item_name)
            item_dir = os.path.join(root_dir, item_slug)
            os.makedirs(item_dir, exist_ok=True)
            file_path = os.path.join(item_dir, "experience.json")
            exp_data["promoted_at"] = datetime.now(timezone.utc).isoformat()
            exp_data["promotion_type"] = promotion_type
            exp_data.pop("_asset_contents", None)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(exp_data, f, indent=2)

            promoted_asset_paths = [file_path]
            for asset_name, asset_content in asset_contents.items():
                if os.path.isabs(asset_name) or ".." in asset_name.split(os.sep):
                    raise ValueError(f"Unsafe asset name: {asset_name}")
                asset_path = os.path.join(item_dir, asset_name)
                os.makedirs(os.path.dirname(asset_path), exist_ok=True)
                self._write_asset_content(asset_path, asset_content)
                promoted_asset_paths.append(asset_path)

            promoted_id = f"promoted-{item_slug}"
            self.upsert_index({
                "id": promoted_id,
                "type": promotion_type,
                "category": exp_data.get("category", ""),
                "subtype": exp_data.get("subtype", ""),
                "title": exp_data.get("title", skill_name),
                "tags": exp_data.get("tags", []),
                "confidence": exp_data.get("confidence", 0.0),
                "target_roles": exp_data.get("target_roles", []),
                "target_phases": exp_data.get("target_phases", []),
                "trigger_fingerprint": exp_data.get("trigger_fingerprint", ""),
                "status": "promoted",
                "run_id": run_id,
                "created_at": exp_data.get("promoted_at", ""),
            })
            self.upsert_catalog_entry(self.registry.catalog_entry_from_promotion(
                run_id,
                promotion_type,
                {**exp_data, "skill_name": item_slug, "name": item_slug},
                [os.path.relpath(path, self.repo_root) for path in promoted_asset_paths],
            ))

            return file_path

    def _tags_overlap(self, tags_a: list, tags_b: list) -> int:
        if not tags_a or not tags_b:
            return 0
        return len(set(tags_a) & set(tags_b))

    def _find_similar_exp_entries(
        self,
        category: str,
        tags: list,
        min_overlap: int = 2,
        status_filter: str | None = None,
    ) -> list[dict]:
        entries = self.read_index()
        similar: list[dict] = []

        for entry in entries:
            if status_filter is not None and entry.get("status") != status_filter:
                continue
            if entry.get("category") != category:
                continue
            entry_tags = entry.get("tags", [])
            overlap = self._tags_overlap(tags, entry_tags)
            if overlap >= min_overlap:
                similar.append(entry)

        return similar

    def _find_promoted_skill(self, category: str, skill_name: str | None = None) -> dict | None:
        if not os.path.isdir(self.skills_dir):
            return None

        for entry in os.listdir(self.skills_dir):
            skill_dir = os.path.join(self.skills_dir, entry)
            if not os.path.isdir(skill_dir):
                continue

            data_path = os.path.join(skill_dir, "skill_data.json")
            if os.path.isfile(data_path):
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                skill_md = os.path.join(skill_dir, "SKILL.md")
                if not os.path.isfile(skill_md):
                    continue
                data = self._parse_meta_from_skill(open(skill_md, "r", encoding="utf-8").read())

            if data.get("category") != category:
                continue

            if skill_name is not None:
                actual_name = data.get("name", entry)
                if actual_name != skill_name:
                    continue

            return {"dir": skill_dir, "data": data, "name": entry}

        return None

    def check_and_auto_promote(self, exp: dict, run_id: str) -> bool:
        category = exp.get("category", "")
        tags = exp.get("tags", [])
        skill_name = exp.get("skill_name", exp.get("name", None))
        title = exp.get("title", "")
        exp_type = self._normalize_promotion_type(exp.get("type", exp.get("promotion_type", "skill")))

        index_id = f"{run_id}-exp-{skill_name or title}"

        similar_staging = self._find_similar_exp_entries(category, tags, min_overlap=2, status_filter="staging")
        similar_staging = [e for e in similar_staging if e.get("id") != index_id]

        existing_skill = self._find_promoted_skill(category, skill_name) if exp_type == "skill" else None

        exists_count = 1 if existing_skill is not None else 0
        total_count = len(similar_staging) + exists_count + 1

        if total_count < 2:
            exp["id"] = index_id
            exp["run_id"] = run_id
            exp["status"] = "staging"
            exp["created_at"] = datetime.now(timezone.utc).isoformat()
            self.upsert_index(exp)
            return False

        if existing_skill is not None:
            exp["id"] = index_id
            exp["run_id"] = run_id
            result = self._merge_into_promoted_skill(existing_skill["dir"], exp, similar_staging)
            if result:
                all_entries = self._read_persisted_index()
                consumed_ids = [e.get("id") for e in similar_staging if e.get("id")]
                self._mark_entries_consumed(consumed_ids, all_entries)
                exp_id = exp.get("id", index_id)
                if exp_id:
                    self._mark_entries_consumed([exp_id], all_entries)
                self._rewrite_index(all_entries)
            return result

        all_candidates = similar_staging + [exp]
        best_entry = max(all_candidates, key=lambda e: e.get("confidence", 0.0))

        if not best_entry.get("id"):
            best_entry["id"] = f"{run_id}-exp-{skill_name or title}"

        collected_run_ids = set()
        for entry in similar_staging:
            rid = entry.get("run_id")
            if rid:
                collected_run_ids.add(rid)
        best_id = best_entry.get("id", index_id)
        collected_run_ids.add(best_id)
        best_entry["merged_from_runs"] = sorted(collected_run_ids)

        self.promote_from_staging(run_id, exp_type, best_entry)

        all_entries = self._read_persisted_index()
        consumed_ids = [e.get("id") for e in similar_staging if e.get("id")]
        if exp.get("id"):
            consumed_ids.append(exp["id"])
        self._mark_entries_consumed(consumed_ids, all_entries)
        self._rewrite_index(all_entries)

        return True

    def _normalize_promotion_type(self, promotion_type: str) -> str:
        aliases = {
            "knowledge": "document",
            "doc": "document",
            "case": "document",
            "prompt_proposal": "prompt",
            "prompt_improvement": "prompt",
        }
        value = str(promotion_type or "skill").strip().lower().replace("-", "_")
        value = aliases.get(value, value)
        return value if value in {"skill", "document", "rule", "prompt"} else "skill"

    def _promotion_root(self, promotion_type: str) -> str:
        roots = {
            "document": os.path.join(self.promotions_dir, "knowledge"),
            "rule": os.path.join(self.promotions_dir, "rules"),
            "prompt": os.path.join(self.promotions_dir, "prompt_proposals"),
        }
        return roots.get(promotion_type, os.path.join(self.promotions_dir, promotion_type))

    @staticmethod
    def _slug(value: str) -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value)).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug or "experience"

    def _merge_into_promoted_skill(self, skill_path: str, new_exp: dict, staging_others: list) -> bool:
        data_path = os.path.join(skill_path, "skill_data.json")

        if os.path.isfile(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            skill_md = os.path.join(skill_path, "SKILL.md")
            if os.path.isfile(skill_md):
                data = self.read_skill(os.path.basename(skill_path))
            else:
                data = {}

        entries_to_merge = staging_others + [new_exp]

        existing_steps = data.get("fix_steps", [])
        seen_steps = set(existing_steps)
        for entry in entries_to_merge:
            for step in entry.get("fix_steps", []):
                if step not in seen_steps:
                    existing_steps.append(step)
                    seen_steps.add(step)
        data["fix_steps"] = existing_steps

        existing_antipatterns = set(data.get("antipatterns", []))
        for entry in entries_to_merge:
            for ap in entry.get("antipatterns", []):
                existing_antipatterns.add(ap)
        data["antipatterns"] = sorted(existing_antipatterns)

        existing_changes = {c.get("file", ""): c for c in data.get("code_changes", [])}
        for entry in entries_to_merge:
            for change in entry.get("code_changes", []):
                file_key = change.get("file", "")
                if file_key and file_key not in existing_changes:
                    existing_changes[file_key] = change
        data["code_changes"] = list(existing_changes.values())

        existing_runs = set(data.get("merged_from_runs", []))
        for entry in entries_to_merge:
            rid = entry.get("run_id")
            if rid:
                existing_runs.add(rid)
        data["merged_from_runs"] = sorted(existing_runs)

        max_confidence = data.get("confidence", 0.0)
        for entry in entries_to_merge:
            c = entry.get("confidence", 0.0)
            if c > max_confidence:
                max_confidence = c
        data["confidence"] = max_confidence

        data["occurrence_count"] = len(existing_runs)

        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        self._render_skill_md_with_data(data, os.path.join(skill_path, "SKILL.md"))

        return True

    def _render_skill_md_with_data(self, data: dict, output_path: str) -> None:
        name = data.get("name", "unknown")
        description = data.get("title", data.get("description", ""))
        tags = data.get("tags", [])
        category = data.get("category", "")
        subtype = data.get("subtype", "")
        confidence = data.get("confidence", 0.0)
        occurrence_count = data.get("occurrence_count", 1)

        lines: list[str] = []

        lines.append("---")
        lines.append(f"name: {name}")
        lines.append(f"description: {description}")
        lines.append(f"tags: {json.dumps(tags)}")
        lines.append(f"category: {category}")
        lines.append(f"subtype: {subtype}")
        lines.append(f"confidence: {confidence}")
        lines.append(f"occurrence_count: {occurrence_count}")
        lines.append("---")
        lines.append("")

        title = data.get("title", name)
        lines.append(f"# {title}")
        lines.append("")

        when_to_use = data.get("when_to_use", "")
        symptoms = data.get("symptoms", [])
        affected_patterns = data.get("affected_patterns", [])
        lines.append("## When to Use")
        if isinstance(when_to_use, str):
            for line_text in when_to_use.strip().split("\n"):
                line_text = line_text.strip()
                if line_text.startswith("- "):
                    lines.append(line_text)
                elif line_text:
                    lines.append(f"- {line_text}")
        if symptoms:
            for symptom in symptoms:
                lines.append(f"- {symptom}")
        if affected_patterns:
            lines.append(f"- Error involves: {', '.join(affected_patterns)}")
        lines.append("")

        root_cause = data.get("root_cause", "")
        if root_cause:
            lines.append("## Root Cause")
            lines.append(root_cause)
            lines.append("")

        fix_steps = data.get("fix_steps", data.get("steps", []))
        if fix_steps:
            lines.append("## How to Use")
            for i, step in enumerate(fix_steps, 1):
                lines.append(f"{i}. {step}")
            lines.append("")

        code_changes = data.get("code_changes", [])
        if code_changes:
            lines.append("## Code Examples")
            for change in code_changes:
                file_path = change.get("file", "unknown")
                before = change.get("before", "")
                after = change.get("after", "")
                lines.append(f"**File: {file_path}**")
                if before:
                    lines.append("# Before")
                    lines.append(before)
                if after:
                    lines.append("# After")
                    lines.append(after)
                lines.append("")

        antipatterns = data.get("antipatterns", [])
        if antipatterns:
            lines.append("## Do Not")
            for ap in antipatterns:
                lines.append(f"- {ap}")
            lines.append("")

        refs = data.get("references", data.get("refs", []))
        if refs:
            lines.append("## References")
            if isinstance(refs, str):
                for r in refs.strip().split("\n"):
                    r = r.strip()
                    if r.startswith("- "):
                        lines.append(r)
                    elif r:
                        lines.append(f"- {r}")
            else:
                for ref in refs:
                    lines.append(f"- {ref}")
            lines.append("")

        merged_from_runs = data.get("merged_from_runs", [])
        lines.append("## Evidence")
        if merged_from_runs:
            for run in merged_from_runs:
                lines.append(f"- Source runs: {run}")
        else:
            lines.append("- No source runs recorded")
        lines.append("")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _rewrite_index(self, entries: list[dict]) -> None:
        index_dir = os.path.dirname(self.index_path)
        os.makedirs(index_dir, exist_ok=True)

        tmp_path = self.index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        os.replace(tmp_path, self.index_path)

    def _mark_entries_consumed(self, entry_ids: list[str], all_entries: list[dict]) -> None:
        id_set = set(entry_ids)
        for entry in all_entries:
            if entry.get("id") in id_set:
                entry["status"] = "consumed"

    @staticmethod
    def _parse_steps_from_skill(content: str) -> list[str]:
        section = ""
        lines = content.split("\n")
        in_section = False
        for line in lines:
            if line.strip().startswith("## "):
                if "How to Use" in line or "how to use" in line.lower():
                    in_section = True
                    continue
                if in_section:
                    break
            if in_section:
                section += line + "\n"

        steps: list[str] = []
        for line in section.strip().split("\n"):
            line = line.strip()
            if line.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                stripped = line.lstrip("0123456789. ")
                if stripped:
                    steps.append(stripped)
        return steps

    @staticmethod
    def _parse_antipatterns_from_skill(content: str) -> list[str]:
        section = ""
        lines = content.split("\n")
        in_section = False
        for line in lines:
            if line.strip().startswith("## "):
                if "Do Not" in line or "do not" in line.lower():
                    in_section = True
                    continue
                if in_section:
                    break
            if in_section:
                section += line + "\n"

        antipatterns: list[str] = []
        for line in section.strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                antipatterns.append(line[2:])
        return antipatterns

    @staticmethod
    def _parse_meta_from_skill(content: str) -> dict:
        meta: dict[str, Any] = {}
        if not content.startswith("---"):
            return meta

        parts = content.split("---", 2)
        if len(parts) < 3:
            return meta

        yaml_block = parts[1].strip()
        for line in yaml_block.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()

                if value.startswith("[") and value.endswith("]"):
                    try:
                        meta[key] = json.loads(value)
                        continue
                    except json.JSONDecodeError:
                        pass

                try:
                    meta[key] = float(value) if "." in value else int(value)
                    continue
                except ValueError:
                    pass

                if value.lower() == "true":
                    meta[key] = True
                elif value.lower() == "false":
                    meta[key] = False
                else:
                    meta[key] = value

        return meta

    @staticmethod
    def _get_skill_title(content: str) -> str:
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# ") and not line.startswith("## "):
                return line[2:].strip()
        return ""

    @staticmethod
    def _get_skill_field(content: str, field: str) -> str:
        lines = content.split("\n")
        in_section = False
        section_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                if in_section:
                    break
                if field.lower() in stripped.lower():
                    in_section = True
                    continue
                continue
            if in_section:
                section_lines.append(line)

        return "\n".join(section_lines).strip()

    @staticmethod
    def _get_skill_confidence(content: str) -> float:
        if not content.startswith("---"):
            return 0.0

        parts = content.split("---", 2)
        if len(parts) < 3:
            return 0.0

        yaml_block = parts[1].strip()
        for line in yaml_block.split("\n"):
            line = line.strip()
            if line.startswith("confidence:"):
                value = line.split(":", 1)[1].strip()
                try:
                    return float(value)
                except ValueError:
                    return 0.0
        return 0.0
