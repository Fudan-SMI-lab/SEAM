from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import typing

from .execution_env_context import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    Phase2EnvironmentRequest,
    ProbedEnvironment,
    _ENVIRONMENT_FACT_NAMES,
)
from .resource_manifest_models import (
    EnvironmentRecord,
    EnvironmentType,
    FactProvenance,
    FactStatus,
    ProbeReceipt,
    ProvenanceFact,
)
from .resource_manifest_facts import Evidence, build_fact


def build_phase2_environment(request: Phase2EnvironmentRequest) -> EnvironmentRecord:
    reported = FactProvenance.AGENT_REPORTED
    configured = FactProvenance.CONFIGURED
    derived = FactProvenance.DERIVED
    inventory = "\n".join(sorted(request.report.installed_packages)).encode()
    environment_type = (
        EnvironmentType.BASE
        if request.report.env_type == "base_env"
        else EnvironmentType.PROJECT_VENV
    )
    facts = (
        build_fact(
            "environment.type",
            Evidence(environment_type.value, reported, request.namespace),
        ),
        build_fact(
            "environment.namespace",
            Evidence(request.namespace, configured, request.namespace),
        ),
        build_fact(
            "environment.container_id",
            Evidence(
                request.container_id,
                configured,
                request.namespace,
                "host environment has no container",
            ),
        ),
        build_fact(
            "interpreter.realpath",
            Evidence(None, reported, request.namespace, "agent-reported; symlink resolution requires framework probe"),
        ),
        build_fact(
            "interpreter.sys_executable",
            Evidence(request.report.python_path, reported, request.namespace),
        ),
        build_fact(
            "interpreter.sys_prefix",
            Evidence(request.report.venv_path, reported, request.namespace),
        ),
        build_fact(
            "interpreter.sys_base_prefix",
            Evidence(None, reported, request.namespace, "awaiting framework probe"),
        ),
        build_fact(
            "python.implementation",
            Evidence(None, reported, request.namespace, "awaiting framework probe"),
        ),
        build_fact(
            "python.version",
            Evidence(None, reported, request.namespace, "awaiting framework probe"),
        ),
        build_fact(
            "platform.system",
            Evidence(None, reported, request.namespace, "awaiting framework probe"),
        ),
        build_fact(
            "platform.architecture",
            Evidence(None, reported, request.namespace, "awaiting framework probe"),
        ),
        build_fact(
            "packages.inventory_sha256",
            Evidence(hashlib.sha256(inventory).hexdigest(), derived, request.namespace),
        ),
    )
    if environment_type is EnvironmentType.BASE:
        facts = facts + (
            build_fact(
                "phase2.base_alias",
                Evidence("true", derived, request.namespace),
            ),
        )
    return EnvironmentRecord(environment_id=request.environment_id, facts=facts)


def _successful_probe(request: EnvironmentProbeRequest) -> ProbedEnvironment:
    probe = request.probe
    observed = FactProvenance.FRAMEWORK_OBSERVED
    derived = FactProvenance.DERIVED
    environment_type = (
        EnvironmentType.BASE
        if probe.sys_prefix == probe.sys_base_prefix
        else EnvironmentType.PROJECT_VENV
    )
    container_id = (
        request.namespace.partition(":")[2]
        if request.namespace.startswith("container:")
        else None
    )
    observed_values = (
        ("interpreter.realpath", probe.interpreter_realpath),
        ("interpreter.sys_executable", probe.sys_executable),
        ("interpreter.sys_prefix", probe.sys_prefix),
        ("interpreter.sys_base_prefix", probe.sys_base_prefix),
        ("python.implementation", probe.python_implementation),
        ("python.version", probe.python_version),
        ("platform.system", probe.platform),
        ("platform.architecture", probe.architecture),
        ("packages.inventory_sha256", probe.package_inventory_hash),
    )
    verified = tuple(
        build_fact(name, Evidence(value, observed, request.namespace))
        for name, value in observed_values
    )
    context = (
        build_fact(
            "environment.type",
            Evidence(environment_type.value, derived, request.namespace),
        ),
        build_fact(
            "environment.namespace",
            Evidence(request.namespace, FactProvenance.CONFIGURED, request.namespace),
        ),
        build_fact(
            "environment.container_id",
            Evidence(
                container_id,
                derived,
                request.namespace,
                "host environment has no container",
            ),
        ),
    )
    return ProbedEnvironment(
        environment=EnvironmentRecord(
            environment_id=request.environment_id,
            facts=context + verified,
        ),
        receipt=ProbeReceipt(
            probe_id=request.probe_id,
            environment_id=request.environment_id,
            namespace=request.namespace,
            status=FactStatus.KNOWN,
            verified_facts=verified,
        ),
    )


