from pathlib import Path
from typing import cast

from core.config import load_workflow
from core.platform_policy import TargetPlatformConfig, resolve_policy
from migrator.yaml_rule_based import YamlRuleBasedMigrator
from rule_strategies import create_migrator_resolved, resolve_rule_migration_strategy


ROOT = Path(__file__).resolve().parent.parent


def test_npu_workflow_smoke_loads_policy_and_yaml_strategy(tmp_path: Path):
    workflow = load_workflow(str(ROOT / "workflows" / "npu_migration_v2.yaml"))
    policy = resolve_policy(cast(TargetPlatformConfig, workflow.target_platform), workflow.name)
    strategy = resolve_rule_migration_strategy(
        workflow_rule_migration=workflow.rule_migration,
        platform_policy_strategy=policy.default_rule_migration_strategy,
    )
    migrator = create_migrator_resolved(
        workflow_rule_migration=workflow.rule_migration,
        platform_policy_strategy=policy.default_rule_migration_strategy,
    )

    assert policy.id == "npu_ascend"
    assert strategy == "rule_strategies/cuda_to_npu.yaml"
    assert isinstance(migrator, YamlRuleBasedMigrator)

    project_file = tmp_path / "train.py"
    source = "\n".join([
        "import torch",
        "print(torch.cuda.is_available())",
        "model.cuda()",
        'backend = "nccl"',
        "",
    ])
    _ = project_file.write_text(source, encoding="utf-8")

    report = migrator.migrate_directory(str(tmp_path))
    migrated = project_file.read_text(encoding="utf-8")

    assert report["summary"]["strategy"] == "cuda_to_npu"
    assert "import torch_npu" in migrated
    assert "torch.npu.is_available()" in migrated
    assert "model.npu()" in migrated
    assert 'backend = "hccl"' in migrated
