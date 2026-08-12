"""Manage an initializer-owned OpenCode server on 127.0.0.1:4098.

Reuse ready foreign or own+cleanup; raw diagnose 40-43/50 → typed facts.
All owned-process exits route through one no-throw ``safe_stop`` operation.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import final

from seam_init.models import FailureKind, SafeDetail
from seam_init.opencode_runtime_types import (
    DEFAULT_HOSTNAME,
    DEFAULT_PORT,
    DEFAULT_URL,
    DiagnoseResult,
    DiagnoseRunner,
    EnvPatch,
    OwnedProcessRef,
    ReadinessFact,
    ReadinessMode,
    READY_FACTS,
    RuntimePorts,
    RuntimeRequest,
    ServerLifecyclePort,
    ServerOwnership,
    _ALLOWED_ENV_KEYS,
    classify_diagnose_exit,
)

__all__ = [
    "DiagnoseResult", "DiagnoseRunner", "EnvPatch", "OwnedProcessRef",
    "OwnedServerHandle", "ReadinessFact", "ReadinessMode", "RuntimeOutcome",
    "RuntimePorts", "RuntimeRequest", "ServerLifecyclePort", "ServerOwnership",
    "classify_diagnose_exit", "ensure_server", "parse_env_patch", "safe_stop",
]


@final
@dataclass(frozen=True, slots=True)
class RuntimeOutcome:
    readiness_fact: ReadinessFact
    ownership: ServerOwnership
    server_url: str
    env_patch: EnvPatch
    owned_handle: OwnedServerHandle | None = None
    failure_kind: FailureKind | None = None
    failure_detail: SafeDetail = SafeDetail("")
    diagnostics: tuple[SafeDetail, ...] = ()

    def __post_init__(self) -> None:
        is_ready = self.readiness_fact in READY_FACTS
        if is_ready and self.failure_kind is not None:
            raise ValueError("ready fact must not carry failure_kind")
        if not is_ready and self.failure_kind is None:
            raise ValueError("non-ready fact requires failure_kind")

    @property
    def ok(self) -> bool:
        return self.failure_kind is None


def safe_stop(
    lifecycle: ServerLifecyclePort, ref: OwnedProcessRef,
) -> tuple[bool, SafeDetail]:
    """No-throw stop: returns (succeeded, detail). Never raises."""
    try:
        return True, lifecycle.stop(ref)
    except Exception as exc:
        return False, SafeDetail(f"stop failed: {exc}")


@final
class OwnedServerHandle:
    """RAII cleanup; stops owned process on close/exit/interrupt.

    close() is retryable: if stop() raises, _stopped stays False so the
    caller can retry. Only marks stopped after confirmed termination.
    """

    __slots__ = ("_lifecycle", "_ref", "_stopped", "_serve_argv")

    def __init__(self, *, lifecycle: ServerLifecyclePort, ref: OwnedProcessRef, serve_argv: tuple[str, ...]) -> None:
        self._lifecycle = lifecycle
        self._ref = ref
        self._stopped = False
        self._serve_argv = serve_argv

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def serve_argv(self) -> tuple[str, ...]:
        return self._serve_argv

    def close(self) -> SafeDetail:
        if self._stopped:
            return SafeDetail("owned server already stopped; no action")
        succeeded, detail = safe_stop(self._lifecycle, self._ref)
        if succeeded:
            self._stopped = True
        return detail

    def __enter__(self) -> OwnedServerHandle:
        return self

    def __exit__(self, *exc_info: object) -> None:
        _ = self.close()


def parse_env_patch(stdout: str) -> EnvPatch:
    """Parse only allowlisted ``export KEY=VALUE`` lines; never eval."""
    entries: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("export "):
            continue
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            continue
        if len(tokens) != 2:
            continue
        key, sep, value = tokens[1].partition("=")
        if not sep or key not in _ALLOWED_ENV_KEYS:
            continue
        entries.append((key, value))
    return EnvPatch(entries=tuple(entries))


def _bounded(text: str) -> SafeDetail:
    return SafeDetail(text[:512] + "...[truncated]" if len(text) > 512 else text)


def _diag_argv(request: RuntimeRequest, mode: str, *, emit_env: bool = False) -> list[str]:
    argv = [*request.diagnose_argv_prefix, "--server-url", request.server_url, "--mode", mode]
    if emit_env:
        argv.append("--emit-env")
    return argv


def _serve_argv(request: RuntimeRequest) -> list[str]:
    return [request.opencode_executable, "serve", "--port", str(request.server_port), "--hostname", request.server_hostname]


def _poll_until_ready(
    request: RuntimeRequest, ports: RuntimePorts, child_env: Mapping[str, str], ref: OwnedProcessRef,
) -> ReadinessFact:
    deadline = ports.monotonic() + request.start_timeout
    argv = _diag_argv(request, request.readiness_mode.value)
    fact = ReadinessFact.UNKNOWN
    while True:
        result = ports.diagnose_runner.run(argv, env=child_env)
        fact = classify_diagnose_exit(result.returncode)
        if fact in READY_FACTS:
            return fact
        if not ports.lifecycle.is_running(ref) or ports.monotonic() >= deadline:
            return fact
        ports.sleep(request.poll_interval)


def ensure_server(request: RuntimeRequest, *, ports: RuntimePorts) -> RuntimeOutcome:
    """Resolve the OpenCode server: reuse ready foreign or own on 4098."""
    print(f"[OPENCODE_RUNTIME] Probing for existing OpenCode server at "
          f"{request.server_url}...", flush=True)
    env_result = ports.diagnose_runner.run(
        _diag_argv(request, "env", emit_env=True), env=dict(request.base_env),
    )
    if env_result.returncode != 0:
        return RuntimeOutcome(
            readiness_fact=ReadinessFact.UNKNOWN, ownership=ServerOwnership.NONE,
            server_url=request.server_url, env_patch=EnvPatch(),
            failure_kind=FailureKind.OPENCODE_RUNTIME,
            failure_detail=_bounded(f"env-mode diagnose failed rc={env_result.returncode}: {env_result.stderr}"),
        )
    env_patch = parse_env_patch(str(env_result.stdout))
    child_env = env_patch.apply_to(request.base_env)
    fact = classify_diagnose_exit(
        ports.diagnose_runner.run(_diag_argv(request, request.readiness_mode.value), env=child_env).returncode,
    )
    if fact in READY_FACTS:
        print(f"[OPENCODE_RUNTIME] Server ready (reused foreign server)", flush=True)
        return RuntimeOutcome(
            readiness_fact=fact, ownership=ServerOwnership.REUSED_FOREIGN,
            server_url=request.server_url, env_patch=env_patch,
        )
    if fact is not ReadinessFact.SERVER_UNREACHABLE:
        return RuntimeOutcome(
            readiness_fact=fact, ownership=ServerOwnership.NONE,
            server_url=request.server_url, env_patch=env_patch,
            failure_kind=FailureKind.OPENCODE_RUNTIME,
            failure_detail=SafeDetail(f"port occupied or misconfigured: readiness={fact.value}"),
        )
    if (request.server_url.rstrip("/") != DEFAULT_URL
            or request.server_hostname != DEFAULT_HOSTNAME
            or request.server_port != DEFAULT_PORT):
        return RuntimeOutcome(
            readiness_fact=fact, ownership=ServerOwnership.NONE,
            server_url=request.server_url, env_patch=env_patch,
            failure_kind=FailureKind.OPENCODE_RUNTIME,
            failure_detail=_bounded(
                f"owned startup requires url={DEFAULT_URL} hostname={DEFAULT_HOSTNAME} port={DEFAULT_PORT}",
            ),
        )
    if not os.path.isabs(request.opencode_executable):
        return RuntimeOutcome(
            readiness_fact=fact, ownership=ServerOwnership.NONE,
            server_url=request.server_url, env_patch=env_patch,
            failure_kind=FailureKind.OPENCODE_RUNTIME,
            failure_detail=SafeDetail(
                f"opencode_executable must be absolute resolved path: {request.opencode_executable}",
            ),
        )
    try:
        print(f"[OPENCODE_RUNTIME] Starting OpenCode server at "
              f"{request.server_url}...", flush=True)
        ref = ports.lifecycle.start(_serve_argv(request), env=child_env, cwd=request.work_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        return RuntimeOutcome(
            readiness_fact=fact, ownership=ServerOwnership.NONE,
            server_url=request.server_url, env_patch=env_patch,
            failure_kind=FailureKind.OPENCODE_RUNTIME,
            failure_detail=_bounded(f"failed to start server: {exc}"),
        )
    try:
        wait_fact = _poll_until_ready(request, ports, child_env, ref)
        if wait_fact in READY_FACTS:
            print(f"[OPENCODE_RUNTIME] Server ready", flush=True)
            return RuntimeOutcome(
                readiness_fact=wait_fact, ownership=ServerOwnership.OWNED,
                server_url=request.server_url, env_patch=env_patch,
                owned_handle=OwnedServerHandle(
                    lifecycle=ports.lifecycle, ref=ref, serve_argv=tuple(_serve_argv(request)),
                ),
            )
    except BaseException:
        try:
            _ = safe_stop(ports.lifecycle, ref)
        except BaseException:
            pass
        raise
    _, stop_detail = safe_stop(ports.lifecycle, ref)
    print(f"[OPENCODE_RUNTIME] Server failed to become ready: {wait_fact.value}", flush=True)
    return RuntimeOutcome(
        readiness_fact=wait_fact, ownership=ServerOwnership.OWNED,
        server_url=request.server_url, env_patch=env_patch,
        failure_kind=FailureKind.OPENCODE_RUNTIME,
        failure_detail=SafeDetail(f"owned server started but readiness={wait_fact.value}"),
        diagnostics=(stop_detail,),
    )
