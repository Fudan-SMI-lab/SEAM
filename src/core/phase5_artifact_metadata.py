from __future__ import annotations

from core.secret_redaction import redact_cli_arguments, redact_named_value
from core.phase5_attempt_receipt import (
    EnvironmentVariable,
    ShellAttemptExecution,
    ShellInvocation,
)
from harness.session.opencode_contract import JsonObject, JsonValue


def sanitized_invocation(
    execution: ShellAttemptExecution | None,
) -> ShellInvocation | None:
    if execution is None:
        return None
    return ShellInvocation(
        argv=redact_cli_arguments(execution.invocation.argv),
        environment_delta=tuple(
            EnvironmentVariable(
                name=variable.name,
                value=redact_named_value(variable.name, variable.value),
            )
            for variable in execution.invocation.environment_delta
        ),
    )


def complete_metadata(
    base: JsonObject,
    execution: ShellAttemptExecution | None,
    invocation: ShellInvocation | None,
) -> JsonObject:
    if execution is None or invocation is None:
        return dict(base)
    backend: JsonObject = {
        "kind": execution.backend.kind.value,
        "namespace": execution.backend.namespace,
        "host_cwd": execution.backend.host_cwd,
        "backend_cwd": execution.backend.backend_cwd,
        "runtime": execution.backend.runtime,
        "container_id": execution.backend.container_id,
        "container_retained": execution.backend.container_retained,
    }
    environment: list[JsonValue] = [
        {"name": variable.name, "value": variable.value}
        for variable in invocation.environment_delta
    ]
    return {
        **base,
        "attempt_id": execution.reservation.attempt_id,
        "run_id": execution.reservation.run_id,
        "reservation_nonce": execution.reservation.reservation_nonce,
        "argv": list(invocation.argv),
        "environment_delta": environment,
        "backend": backend,
        "receipt_path": execution.reservation.receipt_path,
    }
