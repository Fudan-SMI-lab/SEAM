from __future__ import annotations

import glob as glob_module
import os
import re
from typing import Any


class YamlRuleBasedMigrator:  # pylint: disable=too-many-instance-attributes; silent
    def __init__(self, strategy: dict[str, Any] | None = None) -> None:
        self.strategy = strategy or {}
        self.strategy_id = str(self.strategy.get("id", "report_only"))
        self.mode = str(self.strategy.get("mode", "report_only")).strip().lower()
        self.rewrite_enabled = self.mode == "rewrite" and bool(
            self.strategy.get("rewrite", {}).get("enabled", False)
            if isinstance(self.strategy.get("rewrite"), dict)
            else False
        )
        self.rules = self._parse_rules("rules", replacement_required=True)
        self.scan_patterns = self._parse_rules("scan_patterns", replacement_required=False)
        self.inject_imports = self._parse_inject_imports()
        self.inject_when_regex = self._parse_inject_when_regex()

    def _parse_rules(self, key: str, *, replacement_required: bool) -> list[dict[str, str]]:
        raw_rules = self.strategy.get(key, [])
        if not isinstance(raw_rules, list):
            return []
        parsed: list[dict[str, str]] = []
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("id", "")).strip()
            pattern = str(item.get("pattern", item.get("regex", ""))).strip()
            replacement = str(item.get("replacement", ""))
            if not rule_id or not pattern:
                continue
            if replacement_required and "replacement" not in item:
                continue
            parsed.append({"id": rule_id, "pattern": pattern, "replacement": replacement})
        return parsed

    def _parse_inject_imports(self) -> list[str]:
        rewrite = self.strategy.get("rewrite")
        if not isinstance(rewrite, dict):
            return []
        raw_imports = rewrite.get("inject_imports", [])
        if not isinstance(raw_imports, list):
            return []
        return [str(item).strip() for item in raw_imports if str(item).strip()]

    def _parse_inject_when_regex(self) -> str:
        rewrite = self.strategy.get("rewrite")
        if not isinstance(rewrite, dict):
            return ""
        return str(rewrite.get("inject_when_regex", "")).strip()

    def _inject_imports_if_needed(self, source_code: str, report: dict[str, Any]) -> str:
        if not self.rewrite_enabled or not self.inject_imports:
            return source_code
        if self.inject_when_regex and not re.search(self.inject_when_regex, source_code):
            return source_code

        lines = source_code.split("\n")
        last_import_idx = 0
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import_idx = idx

        injected_count = 0
        for import_line in self.inject_imports:
            if not re.search(rf"^{re.escape(import_line)}\b", source_code, re.MULTILINE):
                lines.insert(last_import_idx + 1 + injected_count, import_line)
                injected_count += 1

        if injected_count:
            report["rules"]["inject_imports"] = injected_count
            report["total_replacements"] += injected_count
            return "\n".join(lines)
        report["rules"].setdefault("inject_imports", 0)
        return source_code

    def migrate(self, source_code: str) -> tuple[str, dict[str, Any]]:
        report: dict[str, Any] = {
            "strategy": self.strategy_id,
            "mode": self.mode,
            "destructive": self.rewrite_enabled,
            "rules": {},
            "total_replacements": 0,
        }

        if self.rewrite_enabled:
            source_code = self._inject_imports_if_needed(source_code, report)
            for rule in self.rules:
                source_code, count = re.subn(rule["pattern"], rule["replacement"], source_code)
                report["rules"][rule["id"]] = count
                report["total_replacements"] += count
            return source_code, report

        for rule in self.scan_patterns or self.rules:
            report["rules"][rule["id"]] = len(re.findall(rule["pattern"], source_code))
        return source_code, report

    def migrate_file(self, filepath: str) -> tuple[str, dict[str, Any]]:
        with open(filepath, "r", encoding="utf-8") as f:
            source_code = f.read()
        return self.migrate(source_code)

    def migrate_directory(self, dirpath: str, pattern: str = "*.py") -> dict[str, Any]:
        aggregate: dict[str, Any] = {
            "files": {},
            "summary": {
                "strategy": self.strategy_id,
                "mode": self.mode,
                "destructive": self.rewrite_enabled,
                "total_files": 0,
                "total_replacements": 0,
                "rules": {},
            },
        }
        files = glob_module.glob(os.path.join(dirpath, "**", pattern), recursive=True)
        for filepath in files:
            try:
                new_code, report = self.migrate_file(filepath)
                aggregate["files"][filepath] = report
                aggregate["summary"]["total_files"] += 1
                aggregate["summary"]["total_replacements"] += report["total_replacements"]
                for rule_name, count in report["rules"].items():
                    summary_rules = aggregate["summary"]["rules"]
                    summary_rules[rule_name] = summary_rules.get(rule_name, 0) + count

                if self.rewrite_enabled and report["total_replacements"] > 0:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_code)
            except Exception as exc:  # pylint: disable=broad-exception-caught; silent
                aggregate["files"][filepath] = {
                    "error": str(exc),
                    "strategy": self.strategy_id,
                    "mode": self.mode,
                    "destructive": self.rewrite_enabled,
                    "total_replacements": 0,
                    "rules": {},
                }
        return aggregate
