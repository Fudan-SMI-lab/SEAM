# pyright: reportMissingTypeArgument=false,
# reportUnknownParameterType=false, reportUnknownVariableType=false,
# reportUnknownMemberType=false, reportUnknownArgumentType=false,
# reportUnannotatedClassAttribute=false, reportPrivateUsage=false,
# reportUnusedCallResult=false
"""Batch promotion tool for staging experiences."""

import json
import os
from collections import defaultdict

from core.experience_store import ExperienceStore


class ExperiencePromoter:  # pylint: disable=too-few-public-methods; silent
    """Scan ALL staging entries and promote matching ones in batch."""

    min_occurrences = 2
    min_tags_overlap = 2

    def __init__(self, store: ExperienceStore):
        self.store = store

    def batch_promote_staging(self) -> dict:  # pylint: disable=too-many-locals; silent
        result = {"promoted": [], "merged": [], "skipped": []}
        all_entries = self.store.read_index()
        staging = [e for e in all_entries if e.get("status") == "staging"]
        if not staging:
            return result
        groups = self._group_by_similarity(staging)
        for (category, name), entries in groups.items():
            if len(entries) < self.min_occurrences:
                for e in entries:
                    result["skipped"].append(e.get("id"))
                continue
            # pylint: disable-next=protected-access; silent
            promotion_type = self.store._normalize_promotion_type(
                entries[0].get("type", entries[0].get("promotion_type", "skill"))
            )
            existing = (
                # pylint: disable-next=protected-access; silent
                self.store._find_promoted_skill(category, name)
                if promotion_type == "skill"
                else None
            )
            if existing is not None:
                experiences = []
                for e in entries:
                    exp = self._load_staging_exp(e)
                    if exp is not None:
                        experiences.append(exp)
                if experiences:
                    # pylint: disable-next=protected-access; silent
                    self.store._merge_into_promoted_skill(
                        existing["dir"], experiences[-1], experiences[:-1]
                    )
                    result["merged"].append(existing["name"])
            else:
                best = max(entries, key=lambda e: e.get("confidence", 0.0))
                exp = self._load_staging_exp(best) or best
                if not exp.get("id"):
                    exp["id"] = best.get("id")
                self.store.promote_from_staging(exp.get("run_id", "unknown"), promotion_type, exp)
                result["promoted"].append(best.get("id"))
            consumed = [eid for e in entries if (eid := e.get("id")) is not None]
            # pylint: disable-next=protected-access; silent
            self.store._mark_entries_consumed(consumed, all_entries)
            self.store._rewrite_index(all_entries)  # pylint: disable=protected-access; silent
        return result

    def _group_by_similarity(self, entries: list[dict]) -> dict[tuple, list[dict]]:
        """Group entries by (category, skill_name or title)."""
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for entry in entries:
            category = entry.get("category", "unknown")
            skill_name = (
                entry.get("skill_name") or entry.get("name") or entry.get("title", "unknown")
            )
            groups[(category, skill_name)].append(entry)
        return dict(groups)

    def _load_staging_exp(self, entry: dict) -> dict | None:
        """Load full experience JSON from staging directory."""
        run_id = entry.get("run_id")
        exp_id = entry.get("id")
        if not run_id or not exp_id:
            return None
        path = os.path.join(self.store.staging_dir, run_id, "refined", f"{exp_id}.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
