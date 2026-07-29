from __future__ import annotations

import os
import subprocess
import hashlib
import hmac
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from .continuation_environment_models import (
    BindMount,
    ContainerObservation,
    ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind,
    EnvironmentFingerprint,
    RetainedContainerProbeRequest,
    RetainedEnvironmentProbeRequest,
)
from .resource_manifest_models import EnvironmentType


class _ObservedModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def project_observed_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {name: value[name] for name in cls.model_fields if name in value}


class _ContainerState(_ObservedModel):
    Status: str


class _ContainerConfig(_ObservedModel):
    Image: str = ""
    WorkingDir: str = ""
    Labels: dict[str, str] = Field(default_factory=dict)


class _ContainerDevice(_ObservedModel):
    PathOnHost: str = ""


class _HostConfig(_ObservedModel):
    Devices: tuple[_ContainerDevice, ...] = ()


class _ContainerMount(_ObservedModel):
    Type: str = ""
    Source: str
    Destination: str


class _ContainerInspect(_ObservedModel):
    Id: str
    Name: str
    Image: str
    State: _ContainerState
    Config: _ContainerConfig
    HostConfig: _HostConfig = _HostConfig()
    Mounts: tuple[_ContainerMount, ...] = ()


class _FingerprintPayload(_ObservedModel):
    interpreter_realpath: str
    sys_executable: str
    sys_prefix: str
    sys_base_prefix: str
    python_implementation: str
    python_version: str
    platform_system: str
    platform_architecture: str
    package_inventory_hash: str


_INSPECT_ADAPTER = TypeAdapter(Tuple[_ContainerInspect, ...])
_FINGERPRINT_SCRIPT = """import hashlib
import importlib.metadata
import json
import os
import platform
import sys

packages = sorted(
    f"{name}=={distribution.version}"
    for distribution in importlib.metadata.distributions()
    for name in (distribution.metadata.get("Name"),)
    if name
)
print(json.dumps({
    "interpreter_realpath": os.path.realpath(sys.executable),
    "sys_executable": sys.executable,
    "sys_prefix": sys.prefix,
    "sys_base_prefix": sys.base_prefix,
    "python_implementation": platform.python_implementation(),
    "python_version": platform.python_version(),
    "platform_system": platform.system(),
    "platform_architecture": platform.machine(),
    "package_inventory_hash": hashlib.sha256("\\n".join(packages).encode()).hexdigest(),
}))
"""


def _error(
    kind: ContinuationEnvironmentErrorKind,
    field: str,
    detail: str,
) -> ContinuationEnvironmentError:
    return ContinuationEnvironmentError(kind, field, detail)


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH,
            "probe.timeout",
            "retained environment probe timed out",
        ) from exc
    except OSError as exc:
        raise _error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISSING,
            "probe.runtime",
            "retained environment probe could not start",
        ) from exc


def _matching_label(labels: dict[str, str], expected: str | None) -> str | None:
    if expected is None:
        return None
    key, separator, value = expected.partition("=")
    if separator and labels.get(key) == value:
        return expected
    return None


