from __future__ import annotations

import os
import platform
import sys
import typing
import hashlib

from .execution_env_context import BackendFactRequest, OpenCodeFactRequest
from .resource_manifest_facts import Evidence, build_fact
from .resource_manifest_models import FactProvenance, ProvenanceFact


def capture_launcher_facts() -> typing.Tuple[ProvenanceFact, ...]:
    observed = FactProvenance.FRAMEWORK_OBSERVED
    values = (
        ("launcher.python_executable", sys.executable),
        ("launcher.python_realpath", os.path.realpath(sys.executable)),
        ("launcher.python_implementation", platform.python_implementation()),
        ("launcher.python_version", platform.python_version()),
        ("launcher.platform", platform.system()),
        ("launcher.architecture", platform.machine()),
        ("launcher.cwd", os.path.realpath(os.getcwd())),
    )
    return tuple(
        build_fact(name, Evidence(value, observed, "host")) for name, value in values
    )


def build_backend_facts(
    request: BackendFactRequest,
) -> typing.Tuple[ProvenanceFact, ...]:
    namespace = f"container:{request.container_id}" if request.container_id else "host"
    configured = FactProvenance.CONFIGURED
    derived = FactProvenance.DERIVED
    ownership_token_sha256 = (
        hashlib.sha256(request.framework_ownership_token.encode()).hexdigest()
        if request.framework_ownership_token is not None
        else None
    )
    values = (
        (
            "workflow.requested",
            Evidence(request.requested_workflow, configured, "host"),
        ),
        ("workflow.effective", Evidence(request.effective_workflow, derived, "host")),
        ("backend.requested", Evidence(request.requested_backend, configured, "host")),
        ("backend.effective", Evidence(request.effective_backend, derived, "host")),
        ("backend.local_identity", Evidence("host-process", derived, "host")),
        (
            "container.attachment_mode",
            Evidence(
                request.attachment_mode,
                configured,
                namespace,
                "not applicable to local execution",
            ),
        ),
        ("container.owner_kind", Evidence(request.owner_kind, derived, namespace)),
        (
            "container.original_owner_run_id",
            Evidence(
                request.original_owner_run_id,
                configured,
                namespace,
                "unknown original owner",
            ),
        ),
        (
            "container.lineage_root_run_id",
            Evidence(
                request.lineage_root_run_id, configured, namespace, "unknown lineage"
            ),
        ),
        (
            "container.framework_ownership_token_sha256",
            Evidence(
                ownership_token_sha256,
                configured,
                namespace,
                "no verified framework token attestation",
            ),
        ),
        (
            "container.framework_ownership_label",
            Evidence(
                request.framework_ownership_label,
                configured,
                namespace,
                "no verified framework label",
            ),
        ),
        (
            "container.runtime",
            Evidence(
                request.container_runtime,
                configured,
                namespace,
                "not applicable to local execution",
            ),
        ),
        (
            "container.name",
            Evidence(
                request.container_name,
                configured,
                namespace,
                "container name unavailable",
            ),
        ),
        (
            "container.id",
            Evidence(
                request.container_id,
                configured,
                namespace,
                "container identity unavailable",
            ),
        ),
        (
            "container.image",
            Evidence(
                request.image,
                configured,
                namespace,
                "image not configured or externally unknown",
            ),
        ),
        (
            "container.workdir",
            Evidence(
                request.container_workdir,
                configured,
                namespace,
                "not applicable to local execution",
            ),
        ),
        (
            "container.mount_source",
            Evidence(
                request.container_mount_source,
                configured,
                namespace,
                "not applicable to local execution",
            ),
        ),
        (
            "container.mount_destination",
            Evidence(
                request.container_mount_destination,
                configured,
                namespace,
                "not applicable to local execution",
            ),
        ),
        (
            "container.probe_status",
            Evidence(request.probe_status, configured, namespace),
        ),
        (
            "ownership.resource_owner_kind",
            Evidence(request.owner_kind, derived, namespace),
        ),
        (
            "retention.requested",
            Evidence(
                request.retention_requested,
                configured,
                namespace,
                "Task 17 policy is not resolved",
            ),
        ),
        (
            "retention.effective",
            Evidence(
                request.retention_effective,
                derived,
                namespace,
                "Task 17 policy is not resolved",
            ),
        ),
        ("lifecycle.status", Evidence("capturing", derived, "host")),
    )
    return tuple(build_fact(name, evidence) for name, evidence in values)


def build_opencode_facts(
    request: OpenCodeFactRequest,
) -> typing.Tuple[ProvenanceFact, ...]:
    return (
        build_fact(
            "opencode.endpoint",
            Evidence(request.endpoint, FactProvenance.CONFIGURED, "host"),
        ),
        build_fact(
            "opencode.version",
            Evidence(
                request.version,
                FactProvenance.AGENT_REPORTED,
                "host",
                "version probe unavailable",
            ),
        ),
        build_fact(
            "opencode.owner_kind",
            Evidence(request.owner_kind, FactProvenance.DERIVED, "host"),
        ),
        build_fact(
            "opencode.pid",
            Evidence(
                request.process_id,
                FactProvenance.AGENT_REPORTED,
                "host",
                "process identity unavailable",
            ),
        ),
    )
