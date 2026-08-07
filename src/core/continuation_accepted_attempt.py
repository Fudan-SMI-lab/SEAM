from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from core.continuation_hydration_authority import load_verified_bytes
from core.continuation_hydration_models import (
    ContinuationHydrationError,
    ContinuationHydrationErrorKind,
    ContinuationHydrationRequest,
    ParentAcceptedAttemptReference,
)
from core.phase5_attempt_models import Phase5AttemptReceipt, is_attempt_acceptable
from core.run_manifest import EvidenceDigest


def _matches_sealed_artifact(
    artifact_path: str,
    artifact_digest: str,
    artifact_size: int,
    request: ContinuationHydrationRequest,
    inventory: tuple[EvidenceDigest, ...],
) -> bool:
    artifact_root = (
        request.parent.output_project / ".sm-artifacts" / str(request.parent.run_id)
    )
    try:
        relative_path = Path(artifact_path).relative_to(artifact_root).as_posix()
    except ValueError:
        return False
    return (
        sum(
            evidence.relative_path == relative_path
            and str(evidence.digest) == artifact_digest
            and evidence.size_bytes == artifact_size
            for evidence in inventory
        )
        == 1
    )


def verify_accepted_attempt(
    reference: ParentAcceptedAttemptReference,
    request: ContinuationHydrationRequest,
    sealed_root: Path,
) -> None:
    if (
        reference.parent_run_id != request.parent.run_id
        or reference.receipt_evidence not in request.parent.run_manifest.sealed_evidence
    ):
        raise ContinuationHydrationError(
            ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
            "accepted attempt is not bound to the resolved parent",
        )
    content = load_verified_bytes(sealed_root, reference.receipt_evidence)
    try:
        receipt = Phase5AttemptReceipt.model_validate_json(content)
    except ValidationError as exc:
        raise ContinuationHydrationError(
            ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
            "accepted attempt receipt is malformed",
        ) from exc
    valid = (
        receipt.run_id == str(reference.parent_run_id)
        and str(receipt.attempt_id) == str(reference.attempt_id)
        and receipt.accepted
        and is_attempt_acceptable(receipt)
        and receipt.review.outcome is reference.review_outcome
    )
    inventory = request.parent.run_manifest.sealed_evidence
    artifacts = [
        receipt.artifacts.stdout,
        receipt.artifacts.stderr,
        receipt.artifacts.metadata,
    ]
    if receipt.custom_op_gate.report is not None:
        artifacts.append(receipt.custom_op_gate.report)
    artifacts_match = all(
        _matches_sealed_artifact(
            artifact.path,
            str(artifact.sha256),
            artifact.size_bytes,
            request,
            inventory,
        )
        for artifact in artifacts
    )
    review_matches = (not receipt.review.enabled and not reference.review_rounds) or (
        receipt.review.enabled and bool(reference.review_rounds)
    )
    if not valid or not artifacts_match or not review_matches:
        raise ContinuationHydrationError(
            ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
            "accepted attempt receipt does not match the inherited reference",
        )
