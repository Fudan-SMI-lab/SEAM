from __future__ import annotations

from pathlib import Path
import typing

from core.resource_manifest import (
    BackendFactRequest,
    OpenCodeFactRequest,
    ResourceManifest,
    ResourceManifestContext,
    ResourceManifestIdentity,
    ResourceManifestStore,
    build_backend_facts,
    build_initial_manifest,
    build_opencode_facts,
)

RUN_ID = "run-safe-16"
WORKFLOW_DIGEST = "a" * 64
WORKSPACE_DIGEST = "b" * 64


def manifest_identity() -> ResourceManifestIdentity:
    return ResourceManifestIdentity(
        run_id=RUN_ID,
        workflow_digest=WORKFLOW_DIGEST,
        workspace_digest=WORKSPACE_DIGEST,
    )


def initial_manifest(
    tmp_path: Path,
    context: typing.Optional[ResourceManifestContext] = None,
) -> ResourceManifest:
    identity = manifest_identity()
    selected = context
    if selected is None:
        report_dir = tmp_path / "e2e-reports" / identity.run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        selected = ResourceManifestContext.bind(report_dir, identity)
    launcher = selected.capture_launcher()
    facts = (
        launcher.facts
        + build_backend_facts(
            BackendFactRequest(
                requested_workflow="workflow-requested.yaml",
                effective_workflow="workflow-effective.yaml",
                requested_backend="auto",
                effective_backend="local",
            )
        )
        + build_opencode_facts(
            OpenCodeFactRequest(
                endpoint="http://127.0.0.1:4096",
                version="1.18.5",
                owner_kind="framework",
                process_id="1234",
            )
        )
    )
    return build_initial_manifest(identity, facts, (launcher.receipt,))


def manifest_store(tmp_path: Path) -> ResourceManifestStore:
    report_dir = tmp_path / "e2e-reports" / RUN_ID
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, manifest_identity())
    return ResourceManifestStore.create(context, initial_manifest(tmp_path, context))


def container_manifest_store(
    tmp_path: Path,
    container_id: str,
) -> ResourceManifestStore:
    identity = manifest_identity()
    report_dir = tmp_path / "e2e-reports" / RUN_ID
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    launcher = context.capture_launcher()
    facts = (
        launcher.facts
        + build_backend_facts(
            BackendFactRequest(
                requested_workflow="workflow-requested.yaml",
                effective_workflow="workflow-effective.yaml",
                requested_backend="container",
                effective_backend="container",
                attachment_mode="image_created",
                original_owner_run_id=identity.run_id,
                lineage_root_run_id=identity.run_id,
                framework_ownership_token=f"token-{container_id}",
                framework_ownership_label=f"seam.owner={identity.run_id}",
                container_runtime="docker",
                container_id=container_id,
                image="cpu:test",
                probe_status="ok",
            )
        )
        + build_opencode_facts(
            OpenCodeFactRequest(
                endpoint="http://127.0.0.1:4096",
                owner_kind="framework",
            )
        )
    )
    return ResourceManifestStore.create(
        context,
        build_initial_manifest(identity, facts, (launcher.receipt,)),
    )
