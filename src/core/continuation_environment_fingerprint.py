from __future__ import annotations

from .continuation_environment_manifest import (
    error,
    known_fact,
    known_framework_fact,
    required_fact,
)
from .continuation_environment_models import (
    AnchorRelation,
    ContinuationEnvironmentErrorKind,
    ContinuationEnvironmentRequest,
    EnvironmentFingerprint,
    ParentPhase2State,
)
from .resource_manifest_models import EnvironmentRecord, EnvironmentType


def _fingerprint(record: EnvironmentRecord) -> EnvironmentFingerprint:
    environment_type_value = required_fact(record.facts, "environment.type")
    try:
        environment_type = EnvironmentType(environment_type_value)
    except ValueError as exc:
        raise error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH,
            "environment.type",
            "recorded environment type is unsupported",
        ) from exc
    return EnvironmentFingerprint(
        environment_type=environment_type,
        namespace=required_fact(record.facts, "environment.namespace"),
        container_id=known_fact(
            record.facts, "environment.container_id", required=False
        ),
        interpreter_realpath=known_fact(record.facts, "interpreter.realpath", required=False),
        sys_executable=required_fact(record.facts, "interpreter.sys_executable"),
        sys_prefix=required_fact(record.facts, "interpreter.sys_prefix"),
        sys_base_prefix=known_fact(record.facts, "interpreter.sys_base_prefix", required=False),
        python_implementation=known_fact(record.facts, "python.implementation", required=False),
        python_version=known_fact(record.facts, "python.version", required=False),
        platform_system=known_fact(record.facts, "platform.system", required=False),
        platform_architecture=known_fact(record.facts, "platform.architecture", required=False),
        package_inventory_hash=known_framework_fact(
            record.facts, "packages.inventory_sha256", required=False
        ),
        interpreter_available=True,
        interpreter_executable=True,
    )


def target_environment(
    request: ContinuationEnvironmentRequest,
) -> EnvironmentFingerprint | None:
    target_id = request.target_environment_id
    if target_id is None:
        allowed = (
            request.parent_phase2_state is ParentPhase2State.FAILED_BEFORE_TARGET
            and request.anchor_relation is AnchorRelation.AT_OR_BEFORE_PHASE2
            and not request.resource_manifest.environments
            and request.observed_environment is None
        )
        if allowed:
            return None
        raise error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISSING,
            "target_environment_id",
            "continuation requires the retained target environment",
        )
    matches = tuple(
        environment
        for environment in request.resource_manifest.environments
        if environment.environment_id == target_id
    )
    if len(matches) != 1 or request.observed_environment is None:
        raise error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISSING,
            "target_environment_id",
            "recorded or observed target environment is unavailable",
        )
    recorded = _fingerprint(matches[0])
    observed = request.observed_environment
    if not observed.interpreter_available or not observed.interpreter_executable:
        raise error(
            ContinuationEnvironmentErrorKind.INTERPRETER_UNAVAILABLE,
            "interpreter.realpath",
            "recorded interpreter is missing or non-executable",
        )
    if not recorded.matches(observed):
        raise error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH,
            "environment.fingerprint",
            "live environment differs from the authenticated record",
        )
    return observed
