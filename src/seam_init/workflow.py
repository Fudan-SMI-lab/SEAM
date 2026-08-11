"""Typed ordered workflow: one current stage, one frozen terminal outcome."""
from __future__ import annotations

from typing import Final

from core.secret_redaction import redact_sensitive_text
from seam_init.config_transaction import ConfigTransaction, TransactionError
from seam_init.environment import EnvironmentSelectionError, inspect_current_interpreter, select_environment
from seam_init.models import (
    AuthState, BillableCallConsent, EnvironmentChoice, FailureKind,
    InitializerFailure, InitializerOutcome, InitializerStatus, SafeDetail, StageKind, StageStatus,
)
from seam_init.omo_config import OmoConfigRequest, configure_omo
from seam_init.omo_install import OmoInstallRequest, ensure_omo_install
from seam_init.omo_validation import (
    OmoValidationPorts, OmoValidationRequest, validate_omo_runtime,
)
from seam_init.opencode_config import OpencodeConfigRequest, configure_opencode
from seam_init.opencode_install import InstallAction
from seam_init.opencode_runtime import OwnedServerHandle, ensure_server
from seam_init.opencode_runtime_types import ReadinessMode, RuntimePorts, RuntimeRequest
from seam_init.opencode_validation import (
    ValidationPorts, ValidationRequest, validate_opencode_messages,
)
from seam_init.seam_install import InstallStatus, SeamInstallRequest, install_seam
from seam_init.workflow_env import is_supported_environment, select_environment_answers, unsupported_environment_detail
from seam_init.workflow_ledger import StageLedger
from seam_init.workflow_repair import resolve_validation
from seam_init.workflow_types import (
    WorkflowFacts, WorkflowRequest, refresh_config_facts,
)

__all__ = ["run_workflow"]

_OMO_PREFIX: Final[tuple[str, ...]] = ("bunx", "oh-my-openagent")


def _dp(env: EnvironmentChoice, req: WorkflowRequest) -> tuple[str, ...]:
    s = req.seam_source_path.parent / "scripts" / "diagnose_seam_opencode.py"
    return (env.python_executable, str(s))


def _consent(req: WorkflowRequest, auth: AuthState) -> BillableCallConsent:
    if auth is AuthState.SKIPPED:
        return BillableCallConsent.DECLINED
    if req.answers is not None:
        return BillableCallConsent.GIVEN if req.answers.billable_consent else BillableCallConsent.DECLINED
    return (BillableCallConsent.GIVEN
            if req.prompt.confirm("Attempt a real (billable) provider validation call?", default=False)
            else BillableCallConsent.DECLINED)


def _validate_answers(req: WorkflowRequest) -> InitializerOutcome | None:
    if req.interactive or req.answers is None:
        return None
    a = req.answers
    has_pid = bool(a.provider_id and a.provider_id.strip())
    has_mid = bool(a.model_id and a.model_id.strip())
    if not (has_pid and has_mid):
        return InitializerOutcome.failed(
            failure_kind=FailureKind.OPENCODE_CONFIG, stages=(),
            safe_detail=SafeDetail("non-interactive answers require both provider_id and model_id"))
    if not is_supported_environment(a.environment):
        return InitializerOutcome.failed(
            failure_kind=FailureKind.PYTHON_ENVIRONMENT, stages=(),
            safe_detail=unsupported_environment_detail(a.environment))
    return None


def _close_owned(handle: OwnedServerHandle | None, facts: WorkflowFacts) -> SafeDetail | None:
    """Stop an initializer-owned server; one bounded retry, typed failure detail."""
    if handle is None:
        return None
    detail = handle.close()
    if not handle.is_stopped:
        detail = handle.close()
    if handle.is_stopped:
        return None
    text = redact_sensitive_text(str(detail)[:4096])[:200]
    facts.warnings = (*facts.warnings, f"server cleanup: {text}")
    return SafeDetail(f"owned OpenCode server cleanup failed: {text}")


