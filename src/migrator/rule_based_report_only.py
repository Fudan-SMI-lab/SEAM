from __future__ import annotations

import glob as glob_module
import os
import re
from typing import Any


class ReportOnlyRuleBasedMigrator:
    def __init__(self) -> None:
        self._patterns = self._build_patterns()

    def _build_patterns(self) -> list[tuple[str, str]]:
        return [
            ("torch_cuda_references", r"torch\.cuda"),
            ("cuda_method_calls", r"\.cuda\("),
            ("cuda_device_literals", r"[\"']cuda(?::\d+)?[\"']"),
            ("nccl_backend_literals", r"[\"']nccl[\"']"),
            ("nvidia_smi_references", r"nvidia-smi"),
            ("nvml_references", r"pynvml|py3nvml|nvml"),
            (
                "cuda_extension_references",
                r"CUDAExtension|cpp_extension|torch\.utils\.cpp_extension",
            ),
        ]

    def migrate(self, source_code: str) -> tuple[str, dict[str, Any]]:
        report: dict[str, Any] = {
            "rules": {},
            "total_replacements": 0,
            "mode": "report_only",
        }
        for name, pattern in self._patterns:
            report["rules"][name] = len(re.findall(pattern, source_code))
        return source_code, report

    def migrate_file(self, filepath: str) -> tuple[str, dict[str, Any]]:
        with open(filepath, "r", encoding="utf-8") as f:
            source_code = f.read()
        return self.migrate(source_code)

    def migrate_directory(self, dirpath: str, pattern: str = "*.py") -> dict[str, Any]:
        aggregate: dict[str, Any] = {
            "files": {},
            "summary": {
                "total_files": 0,
                "total_replacements": 0,
                "rules": {},
                "mode": "report_only",
            },
        }
        files = glob_module.glob(os.path.join(dirpath, "**", pattern), recursive=True)
        for filepath in files:
            try:
                _source_code, report = self.migrate_file(filepath)
                aggregate["files"][filepath] = report
                aggregate["summary"]["total_files"] += 1
                for rule_name, count in report["rules"].items():
                    summary_rules = aggregate["summary"]["rules"]
                    summary_rules[rule_name] = summary_rules.get(rule_name, 0) + count
            except Exception as exc:  # pylint: disable=broad-exception-caught; silent
                aggregate["files"][filepath] = {
                    "error": str(exc),
                    "total_replacements": 0,
                    "rules": {},
                    "mode": "report_only",
                }
        return aggregate
