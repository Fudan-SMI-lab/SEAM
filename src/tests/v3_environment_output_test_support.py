from __future__ import annotations

from pathlib import Path
from typing import Literal

from core.execution_env_context import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
)
from core.phase5_attempt_receipt import (
    BackendKind,
    Phase5AttemptReceipt,
    phase5_attempt_authority,
)
from core.resource_manifest import (
    BackendFactRequest,
    OpenCodeFactRequest,
    ResourceManifestContext,
    ResourceManifestIdentity,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_initial_manifest,
    build_opencode_facts,
    build_phase2_environment,
    build_phase5_reference,
    Phase5ReferenceRequest,
)
from core.v3_runtime_report import AcceptedReplaySource
from tests.phase5_receipt_test_support import accepted_receipt
from tests.v3_environment_output_lifecycle_support import (
    seal_lifecycle as seal_lifecycle,
)

RUN_ID = "run-safe-1"


def runtime_store(
    tmp_path: Path,
    *,
    requested_backend: Literal["auto", "local", "container"] = "local",
    effective_backend: Literal["local", "container"] = "local",
) -> ResourceManifestStore:
    report_dir = tmp_path / RUN_ID
    report_dir.mkdir(parents=True)
    identity = ResourceManifestIdentity(
        run_id=RUN_ID,
        workflow_digest="a" * 64,
        workspace_digest="b" * 64,
    )
    context = ResourceManifestContext.bind(report_dir, identity)
    launcher = context.capture_launcher()
    container = effective_backend == "container"
    backend = BackendFactRequest(
        requested_workflow="requested.yaml",
        effective_workflow="effective.yaml",
        requested_backend=requested_backend,
        effective_backend=effective_backend,
        attachment_mode="image_created" if container else None,
        owner_kind="framework" if container else "unknown",
        original_owner_run_id=RUN_ID if container else None,
        lineage_root_run_id=RUN_ID if container else None,
        framework_ownership_token="owner-token" if container else None,
        framework_ownership_label=(
            f"seam.owner-run-id={RUN_ID}" if container else None
        ),
        container_runtime="docker" if container else None,
        container_name="seam-migration-42" if container else None,
        container_id="cid-123" if container else None,
        image="python:3.11" if container else None,
        container_workdir="/workspace/project" if container else None,
        container_mount_source=str(tmp_path.resolve()) if container else None,
        container_mount_destination="/workspace/project" if container else None,
        probe_status="ok" if container else "not_requested",
        retention_requested="retain",
        retention_effective="retain",
    )
    captured_backend = context._capture_backend_observation(backend)
    manifest = build_initial_manifest(
        identity,
        launcher.facts
        + captured_backend.facts
        + build_opencode_facts(
            OpenCodeFactRequest(
                endpoint="http://127.0.0.1:4096",
                version="1.18.5",
                owner_kind="framework",
                process_id="4242",
            )
        ),
        launcher.receipts + captured_backend.receipts,
    )
    return ResourceManifestStore.create(context, manifest)


def add_base_environment(store: ResourceManifestStore) -> None:
    captured = store.context.capture_local_environment("execution-python")
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(captured.environment,),
            probe_receipts=(captured.receipt,),
        )
    )


def add_project_venv(store: ResourceManifestStore, root: Path) -> None:
    environment = build_phase2_environment(
        Phase2EnvironmentRequest(
            environment_id="phase2-project-venv",
            namespace="host",
            report=Phase2EnvironmentReport(
                venv_path=str((root / ".venv").resolve()),
                python_path=str((root / ".venv" / "bin" / "python").resolve()),
                installed_packages=("pytest==8.4.1",),
            ),
        )
    )
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(environment,),
        )
    )


def add_container_environment(store: ResourceManifestStore) -> None:
    captured = store.context._capture_environment_probe(
        EnvironmentProbeRequest(
            probe_id="probe-container-python",
            environment_id="execution-python",
            namespace="container:cid-123",
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath="/usr/local/bin/python3.11",
                sys_executable="/usr/local/bin/python",
                sys_prefix="/usr/local",
                sys_base_prefix="/usr/local",
                python_implementation="CPython",
                python_version="3.11.9",
                platform="Linux",
                architecture="x86_64",
                package_inventory_hash="c" * 64,
            ),
        )
    )
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(captured.environment,),
            probe_receipts=(captured.receipt,),
        )
    )


def replay_source(
    tmp_path: Path,
    *,
    backend_kind: BackendKind = BackendKind.LOCAL,
    accepted: bool = True,
) -> tuple[Phase5AttemptReceipt, AcceptedReplaySource]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    receipt = accepted_receipt(tmp_path, backend_kind=backend_kind).model_copy(
        update={"run_id": RUN_ID, "accepted": accepted}
    )
    receipt_path = tmp_path / "accepted-phase5.receipt.json"
    _ = receipt_path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    authority = phase5_attempt_authority(receipt_path, receipt)
    return receipt, AcceptedReplaySource(receipt_path=receipt_path, authority=authority)


def add_phase5_environment_reference(
    store: ResourceManifestStore,
    receipt: Phase5AttemptReceipt,
    environment_id: str,
) -> None:
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            phase5_environment_references=(
                build_phase5_reference(
                    Phase5ReferenceRequest(
                        attempt_id=receipt.attempt_id,
                        environment_id=environment_id,
                        namespace=receipt.backend.namespace,
                    )
                ),
            ),
        )
    )
