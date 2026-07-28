from __future__ import annotations

import shlex
from pathlib import PurePosixPath, PureWindowsPath

from core.resource_manifest import (
    EnvironmentRecord,
    FactProvenance,
    ResourceManifest,
)
from core.v3_runtime_report_facts import authenticated_fact, known_fact
from core.v3_runtime_report_models import RuntimeAccessKind, RuntimeAccessReport


def _activation_command(
    environment: EnvironmentRecord,
) -> tuple[str | None, FactProvenance | None]:
    environment_type = known_fact(environment.facts, "environment.type")
    prefix = known_fact(environment.facts, "interpreter.sys_prefix")
    platform = known_fact(environment.facts, "platform.system")
    if environment_type is None or environment_type.value != "project_venv":
        provenance = (
            environment_type.provenance if environment_type is not None else None
        )
        return None, provenance
    if prefix is None or prefix.value is None:
        return None, environment_type.provenance
    if environment.facts[0].namespace.startswith("container:"):
        activation = PurePosixPath(prefix.value, "bin", "activate")
        return f"source {shlex.quote(str(activation))}", prefix.provenance
    if platform is not None and platform.value == "Windows":
        activation = str(PureWindowsPath(prefix.value, "Scripts", "Activate.ps1"))
        escaped = activation.replace("'", "''")
        return f"& '{escaped}'", prefix.provenance
    activation = PurePosixPath(prefix.value.replace("\\", "/"), "bin", "activate")
    return f"source {shlex.quote(str(activation))}", prefix.provenance


def build_access_report(
    manifest: ResourceManifest,
    active_environment_id: str | None,
) -> RuntimeAccessReport:
    backend = known_fact(manifest.facts, "backend.effective")
    environment = next(
        (
            item
            for item in manifest.environments
            if item.environment_id == active_environment_id
        ),
        None,
    )
    activation, activation_provenance = (
        _activation_command(environment) if environment is not None else (None, None)
    )
    if backend is not None and backend.value == "container":
        runtime = authenticated_fact(
            manifest.facts,
            "container.runtime",
            FactProvenance.FRAMEWORK_OBSERVED,
        )
        container_id = authenticated_fact(
            manifest.facts,
            "container.id",
            FactProvenance.FRAMEWORK_OBSERVED,
        )
        post_state = authenticated_fact(
            manifest.facts,
            "retention.post_state",
            FactProvenance.DERIVED,
        )
        available = (
            runtime is not None
            and runtime.value is not None
            and container_id is not None
            and container_id.value is not None
            and post_state is not None
            and post_state.value == "running"
        )
        entry_command = (
            shlex.join((runtime.value, "exec", "-it", container_id.value, "bash"))
            if available
            and runtime is not None
            and runtime.value is not None
            and container_id is not None
            and container_id.value is not None
            else None
        )
        return RuntimeAccessReport(
            available=available,
            kind=RuntimeAccessKind.RETAINED_CONTAINER,
            entry_command=entry_command,
            activation_command=activation if available else None,
            detail=(
                "retained container is running; entry uses its immutable ID"
                if available
                else "retained container access unavailable after cleanup"
            ),
            provenance=runtime.provenance if runtime is not None else None,
        )
    if environment is None:
        return RuntimeAccessReport(
            available=False,
            kind=RuntimeAccessKind.UNAVAILABLE,
            detail="execution environment probe unavailable",
        )
    environment_type = known_fact(environment.facts, "environment.type")
    if environment_type is not None and environment_type.value == "project_venv":
        return RuntimeAccessReport(
            available=activation is not None,
            kind=RuntimeAccessKind.PROJECT_VENV,
            activation_command=activation,
            detail=(
                "activate the retained project virtual environment"
                if activation is not None
                else "project virtual environment activation unavailable"
            ),
            provenance=activation_provenance,
        )
    return RuntimeAccessReport(
        available=True,
        kind=RuntimeAccessKind.BASE_ENVIRONMENT,
        detail="base environment; no activation required",
        provenance=(
            environment_type.provenance if environment_type is not None else None
        ),
    )
