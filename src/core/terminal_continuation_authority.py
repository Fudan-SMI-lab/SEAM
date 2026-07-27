from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .continuation_hydration_authority import load_verified_bytes
from .continuation_hydration_models import ParentAcceptedAttemptReference
from .continuation_models import ResolvedTerminalParent
from .continuation_paths import read_explicit_summary_snapshot
from .phase5_attempt_models import Phase5AttemptReceipt, is_attempt_acceptable
from .run_manifest import CanonicalReference, EvidenceDigest
from .run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    ReviewRound,
    ReviewVerdict,
)
from .terminal_continuation_models import (
    TerminalContinuationError,
    TerminalContinuationErrorKind,
)
from .types import WorkflowDefinition


def _accepted_receipts(
    parent: ResolvedTerminalParent,
    sealed_root: Path,
) -> tuple[tuple[EvidenceDigest, Phase5AttemptReceipt], ...]:
    accepted: list[tuple[EvidenceDigest, Phase5AttemptReceipt]] = []
    for evidence in parent.run_manifest.sealed_evidence:
        if not evidence.relative_path.endswith(".receipt.json"):
            continue
        try:
            receipt = Phase5AttemptReceipt.model_validate_json(
                load_verified_bytes(sealed_root, evidence)
            )
        except ValidationError as exc:
            raise TerminalContinuationError(
                TerminalContinuationErrorKind.ACCEPTED_ATTEMPT_MALFORMED,
                "sealed Phase 5 receipt is malformed",
            ) from exc
        if (
            receipt.run_id == str(parent.run_id)
            and receipt.accepted
            and is_attempt_acceptable(receipt)
        ):
            accepted.append((evidence, receipt))
    return tuple(accepted)


def _review_rounds(summary_path: Path) -> tuple[ReviewRound, ...]:
    summary = read_explicit_summary_snapshot(summary_path).document
    return tuple(
        ReviewRound(
            round_number=item.logical_round,
            max_rounds=item.max_rounds,
            verdict=ReviewVerdict.from_raw(item.verdict),
            outcome=ReviewOutcome.from_raw(item.outcome),
        )
        for item in summary.review_timeout_observability.reviews
    )


def resolve_parent_accepted_attempt(
    parent: ResolvedTerminalParent,
    summary_path: Path,
    workflow: WorkflowDefinition,
) -> ParentAcceptedAttemptReference | None:
    sealed_root = summary_path.resolve().parent / "sealed-artifacts"
    matches = _accepted_receipts(parent, sealed_root)
    if not matches:
        return None
    if len(matches) != 1:
        raise TerminalContinuationError(
            TerminalContinuationErrorKind.ACCEPTED_ATTEMPT_AMBIGUOUS,
            "parent has multiple accepted Phase 5 receipts",
        )
    receipt_evidence, receipt = matches[0]
    canonical_matches = tuple(
        item
        for item in parent.run_manifest.sealed_evidence
        if item.relative_path == "validated/phase_5_validation_canonical.json"
    )
    if len(canonical_matches) != 1:
        raise TerminalContinuationError(
            TerminalContinuationErrorKind.ACCEPTED_ATTEMPT_MALFORMED,
            "accepted Phase 5 has no unique canonical evidence",
        )
    canonical_evidence = canonical_matches[0]
    review_rounds = _review_rounds(summary_path)
    if receipt.review.outcome is ReviewOutcome.DISABLED:
        review_rounds = ()
    return ParentAcceptedAttemptReference(
        parent_run_id=parent.run_id,
        attempt_id=AcceptedAttemptId(str(receipt.attempt_id)),
        canonical_reference=CanonicalReference(
            phase_id=PhaseId("phase_5_validation"),
            artifact_name=Path(canonical_evidence.relative_path).name,
            digest=canonical_evidence.digest,
        ),
        receipt_evidence=receipt_evidence,
        review_outcome=receipt.review.outcome,
        review_fail_closed=(workflow.globals or {}).get("review_fail_closed")
        is not False,
        review_rounds=review_rounds,
    )