def run_workflow(request: WorkflowRequest, *, facts_out: WorkflowFacts | None = None) -> InitializerOutcome:
    """Run the nine-stage initializer workflow; returns a frozen outcome."""
    ledger = StageLedger()
    facts = facts_out if facts_out is not None else WorkflowFacts()
    facts.server_url = request.server_url
    handle: OwnedServerHandle | None = None
    outcome: InitializerOutcome | None = None
    try:
        try:
            ConfigTransaction(request.project_root).recover_interrupted()
        except (TransactionError, OSError) as exc:
            return InitializerOutcome.failed(
                failure_kind=FailureKind.OPENCODE_CONFIG, stages=(),
                safe_detail=SafeDetail(f"interrupted-state recovery failed: {exc}"))
        bad = _validate_answers(request)
        if bad is not None:
            return bad
        ledger.begin(StageKind.PYTHON_ENVIRONMENT)
        try:
            env = (select_environment(
                base_info=inspect_current_interpreter(), seam_root=request.project_root,
                prompt=request.prompt, venv_creator=request.ports.venv_creator,
                interpreter_probe=request.ports.interpreter_probe)
                if request.interactive else select_environment_answers(request))
        except EnvironmentSelectionError as exc:
            ledger.complete(StageStatus.FAILED)
            raise InitializerFailure(
                kind=FailureKind.PYTHON_ENVIRONMENT, safe_detail=exc.safe_detail) from exc
        if env is None:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=FailureKind.PYTHON_ENVIRONMENT, stages=ledger.snapshot(),
                safe_detail=SafeDetail("environment selection cancelled"))
        facts.environment = env
        ledger.complete(StageStatus.SUCCEEDED)
        ledger.begin(StageKind.SEAM_INSTALL)
        si = install_seam(
            SeamInstallRequest(environment=env, source_path=request.seam_source_path),
            prompt=request.stage_prompt(), runner=request.ports.pip_runner)
        facts.seam_status = si.status.value
        if si.status is InstallStatus.DECLINED:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=FailureKind.SEAM_INSTALL, stages=ledger.snapshot(),
                safe_detail=SafeDetail("SEAM install declined by user"))
        if not si.ok:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=si.failure_kind or FailureKind.SEAM_INSTALL,
                stages=ledger.snapshot(), safe_detail=si.failure_detail)
        ledger.complete(StageStatus.SUCCEEDED)
        ledger.begin(StageKind.OPENCODE_INSTALL)
        oi = request.ports.opencode_installer.install()
        if oi.binary:
            facts.opencode_binary_path = str(oi.binary.path)
            facts.opencode_version = oi.binary.version_text
        if oi.action is InstallAction.REFUSED:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=FailureKind.OPENCODE_INSTALL, stages=ledger.snapshot(),
                safe_detail=oi.refusal_reason)
        ledger.complete(StageStatus.SUCCEEDED)
        binary = str(oi.binary.path) if oi.binary else ""
        ledger.begin(StageKind.OPENCODE_CONFIG)
        occ = configure_opencode(OpencodeConfigRequest(
            project_root=request.project_root, prompt=request.stage_prompt(),
            runtime=request.ports.opencode_runtime, schema_validator=request.ports.schema_validator,
            selection=request.effective_selection, api_key=request.resolve_api_key(),
            custom=request.effective_custom))
        facts.opencode_config_committed = occ.committed
        if not occ.committed:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=FailureKind.OPENCODE_CONFIG,
                stages=ledger.snapshot(), safe_detail=occ.safe_detail)
        sel = occ.selection or request.effective_selection
        if sel is None:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=FailureKind.OPENCODE_CONFIG, stages=ledger.snapshot(),
                safe_detail=SafeDetail("config committed but selection missing"))
        facts.provider_model = f"{sel.provider_id}/{sel.model_id}"
        if occ.transaction is not None:
            facts.opencode_transaction_id = occ.transaction.transaction_id
        facts.auth_state = AuthState.PROVIDED if not occ.pending_auth else AuthState.SKIPPED
        refresh_config_facts(facts, request.project_root)
        ledger.complete(StageStatus.SUCCEEDED)
        facts.billable_consent = _consent(request, facts.auth_state)
        model = facts.provider_model
        ledger.begin(StageKind.OMO_INSTALL)
        sub = request.ports.subscription_selector.select(str(sel.provider_id))
        ominst = ensure_omo_install(
            OmoInstallRequest(subscription=sub),
            bun_installer=request.ports.bun_installer, omo_installer=request.ports.omo_installer,
            registrar=request.ports.plugin_registrar)
        facts.bun_path, facts.bun_version = str(ominst.bun.path), ominst.bun.version_text
        facts.omo_action = ominst.omo_action
        if ominst.legacy_warning:
            facts.warnings = (*facts.warnings, ominst.legacy_warning)
        ledger.complete(StageStatus.SUCCEEDED)
        ledger.begin(StageKind.OMO_CONFIG)
        omc = configure_omo(OmoConfigRequest(
            project_root=request.project_root, prompt=request.stage_prompt(),
            capability_port=request.ports.omo_capability, runtime=request.ports.opencode_runtime,
            selected_model=model, selected_reasoning=request.effective_reasoning))
        facts.omo_config_committed = omc.committed
        if not omc.committed:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=FailureKind.OMO_CONFIG,
                stages=ledger.snapshot(), safe_detail=omc.safe_detail)
        facts.omo_version = omc.plugin_version
        if omc.transaction is not None:
            facts.omo_transaction_id = omc.transaction.transaction_id
        refresh_config_facts(facts, request.project_root)
        ledger.complete(StageStatus.SUCCEEDED)
        ledger.begin(StageKind.OPENCODE_RUNTIME)
        rt = ensure_server(
            RuntimeRequest(
                diagnose_argv_prefix=_dp(env, request), opencode_executable=binary,
                server_url=request.server_url, server_hostname=request.server_hostname,
                server_port=request.server_port, readiness_mode=ReadinessMode.BASIC,
                base_env=request.base_env, start_timeout=request.start_timeout,
                poll_interval=request.poll_interval, work_dir=str(request.project_root)),
            ports=RuntimePorts(diagnose_runner=request.ports.diagnose_runner,
                                lifecycle=request.ports.server_lifecycle,
                                sleep=request.ports.sleep, monotonic=request.ports.monotonic))
        facts.server_ownership = rt.ownership.value
        if not rt.ok:
            ledger.complete(StageStatus.FAILED)
            return InitializerOutcome.failed(
                failure_kind=rt.failure_kind or FailureKind.OPENCODE_RUNTIME,
                stages=ledger.snapshot(), safe_detail=rt.failure_detail)
        handle = rt.owned_handle
        ledger.complete(StageStatus.SUCCEEDED)
        val = validate_opencode_messages(
            ValidationRequest(
                server_url=rt.server_url, provider_model=model,
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                opencode_executable=binary, diagnose_argv_prefix=_dp(env, request),
                message_timeout=request.message_timeout, base_env=request.base_env),
            ports=ValidationPorts(version_probe=request.ports.version_probe,
                                   runtime=request.ports.opencode_runtime,
                                   diagnose_runner=request.ports.diagnose_runner))
        omo = validate_omo_runtime(
            OmoValidationRequest(
                auth_state=facts.auth_state, billable_consent=facts.billable_consent,
                doctor_argv_prefix=_OMO_PREFIX,
                run_argv_prefix=_OMO_PREFIX,
                doctor_timeout_seconds=request.doctor_timeout,
                run_timeout_seconds=request.run_timeout, base_env=request.base_env),
            ports=OmoValidationPorts(command=request.ports.omo_command))
        facts.omo_runtime_command = " ".join(_OMO_PREFIX)
        outcome = resolve_validation(request, val, omo, ledger, facts, binary, model)
    except (KeyboardInterrupt, EOFError):
        current = ledger.current
        if current is not None:
            ledger.complete(StageStatus.FAILED)
            kind = FailureKind[current.name]
        else:
            kind = FailureKind.OPENCODE_VALIDATION
        outcome = InitializerOutcome.failed(
            failure_kind=kind, stages=ledger.snapshot(),
            auth_state=facts.auth_state, billable_consent=facts.billable_consent,
            safe_detail=SafeDetail("interrupted by user"))
    except InitializerFailure as exc:
        if ledger.current is not None:
            ledger.complete(StageStatus.FAILED)
        outcome = InitializerOutcome.failed(
            failure_kind=exc.kind, stages=ledger.snapshot(),
            auth_state=facts.auth_state, billable_consent=facts.billable_consent,
            safe_detail=exc.safe_detail)
    finally:
        cleanup = _close_owned(handle, facts)
    assert outcome is not None
    if cleanup is not None and outcome.status is not InitializerStatus.FAILED:
        return InitializerOutcome.failed(
            failure_kind=FailureKind.OPENCODE_RUNTIME, stages=outcome.stages,
            auth_state=outcome.auth_state, billable_consent=outcome.billable_consent,
            safe_detail=cleanup)
    return outcome
