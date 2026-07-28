from __future__ import annotations

import re
import typing
from typing import ClassVar, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError
from typing_extensions import Annotated, Self

from .resource_manifest_models import (
    EnvironmentRecord,
    ProbeReceipt,
)

_SafeId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_Namespace = Annotated[
    str,
    StringConstraints(pattern=r"^(?:host|container:[A-Za-z0-9][A-Za-z0-9_.-]{0,127})$"),
]
_Endpoint = Annotated[str, StringConstraints(pattern=r"^https?://[^\s]{1,1000}$")]
_SAFE_NAMESPACE = re.compile(r"(?:host|container:[A-Za-z0-9][A-Za-z0-9_.-]{0,127})\Z")
_ENVIRONMENT_FACT_NAMES = (
    "interpreter.realpath",
    "interpreter.sys_executable",
    "interpreter.sys_prefix",
    "interpreter.sys_base_prefix",
    "python.implementation",
    "python.version",
    "platform.system",
    "platform.architecture",
    "packages.inventory_sha256",
)


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class BackendFactRequest(_FrozenModel):
    requested_workflow: _Text
    effective_workflow: _Text
    requested_backend: Literal["auto", "local", "container"]
    effective_backend: Literal["local", "container"]
    if typing.TYPE_CHECKING:
        attachment_mode: typing.Optional[
            Literal["image_created", "existing_container"]
        ] = None
        original_owner_run_id: typing.Optional[_SafeId] = None
        lineage_root_run_id: typing.Optional[_SafeId] = None
        framework_ownership_token: typing.Optional[_Text] = None
        framework_ownership_label: typing.Optional[_Text] = None
        container_runtime: typing.Optional[_Text] = None
        container_id: typing.Optional[_SafeId] = None
        image: typing.Optional[_Text] = None
        retention_requested: typing.Optional[Literal["retain", "delete"]] = None
        retention_effective: typing.Optional[Literal["retain", "delete"]] = None
    else:
        attachment_mode: typing.Optional[
            Literal["image_created", "existing_container"]
        ] = None
        original_owner_run_id: typing.Optional[_SafeId] = None
        lineage_root_run_id: typing.Optional[_SafeId] = None
        framework_ownership_token: typing.Optional[_Text] = None
        framework_ownership_label: typing.Optional[_Text] = None
        container_runtime: typing.Optional[_Text] = None
        container_id: typing.Optional[_SafeId] = None
        image: typing.Optional[_Text] = None
        retention_requested: typing.Optional[Literal["retain", "delete"]] = None
        retention_effective: typing.Optional[Literal["retain", "delete"]] = None
    owner_kind: Literal["framework", "user", "external", "unknown"] = "framework"
    probe_status: _Text = "not_requested"

    @model_validator(mode="after")
    def require_consistent_ownership(self) -> Self:
        container = self.effective_backend == "container"
        framework_owned = self.owner_kind == "framework"
        ownership = (
            self.original_owner_run_id,
            self.lineage_root_run_id,
            self.framework_ownership_token,
            self.framework_ownership_label,
        )
        container_context = (
            self.attachment_mode,
            self.container_runtime,
            self.container_id,
            self.image,
        )
        if container and (
            self.attachment_mode is None
            or self.container_runtime is None
            or self.container_id is None
        ):
            raise PydanticCustomError(
                "incomplete_container",
                "container backends require attachment, runtime, and identity",
            )
        if not container and any(
            value is not None for value in container_context + ownership
        ):
            raise PydanticCustomError(
                "invalid_attachment", "local backends cannot claim container context"
            )
        if self.attachment_mode == "image_created" and not framework_owned:
            raise PydanticCustomError(
                "invalid_owner", "image-created containers are framework-owned"
            )
        if container and framework_owned and any(value is None for value in ownership):
            raise PydanticCustomError(
                "incomplete_owner", "framework ownership requires token and lineage"
            )
        if not framework_owned and any(value is not None for value in ownership[2:]):
            raise PydanticCustomError(
                "invalid_owner", "non-framework resources cannot claim framework tokens"
            )
        return self


