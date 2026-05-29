"""Rule-based CUDA-to-PPU code migrator.

Conservative and auditable: does NOT convert torch.cuda to torch.npu,
does NOT inject import torch.npu, does NOT emit invented commands.
Only scans for NVIDIA-specific patterns and reports them.
"""

from __future__ import annotations

import glob as glob_module
import os
import re
from typing import Any  # noqa: F401


class PPURuleBasedMigrator:
    """Conservative CUDA-to-PPU code migrator.

    Unlike the NPU RuleBasedMigrator which converts torch.cuda to torch.npu,
    this migrator preserves torch.cuda (the correct API for PPU) and only
    reports NVIDIA-specific patterns without modifying source code.
    """

    def __init__(self):
        self._patterns = self._build_patterns()

    def _build_patterns(self) -> list[tuple[str, str]]:
        """Return list of (name, regex_pattern) tuples for detecting NVIDIA-specific code."""
        return [
            # Detect nvidia-smi command references (report-only, no replacement)
            ("nvidia_smi_references", r"nvidia-smi"),
            # Detect subprocess/system calls to nvidia-smi
            ("nvidia_smi_subprocess", r"subprocess\.run.*nvidia|os\.system.*nvidia"),
            # Detect torch.cuda.is_available in probe contexts
            # (report for awareness, no replacement needed for PPU)
            ("nvidia_smi_imports", r"pynvml|py3nvml"),
        ]

    def migrate(self, source_code: str) -> tuple[str, dict[str, Any]]:
        """Scan source code for NVIDIA-specific patterns.

        Args:
            source_code: Python source code as string.

        Returns:
            Tuple of (unchanged_source_code, report_dict).
            Report contains per-pattern reference counts and summary.
            Source code is NOT modified.
        """
        report: dict[str, Any] = {
            "rules": {},
            "total_replacements": 0,
        }

        report["rules"]["inject_torch_npu"] = 0
        report["rules"]["torch_cuda_to_npu"] = 0
        report["rules"]["cuda_method_to_npu"] = 0

        for name, pattern in self._patterns:
            count = len(re.findall(pattern, source_code))
            report["rules"][name] = count

        # No replacements are made - this is report-only
        return source_code, report

    def migrate_file(self, filepath: str) -> tuple[str, dict[str, Any]]:
        """Read and scan a single file."""
        with open(filepath, "r", encoding="utf-8") as f:
            source_code = f.read()
        return self.migrate(source_code)

    def migrate_directory(
        self,
        dirpath: str,
        pattern: str = "*.py",
    ) -> dict[str, Any]:
        """Scan all matching files in a directory.

        Does NOT modify any source files.
        """
        aggregate: dict[str, Any] = {
            "files": {},
            "summary": {"total_files": 0, "total_replacements": 0, "rules": {}},
        }
        files = glob_module.glob(os.path.join(dirpath, "**", pattern), recursive=True)

        for filepath in files:
            try:
                # pylint: disable-next=unused-variable; silent
                source_code, report = self.migrate_file(filepath)
                aggregate["files"][filepath] = report
                aggregate["summary"]["total_files"] += 1
                aggregate["summary"]["total_replacements"] += report["total_replacements"]

                for rule_name, count in report["rules"].items():
                    aggregate["summary"]["rules"][rule_name] = (
                        aggregate["summary"]["rules"].get(rule_name, 0) + count
                    )

                # PPU migrator never writes back - source is unchanged
            except Exception as e:  # pylint: disable=broad-exception-caught; silent
                aggregate["files"][filepath] = {
                    "error": str(e),
                    "total_replacements": 0,
                    "rules": {},
                }

        return aggregate