def inspect_retained_container(
    request: RetainedContainerProbeRequest,
) -> ContainerObservation:
    result = _run([request.runtime, "inspect", request.container_id], 30)
    if result.returncode != 0:
        raise _error(
            ContinuationEnvironmentErrorKind.CONTAINER_MISSING,
            "container.id",
            "recorded container is unavailable",
        )
    try:
        records = _INSPECT_ADAPTER.validate_json(result.stdout)
    except ValidationError as exc:
        raise _error(
            ContinuationEnvironmentErrorKind.CONTAINER_MISMATCH,
            "container.inspect",
            "container inspect result is malformed",
        ) from exc
    if len(records) != 1:
        raise _error(
            ContinuationEnvironmentErrorKind.CONTAINER_MISMATCH,
            "container.inspect",
            "container inspect did not resolve exactly one identity",
        )
    record = records[0]
    labels = record.Config.Labels
    token = labels.get("seam.owner-token")
    token_digest = hashlib.sha256(token.encode()).hexdigest() if token else None
    matched_token = (
        token
        if token is not None
        and token_digest is not None
        and request.expected_ownership_token_sha256 is not None
        and hmac.compare_digest(
            token_digest,
            request.expected_ownership_token_sha256,
        )
        else None
    )
    mounts = tuple(
        BindMount(source=Path(item.Source).resolve(), destination=item.Destination)
        for item in record.Mounts
        if item.Type == "bind"
    )
    devices = tuple(
        item.PathOnHost for item in record.HostConfig.Devices if item.PathOnHost
    )
    return ContainerObservation(
        runtime=request.runtime,
        container_id=record.Id,
        name=record.Name.lstrip("/"),
        running=record.State.Status == "running",
        image_identity=record.Image,
        image_reference=record.Config.Image,
        workdir=record.Config.WorkingDir,
        devices=devices,
        bind_mounts=mounts,
        ownership_token=matched_token,
        ownership_label=_matching_label(labels, request.expected_ownership_label),
    )


def _probe_command(request: RetainedEnvironmentProbeRequest) -> list[str]:
    if request.runtime is None or request.container_id is None:
        return [request.interpreter_path, "-c", _FINGERPRINT_SCRIPT]
    return [
        request.runtime,
        "exec",
        request.container_id,
        request.interpreter_path,
        "-c",
        _FINGERPRINT_SCRIPT,
    ]


def probe_retained_environment(
    request: RetainedEnvironmentProbeRequest,
) -> EnvironmentFingerprint:
    container = request.runtime is not None and request.container_id is not None
    if (request.runtime is None) != (request.container_id is None):
        raise _error(
            ContinuationEnvironmentErrorKind.BACKEND_MISMATCH,
            "probe.namespace",
            "runtime and container identity must be supplied together",
        )
    if request.timeout_seconds < 1 or request.timeout_seconds > 60:
        raise _error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH,
            "probe.timeout",
            "retained environment probe timeout must be between 1 and 60 seconds",
        )
    if container:
        check = _run(
            [
                request.runtime or "",
                "exec",
                request.container_id or "",
                "test",
                "-x",
                request.interpreter_path,
            ],
            request.timeout_seconds,
        )
        available = check.returncode == 0
    else:
        path = Path(request.interpreter_path)
        available = path.is_file() and os.access(path, os.X_OK)
    if not available:
        raise _error(
            ContinuationEnvironmentErrorKind.INTERPRETER_UNAVAILABLE,
            "interpreter.realpath",
            "recorded interpreter is missing or non-executable",
        )
    result = _run(_probe_command(request), request.timeout_seconds)
    if result.returncode != 0:
        raise _error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH,
            "environment.fingerprint",
            "retained interpreter fingerprint failed",
        )
    try:
        payload = _FingerprintPayload.model_validate_json(result.stdout)
    except ValidationError as exc:
        raise _error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH,
            "environment.fingerprint",
            "retained interpreter fingerprint is malformed",
        ) from exc
    environment_type = (
        EnvironmentType.BASE
        if payload.sys_prefix == payload.sys_base_prefix
        else EnvironmentType.PROJECT_VENV
    )
    namespace = f"container:{request.container_id}" if container else "host"
    return EnvironmentFingerprint(
        environment_type=environment_type,
        namespace=namespace,
        container_id=request.container_id,
        interpreter_realpath=payload.interpreter_realpath,
        sys_executable=payload.sys_executable,
        sys_prefix=payload.sys_prefix,
        sys_base_prefix=payload.sys_base_prefix,
        python_implementation=payload.python_implementation,
        python_version=payload.python_version,
        platform_system=payload.platform_system,
        platform_architecture=payload.platform_architecture,
        package_inventory_hash=payload.package_inventory_hash,
        interpreter_available=True,
        interpreter_executable=True,
    )