class OpenCodeFactRequest(_FrozenModel):
    endpoint: _Endpoint
    owner_kind: Literal["framework", "user", "external", "unknown"]
    if typing.TYPE_CHECKING:
        version: typing.Optional[_Text] = None
        process_id: typing.Optional[_Text] = None
    else:
        version: typing.Optional[_Text] = None
        process_id: typing.Optional[_Text] = None


class Phase2EnvironmentReport(_FrozenModel):
    venv_path: _Text
    python_path: _Text
    installed_packages: Annotated[typing.Tuple[_Text, ...], Field(max_length=512)]

    @model_validator(mode="after")
    def require_safe_paths(self) -> Self:
        for value in (self.venv_path, self.python_path):
            normalized = value.replace("\\", "/")
            absolute = normalized.startswith("/") or re.match(
                r"^[A-Za-z]:/", normalized
            )
            if absolute is None or ".." in normalized.split("/") or "\x00" in value:
                raise PydanticCustomError(
                    "unsafe_path", "environment paths must be bounded absolute paths"
                )
        return self


class Phase2EnvironmentRequest(_FrozenModel):
    environment_id: _SafeId
    namespace: _Namespace
    if typing.TYPE_CHECKING:
        container_id: typing.Optional[_SafeId] = None
    else:
        container_id: typing.Optional[_SafeId] = None
    report: Phase2EnvironmentReport

    @model_validator(mode="after")
    def require_consistent_namespace(self) -> Self:
        if self.namespace == "host" and self.container_id is not None:
            raise PydanticCustomError(
                "invalid_container", "host environments have no container identity"
            )
        if self.namespace.startswith("container:"):
            expected = self.namespace.partition(":")[2]
            if self.container_id != expected:
                raise PydanticCustomError(
                    "container_mismatch",
                    "environment namespace must match its container identity",
                )
        return self


class EnvironmentProbe(_FrozenModel):
    status: Literal["ok", "error"]
    if typing.TYPE_CHECKING:
        interpreter_realpath: typing.Optional[_Text] = None
        sys_executable: typing.Optional[_Text] = None
        sys_prefix: typing.Optional[_Text] = None
        sys_base_prefix: typing.Optional[_Text] = None
        python_implementation: typing.Optional[_Text] = None
        python_version: typing.Optional[_Text] = None
        platform: typing.Optional[_Text] = None
        architecture: typing.Optional[_Text] = None
        package_inventory_hash: typing.Optional[_Digest] = None
        error: typing.Optional[_Text] = None
    else:
        interpreter_realpath: typing.Optional[_Text] = None
        sys_executable: typing.Optional[_Text] = None
        sys_prefix: typing.Optional[_Text] = None
        sys_base_prefix: typing.Optional[_Text] = None
        python_implementation: typing.Optional[_Text] = None
        python_version: typing.Optional[_Text] = None
        platform: typing.Optional[_Text] = None
        architecture: typing.Optional[_Text] = None
        package_inventory_hash: typing.Optional[_Digest] = None
        error: typing.Optional[_Text] = None

    @model_validator(mode="after")
    def require_complete_probe(self) -> Self:
        values = (
            self.interpreter_realpath,
            self.sys_executable,
            self.sys_prefix,
            self.sys_base_prefix,
            self.python_implementation,
            self.python_version,
            self.platform,
            self.architecture,
            self.package_inventory_hash,
        )
        if self.status == "ok" and any(value is None for value in values):
            raise PydanticCustomError(
                "incomplete_probe", "successful probes are complete"
            )
        if self.status == "ok" and self.error is not None:
            raise PydanticCustomError(
                "unexpected_error", "successful probes cannot carry errors"
            )
        if self.status == "error" and self.error is None:
            raise PydanticCustomError("missing_error", "failed probes require an error")
        if self.status == "error" and any(value is not None for value in values):
            raise PydanticCustomError(
                "unexpected_probe_value", "failed probes cannot carry observed values"
            )
        return self


class EnvironmentProbeRequest(_FrozenModel):
    probe_id: _SafeId
    environment_id: _SafeId
    namespace: _Namespace
    probe: EnvironmentProbe


class ProbedEnvironment(NamedTuple):
    environment: EnvironmentRecord
    receipt: ProbeReceipt


class Phase5ReferenceRequest(_FrozenModel):
    attempt_id: _SafeId
    environment_id: _SafeId
    namespace: _Namespace
