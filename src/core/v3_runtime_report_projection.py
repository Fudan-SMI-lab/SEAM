from __future__ import annotations

from core.replay import NONDETERMINISM_NOTICE, ReplayUnavailableReason
from core.v3_runtime_access import build_access_report
from core.v3_runtime_replay import build_replay_report
from core.v3_runtime_report_facts import (
    active_environment_id,
    environment_report,
    runtime_section,
)
from core.v3_runtime_report_models import (
    RuntimeAccessKind,
    RuntimeAccessReport,
    RuntimeReplayReport,
    RuntimeReportRequest,
    RuntimeOutcomeStatus,
    V3RuntimeReport,
)


def _outcome_status(request: RuntimeReportRequest) -> RuntimeOutcomeStatus:
    outcome = request.outcome
    return (
        RuntimeOutcomeStatus(outcome.terminal_outcome.value)
        if outcome is not None
        else RuntimeOutcomeStatus.UNAVAILABLE
    )


def _manifest_unavailable(request: RuntimeReportRequest) -> V3RuntimeReport:
    return V3RuntimeReport(
        manifest_path=None,
        outcome_status=_outcome_status(request),
        execution=(),
        launcher=(),
        environments=(),
        active_environment_id=None,
        container=(),
        retention=(),
        opencode=(),
        access=RuntimeAccessReport(
            available=False,
            kind=RuntimeAccessKind.UNAVAILABLE,
            detail="authenticated resource manifest unavailable",
        ),
        replay=RuntimeReplayReport(
            available=False,
            reason=ReplayUnavailableReason.RESOURCE_MANIFEST_UNAVAILABLE,
            accepted_attempt_id=None,
            validation_command=None,
            command=None,
            cwd=None,
            nondeterminism_notice=NONDETERMINISM_NOTICE,
        ),
        diagnostics=("authenticated resource manifest unavailable",),
    )


def build_runtime_report(request: RuntimeReportRequest) -> V3RuntimeReport:
    store = request.manifest_store
    if store is None:
        return _manifest_unavailable(request)
    if store.context.identity.run_id != request.expected_run_id:
        return _manifest_unavailable(request)
    manifest = store.read()
    if manifest.run_id != request.expected_run_id:
        return _manifest_unavailable(request)
    accepted_attempt_id = (
        str(request.outcome.accepted_attempt_id)
        if request.outcome is not None
        and request.outcome.accepted_attempt_id is not None
        else None
    )
    selected_environment = active_environment_id(manifest, accepted_attempt_id)
    return V3RuntimeReport(
        manifest_path=str(store.path),
        outcome_status=_outcome_status(request),
        execution=runtime_section(manifest.facts, ("backend.",)),
        launcher=runtime_section(manifest.facts, ("launcher.",)),
        environments=tuple(
            environment_report(environment) for environment in manifest.environments
        ),
        active_environment_id=selected_environment,
        container=runtime_section(
            manifest.facts,
            ("container.", "ownership.resource_owner_kind"),
        ),
        retention=runtime_section(
            manifest.facts,
            ("retention.", "lifecycle.status"),
        ),
        opencode=runtime_section(manifest.facts, ("opencode.",)),
        access=build_access_report(manifest, selected_environment),
        replay=build_replay_report(request, manifest),
    )
