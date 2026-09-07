"""Validation outcome resolution and interactive repair integration."""
from __future__ import annotations

from core.compat import assert_never
from seam_init.models import (
    EnvironmentChoice, FailureKind, InitializerOutcome, SafeDetail, StageKind,
    StageStatus,
)
from seam_init.omo_config import OmoConfigRequest, configure_omo
from seam_init.omo_validation import (
    OmoValidationFact, OmoValidationOutcome, OmoValidationPorts,
    OmoValidationRequest, validate_omo_runtime,
)
from seam_init.opencode_config import OpencodeConfigRequest, configure_opencode
from seam_init.opencode_validation import (
    ValidationFact, ValidationOutcome, ValidationPorts, ValidationRequest,
    validate_opencode_messages,
)
from seam_init.repair import RepairRequest, run_repair
from seam_init.repair_classify import (
    RepairCategory, RepairOutcome, RepairStatus, RepairValidation,
    classify_repair, omo_terminal_fact, opencode_terminal_fact,
)
from seam_init.workflow_ledger import StageLedger
from seam_init.workflow_types import (
    WorkflowFacts, WorkflowRequest, refresh_config_facts,
)

__all__ = ["map_repair_outcome", "resolve_validation", "run_interactive_repair"]

_NON_FAILURE_OC = frozenset({ValidationFact.MESSAGE_READY, ValidationFact.AUTH_DEFERRED})
_NON_FAILURE_OMO = frozenset({OmoValidationFact.VALIDATED, OmoValidationFact.AUTH_DEFERRED})


def _oc_status(fact: ValidationFact) -> StageStatus:
    if fact is ValidationFact.MESSAGE_READY:
        return StageStatus.SUCCEEDED
    if fact is ValidationFact.AUTH_DEFERRED:
        return StageStatus.SKIPPED
    return StageStatus.FAILED


def _omo_status(fact: OmoValidationFact) -> StageStatus:
    if fact is OmoValidationFact.VALIDATED:
        return StageStatus.SUCCEEDED
    if fact is OmoValidationFact.AUTH_DEFERRED:
        return StageStatus.SKIPPED
    return StageStatus.FAILED


def _terminal_priority(val: ValidationOutcome, omo: OmoValidationOutcome) -> FailureKind:
    if omo.failure_kind is not None and omo_terminal_fact(omo.fact):
        return omo.failure_kind
    if val.failure_kind is not None and opencode_terminal_fact(val.fact):
        return val.failure_kind
    return omo.failure_kind or val.failure_kind or FailureKind.OPENCODE_VALIDATION


def _update_facts(facts: WorkflowFacts, val: ValidationOutcome, omo: OmoValidationOutcome) -> None:
    facts.opencode_validation_fact = val.fact.value
    facts.omo_validation_fact = omo.fact.value
    if omo.doctor_config_path:
        facts.omo_live_config_path = omo.doctor_config_path
    facts.diagnostics = (*val.diagnostics, *omo.diagnostics)


def _append_val_stages(ledger: StageLedger, val: ValidationOutcome, omo: OmoValidationOutcome) -> None:
    ledger.append_terminal(StageKind.OPENCODE_VALIDATION, _oc_status(val.fact))
    ledger.append_terminal(StageKind.OMO_VALIDATION, _omo_status(omo.fact))


def resolve_validation(
    req: WorkflowRequest, val: ValidationOutcome, omo: OmoValidationOutcome,
    ledger: StageLedger, facts: WorkflowFacts, binary: str, model: str,
) -> InitializerOutcome:
    """Classify the validation snapshot and map to a terminal outcome."""
    _update_facts(facts, val, omo)
    cat = classify_repair(RepairValidation(opencode=val, omo=omo))
    match cat:
        case RepairCategory.SUCCESS:
            _append_val_stages(ledger, val, omo)
            return InitializerOutcome.ready(
                stages=ledger.snapshot(), safe_detail=SafeDetail("all validations passed"))
        case RepairCategory.PENDING_AUTH:
            _append_val_stages(ledger, val, omo)
            return InitializerOutcome.pending_auth(
                stages=ledger.snapshot(), auth_state=facts.auth_state,
                billable_consent=facts.billable_consent,
                safe_detail=SafeDetail("auth deferred or billable consent declined"))
        case RepairCategory.REPAIRABLE:
            if req.interactive:
                return run_interactive_repair(req, val, omo, binary, model, ledger, facts)
            _append_val_stages(ledger, val, omo)
            return InitializerOutcome.failed(
                failure_kind=_terminal_priority(val, omo), stages=ledger.snapshot(),
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                safe_detail=SafeDetail("repairable failure; rerun interactively"))
        case RepairCategory.TERMINAL:
            _append_val_stages(ledger, val, omo)
            return InitializerOutcome.failed(
                failure_kind=_terminal_priority(val, omo), stages=ledger.snapshot(),
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                safe_detail=SafeDetail(f"terminal: oc={val.fact.value}, omo={omo.fact.value}"))
        case unreachable:
            assert_never(unreachable)