def _failed_probe(request: EnvironmentProbeRequest) -> ProbedEnvironment:
    detail = request.probe.error or "probe unavailable"
    observed = FactProvenance.FRAMEWORK_OBSERVED
    container_id = (
        request.namespace.partition(":")[2]
        if request.namespace.startswith("container:")
        else None
    )
    errors = tuple(
        ProvenanceFact(
            name=name,
            value=None,
            provenance=observed,
            namespace=request.namespace,
            status=FactStatus.ERROR,
            detail=detail,
        )
        for name in _ENVIRONMENT_FACT_NAMES
    )
    context = (
        build_fact(
            "environment.type",
            Evidence(None, FactProvenance.DERIVED, request.namespace, detail),
        ),
        build_fact(
            "environment.namespace",
            Evidence(request.namespace, FactProvenance.CONFIGURED, request.namespace),
        ),
        build_fact(
            "environment.container_id",
            Evidence(
                container_id,
                FactProvenance.DERIVED,
                request.namespace,
                "host environment has no container",
            ),
        ),
    )
    return ProbedEnvironment(
        environment=EnvironmentRecord(
            environment_id=request.environment_id,
            facts=context + errors,
        ),
        receipt=ProbeReceipt(
            probe_id=request.probe_id,
            environment_id=request.environment_id,
            namespace=request.namespace,
            status=FactStatus.ERROR,
            verified_facts=errors,
            detail=detail,
        ),
    )


def probe_environment_record(request: EnvironmentProbeRequest) -> ProbedEnvironment:
    builders: typing.Dict[
        str, typing.Callable[[EnvironmentProbeRequest], ProbedEnvironment]
    ] = {"ok": _successful_probe, "error": _failed_probe}
    return builders[request.probe.status](request)


# ── Replayable dependency plan (bug #14 Gap B) ─────────────────────────────
# Phase-2 ``installed_packages`` was consumed but never persisted, so a
# recreated execution environment lost every recorded dependency.

DEPENDENCY_PLAN_SCHEMA_VERSION = 1
DEPENDENCY_PLAN_KIND = "dependency_plan"


def persist_dependency_plan(
    installed_packages: typing.Iterable[str],
    destination: typing.Union[str, os.PathLike, None] = None,
) -> typing.Dict[str, object]:
    """Persist a phase-2 ``installed_packages`` snapshot as a replayable manifest.

    Returns a JSON-serializable manifest dict. When *destination* is given the
    manifest is also written to that file so a recreated execution environment
    can replay the recorded dependencies.
    """
    packages = [str(package) for package in (installed_packages or ())]
    manifest: typing.Dict[str, object] = {
        "schema_version": DEPENDENCY_PLAN_SCHEMA_VERSION,
        "kind": DEPENDENCY_PLAN_KIND,
        "replayable": True,
        "installed_packages": packages,
        "inventory_sha256": hashlib.sha256(
            "\n".join(sorted(packages)).encode()
        ).hexdigest(),
    }
    if destination is not None:
        path = os.fspath(destination)
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    return manifest


def replay_dependency_plan(
    manifest: typing.Union[typing.Dict[str, object], str, os.PathLike],
) -> typing.List[str]:
    """Replay a persisted dependency plan into the recorded package list.

    Accepts the manifest dict returned by :func:`persist_dependency_plan` or
    the path of a manifest file written to disk. Returns the package specs in
    their recorded order.
    """
    if isinstance(manifest, typing.Mapping):
        data: object = dict(manifest)
    else:
        with open(os.fspath(manifest), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    if not isinstance(data, typing.Mapping):
        raise ValueError(
            "dependency plan must be a JSON object with an 'installed_packages' list"
        )
    packages = data.get("installed_packages")
    if not isinstance(packages, (list, tuple)):
        raise ValueError("dependency plan is missing 'installed_packages' list")
    return [str(package) for package in packages]


def capture_local_environment(environment_id: str) -> ProbedEnvironment:
    packages = tuple(
        sorted(
            f"{name}=={distribution.version}"
            for distribution in importlib.metadata.distributions()
            for name in (distribution.metadata.get("Name"),)
            if name
        )
    )
    probe_id = f"probe-{environment_id}"
    if len(probe_id) > 128:
        probe_id = f"probe-{hashlib.sha256(environment_id.encode()).hexdigest()}"
    return probe_environment_record(
        EnvironmentProbeRequest(
            probe_id=probe_id,
            environment_id=environment_id,
            namespace="host",
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath=os.path.realpath(sys.executable),
                sys_executable=sys.executable,
                sys_prefix=sys.prefix,
                sys_base_prefix=sys.base_prefix,
                python_implementation=platform.python_implementation(),
                python_version=platform.python_version(),
                platform=platform.system(),
                architecture=platform.machine(),
                package_inventory_hash=hashlib.sha256(
                    "\n".join(packages).encode()
                ).hexdigest(),
            ),
        )
    )
