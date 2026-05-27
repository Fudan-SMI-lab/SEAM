"""Tests for rule_strategies: resolver, factory, precedence, backward compat."""
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rule_strategies import (
    resolve_rule_migration_strategy,
    create_migrator_for_strategy,
    create_migrator_resolved,
    get_legacy_backend_map,
)
from migrator.yaml_rule_based import YamlRuleBasedMigrator


class MigratorLike(Protocol):
    def migrate(self, source_code: str) -> tuple[str, dict[str, object]]:
        ...


def report_int(report: dict[str, object], key: str) -> int:
    value = report[key]
    assert isinstance(value, int)
    return value


def rule_int(report: dict[str, object], key: str) -> int:
    rules = report["rules"]
    assert isinstance(rules, dict)
    value = rules[key]
    assert isinstance(value, int)
    return value



class TestResolveRuleMigrationStrategy:
    """Precedence chain tests for strategy resolution."""

    def test_all_none_falls_back_to_report_only(self):
        sid = resolve_rule_migration_strategy()
        assert sid == "report_only"

    def test_legacy_params_backend_ppu_wins(self):
        sid = resolve_rule_migration_strategy(
            workflow_params_backend="ppu",
            workflow_rule_migration={"strategy": "cuda_to_npu"},
            platform_policy_strategy="report_only",
        )
        assert sid == "preserve_cuda_report_only"

    def test_legacy_params_backend_report_only_wins(self):
        sid = resolve_rule_migration_strategy(
            workflow_params_backend="report_only",
            workflow_rule_migration={"strategy": "cuda_to_npu"},
            platform_policy_strategy="cuda_to_npu",
        )
        assert sid == "report_only"

    def test_legacy_params_backend_scan_only_maps_to_report_only(self):
        sid = resolve_rule_migration_strategy(
            workflow_params_backend="scan_only",
        )
        assert sid == "report_only"

    def test_legacy_params_backend_conservative_maps_to_report_only(self):
        sid = resolve_rule_migration_strategy(
            workflow_params_backend="conservative",
        )
        assert sid == "report_only"

    def test_workflow_rule_migration_wins_over_platform_policy(self):
        sid = resolve_rule_migration_strategy(
            workflow_rule_migration={"strategy": "cuda_to_npu"},
            platform_policy_strategy="report_only",
        )
        assert sid == "cuda_to_npu"

    def test_workflow_rule_migration_strategy_file_wins_over_strategy(self):
        sid = resolve_rule_migration_strategy(
            workflow_rule_migration={
                "strategy_file": "rule_strategies/report_only.yaml",
                "strategy": "cuda_to_npu",
            },
            platform_policy_strategy="cuda_to_npu",
        )
        assert sid == "rule_strategies/report_only.yaml"

    def test_platform_policy_wins_over_fallback(self):
        sid = resolve_rule_migration_strategy(
            platform_policy_strategy="preserve_cuda_report_only",
        )
        assert sid == "preserve_cuda_report_only"

    def test_unknown_legacy_backend_falls_through(self):
        """An unknown legacy backend value does NOT map; resolver falls through."""
        sid = resolve_rule_migration_strategy(
            workflow_params_backend="unknown_backend",
            platform_policy_strategy="cuda_to_npu",
        )
        assert sid == "cuda_to_npu"

    def test_empty_workflow_rule_migration_falls_through(self):
        sid = resolve_rule_migration_strategy(
            workflow_rule_migration={},
            platform_policy_strategy="preserve_cuda_report_only",
        )
        assert sid == "preserve_cuda_report_only"

    def test_workflow_rule_migration_no_strategy_key_falls_through(self):
        sid = resolve_rule_migration_strategy(
            workflow_rule_migration={"other_key": "value"},
            platform_policy_strategy="cuda_to_npu",
        )
        assert sid == "cuda_to_npu"

    def test_empty_platform_policy_strategy_falls_back(self):
        sid = resolve_rule_migration_strategy(
            platform_policy_strategy="",
        )
        assert sid == "report_only"

    def test_whitespace_only_platform_policy_strategy_falls_back(self):
        sid = resolve_rule_migration_strategy(
            platform_policy_strategy="   ",
        )
        assert sid == "report_only"


