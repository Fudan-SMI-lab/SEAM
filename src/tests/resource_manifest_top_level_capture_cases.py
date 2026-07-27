from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from core.resource_manifest import (
    BackendFactRequest,
    CapturedFacts,
    OpenCodeFactRequest,
    ProbeReceipt,
    ResourceManifest,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
    ResourceManifestStore,
    build_initial_manifest,
)
from core.resource_manifest_captures import capture_backend, capture_opencode


class TrustedCaptures(NamedTuple):
    identity: ResourceManifestIdentity
    context: ResourceManifestContext
    launcher: CapturedFacts
    backend: CapturedFacts
    opencode: CapturedFacts


def _trusted_captures(tmp_path: Path) -> TrustedCaptures:
    identity = ResourceManifestIdentity(
        run_id="trusted-top-level",
        workflow_digest="1" * 64,
        workspace_digest="2" * 64,
    )
    report_dir = tmp_path / "reports" / identity.run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    backend = capture_backend(
        context._authority,
        BackendFactRequest(
            requested_workflow="wf.yaml",
            effective_workflow="wf.yaml",
            requested_backend="container",
            effective_backend="container",
            attachment_mode="image_created",
            original_owner_run_id=identity.run_id,
            lineage_root_run_id=identity.run_id,
            framework_ownership_token="token-cid-a",
            framework_ownership_label="seam.owner=trusted-top-level",
            container_runtime="docker",
            container_id="cid-a",
            image="cpu:test",
            owner_kind="framework",
            probe_status="ok",
        ),
    )
    opencode = capture_opencode(
        context._authority,
        OpenCodeFactRequest(
            endpoint="http://127.0.0.1:4096",
            version="1.18.5",
            owner_kind="framework",
            process_id="4321",
        ),
    )
    return TrustedCaptures(
        identity,
        context,
        context.capture_launcher(),
        backend,
        opencode,
    )


def _initial_manifest(
    capture: TrustedCaptures,
    receipts: tuple[ProbeReceipt, ...],
) -> ResourceManifest:
    return build_initial_manifest(
        capture.identity,
        capture.launcher.facts + capture.backend.facts + capture.opencode.facts,
        receipts,
    )


def test_internal_container_backend_and_opencode_capture_persist_and_reopen(
    tmp_path: Path,
) -> None:
    # Given trusted adapters captured real mixed-namespace backend and OpenCode facts.
    capture = _trusted_captures(tmp_path)
    receipts = (
        capture.launcher.receipts + capture.backend.receipts + capture.opencode.receipts
    )

    # When all namespace-specific authority receipts form revision one.
    _ = ResourceManifestStore.create(
        capture.context,
        _initial_manifest(capture, receipts),
    )
    reopened = ResourceManifestStore.open(
        ResourceManifestContext.bind(
            capture.context.report_dir,
            capture.identity,
        )
    )
    sealed = reopened.seal(expected_revision=1, terminal_status="passed")

    # Then receipt namespaces are exact and sealed state survives another reopen.
    assert {receipt.namespace for receipt in capture.backend.receipts} == {
        "host",
        "container:cid-a",
    }
    assert len({receipt.probe_id for receipt in receipts}) == len(receipts)
    assert sealed.sealed is True
    final = ResourceManifestStore.open(
        ResourceManifestContext.bind(
            capture.context.report_dir,
            capture.identity,
        )
    )
    assert final.read() == sealed
    with pytest.raises(ResourceManifestError) as singular:
        _ = capture.backend.receipt
    assert singular.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH


def test_top_level_capture_rejects_omitted_namespace_receipt(tmp_path: Path) -> None:
    # Given a mixed backend capture is missing its container receipt.
    capture = _trusted_captures(tmp_path)
    receipts = capture.launcher.receipts + capture.backend.receipts[:1]

    # When the incomplete authority set crosses the create boundary.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = ResourceManifestStore.create(
            capture.context,
            _initial_manifest(capture, receipts),
        )

    # Then observed container facts cannot inherit host authority.
    assert refusal.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH


def test_top_level_capture_rejects_duplicate_receipt_identifier(
    tmp_path: Path,
) -> None:
    # Given a valid authority set repeats one authenticated receipt.
    capture = _trusted_captures(tmp_path)
    receipts = capture.launcher.receipts + capture.backend.receipts
    manifest = _initial_manifest(capture, receipts)
    duplicated = manifest.model_copy(
        update={"probe_receipts": receipts + (capture.backend.receipts[0],)}
    )

    # When duplicate receipt identifiers cross the create boundary.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = ResourceManifestStore.create(capture.context, duplicated)

    # Then dictionary collapse cannot hide duplicate authority evidence.
    assert refusal.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH


def test_top_level_capture_rejects_cross_namespace_receipt(tmp_path: Path) -> None:
    # Given the container receipt is relabeled as host without a new authority tag.
    capture = _trusted_captures(tmp_path)
    host_receipt, container_receipt = capture.backend.receipts
    crossed = container_receipt.model_copy(update={"namespace": "host"})
    valid_receipts = capture.launcher.receipts + capture.backend.receipts
    manifest = _initial_manifest(capture, valid_receipts)
    crossed_manifest = manifest.model_copy(
        update={"probe_receipts": capture.launcher.receipts + (host_receipt, crossed)}
    )

    # When the relabeled receipt crosses the create boundary.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = ResourceManifestStore.create(
            capture.context,
            crossed_manifest,
        )

    # Then exact namespace binding fails closed.
    assert refusal.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH
