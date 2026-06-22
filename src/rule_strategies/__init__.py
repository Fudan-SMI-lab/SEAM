"""Configuration-driven Phase 4 rule migration strategy selection.

Provides a resolver/factory with this precedence:

1. Workflow YAML explicit ``params.backend`` value         (legacy, highest)
2. Workflow YAML ``rule_migration.strategy`` key           (new YAML-driven)
3. ``PlatformPolicy.default_rule_migration_strategy``      (per-platform default)
4. ``report_only`` safe fallback                            (absolute default)

Strategy definitions live in ``src/rule_strategies/*.yaml``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package root for strategy YAML discovery
# ---------------------------------------------------------------------------

_STRATEGIES_DIR = Path(__file__).resolve().parent
_DEFAULT_STRATEGY_ID = "report_only"

# Legacy params.backend value → strategy id mapping
_LEGACY_BACKEND_MAP: dict[str, str] = {
    "ppu": "preserve_cuda_report_only",
    "report_only": "report_only",
    "scan_only": "report_only",
    "conservative": "report_only",
}


def _strategy_yaml_path(strategy_ref: str) -> Path:
    ref_path = Path(strategy_ref)
    if ref_path.suffix in {".yaml", ".yml"} or ref_path.parent != Path("."):
        if ref_path.is_absolute():
            return ref_path
        migration_utils_dir = _STRATEGIES_DIR.parent
        candidate = migration_utils_dir / ref_path
        if candidate.is_file():
            return candidate
        return _STRATEGIES_DIR / ref_path
    return _STRATEGIES_DIR / f"{strategy_ref}.yaml"


def _load_strategy_definition(strategy_id: str) -> dict[str, Any] | None:
    """Load a strategy YAML definition from file.

    Returns the parsed dict or ``None`` if the file is missing.
    """
    yaml_path = _strategy_yaml_path(strategy_id)
    if not yaml_path.is_file():
        _logger.warning("Rule migration strategy YAML not found: %s", yaml_path)
        return None
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            _logger.warning("Invalid strategy YAML (not a mapping): %s", yaml_path)
            return None
        return dict(data)
    except Exception as exc:
        _logger.warning("Failed to load strategy YAML %s: %s", yaml_path, exc)
        return None


def _instantiate_migrator(strategy_def: dict[str, Any]) -> object:
    """Import and instantiate the migrator class referenced by a strategy definition."""
    module_name = strategy_def.get("migrator_module", "migrator.yaml_rule_based")
    class_name = strategy_def.get("migrator_class", "YamlRuleBasedMigrator")
    import importlib

    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        try:
            return cls(strategy_def)
        except TypeError:
            return cls()
    except Exception as exc:
        _logger.error(
            "Failed to instantiate migrator %s.%s: %s "
            "Falling back to ReportOnlyRuleBasedMigrator.",
            module_name,
            class_name,
            exc,
        )
        from migrator.rule_based_report_only import ReportOnlyRuleBasedMigrator  # noqa: PLC0415

        return ReportOnlyRuleBasedMigrator()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_rule_migration_strategy(
    *,
    workflow_params_backend: str | None = None,
    workflow_rule_migration: dict[str, Any] | None = None,
    platform_policy_strategy: str | None = None,
) -> str:
    """Resolve the rule migration strategy id using the precedence chain.

    Args:
        workflow_params_backend: Legacy ``params.backend`` from a workflow
            YAML phase.  Maps to a strategy id via ``_LEGACY_BACKEND_MAP``.
        workflow_rule_migration: New ``rule_migration`` block from workflow
            YAML.  The ``strategy`` key is the strategy id.
        platform_policy_strategy: ``PlatformPolicy.default_rule_migration_strategy``
            for the active platform policy.

    Returns:
        Strategy id string (e.g. ``"cuda_to_npu"``, ``"report_only"``).
    """
    # 1. Legacy params.backend (highest precedence for backward compat)
    if workflow_params_backend:
        backend_lower = workflow_params_backend.strip().lower()
        mapped = _LEGACY_BACKEND_MAP.get(backend_lower)
        if mapped is not None:
            _logger.debug(
                "Strategy resolved from legacy params.backend=%r → %s",
                workflow_params_backend,
                mapped,
            )
            return mapped
        _logger.warning(
            "Unknown legacy params.backend=%r; falling through to next precedence level",
            workflow_params_backend,
        )

    if isinstance(workflow_rule_migration, dict):
        explicit_file = workflow_rule_migration.get("strategy_file")
        if isinstance(explicit_file, str) and explicit_file.strip():
            strategy_file = explicit_file.strip()
            _logger.debug(
                "Strategy resolved from workflow rule_migration.strategy_file → %s",
                strategy_file,
            )
            return strategy_file
        explicit = workflow_rule_migration.get("strategy")
        if isinstance(explicit, str) and explicit.strip():
            sid = explicit.strip()
            _logger.debug("Strategy resolved from workflow rule_migration.strategy → %s", sid)
            return sid

    # 3. PlatformPolicy default_rule_migration_strategy
    if platform_policy_strategy and platform_policy_strategy.strip():
        _logger.debug(
            "Strategy resolved from PlatformPolicy.default_rule_migration_strategy → %s",
            platform_policy_strategy,
        )
        return platform_policy_strategy.strip()

    # 4. Safe fallback
    _logger.debug("No explicit strategy found; using safe default → %s", _DEFAULT_STRATEGY_ID)
    return _DEFAULT_STRATEGY_ID


def create_migrator_for_strategy(
    strategy_id: str,
) -> object:
    """Factory: load the strategy YAML and instantiate its migrator.

    Falls back to ``ReportOnlyRuleBasedMigrator`` when the YAML is missing,
    the class cannot be imported, or any other error occurs.

    Args:
        strategy_id: Strategy id from :func:`resolve_rule_migration_strategy`.

    Returns:
        An instantiated migrator object with ``migrate`` / ``migrate_file`` /
        ``migrate_directory`` methods.
    """
    strategy_def = _load_strategy_definition(strategy_id)
    if strategy_def is None:
        _logger.warning(
            "Strategy definition not found for %s; falling back to %s",
            strategy_id,
            _DEFAULT_STRATEGY_ID,
        )
        strategy_def = _load_strategy_definition(_DEFAULT_STRATEGY_ID) or {
            "migrator_class": "YamlRuleBasedMigrator",
            "migrator_module": "migrator.yaml_rule_based",
            "mode": "report_only",
            "rewrite": {"enabled": False},
        }
    return _instantiate_migrator(strategy_def)


def create_migrator_resolved(
    *,
    workflow_params_backend: str | None = None,
    workflow_rule_migration: dict[str, Any] | None = None,
    platform_policy_strategy: str | None = None,
) -> object:
    """Shortcut: resolve strategy id and instantiate the migrator in one call.

    Combines :func:`resolve_rule_migration_strategy` and
    :func:`create_migrator_for_strategy`.
    """
    strategy_id = resolve_rule_migration_strategy(
        workflow_params_backend=workflow_params_backend,
        workflow_rule_migration=workflow_rule_migration,
        platform_policy_strategy=platform_policy_strategy,
    )
    return create_migrator_for_strategy(strategy_id)


def get_legacy_backend_map() -> dict[str, str]:
    """Return a copy of the legacy params.backend → strategy mapping.

    Useful for documentation and tests.
    """
    return dict(_LEGACY_BACKEND_MAP)


__all__ = [
    "resolve_rule_migration_strategy",
    "create_migrator_for_strategy",
    "create_migrator_resolved",
    "get_legacy_backend_map",
]