class TestCreateMigratorForStrategy:
    """Factory tests for instantiating migrators from strategy YAMLs."""

    def test_report_only_creates_report_only_migrator(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("report_only"))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_cuda_to_npu_creates_rule_based_migrator(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("cuda_to_npu"))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_preserve_cuda_creates_ppu_migrator(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("preserve_cuda_report_only"))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_unknown_strategy_falls_back_to_report_only(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("nonexistent_strategy"))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_report_only_migrator_does_not_modify(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("report_only"))
        code = "import torch\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert result == code
        assert report["mode"] == "report_only"

    def test_cuda_to_npu_migrator_modifies(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("cuda_to_npu"))
        code = "import torch\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert "torch.npu.is_available()" in result
        assert "import torch_npu" in result

    def test_preserve_cuda_migrator_preserves(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("preserve_cuda_report_only"))
        code = "import torch\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert result == code
        assert "import torch_npu" not in result

    def test_cuda_to_npu_rewrites_all_legacy_cuda_forms(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("cuda_to_npu"))
        code = 'import torch\nprint(torch.cuda.is_available())\nmodel.cuda()\nbackend = ("cuda")\ndist = ("nccl")'
        result, report = migrator.migrate(code)
        assert "torch.npu.is_available()" in result
        assert "model.npu()" in result
        assert '"npu"' in result
        assert '"hccl"' in result
        assert "import torch_npu" in result
        assert report["mode"] == "rewrite"
        assert report_int(report, "total_replacements") >= 5

    def test_generic_report_only_does_not_inject_torch_npu(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("report_only"))
        code = 'import torch\nprint(torch.cuda.is_available())\nmodel.cuda()\nbackend = "cuda"'
        result, report = migrator.migrate(code)
        assert result == code
        assert "import torch_npu" not in result
        assert report["mode"] == "report_only"
        assert report["total_replacements"] == 0

    def test_preserve_cuda_report_only_reports_without_mutation(self):
        migrator = cast(MigratorLike, create_migrator_for_strategy("preserve_cuda_report_only"))
        code = 'import torch\nprint(torch.cuda.is_available())\nsubprocess.run(["nvidia-smi"])'
        result, report = migrator.migrate(code)
        assert result == code
        assert "torch.npu" not in result
        assert "import torch_npu" not in result
        assert report["mode"] == "report_only"
        assert rule_int(report, "nvidia_smi_references") == 1


class TestCreateMigratorResolved:
    """Shortcut API tests."""

    def test_no_args_returns_report_only(self):
        migrator = cast(MigratorLike, create_migrator_resolved())
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_ppu_backend_returns_ppu_migrator(self):
        migrator = cast(MigratorLike, create_migrator_resolved(workflow_params_backend="ppu"))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_report_only_backend_returns_report_only_migrator(self):
        migrator = cast(MigratorLike, create_migrator_resolved(workflow_params_backend="report_only"))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_platform_policy_cuda_to_npu_returns_rule_based(self):
        migrator = cast(MigratorLike, create_migrator_resolved(
            platform_policy_strategy="cuda_to_npu",
        ))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_workflow_strategy_wins_over_platform(self):
        migrator = cast(MigratorLike, create_migrator_resolved(
            workflow_rule_migration={"strategy": "cuda_to_npu"},
            platform_policy_strategy="report_only",
        ))
        assert isinstance(migrator, YamlRuleBasedMigrator)

    def test_workflow_strategy_file_creates_migrator(self):
        migrator = cast(MigratorLike, create_migrator_resolved(
            workflow_rule_migration={"strategy_file": "rule_strategies/report_only.yaml"},
            platform_policy_strategy="cuda_to_npu",
        ))
        code = "import torch\nprint(torch.cuda.is_available())"
        result, report = migrator.migrate(code)
        assert result == code
        assert report["mode"] == "report_only"

    def test_legacy_backend_wins_over_all(self):
        """Legacy params.backend=ppu wins over workflow and platform."""
        migrator = cast(MigratorLike, create_migrator_resolved(
            workflow_params_backend="ppu",
            workflow_rule_migration={"strategy": "cuda_to_npu"},
            platform_policy_strategy="cuda_to_npu",
        ))
        assert isinstance(migrator, YamlRuleBasedMigrator)


class TestLegacyBackendMap:
    """Verify the legacy params.backend mapping is complete and correct."""

    def test_known_backends(self):
        m = get_legacy_backend_map()
        assert m["ppu"] == "preserve_cuda_report_only"
        assert m["report_only"] == "report_only"
        assert m["scan_only"] == "report_only"
        assert m["conservative"] == "report_only"

    def test_mapping_is_a_copy(self):
        m1 = get_legacy_backend_map()
        m2 = get_legacy_backend_map()
        m1["custom"] = "other"
        assert "custom" not in m2


class TestPlatformPolicyDefaultStrategy:
    """Verify builtin presets carry the correct default strategies."""

    def test_npu_ascend_default_is_cuda_to_npu(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["npu_ascend"].default_rule_migration_strategy == "cuda_to_npu"

    def test_ppu_default_is_preserve_cuda_report_only(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["ppu_cuda_compatible"].default_rule_migration_strategy == "preserve_cuda_report_only"

    def test_generic_default_is_report_only(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["generic_accelerator"].default_rule_migration_strategy == "report_only"

    def test_cuda_nvidia_default_is_report_only(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["cuda_nvidia"].default_rule_migration_strategy == "report_only"

    def test_musa_muxi_default_is_report_only(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["musa_muxi"].default_rule_migration_strategy == "report_only"

    def test_rocm_amd_default_is_report_only(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["rocm_amd"].default_rule_migration_strategy == "report_only"

    def test_mlu_cambrian_default_is_report_only(self):
        from core.platform_policy import BUILTIN_PRESETS
        assert BUILTIN_PRESETS["mlu_cambrian"].default_rule_migration_strategy == "report_only"
