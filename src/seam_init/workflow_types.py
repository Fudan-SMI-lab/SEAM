"""Typed composition boundary for the initializer workflow."""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from seam_init.answers import Answers
from seam_init.environment import InterpreterProbe, VenvCreator
from seam_init.models import (
    AuthState, BillableCallConsent, EnvironmentChoice, FailureKind,
    InitializerFailure, ModelId, ProviderId, ProviderSelection, SafeDetail,
)
from seam_init.omo_config import OmoCapabilityPort
from seam_init.omo_install import (
    BunInstaller, OmoInstaller, PluginRegistrar, SubscriptionSelection,
)
from seam_init.omo_validation import OmoCommandPort
from seam_init.opencode_discovery import RuntimePort
from seam_init.opencode_config import SchemaValidator
from seam_init.opencode_install import InstallOutcome
from seam_init.opencode_runtime_types import (
    DEFAULT_POLL_INTERVAL, DEFAULT_PORT, DEFAULT_START_TIMEOUT, DEFAULT_URL,
    DiagnoseRunner, ServerLifecyclePort,
)
from seam_init.opencode_selection import CustomProviderSpec
from seam_init.opencode_validation import VersionProbe
from seam_init.seam_install import PipRunner

__all__ = [
    "BACKUP_POLICY", "ConfirmOnlyPort", "DEFAULT_DOCTOR_TIMEOUT",
    "DEFAULT_MESSAGE_TIMEOUT", "DEFAULT_RUN_TIMEOUT",
    "NonInteractivePromptReached", "OpencodeInstallPort", "PromptPort",
    "SubscriptionSelector", "WorkflowFacts", "WorkflowPorts", "WorkflowRequest",
    "refresh_config_facts",
]

DEFAULT_MESSAGE_TIMEOUT: Final[int] = 120
DEFAULT_DOCTOR_TIMEOUT: Final[float] = 60.0
DEFAULT_RUN_TIMEOUT: Final[float] = 120.0
_PROJECT_CONFIG_REL: Final[str] = ".opencode/opencode.jsonc"
_OMO_CONFIG_REL: Final[str] = ".omo/omo.jsonc"
BACKUP_POLICY: Final[str] = (
    "owner-only 0600 backups under .seam-init/backups/<tx>; restored then "
    "deleted on rollback; deleted after commit")


@runtime_checkable
class PromptPort(Protocol):
    def ask(self, prompt: str, *, default: str | None = None) -> str: ...
    def secret(self, prompt: str) -> str: ...
    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...


class NonInteractivePromptReached(Exception):
    """Raised when a prompt is reached in non-interactive mode."""


@runtime_checkable
class SubscriptionSelector(Protocol):
    def select(self, provider_id: str) -> SubscriptionSelection: ...


@runtime_checkable
class OpencodeInstallPort(Protocol):
    """Injectable boundary for the OpenCode standalone install operation."""
    def install(self) -> InstallOutcome: ...


