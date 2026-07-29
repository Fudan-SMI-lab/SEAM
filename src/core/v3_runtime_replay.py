from __future__ import annotations

from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    load_attempt_receipt,
)
from core.replay import (
    NONDETERMINISM_NOTICE,
    ContainerObservation,
    ReplayUnavailableReason,
    render_replay,
)
from core.replay_public import (
    public_cwd,
    public_replay_command,
    public_validation_command,
)
from core.resource_manifest import ResourceManifest
from core.resource_manifest import FactProvenance
from core.run_outcome import AcceptedAttemptId
from core.v3_runtime_report_facts import authenticated_fact
from core.v3_runtime_report_models import RuntimeReplayReport, RuntimeReportRequest

_VALIDATION_VISIBLE_REASONS = frozenset(
    {
        ReplayUnavailableReason.CONTAINER_NOT_RETAINED,
        ReplayUnavailableReason.CONTAINER_STATUS_UNKNOWN,
        ReplayUnavailableReason.CONTAINER_UNAVAILABLE,
        ReplayUnavailableReason.CONTAINER_IDENTITY_MISMATCH,
    }
)


def _container_observation(manifest: ResourceManifest) -> ContainerObservation | None:
    runtime = authenticated_fact(
        manifest.facts,
        "container.runtime",
        FactProvenance.FRAMEWORK_OBSERVED,
    )
    container_id = authenticated_fact(
        manifest.facts,
        "container.id",
        FactProvenance.FRAMEWORK_OBSERVED,
    )
    post_state = authenticated_fact(
        manifest.facts,
        "retention.post_state",
        FactProvenance.DERIVED,
    )
    if runtime is None or container_id is None or post_state is None:
        return None
    if runtime.value is None or container_id.value is None:
        return None
    return ContainerObservation(
        runtime=runtime.value,
        container_id=container_id.value,
        running=post_state.value == "running",
    )


def _unavailable(
    reason: ReplayUnavailableReason,
    accepted_attempt_id: AcceptedAttemptId | None,
) -> RuntimeReplayReport:
    return RuntimeReplayReport(
        available=False,
        reason=reason,
        accepted_attempt_id=accepted_attempt_id,
        validation_command=None,
        command=None,
        cwd=None,
        nondeterminism_notice=NONDETERMINISM_NOTICE,
    )


def build_replay_report(
    request: RuntimeReportRequest,
    manifest: ResourceManifest,
) -> RuntimeReplayReport:
    outcome = request.outcome
    accepted_attempt_id = (
        outcome.accepted_attempt_id
        if outcome is not None and outcome.accepted_attempt_id is not None
        else None
    )
    if outcome is None:
        return _unavailable(ReplayUnavailableReason.RUN_OUTCOME_UNAVAILABLE, None)
    source = request.accepted_receipt
    if source is None:
        return _unavailable(
            ReplayUnavailableReason.RECEIPT_MISSING,
            accepted_attempt_id,
        )
    if str(source.receipt_path.resolve()) != source.authority.receipt_path:
        return _unavailable(
            ReplayUnavailableReason.RECEIPT_MALFORMED,
            accepted_attempt_id,
        )
    try:
        receipt = load_attempt_receipt(source.receipt_path)
    except AttemptReceiptError as exc:
        reason = (
            ReplayUnavailableReason.RECEIPT_MISSING
            if exc.kind is AttemptReceiptErrorKind.MISSING
            else ReplayUnavailableReason.RECEIPT_MALFORMED
        )
        return _unavailable(reason, accepted_attempt_id)
    rendered = render_replay(
        receipt,
        outcome,
        expected_run_id=request.expected_run_id,
        authority=source.authority,
        container_observation=_container_observation(manifest),
    )
    validation_visible = (
        rendered.available or rendered.reason in _VALIDATION_VISIBLE_REASONS
    )
    return RuntimeReplayReport(
        available=rendered.available,
        reason=rendered.reason,
        accepted_attempt_id=accepted_attempt_id,
        validation_command=(
            public_validation_command(receipt) if validation_visible else None
        ),
        command=public_replay_command(receipt) if rendered.available else None,
        cwd=public_cwd(receipt) if rendered.available else None,
        nondeterminism_notice=rendered.nondeterminism_notice,
        auto_execute=rendered.auto_execute,
    )
