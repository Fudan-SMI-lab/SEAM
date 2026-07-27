from __future__ import annotations

import os
import platform
import sys
import typing
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from pydantic import ValidationError

from .execution_env_context import BackendFactRequest, OpenCodeFactRequest
from .resource_manifest_models import (
    FactProvenance,
    ProvenanceFact,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
)
from .resource_manifest_facts import Evidence, build_fact
from .resource_manifest_validation import validate_manifest_structure

atomic_replace = os.replace


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
            "container.framework_ownership_token",
            Evidence(
                request.framework_ownership_token,
                configured,
                namespace,
                "no verified framework token",
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
            "container.probe_status",
            Evidence(request.probe_status, configured, namespace),
        ),
        (
            "ownership.resource_owner_kind",
            Evidence(request.owner_kind, derived, namespace),
        ),
        (
            "retention.requested",
            Evidence(None, derived, namespace, "Task 17 policy is not resolved"),
        ),
        (
            "retention.effective",
            Evidence(None, derived, namespace, "Task 17 policy is not resolved"),
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


class ResourceManifestPayload(NamedTuple):
    manifest: ResourceManifest
    content: bytes


def validated_payload(manifest: ResourceManifest) -> ResourceManifestPayload:
    content = (manifest.model_dump_json(indent=2, by_alias=True) + "\n").encode()
    try:
        parsed = ResourceManifest.model_validate_json(content)
    except ValidationError as exc:
        raise ResourceManifestError(
            ResourceManifestErrorKind.MALFORMED, str(exc)
        ) from exc
    return ResourceManifestPayload(parsed, content)


def read_resource_manifest(path: Path) -> ResourceManifest:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise ResourceManifestError(
            ResourceManifestErrorKind.MISSING_MANIFEST,
            f"missing resource manifest: {path.name}",
        ) from exc
    try:
        manifest = ResourceManifest.model_validate_json(content)
        validate_manifest_structure(manifest)
        return manifest
    except ValidationError as exc:
        detail = str(exc)
        if "schema_version" in detail:
            kind = ResourceManifestErrorKind.VERSION_MISMATCH
        elif "schema" in detail:
            kind = ResourceManifestErrorKind.SCHEMA_MISMATCH
        else:
            kind = ResourceManifestErrorKind.MALFORMED
        raise ResourceManifestError(kind, detail) from exc


def atomic_write(path: Path, payload: ResourceManifestPayload) -> None:
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("xb") as handle:
            _ = handle.write(payload.content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temp_path, path)
    except OSError as exc:
        cleanup_detail = ""
        try:
            temp_path.unlink()
        except FileNotFoundError:
            cleanup_detail = "; staging file already absent"
        except OSError as cleanup_exc:
            cleanup_detail = f"; staging cleanup failed: {cleanup_exc}"
        raise ResourceManifestError(
            ResourceManifestErrorKind.WRITE_INTERRUPTED,
            f"resource manifest write interrupted: {exc}{cleanup_detail}",
        ) from exc
