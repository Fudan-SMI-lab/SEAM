from __future__ import annotations

import hashlib
import importlib.metadata
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


def _extract_python_version(python_path: str) -> str | None:
    import re
    m = re.search(r"python3\.(\d+)(?:\.(\d+))?", python_path)
    if m:
        return f"3.{m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
    return None


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
            Evidence(request.report.venv_path, reported, request.namespace),
        ),
        build_fact(
            "python.implementation",
            Evidence("CPython", reported, request.namespace),
        ),
        build_fact(
            "python.version",
            Evidence(_extract_python_version(request.report.python_path), reported, request.namespace, "derived from python_path"),
        ),
        build_fact(
            "platform.system",
            Evidence("Linux", reported, request.namespace),
        ),
        build_fact(
            "platform.architecture",
            Evidence("x86_64", reported, request.namespace),
        ),
        build_fact(
            "packages.inventory_sha256",
            Evidence(None, reported, request.namespace, "agent-reported package subset; full inventory hash requires framework probe"),
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
