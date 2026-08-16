"""Concrete bounded OMO capability production adapter.

``SubprocessOmoCapabilityPort`` resolves the installed OMO version via
``bunx oh-my-openagent doctor --json --platform opencode`` (reading ONLY
``systemInfo.pluginVersion``).  The version is then **semver-gated**:
only major version 5 (matching the vendored schema generation at commit
``ee81ab7``) yields a :class:`SchemaCapability`.  Fake strings like
``3.5.0`` or future ``6.0.0`` return ``None``.

The schema URL and reasoning vocabulary are extracted FROM the loaded
schema document — never from ``configPath``/``configValid`` (which describe
the OpenCode plugin config, not ``.omo/omo.jsonc``) and never from the
network.

``migrate_dry_run`` invokes the official ``config migrate --dry-run --json``
subcommand and returns a typed :class:`DryRunResult`.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import final

from seam_init.omo_config import DryRunResult, DryRunStatus, SchemaCapability
from seam_init.omo_schema import (
    SchemaAssetError,
    extract_reasoning_values,
    extract_schema_url,
    load_schema_document,
)
from seam_init.omo_version import is_supported_version

__all__ = ["OmoCommand", "SubprocessOmoCapabilityPort"]


@final
@dataclass(frozen=True, slots=True)
class OmoCommand:
    """OMO CLI invocation. ``argv`` is the runtime prefix (bunx or npx) plus
    the package name; callers may pass either ``("bunx", "oh-my-openagent")``
    or ``("npx", "oh-my-openagent")`` depending on the detected runtime."""
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = 30.0


@final
class SubprocessOmoCapabilityPort:
    """Production adapter: version-gated schema capability + migration dry-run."""

    _command: OmoCommand

    def __init__(self, command: OmoCommand) -> None:
        self._command = command

    def _run(self, suffix: tuple[str, ...]) -> subprocess.CompletedProcess[str] | None:
        try:
            result = subprocess.run(
                [*self._command.argv, *suffix], cwd=self._command.cwd,
                capture_output=True, text=True,
                timeout=self._command.timeout_seconds, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result if result.returncode == 0 else None

    def resolve_capability(self) -> SchemaCapability | None:
        version = self._extract_version()
        if version is None:
            return self._offline_capability()
        if not is_supported_version(version):
            return None
        return self._capability_from_schema(version)

    def _capability_from_schema(self, version: str) -> SchemaCapability | None:
        try:
            schema = load_schema_document()
        except SchemaAssetError:
            return None
        try:
            url = extract_schema_url(schema)
            reasoning = extract_reasoning_values(schema)
        except SchemaAssetError:
            return None
        return SchemaCapability(
            schema_url=url, reasoning_values=reasoning,
            version=version, schema_document=schema,
        )

    def _offline_capability(self) -> SchemaCapability | None:
        try:
            schema = load_schema_document()
        except SchemaAssetError:
            return None
        try:
            url = extract_schema_url(schema)
            reasoning = extract_reasoning_values(schema)
        except SchemaAssetError:
            return None
        return SchemaCapability(
            schema_url=url, reasoning_values=reasoning,
            version="5.0.0", schema_document=schema,
        )

    def _extract_version(self) -> str | None:
        result = self._run(("doctor", "--json", "--platform", "opencode"))
        if result is None:
            return None
        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        info = data.get("systemInfo")
        if not isinstance(info, dict):
            return None
        version_raw = info.get("pluginVersion")
        if not isinstance(version_raw, str) or not version_raw.strip():
            return None
        return version_raw.strip()

    def migrate_dry_run(self) -> DryRunResult:
        try:
            result = subprocess.run(
                [*self._command.argv, "config", "migrate", "--dry-run", "--json"],
                cwd=self._command.cwd, capture_output=True, text=True,
                timeout=self._command.timeout_seconds, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return DryRunResult(DryRunStatus.UNSUPPORTED, None)
        if result.returncode != 0:
            return DryRunResult(DryRunStatus.FAILURE, None)
        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return DryRunResult(DryRunStatus.FAILURE, None)
        if not isinstance(data, dict):
            return DryRunResult(DryRunStatus.FAILURE, None)
        return DryRunResult(DryRunStatus.SUCCESS, data)
