# pyright: reportMissingTypeArgument=false,
# reportAttributeAccessIssue=false, reportArgumentType=false,
# reportUnknownParameterType=false, reportUnknownVariableType=false,
# reportUnknownMemberType=false, reportUnknownArgumentType=false,
# reportUnannotatedClassAttribute=false, reportAny=false,
# reportUnusedCallResult=false
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any

CATALOG_SCHEMA_VERSION = 1


class ExperienceRegistry:  # pylint: disable=too-many-instance-attributes; silent
    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root
        self.memory_dir = os.path.join(repo_root, "memory")
        self.index_dir = os.path.join(self.memory_dir, "index")
        self.staging_dir = os.path.join(self.memory_dir, "staging")
        self.cases_dir = os.path.join(self.memory_dir, "cases")
        self.promotions_dir = os.path.join(self.memory_dir, "promotions")
        self.quarantine_dir = os.path.join(self.memory_dir, "quarantine")
        self.archive_dir = os.path.join(self.memory_dir, "archive")
        self.skills_dir = os.path.join(repo_root, ".memory", "skills")
        self.local_skills_dir = self._resolve_local_skills_dir()
        self.catalog_path = os.path.join(self.index_dir, "experiences.jsonl")
        self.manifest_path = os.path.join(self.memory_dir, "manifest.json")
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        os.makedirs(self.index_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)
        os.makedirs(self.cases_dir, exist_ok=True)
        os.makedirs(self.promotions_dir, exist_ok=True)
        os.makedirs(self.skills_dir, exist_ok=True)

    def read_catalog(self) -> list[dict]:
        if not os.path.isfile(self.catalog_path):
            return []

        entries: list[dict] = []
        with open(self.catalog_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def upsert_catalog_entry(self, entry: dict) -> dict:
        entries = self.read_catalog()
        normalized = self.normalize_entry(entry)
        updated = False

        for i, existing in enumerate(entries):
            if existing.get("id") == normalized.get("id"):
                merged = dict(existing)
                merged.update(normalized)
                merged.setdefault(
                    "created_at", existing.get("created_at", normalized["created_at"])
                )
                if "usage" not in entry:
                    merged["usage"] = existing.get("usage", normalized.get("usage", {}))
                for counter_field in ("use_count", "failure_count", "last_used_at"):
                    if counter_field not in entry and counter_field in existing:
                        merged[counter_field] = existing[counter_field]
                merged["updated_at"] = normalized["updated_at"]
                entries[i] = self.normalize_entry(merged)
                normalized = entries[i]
                updated = True
                break

        if not updated:
            entries.append(normalized)

        self._rewrite_catalog(entries)
        self.refresh_manifest(entries)
        return normalized

    def write_manifest(self, manifest: dict) -> dict:
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
            f.write("\n")
        return manifest

    def refresh_manifest(self, entries: list[dict] | None = None) -> dict:
        if entries is None:
            entries = self.read_catalog()

        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for entry in entries:
            entry_type = str(entry.get("type", "unknown"))
            status = str(entry.get("status", "unknown"))
            by_type[entry_type] = by_type.get(entry_type, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1

        manifest = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "updated_at": self._now(),
            "counts": {
                "total": len(entries),
                "by_type": dict(sorted(by_type.items())),
                "by_status": dict(sorted(by_status.items())),
            },
            "storage_roots": {
                "memory": self._relpath(self.memory_dir),
                "staging": self._relpath(self.staging_dir),
                "cases": self._relpath(self.cases_dir),
                "promotions": self._relpath(self.promotions_dir),
                "skills": self._relpath(self.skills_dir),
                "local_skills": self._relpath(self.local_skills_dir),
                "catalog": self._relpath(self.catalog_path),
                "legacy_index": os.path.join("memory", "index", "cases.jsonl"),
                "archive": self._relpath(self.archive_dir),
                "quarantine": self._relpath(self.quarantine_dir),
            },
        }
        return self.write_manifest(manifest)

    def validate_integrity(self) -> dict:
        entries = self.read_catalog()
        seen: set[str] = set()
        duplicate_ids: list[str] = []
        missing_assets: list[dict] = []

        for entry in entries:
            entry_id = entry.get("id", "")
            if entry_id in seen:
                duplicate_ids.append(entry_id)
            seen.add(entry_id)

            for asset_path in entry.get("asset_paths", []):
                absolute_path = self._abs_asset_path(asset_path)
                if not os.path.exists(absolute_path):
                    missing_assets.append({"id": entry_id, "path": asset_path})

        return {
            "ok": not duplicate_ids and not missing_assets,
            "entry_count": len(entries),
            "duplicate_ids": duplicate_ids,
            "missing_asset_paths": missing_assets,
        }

    def rebuild_catalog(self) -> list[dict]:
        entries: list[dict] = []
        entries.extend(self._scan_skill_entries())
        entries.extend(self._scan_local_skill_entries())
        entries.extend(self._scan_promoted_json_entries())
        entries.extend(self._scan_case_json_entries())
        entries = self._dedupe_entries(entries)
        self._rewrite_catalog(entries)
        self.refresh_manifest(entries)
        return entries

    def local_skill_entries(self) -> list[dict]:
        return self._scan_local_skill_entries()

    def _scan_local_skill_entries(self) -> list[dict]:
        entries: list[dict] = []
        if not os.path.isdir(self.local_skills_dir):
            return entries

        for skill_name in sorted(os.listdir(self.local_skills_dir)):
            skill_dir = os.path.join(self.local_skills_dir, skill_name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isdir(skill_dir) or not os.path.isfile(skill_md):
                continue
            asset_paths = self._collect_local_skill_asset_paths(skill_dir)
            data = self._read_local_skill_metadata(skill_name, skill_md)
            data_path = os.path.join(skill_dir, "skill_data.json")
            if os.path.isfile(data_path):
                with open(data_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)
            entries.append(
                self.normalize_entry(
                    {
                        "id": f"local-skill-{skill_name}",
                        "type": "skill-pack",
                        "status": "local",
                        "title": data.get("title", skill_name),
                        "category": data.get("category", "local_skill_pack"),
                        "subtype": data.get("subtype", ""),
                        "tags": data.get("tags", []),
                        "confidence": data.get("confidence", 0.0),
                        "target_roles": data.get("target_roles", []),
                        "target_phases": data.get("target_phases", []),
                        "trigger_fingerprint": data.get(
                            "trigger_fingerprint", f"local_skill_pack|{skill_name}"
                        ),
                        "asset_paths": asset_paths,
                        "source_runs": ["local-skill-pack"],
                    }
                )
            )
        return entries

    def _collect_local_skill_asset_paths(self, skill_dir: str) -> list[str]:
        asset_paths: list[str] = []
        for current_root, dirs, files in os.walk(skill_dir):
            dirs[:] = sorted(
                d for d in dirs if d not in {"__pycache__", ".pytest_cache", ".ruff_cache"}
            )
            for filename in sorted(files):
                if filename.endswith((".pyc", ".pyo")):
                    continue
                asset_paths.append(self._relpath(os.path.join(current_root, filename)))
        skill_md = self._relpath(os.path.join(skill_dir, "SKILL.md"))
        return [skill_md] + [path for path in asset_paths if path != skill_md]

    def _read_local_skill_metadata(self, skill_name: str, skill_md: str) -> dict:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()
        metadata = self._parse_front_matter(content)
        title = (
            metadata.get("title")
            or metadata.get("name")
            or self._markdown_title(content)
            or skill_name
        )
        metadata["title"] = title
        return metadata

    def _parse_front_matter(self, content: str) -> dict:
        metadata: dict[str, Any] = {}
        if not content.startswith("---"):
            return metadata
        parts = content.split("---", 2)
        if len(parts) < 3:
            return metadata
        for line in parts[1].strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    metadata[key] = json.loads(value)
                    continue
                except json.JSONDecodeError:
                    pass
            metadata[key] = value
        return metadata

    @staticmethod
    def _markdown_title(content: str) -> str:
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return ""

    def compact_catalog(self, dry_run: bool = True) -> dict:
        before = self.read_catalog()
        after = self._dedupe_entries(before)
        result = {
            "dry_run": dry_run,
            "before_count": len(before),
            "after_count": len(after),
            "removed_count": len(before) - len(after),
            "removed_ids": self._removed_duplicate_ids(before),
        }
        if not dry_run:
            self._rewrite_catalog(after)
            self.refresh_manifest(after)
        return result

    def cleanup_staging(self, dry_run: bool = True, archive: bool = False) -> dict:
        run_dirs = self._list_dirs(self.staging_dir)
        consumed_runs = self._consumed_run_ids()
        targets = [path for path in run_dirs if os.path.basename(path) in consumed_runs]
        return self._cleanup_paths(
            targets, dry_run=dry_run, archive=archive, action="cleanup_staging"
        )

    def archive_consumed(self, dry_run: bool = True) -> dict:
        return self.cleanup_staging(dry_run=dry_run, archive=True)

    def prune_orphans(self, dry_run: bool = True, quarantine: bool = True) -> dict:
        protected_assets = {
            self._abs_asset_path(path)
            for entry in self.read_catalog() + self._read_legacy_index_entries()
            for path in entry.get("asset_paths", [])
        }
        roots = [self.cases_dir, self.promotions_dir]
        targets: list[str] = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for current_root, _, files in os.walk(root):
                for filename in files:
                    file_path = os.path.join(current_root, filename)
                    if file_path not in protected_assets:
                        targets.append(file_path)

        return self._cleanup_paths(
            targets, dry_run=dry_run, archive=quarantine, action="prune_orphans"
        )

    def normalize_entry(self, entry: dict) -> dict:
        now = self._now()
        entry_id = str(entry.get("id") or self._entry_id_from_data(entry))
        source_runs = entry.get(
            "source_runs", entry.get("merged_from_runs", entry.get("run_id", []))
        )
        if isinstance(source_runs, str):
            source_runs = [source_runs]

        normalized = {
            "id": entry_id,
            "type": entry.get("type", entry.get("promotion_type", "skill")),
            "status": entry.get("status", "promoted"),
            "title": entry.get("title", entry.get("name", entry_id)),
            "category": entry.get("category", ""),
            "subtype": entry.get("subtype", ""),
            "tags": self._as_list(entry.get("tags", [])),
            "confidence": float(entry.get("confidence", 0.0) or 0.0),
            "target_roles": self._as_list(entry.get("target_roles", [])),
            "target_phases": self._as_list(entry.get("target_phases", [])),
            "trigger_fingerprint": entry.get(
                "trigger_fingerprint", self._trigger_fingerprint(entry)
            ),
            "asset_paths": self._as_list(entry.get("asset_paths", [])),
            "source_runs": self._as_list(source_runs),
            "created_at": entry.get("created_at", entry.get("promoted_at", now)),
            "updated_at": entry.get("updated_at", entry.get("promoted_at", now)),
            "last_used_at": entry.get("last_used_at"),
            "use_count": int(entry.get("use_count", entry.get("used_count", 0)) or 0),
            "failure_count": int(entry.get("failure_count", 0) or 0),
            "usage": self._normalize_usage(entry.get("usage", entry)),
        }
        return normalized

    def record_usage(
        self,
        *,
        selected_ids: list[str] | None = None,
        used_ids: list[str] | None = None,
        ignored_ids: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        entries = self.read_catalog()
        changed = self._apply_usage_updates(
            entries,
            selected_ids=selected_ids,
            used_ids=used_ids,
            ignored_ids=ignored_ids,
            verification=verification,
        )
        if changed:
            self._rewrite_catalog(entries)
            self.refresh_manifest(entries)

    def _apply_usage_updates(  # pylint: disable=too-many-locals; silent
        self,
        entries: list[dict],
        *,
        selected_ids: list[str] | None = None,
        used_ids: list[str] | None = None,
        ignored_ids: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> bool:
        now = self._now()
        selected = set(str(item) for item in selected_ids or [] if item)
        used = set(str(item) for item in used_ids or [] if item)
        ignored = set(str(item) for item in ignored_ids or [] if item)
        verified_ids = set(
            str(item) for item in (verification or {}).get("experience_ids", []) if item
        )
        verification_passed = (verification or {}).get("passed")
        changed = False

        for index, entry in enumerate(entries):
            entry_id = str(entry.get("id", ""))
            if entry_id not in selected | used | ignored | verified_ids:
                continue
            updated = dict(entry)
            usage = self._normalize_usage(updated.get("usage", updated))
            if entry_id in selected:
                usage["selected_count"] += 1
                usage["last_selected_at"] = now
            if entry_id in used:
                usage["used_count"] += 1
                usage["last_used_at"] = now
                updated["last_used_at"] = now
                updated["use_count"] = int(updated.get("use_count", 0) or 0) + 1
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
                    updated["failure_count"] = int(updated.get("failure_count", 0) or 0) + 1
            updated["usage"] = usage
            updated["updated_at"] = now
            entries[index] = self.normalize_entry(updated)
            changed = True
        return changed

    def normalize_usage(self, value: object) -> dict[str, Any]:
        return self._normalize_usage(value)

    def _normalize_usage(self, value: object) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        return {
            "selected_count": int(raw.get("selected_count", 0) or 0),
            "used_count": int(raw.get("used_count", raw.get("use_count", 0)) or 0),
            "ignored_count": int(raw.get("ignored_count", 0) or 0),
            "verification_attempt_count": int(raw.get("verification_attempt_count", 0) or 0),
            "verification_success_count": int(raw.get("verification_success_count", 0) or 0),
            "verification_failure_count": int(
                raw.get("verification_failure_count", raw.get("failure_count", 0)) or 0
            ),
            "last_selected_at": raw.get("last_selected_at"),
            "last_used_at": raw.get("last_used_at"),
            "last_ignored_at": raw.get("last_ignored_at"),
            "last_verification_at": raw.get("last_verification_at"),
            "last_verification_passed": raw.get("last_verification_passed"),
        }

    def catalog_entry_from_promotion(
        self, run_id: str, promotion_type: str, exp_data: dict, asset_paths: list[str]
    ) -> dict:
        skill_name = exp_data.get("skill_name", exp_data.get("name", f"{run_id}-promoted"))
        return self.normalize_entry(
            {
                "id": f"promoted-{skill_name}",
                "type": promotion_type,
                "status": "promoted",
                "title": exp_data.get("title", skill_name),
                "category": exp_data.get("category", ""),
                "subtype": exp_data.get("subtype", ""),
                "tags": exp_data.get("tags", []),
                "confidence": exp_data.get("confidence", 0.0),
                "target_roles": exp_data.get("target_roles", []),
                "target_phases": exp_data.get("target_phases", []),
                "trigger_fingerprint": exp_data.get(
                    "trigger_fingerprint", self._trigger_fingerprint(exp_data)
                ),
                "asset_paths": asset_paths,
                "source_runs": exp_data.get(
                    "source_runs", exp_data.get("merged_from_runs", [run_id])
                ),
                "created_at": exp_data.get("created_at", exp_data.get("promoted_at", self._now())),
                "updated_at": exp_data.get("promoted_at", self._now()),
            }
        )

    def _scan_skill_entries(self) -> list[dict]:
        entries: list[dict] = []
        if not os.path.isdir(self.skills_dir):
            return entries

        for skill_name in sorted(os.listdir(self.skills_dir)):
            skill_dir = os.path.join(self.skills_dir, skill_name)
            data_path = os.path.join(skill_dir, "skill_data.json")
            if not os.path.isdir(skill_dir) or not os.path.isfile(data_path):
                continue
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            asset_paths = [self._relpath(data_path)]
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_md):
                asset_paths.append(self._relpath(skill_md))
            entries.append(
                self.catalog_entry_from_promotion(
                    data.get("run_id", data.get("source_run", "rebuild")),
                    data.get("promotion_type", "skill"),
                    {**data, "skill_name": data.get("skill_name", data.get("name", skill_name))},
                    asset_paths,
                )
            )
        return entries

    def _scan_promoted_json_entries(self) -> list[dict]:
        entries: list[dict] = []
        if not os.path.isdir(self.promotions_dir):
            return entries
        for current_root, _, files in os.walk(self.promotions_dir):
            if "experience.json" not in files:
                continue
            file_path = os.path.join(current_root, "experience.json")
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            promotion_type = data.get(
                "promotion_type", os.path.basename(os.path.dirname(os.path.dirname(file_path)))
            )
            asset_paths = [self._relpath(file_path)]
            for asset_name in data.get("asset_names", []):
                asset_path = os.path.join(os.path.dirname(file_path), asset_name)
                if os.path.exists(asset_path):
                    asset_paths.append(self._relpath(asset_path))
            entries.append(
                self.catalog_entry_from_promotion(
                    data.get("run_id", "rebuild"),
                    promotion_type,
                    {
                        **data,
                        "skill_name": data.get(
                            "skill_name", data.get("name", os.path.basename(current_root))
                        ),
                    },
                    sorted(set(asset_paths)),
                )
            )
        return entries

    def _scan_case_json_entries(self) -> list[dict]:
        entries: list[dict] = []
        if not os.path.isdir(self.cases_dir):
            return entries
        for current_root, _, files in os.walk(self.cases_dir):
            for filename in sorted(files):
                if not filename.endswith(".json"):
                    continue
                file_path = os.path.join(current_root, filename)
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries.append(
                    self.normalize_entry({**data, "asset_paths": [self._relpath(file_path)]})
                )
        return entries

    def _dedupe_entries(self, entries: list[dict]) -> list[dict]:
        by_id: dict[str, dict] = {}
        for entry in entries:
            normalized = self.normalize_entry(entry)
            entry_id = normalized["id"]
            if entry_id not in by_id:
                by_id[entry_id] = normalized
                continue
            by_id[entry_id] = self._merge_entries(by_id[entry_id], normalized)
        return [by_id[key] for key in sorted(by_id)]

    def _merge_entries(self, old: dict, new: dict) -> dict:
        merged = dict(old)
        merged.update({key: value for key, value in new.items() if value not in (None, "", [])})
        merged["created_at"] = min(
            str(old.get("created_at", new.get("created_at", ""))),
            str(new.get("created_at", old.get("created_at", ""))),
        )
        merged["updated_at"] = max(str(old.get("updated_at", "")), str(new.get("updated_at", "")))
        merged["asset_paths"] = sorted(
            set(self._as_list(old.get("asset_paths", [])))
            | set(self._as_list(new.get("asset_paths", [])))
        )
        merged["source_runs"] = sorted(
            set(self._as_list(old.get("source_runs", [])))
            | set(self._as_list(new.get("source_runs", [])))
        )
        merged["tags"] = sorted(
            set(self._as_list(old.get("tags", []))) | set(self._as_list(new.get("tags", [])))
        )
        merged["use_count"] = int(old.get("use_count", 0) or 0) + int(new.get("use_count", 0) or 0)
        merged["failure_count"] = int(old.get("failure_count", 0) or 0) + int(
            new.get("failure_count", 0) or 0
        )
        old_usage = self._normalize_usage(old.get("usage", old))
        new_usage = self._normalize_usage(new.get("usage", new))
        usage = old_usage
        for key in (
            "selected_count",
            "used_count",
            "ignored_count",
            "verification_attempt_count",
            "verification_success_count",
            "verification_failure_count",
        ):
            usage[key] = int(old_usage.get(key, 0) or 0) + int(new_usage.get(key, 0) or 0)
        for key in (
            "last_selected_at",
            "last_used_at",
            "last_ignored_at",
            "last_verification_at",
        ):
            usage[key] = max(str(old_usage.get(key) or ""), str(new_usage.get(key) or "")) or None
        usage["last_verification_passed"] = new_usage.get(
            "last_verification_passed", old_usage.get("last_verification_passed")
        )
        merged["usage"] = usage
        return self.normalize_entry(merged)

    def _read_legacy_index_entries(self) -> list[dict]:
        legacy_index = os.path.join(self.index_dir, "cases.jsonl")
        if not os.path.isfile(legacy_index):
            return []
        entries: list[dict] = []
        with open(legacy_index, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def _removed_duplicate_ids(self, entries: list[dict]) -> list[str]:
        seen: set[str] = set()
        removed: list[str] = []
        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if entry_id in seen:
                removed.append(entry_id)
            seen.add(entry_id)
        return removed

    def _cleanup_paths(self, targets: list[str], dry_run: bool, archive: bool, action: str) -> dict:
        result = {
            "action": action,
            "dry_run": dry_run,
            "archive": archive,
            "target_count": len(targets),
            "targets": [self._relpath(path) for path in targets],
            "moved": [],
            "deleted": [],
        }
        if dry_run:
            return result

        for target in targets:
            if archive:
                destination_root = (
                    self.archive_dir if action != "prune_orphans" else self.quarantine_dir
                )
                destination = os.path.join(destination_root, self._relpath(target))
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.move(target, destination)
                result["moved"].append(
                    {"from": self._relpath(target), "to": self._relpath(destination)}
                )
            else:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
                result["deleted"].append(self._relpath(target))
        return result

    def _consumed_run_ids(self) -> set[str]:
        run_ids: set[str] = set()
        for entry in self.read_catalog():
            if entry.get("status") in {"promoted", "consumed", "archived"}:
                run_ids.update(str(run_id) for run_id in entry.get("source_runs", []) if run_id)
        return run_ids

    def _rewrite_catalog(self, entries: list[dict]) -> None:
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        tmp_path = self.catalog_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(self.normalize_entry(entry), sort_keys=True) + "\n")
        os.replace(tmp_path, self.catalog_path)

    def _entry_id_from_data(self, entry: dict) -> str:
        name = entry.get("skill_name", entry.get("name", entry.get("title", "experience")))
        return f"promoted-{str(name).strip().replace(' ', '-').lower()}"

    def _trigger_fingerprint(self, entry: dict) -> str:
        parts = [str(entry.get("category", "")), str(entry.get("subtype", ""))]
        parts.extend(sorted(str(tag) for tag in self._as_list(entry.get("tags", []))))
        return "|".join(part for part in parts if part)

    def _abs_asset_path(self, asset_path: str) -> str:
        if os.path.isabs(asset_path):
            return asset_path
        return os.path.join(self.repo_root, asset_path)

    def _relpath(self, path: str) -> str:
        return os.path.relpath(path, self.repo_root)

    def _resolve_local_skills_dir(self) -> str:
        local_root = os.path.join(self.repo_root, ".skills")
        parent_root = os.path.join(os.path.dirname(self.repo_root), ".skills")
        if os.path.isdir(parent_root) and not os.path.isdir(local_root):
            return parent_root
        return local_root

    def _list_dirs(self, root: str) -> list[str]:
        if not os.path.isdir(root):
            return []
        return [
            os.path.join(root, entry)
            for entry in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, entry))
        ]

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
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
