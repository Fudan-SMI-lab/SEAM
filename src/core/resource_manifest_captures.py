from __future__ import annotations

import hashlib
import typing
from typing import NamedTuple

from .execution_env_context import (
    BackendFactRequest,
    EnvironmentProbeRequest,
    OpenCodeFactRequest,
    ProbedEnvironment,
)
from .execution_env_records import (
    capture_local_environment as normalize_local_environment,
)
from .execution_env_records import probe_environment_record
from .resource_manifest_authority import (
    _ResourceCaptureAuthority,
    _receipt_fields,
    _signed_fact,
    capture_environment,
)
from .resource_manifest_io import (
    build_backend_facts,
    build_opencode_facts,
    capture_launcher_facts,
)
from .resource_manifest_models import (
    FactProvenance,
    FactStatus,
    ProbeReceipt,
    ProvenanceFact,
    ResourceManifestError,
    ResourceManifestErrorKind,
)


class CapturedFacts(NamedTuple):
    facts: typing.Tuple[ProvenanceFact, ...]
    receipts: typing.Tuple[ProbeReceipt, ...]

    @property
    def receipt(self) -> ProbeReceipt:
        if len(self.receipts) != 1:
            raise ResourceManifestError(
                ResourceManifestErrorKind.AUTHORITY_MISMATCH,
                "multi-namespace capture has no singular receipt",
            )
        return self.receipts[0]


def capture_top_level(
    authority: _ResourceCaptureAuthority,
    facts: typing.Tuple[ProvenanceFact, ...],
    resource_id: str,
    probe_type: str,
) -> CapturedFacts:
    signed_facts = tuple(
        _signed_fact(authority, fact, resource_id, probe_type)
        if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        else fact
        for fact in facts
    )
    grouped: typing.Dict[str, typing.List[ProvenanceFact]] = {}
    for fact in signed_facts:
        if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED:
            grouped.setdefault(fact.namespace, []).append(fact)
    receipts: typing.List[ProbeReceipt] = []
    for namespace, observed in grouped.items():
        statuses = {fact.status for fact in observed}
        if len(statuses) != 1:
            raise ResourceManifestError(
                ResourceManifestErrorKind.AUTHORITY_MISMATCH,
                f"top-level capture mixes statuses in namespace: {namespace}",
            )
        suffix = namespace.replace(":", "-")
        probe_id = f"capture-{probe_type}-{suffix}"
        if len(probe_id) > 128:
            digest = hashlib.sha256(namespace.encode()).hexdigest()
            probe_id = f"capture-{probe_type}-{digest}"
        receipt = ProbeReceipt(
            probe_id=probe_id,
            environment_id=resource_id,
            namespace=namespace,
            status=next(iter(statuses)),
            verified_facts=tuple(observed),
        )
        receipts.append(
            receipt.model_copy(
                update={
                    "authority_tag": authority._tag(
                        _receipt_fields(authority, receipt, probe_type)
                    )
                }
            )
        )
    return CapturedFacts(signed_facts, tuple(receipts))


def capture_launcher(
    authority: _ResourceCaptureAuthority,
) -> CapturedFacts:
    return capture_top_level(
        authority,
        capture_launcher_facts(),
        "resource-launcher",
        "launcher",
    )


def capture_backend(
    authority: _ResourceCaptureAuthority,
    request: BackendFactRequest,
) -> CapturedFacts:
    observed_names = frozenset(
        {
            "backend.effective",
            "container.attachment_mode",
            "container.owner_kind",
            "container.original_owner_run_id",
            "container.lineage_root_run_id",
            "container.framework_ownership_token",
            "container.framework_ownership_label",
            "container.runtime",
            "container.id",
            "container.image",
            "container.probe_status",
            "ownership.resource_owner_kind",
        }
    )
    facts = tuple(
        fact.model_copy(update={"provenance": FactProvenance.FRAMEWORK_OBSERVED})
        if fact.name in observed_names and fact.status is FactStatus.KNOWN
        else fact
        for fact in build_backend_facts(request)
    )
    return capture_top_level(authority, facts, "resource-backend", "backend")


def capture_opencode(
    authority: _ResourceCaptureAuthority,
    request: OpenCodeFactRequest,
) -> CapturedFacts:
    facts = tuple(
        fact.model_copy(update={"provenance": FactProvenance.FRAMEWORK_OBSERVED})
        if fact.status is FactStatus.KNOWN
        else fact
        for fact in build_opencode_facts(request)
    )
    return capture_top_level(authority, facts, "resource-opencode", "opencode")


def capture_environment_probe(
    authority: _ResourceCaptureAuthority,
    request: EnvironmentProbeRequest,
) -> ProbedEnvironment:
    normalized = probe_environment_record(request)
    environment, receipt = capture_environment(
        authority,
        normalized.environment,
        normalized.receipt,
        "environment",
    )
    return ProbedEnvironment(environment, receipt)


def capture_local_environment(
    authority: _ResourceCaptureAuthority,
    environment_id: str,
) -> ProbedEnvironment:
    normalized = normalize_local_environment(environment_id)
    environment, receipt = capture_environment(
        authority,
        normalized.environment,
        normalized.receipt,
        "environment",
    )
    return ProbedEnvironment(environment, receipt)
