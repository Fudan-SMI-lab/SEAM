from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

from rule_strategies import _load_strategy_definition, create_migrator_for_strategy


class _RuleMigratorDelegate(Protocol):
    rewrite_enabled: bool

    def migrate(self, source_code: str) -> tuple[str, dict[str, object]]:
        ...

    def migrate_file(self, filepath: str) -> tuple[str, dict[str, object]]:
        ...

    def migrate_directory(self, dirpath: str, pattern: str = "*.py") -> dict[str, object]:
        ...


class RuleBasedMigrator:
    strategy: str
    _delegate: _RuleMigratorDelegate
    _report_injections: dict[str, str]
    rewrite_enabled: bool

    def __init__(self, target_platform: str | None = None, strategy: str | None = None):
        requested = (strategy or target_platform or "report_only").strip().lower()
        self.strategy = requested or "report_only"
        self._delegate = cast(_RuleMigratorDelegate, create_migrator_for_strategy(self.strategy))
        self.rewrite_enabled = self._delegate.rewrite_enabled
        self._report_injections = self._load_report_injections()

    def _load_report_injections(self) -> dict[str, str]:
        strategy_def = _load_strategy_definition(self.strategy)
        if strategy_def is None:
            return {}
        raw = strategy_def.get("report_injections")
        if not isinstance(raw, dict):
            return {}
        result: dict[str, str] = {}
        for k, v in cast(dict[str, Any], raw).items():
            if isinstance(k, str) and isinstance(v, str):
                result[k] = v
        return result

    def _normalize_report(self, report: dict[str, object]) -> dict[str, object]:
        rules = report.setdefault("rules", {})
        for inj_key, source_key in self._report_injections.items():
            if isinstance(rules, dict) and inj_key not in rules:
                rules[inj_key] = int(rules.get(source_key, 0) or 0)
        report.setdefault("strategy", self.strategy)
        return report

    def migrate(self, source_code: str) -> tuple[str, dict[str, object]]:
        migrated, report = self._delegate.migrate(source_code)
        return migrated, self._normalize_report(report)

    def migrate_file(self, filepath: str) -> tuple[str, dict[str, object]]:
        migrated, report = self._delegate.migrate_file(filepath)
        return migrated, self._normalize_report(report)

    def migrate_directory(self, dirpath: str, pattern: str = "*.py") -> dict[str, object]:
        aggregate = self._delegate.migrate_directory(dirpath, pattern=pattern)
        files = aggregate.get("files", {})
        if not isinstance(files, Mapping):
            files = {}
        for report in files.values():
            if isinstance(report, dict):
                _ = self._normalize_report(cast(dict[str, object], report))
        summary = aggregate.get("summary")
        if isinstance(summary, dict):
            normalized_summary = cast(dict[str, object], summary)
            rules = normalized_summary.setdefault("rules", {})
            for inj_key, source_key in self._report_injections.items():
                if isinstance(rules, dict) and inj_key not in rules:
                    rules[inj_key] = int(rules.get(source_key, 0) or 0)
            normalized_summary.setdefault("strategy", self.strategy)
        return aggregate
