from __future__ import annotations

import hashlib
import hmac
import secrets
import typing
from pathlib import Path
from typing import final

from .resource_manifest_models import (
    RESOURCE_MANIFEST_SCHEMA,
    RESOURCE_MANIFEST_SCHEMA_VERSION,
    EnvironmentRecord,
    FactProvenance,
    ProbeReceipt,
    ProvenanceFact,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
)
from .resource_manifest_paths import (
    load_internal_capture_secret as _load_or_create_capture_secret,
)

_TOP_LEVEL_RESOURCES = frozenset(
    {"resource-launcher", "resource-backend", "resource-opencode"}
)


@final
class _ResourceCaptureAuthority:
    __slots__ = ("_identity", "_secret")

    def __init__(self, identity: ResourceManifestIdentity, secret: bytes) -> None:
        self._identity = identity
        self._secret = secret

    @property
    def identity(self) -> ResourceManifestIdentity:
        return self._identity

    def _tag(self, fields: typing.Tuple[str, ...]) -> str:
        framed = "".join(f"{len(field)}:{field}" for field in fields).encode()
        return hmac.new(self._secret, framed, hashlib.sha256).hexdigest()


def create_resource_capture_authority(
    identity: ResourceManifestIdentity,
    report_dir: Path,
) -> _ResourceCaptureAuthority:
    secret = _load_or_create_capture_secret(
        report_dir,
        secrets.token_bytes(32),
    )
    return _ResourceCaptureAuthority(identity, secret)


def _identity_fields(
    authority: _ResourceCaptureAuthority,
) -> typing.Tuple[str, ...]:
    identity = authority.identity
    return (
        RESOURCE_MANIFEST_SCHEMA,
        str(RESOURCE_MANIFEST_SCHEMA_VERSION),
        identity.run_id,
        identity.workflow_digest,
        identity.workspace_digest,
    )


def _fact_fields(
    authority: _ResourceCaptureAuthority,
    fact: ProvenanceFact,
    resource_id: str,
    probe_type: str,
) -> typing.Tuple[str, ...]:
    return _identity_fields(authority) + (
        "fact",
        resource_id,
        probe_type,
        fact.name,
        fact.value or "",
        fact.status.value,
        fact.provenance.value,
        fact.namespace,
        fact.detail or "",
    )


def _signed_fact(
    authority: _ResourceCaptureAuthority,
    fact: ProvenanceFact,
    resource_id: str,
    probe_type: str,
) -> ProvenanceFact:
    return fact.model_copy(
        update={
            "authority_tag": authority._tag(
                _fact_fields(authority, fact, resource_id, probe_type)
            )
        }
    )


def _receipt_fields(
    authority: _ResourceCaptureAuthority,
    receipt: ProbeReceipt,
    probe_type: str,
) -> typing.Tuple[str, ...]:
    facts = tuple(
        field
        for fact in receipt.verified_facts
        for field in _fact_fields(authority, fact, receipt.environment_id, probe_type)
    )
    return (
        _identity_fields(authority)
        + (
            "receipt",
            receipt.probe_id,
            receipt.environment_id,
            receipt.namespace,
            probe_type,
            receipt.status.value,
            receipt.detail or "",
        )
        + facts
    )


def capture_environment(
    authority: _ResourceCaptureAuthority,
    environment: EnvironmentRecord,
    receipt: ProbeReceipt,
    probe_type: str,
) -> tuple[EnvironmentRecord, ProbeReceipt]:
    signed = tuple(
        _signed_fact(authority, fact, environment.environment_id, probe_type)
        if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        else fact
        for fact in environment.facts
    )
    authenticated = EnvironmentRecord(
        environment_id=environment.environment_id,
        facts=signed,
    )
    observed = tuple(
        fact for fact in signed if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
    )
    unsigned_receipt = receipt.model_copy(update={"verified_facts": observed})
    authenticated_receipt = unsigned_receipt.model_copy(
        update={
            "authority_tag": authority._tag(
                _receipt_fields(authority, unsigned_receipt, probe_type)
            )
        }
    )
    return authenticated, authenticated_receipt


def _probe_type(receipt: ProbeReceipt) -> str:
    if receipt.environment_id in _TOP_LEVEL_RESOURCES:
        return receipt.environment_id.partition("-")[2]
    return "environment"


def _require_fact_tag(
    authority: _ResourceCaptureAuthority,
    fact: ProvenanceFact,
    resource_id: str,
    probe_type: str,
) -> None:
    expected = authority._tag(_fact_fields(authority, fact, resource_id, probe_type))
    if fact.authority_tag is None or not hmac.compare_digest(
        fact.authority_tag, expected
    ):
        raise ResourceManifestError(
            ResourceManifestErrorKind.AUTHORITY_MISMATCH,
            f"observed fact lacks framework authority: {fact.name}",
        )


def require_manifest_authority(
    manifest: ResourceManifest,
    authority: _ResourceCaptureAuthority,
) -> None:
    receipts = {receipt.probe_id: receipt for receipt in manifest.probe_receipts}
    if len(receipts) != len(manifest.probe_receipts):
        raise ResourceManifestError(
            ResourceManifestErrorKind.AUTHORITY_MISMATCH,
            "resource manifest contains duplicate authority receipt identifiers",
        )
    for receipt in receipts.values():
        probe_type = _probe_type(receipt)
        expected = authority._tag(_receipt_fields(authority, receipt, probe_type))
        if receipt.authority_tag is None or not hmac.compare_digest(
            receipt.authority_tag, expected
        ):
            raise ResourceManifestError(
                ResourceManifestErrorKind.AUTHORITY_MISMATCH,
                f"probe receipt lacks framework authority: {receipt.probe_id}",
            )
        for fact in receipt.verified_facts:
            _require_fact_tag(authority, fact, receipt.environment_id, probe_type)
    observed_top = tuple(
        fact
        for fact in manifest.facts
        if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
    )
    for receipt in receipts.values():
        if receipt.environment_id in _TOP_LEVEL_RESOURCES and any(
            fact not in observed_top for fact in receipt.verified_facts
        ):
            raise ResourceManifestError(
                ResourceManifestErrorKind.AUTHORITY_MISMATCH,
                f"top-level receipt verifies an absent fact: {receipt.probe_id}",
            )
    for fact in observed_top:
        matches = tuple(
            receipt
            for receipt in receipts.values()
            if receipt.environment_id in _TOP_LEVEL_RESOURCES
            and receipt.namespace == fact.namespace
            and fact in receipt.verified_facts
        )
        if len(matches) != 1:
            raise ResourceManifestError(
                ResourceManifestErrorKind.AUTHORITY_MISMATCH,
                f"top-level observation lacks one authority receipt: {fact.name}",
            )
    for environment in manifest.environments:
        for fact in environment.facts:
            if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED:
                _require_fact_tag(
                    authority, fact, environment.environment_id, "environment"
                )