def map_repair_outcome(
    repair: RepairOutcome, ledger: StageLedger, facts: WorkflowFacts,
) -> InitializerOutcome:
    """Map a Task 14 RepairOutcome to a terminal InitializerOutcome."""
    fv, fo = repair.final.opencode, repair.final.omo
    _update_facts(facts, fv, fo)
    match repair.status:
        case RepairStatus.READY:
            _append_val_stages(ledger, fv, fo)
            return InitializerOutcome.ready(
                stages=ledger.snapshot(), safe_detail=SafeDetail("repair complete; all validators pass"))
        case RepairStatus.PENDING_AUTH:
            _append_val_stages(ledger, fv, fo)
            return InitializerOutcome.pending_auth(
                stages=ledger.snapshot(), auth_state=facts.auth_state,
                billable_consent=facts.billable_consent,
                safe_detail=repair.safe_detail)
        case RepairStatus.STOPPED | RepairStatus.EXHAUSTED | RepairStatus.TERMINAL:
            _append_val_stages(ledger, fv, fo)
            return InitializerOutcome.failed(
                failure_kind=repair.failure_kind or FailureKind.OPENCODE_VALIDATION,
                stages=ledger.snapshot(),
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                safe_detail=repair.safe_detail)
        case unreachable:
            assert_never(unreachable)


def _dp(env: EnvironmentChoice, req: WorkflowRequest) -> tuple[str, ...]:
    s = req.seam_source_path.parent / "scripts" / "diagnose_seam_opencode.py"
    return (env.python_executable, str(s))


def run_interactive_repair(
    request: WorkflowRequest, val: ValidationOutcome, omo: OmoValidationOutcome,
    binary_path: str, provider_model: str,
    ledger: StageLedger, facts: WorkflowFacts,
) -> InitializerOutcome:
    """Build callbacks, run the two-round repair loop, map the outcome."""
    env = facts.environment
    assert env is not None
    diag = _dp(env, request)
    current_model = [provider_model]
    edit_tx: dict[str, str] = {}

    def _revalidate_oc() -> ValidationOutcome:
        return validate_opencode_messages(
            ValidationRequest(
                server_url=request.server_url, provider_model=current_model[0],
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                opencode_executable=binary_path, diagnose_argv_prefix=diag,
                message_timeout=request.message_timeout, base_env=request.base_env),
            ports=ValidationPorts(version_probe=request.ports.version_probe,
                                   runtime=request.ports.opencode_runtime,
                                   diagnose_runner=request.ports.diagnose_runner))

    def _revalidate_omo() -> OmoValidationOutcome:
        return validate_omo_runtime(
            OmoValidationRequest(
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                doctor_argv_prefix=("bunx", "oh-my-openagent"),
                run_argv_prefix=("bunx", "oh-my-openagent"),
                doctor_timeout_seconds=request.doctor_timeout,
                run_timeout_seconds=request.run_timeout, base_env=request.base_env),
            ports=OmoValidationPorts(command=request.ports.omo_command))

    def _edit_oc():
        result = configure_opencode(OpencodeConfigRequest(
            project_root=request.project_root, prompt=request.prompt,
            runtime=request.ports.opencode_runtime,
            schema_validator=request.ports.schema_validator,
            selection=request.provider_selection, api_key=None,
            custom=request.custom_provider))
        if result.selection is not None:
            current_model[0] = f"{result.selection.provider_id}/{result.selection.model_id}"
        if result.committed and result.transaction is not None:
            edit_tx["oc"] = result.transaction.transaction_id
        return result

    def _edit_omo():
        result = configure_omo(OmoConfigRequest(
            project_root=request.project_root, prompt=request.prompt,
            capability_port=request.ports.omo_capability,
            runtime=request.ports.opencode_runtime,
            selected_model=current_model[0] if "/" in current_model[0] else None))
        if result.committed and result.transaction is not None:
            edit_tx["omo"] = result.transaction.transaction_id
        if result.plugin_version:
            facts.omo_version = result.plugin_version
        return result

    repair = run_repair(RepairRequest(
        project_root=request.project_root,
        opencode_target=request.opencode_config_path,
        omo_target=request.omo_config_path,
        prompt=request.prompt,
        initial=RepairValidation(opencode=val, omo=omo),
        revalidate_opencode=_revalidate_oc, revalidate_omo=_revalidate_omo,
        edit_opencode=_edit_oc, edit_omo=_edit_omo))
    facts.provider_model = current_model[0]
    if "oc" in edit_tx:
        facts.opencode_transaction_id = edit_tx["oc"]
    if "omo" in edit_tx:
        facts.omo_transaction_id = edit_tx["omo"]
    refresh_config_facts(facts, request.project_root)
    return map_repair_outcome(repair, ledger, facts)