@final
class ConfirmOnlyPort:
    """Non-interactive prompt shim: auto-confirms, raises on ask/secret."""

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        raise NonInteractivePromptReached(f"ask() reached: {prompt!r}")

    def secret(self, prompt: str) -> str:
        raise NonInteractivePromptReached(f"secret() reached: {prompt!r}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        return True


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowPorts:
    """Every stage adapter port bundled for dependency injection."""

    venv_creator: VenvCreator
    interpreter_probe: InterpreterProbe
    pip_runner: PipRunner
    opencode_installer: OpencodeInstallPort
    opencode_runtime: RuntimePort
    schema_validator: SchemaValidator
    subscription_selector: SubscriptionSelector
    bun_installer: BunInstaller
    omo_installer: OmoInstaller
    plugin_registrar: PluginRegistrar
    omo_capability: OmoCapabilityPort
    server_lifecycle: ServerLifecyclePort
    diagnose_runner: DiagnoseRunner
    version_probe: VersionProbe
    omo_command: OmoCommandPort
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic

    def __repr__(self) -> str:
        return "WorkflowPorts(<redacted>)"


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    """All orchestration inputs; ``answers`` None means interactive mode."""

    project_root: Path
    seam_source_path: Path
    prompt: PromptPort = field(repr=False)
    ports: WorkflowPorts = field(repr=False)
    answers: Answers | None = None
    provider_selection: ProviderSelection | None = None
    custom_provider: CustomProviderSpec | None = None
    base_env: Mapping[str, str] = field(default_factory=dict, repr=False)
    server_url: str = DEFAULT_URL
    server_hostname: str = "127.0.0.1"
    server_port: int = DEFAULT_PORT
    message_timeout: int = DEFAULT_MESSAGE_TIMEOUT
    doctor_timeout: float = DEFAULT_DOCTOR_TIMEOUT
    run_timeout: float = DEFAULT_RUN_TIMEOUT
    start_timeout: float = DEFAULT_START_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL

    @property
    def interactive(self) -> bool:
        return self.answers is None

    @property
    def effective_selection(self) -> ProviderSelection | None:
        if self.provider_selection is not None:
            return self.provider_selection
        if self.answers is not None and self.answers.provider_id:
            mid = self.answers.model_id
            if not mid or not mid.strip():
                return None
            return ProviderSelection(ProviderId(self.answers.provider_id), ModelId(mid))
        return None

    @property
    def effective_custom(self) -> CustomProviderSpec | None:
        if self.custom_provider is not None:
            return self.custom_provider
        if self.answers is not None and self.answers.base_url:
            mid = self.answers.model_id or ""
            return CustomProviderSpec(
                provider_id=self.answers.provider_id or "custom",
                base_url=self.answers.base_url,
                model_id=mid, model_name=mid)
        return None

    @property
    def effective_reasoning(self) -> str | None:
        if self.answers is None:
            return None
        raw = self.answers.reasoning
        if raw is None or not raw.strip():
            return None
        return raw.strip()

    @property
    def opencode_config_path(self) -> Path:
        return self.project_root / _PROJECT_CONFIG_REL

    @property
    def omo_config_path(self) -> Path:
        return self.project_root / _OMO_CONFIG_REL

    def resolve_api_key(self) -> str | None:
        if self.answers is None:
            return None
        env_name = self.answers.api_key_env or ""
        if not env_name:
            return ""
        value = self.base_env.get(env_name, "")
        return value if value.strip() else ""

    def stage_prompt(self) -> PromptPort:
        if self.answers is None:
            return self.prompt
        return ConfirmOnlyPort()


@dataclass(slots=True, repr=False)
class WorkflowFacts:
    """Mutable fact ledger for report rendering; secret-free summaries only."""

    environment: EnvironmentChoice | None = None
    seam_status: str = ""
    opencode_binary_path: str = ""
    opencode_version: str = ""
    opencode_config_committed: bool = False
    opencode_config_path: str = ""
    opencode_config_sha256: str = ""
    opencode_transaction_id: str = ""
    provider_model: str = ""
    auth_state: AuthState = AuthState.SKIPPED
    billable_consent: BillableCallConsent = BillableCallConsent.DECLINED
    bun_path: str = ""
    bun_version: str = ""
    omo_action: str = ""
    omo_version: str = ""
    omo_runtime_command: str = ""
    omo_live_config_path: str = ""
    omo_config_committed: bool = False
    omo_config_path: str = ""
    omo_config_sha256: str = ""
    omo_transaction_id: str = ""
    backup_policy: str = ""
    server_url: str = ""
    server_ownership: str = ""
    opencode_validation_fact: str = ""
    omo_validation_fact: str = ""
    doctor_diagnostics: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics: tuple[SafeDetail, ...] = field(default_factory=tuple, repr=False)

    def __repr__(self) -> str:
        return f"WorkflowFacts(status={self.opencode_validation_fact!r})"


def _config_sha256(path: Path, kind: FailureKind) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise InitializerFailure(
            kind=kind,
            safe_detail=SafeDetail(f"cannot read config {path}: {exc}")) from exc


def refresh_config_facts(facts: WorkflowFacts, root: Path) -> None:
    """Refresh config path/hash facts and the backup policy from disk state."""
    oc, omo = root / _PROJECT_CONFIG_REL, root / _OMO_CONFIG_REL
    facts.opencode_config_path, facts.omo_config_path = str(oc), str(omo)
    if oc.is_file():
        facts.opencode_config_sha256 = _config_sha256(oc, FailureKind.OPENCODE_CONFIG)
    if omo.is_file():
        facts.omo_config_sha256 = _config_sha256(omo, FailureKind.OMO_CONFIG)
    facts.backup_policy = BACKUP_POLICY
