"""Rule-based CUDA to NPU code migrator."""

import os
import re
import glob as glob_module
from typing import Any  # noqa: F401

# pyright: reportIndexIssue=false, reportOperatorIssue=false, reportAttributeAccessIssue=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportExplicitAny=false, reportUnannotatedClassAttribute=false, reportUnusedVariable=false, reportAny=false, reportUnusedCallResult=false


class RuleBasedMigrator:
    """Legacy rule migrator with safe default and explicit NPU opt-in."""

    _NPU_STRATEGIES = {"npu", "npu_ascend", "cuda_to_npu"}

    def __init__(self, target_platform: str | None = None, strategy: str | None = None):
        strategy_key = (strategy or target_platform or "report_only").strip().lower()
        self.strategy = strategy_key or "report_only"
        self.rewrite_enabled = self.strategy in self._NPU_STRATEGIES
        self._rules = self._build_rules()
        self._scan_patterns = self._build_scan_patterns()

    def _build_rules(self) -> list[tuple[str, str]]:
        """Return list of (pattern, replacement) regex rules."""
        return [
            # Rule 1: torch.cuda.amp -> torch.npu.amp (before generic torch.cuda rule)
            (r"torch\.cuda\.amp", "torch.npu.amp"),
            # Rule 2: torch.cuda -> torch.npu
            (r"torch\.cuda", "torch.npu"),
            # Rule 3: .cuda() -> .npu()
            (r"\.cuda\(", ".npu("),
            # Rule 4: "cuda" / 'cuda' string literals -> "npu" / 'npu'
            (r'(?<=[\s(,=\[])\"cuda\"(?=[\s)\],;])', '"npu"'),
            (r"(?<=[\s(,=\[])'cuda'(?=[\s)\],;])", "'npu'"),
            # Rule 5: "nccl" / 'nccl' -> "hccl" / 'hccl'
            (r'(?<=[\s(,=\[])\"nccl\"(?=[\s)\],;])', '"hccl"'),
            (r"(?<=[\s(,=\[])'nccl'(?=[\s)\],;])", "'hccl'"),
        ]


    def _build_scan_patterns(self) -> list[tuple[str, str]]:
        return [
            ("torch_cuda_references", r"torch\.cuda"),
            ("cuda_method_calls", r"\.cuda\("),
            ("cuda_device_literals", r"[\"']cuda(?::\d+)?[\"']"),
            ("nccl_backend_literals", r"[\"']nccl[\"']"),
            ("nvidia_smi_references", r"nvidia-smi"),
            ("nvml_references", r"pynvml|py3nvml|nvml"),
            ("cuda_extension_references", r"CUDAExtension|cpp_extension|torch\.utils\.cpp_extension"),
        ]

    def migrate(self, source_code: str) -> tuple[str, dict[str, Any]]:
        """Apply all migration rules to source code.

        Args:
            source_code: Python source code as string.

        Returns:
            Tuple of (migrated_code, report_dict).
            Report contains per-rule replacement counts and summary.
        """
        if not self.rewrite_enabled:
            report: dict[str, Any] = {
                "rules": {},
                "total_replacements": 0,
                "mode": "report_only",
                "strategy": self.strategy,
                "destructive": False,
            }
            for name, pattern in self._scan_patterns:
                report["rules"][name] = len(re.findall(pattern, source_code))
            report["rules"].setdefault("inject_torch_npu", 0)
            return source_code, report

        report = {"rules": {}, "total_replacements": 0, "mode": "rewrite", "strategy": self.strategy, "destructive": True}

        # Check if torch.cuda exists anywhere for Rule 1 (inject torch_npu)
        has_cuda = bool(re.search(r"torch\.cuda|\.cuda\(|[\"']cuda[\"']", source_code))
        torch_npu_injected = False

        # Rule 1 injection: add import torch_npu at top if CUDA patterns found
        if has_cuda and not re.search(r"^import torch_npu\b|^from torch_npu\b", source_code, re.MULTILINE):
            # Find position after existing imports or at very beginning
            lines = source_code.split("\n")
            last_import_idx = 0
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    last_import_idx = i
            lines.insert(last_import_idx + 1, "import torch_npu")
            source_code = "\n".join(lines)
            torch_npu_injected = True
            report["rules"]["inject_torch_npu"] = 1
        else:
            report["rules"]["inject_torch_npu"] = 0

        # Apply regex transformation rules
        rule_names = [
            "torch_cuda_amp",
            "torch_cuda",
            "cuda_method",
            "cuda_string_literal_double",
            "cuda_string_literal_single",
            "nccl_string_literal_double",
            "nccl_string_literal_single",
        ]

        for name, (pattern, replacement) in zip(rule_names, self._rules):
            new_code = source_code
            source_code, count = re.subn(pattern, replacement, source_code)
            report["rules"][name] = count
            report["total_replacements"] += count

        if torch_npu_injected:
            report["total_replacements"] += 1

        return source_code, report

    def migrate_file(self, filepath: str) -> tuple[str, dict[str, Any]]:
        """Read and migrate a single file.

        Args:
            filepath: Path to source file.

        Returns:
            Tuple of (migrated_code, report_dict).

        Raises:
            FileNotFoundError: If file does not exist.
            UnicodeDecodeError: If file is not valid UTF-8 text.
        """
        with open(filepath, "r", encoding="utf-8") as f:
            source_code = f.read()
        return self.migrate(source_code)

    def migrate_directory(
        self,
        dirpath: str,
        pattern: str = "*.py",
    ) -> dict[str, Any]:
        """Migrate all matching files in a directory.

        Args:
            dirpath: Path to directory.
            pattern: Glob pattern for matching files.

        Returns:
            Aggregate report dict with per-file results and summary.
        """
        aggregate = {"files": {}, "summary": {"total_files": 0, "total_replacements": 0, "rules": {}, "mode": "rewrite" if self.rewrite_enabled else "report_only", "strategy": self.strategy, "destructive": self.rewrite_enabled}}
        files = glob_module.glob(os.path.join(dirpath, "**", pattern), recursive=True)

        for filepath in files:
            try:
                new_code, report = self.migrate_file(filepath)
                aggregate["files"][filepath] = report
                aggregate["summary"]["total_files"] += 1
                aggregate["summary"]["total_replacements"] += report["total_replacements"]

                # Merge rule counts
                for rule_name, count in report["rules"].items():
                    aggregate["summary"]["rules"][rule_name] = (
                        aggregate["summary"]["rules"].get(rule_name, 0) + count
                    )

                # Write migrated code back if changes were made
                if self.rewrite_enabled and report["total_replacements"] > 0:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_code)
            except Exception as e:
                aggregate["files"][filepath] = {"error": str(e), "total_replacements": 0, "rules": {}}

        return aggregate
