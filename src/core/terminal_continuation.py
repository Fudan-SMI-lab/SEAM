from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .continuation import (
    ContinuationHydrationRequest,
    ContinuationRequest,
    claim_terminal_parent,
    hydrate_terminal_parent,
)
from .continuation_environment_models import (
    Phase2EstablishmentEligible,
    RetainedEnvironmentEligible,
)
from .continuation_evidence import ChildEvidenceRequest, prepare_child_evidence
from .continuation_hydration_authority import require_hydration_authority
from .terminal_continuation_authority import resolve_parent_accepted_attempt
from .terminal_continuation_environment import (
    apply_verified_continuation_backend,
    verify_terminal_continuation_environment,
)
from .terminal_continuation_models import (
    ContinuationPromptFacts,
    PreparedTerminalContinuation,
    TerminalEnvironmentVerificationRequest,
)


@contextmanager
def prepare_terminal_continuation(
    summary_path: Path,
    child_run_id: str,
) -> Iterator[PreparedTerminalContinuation]:
    request = ContinuationRequest(
        summary_path=summary_path,
        child_run_id=child_run_id,
    )
    with claim_terminal_parent(request) as parent:
        authority_request = ContinuationHydrationRequest(
            parent=parent,
            summary_path=summary_path,
        )
        workflow = require_hydration_authority(authority_request).workflow
        accepted = resolve_parent_accepted_attempt(
            parent,
            summary_path,
            workflow,
        )
        hydration_request = ContinuationHydrationRequest(
            parent=parent,
            summary_path=summary_path,
            parent_accepted_attempt=accepted,
        )
        hydration = hydrate_terminal_parent(hydration_request)
        eligibility = verify_terminal_continuation_environment(
            TerminalEnvironmentVerificationRequest(
                parent=parent,
                hydration=hydration,
                workflow=workflow,
                child_run_id=child_run_id,
                parent_accepted_attempt=accepted,
            )
        )
        apply_verified_continuation_backend(parent, workflow, eligibility)
        evidence = prepare_child_evidence(
            parent,
            ChildEvidenceRequest(
                continuation=request,
                inherited_canonical=tuple(
                    item.canonical_reference for item in hydration.phase_results
                ),
            ),
        )
        resource_status = {
            RetainedEnvironmentEligible: "retained_environment_verified",
            Phase2EstablishmentEligible: "phase2_establishment_eligible",
        }[type(eligibility)]
        yield PreparedTerminalContinuation(
            parent=parent,
            workflow=workflow,
            hydration=hydration,
            eligibility=eligibility,
            evidence=evidence,
            prompt_facts=ContinuationPromptFacts(
                parent_run_id=str(parent.run_id),
                child_run_id=request.child_run_id,
                anchor_phase_id=str(hydration.start_phase_id),
                inherited_phase_ids=tuple(
                    str(item.phase_id) for item in hydration.phase_results
                ),
                resource_eligibility=resource_status,
                attachment_mode=(
                    "existing_container"
                    if eligibility.attachment is not None
                    else "none"
                ),
            ),
        )
