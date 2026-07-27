from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.continuation_environment import (
    AnchorRelation,
    ContainerDeleteForbidden,
    ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind,
    ParentPhase2State,
    Phase2EstablishmentEligible,
    RetainedEnvironmentEligible,
    verify_continuation_environment,
)
from core.resource_manifest import EnvironmentType
from tests.continuation_environment_test_support import (
    environment_record,
    fingerprint,
    manifest,
    request,
)


@pytest.mark.parametrize("environment_type", list(EnvironmentType))
def test_exact_host_environment_is_eligible(
    tmp_path: Path,
    environment_type: EnvironmentType,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    live = fingerprint(environment_type=environment_type)
    recorded = manifest(environment=environment_record(live))

    result = verify_continuation_environment(
        request(project, resource_manifest=recorded, observed_environment=live)
    )

    assert isinstance(result, RetainedEnvironmentEligible)
    assert result.environment == live
    assert result.attachment is None
    assert isinstance(result.deletion, ContainerDeleteForbidden)


def test_phase2_may_establish_only_when_parent_never_had_target(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = verify_continuation_environment(
        request(
            project,
            resource_manifest=manifest(),
            observed_environment=None,
            phase2_state=ParentPhase2State.FAILED_BEFORE_TARGET,
            anchor_relation=AnchorRelation.AT_OR_BEFORE_PHASE2,
        )
    )

    assert isinstance(result, Phase2EstablishmentEligible)


@pytest.mark.parametrize(
    ("phase2_state", "anchor_relation"),
    [
        (ParentPhase2State.TARGET_ESTABLISHED, AnchorRelation.AT_OR_BEFORE_PHASE2),
        (ParentPhase2State.FAILED_BEFORE_TARGET, AnchorRelation.AFTER_PHASE2),
    ],
)
def test_missing_required_environment_is_rejected(
    tmp_path: Path,
    phase2_state: ParentPhase2State,
    anchor_relation: AnchorRelation,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ContinuationEnvironmentError) as raised:
        _ = verify_continuation_environment(
            request(
                project,
                resource_manifest=manifest(),
                observed_environment=None,
                phase2_state=phase2_state,
                anchor_relation=anchor_relation,
            )
        )

    assert raised.value.kind is ContinuationEnvironmentErrorKind.ENVIRONMENT_MISSING


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("namespace", "container:other"),
        ("interpreter_realpath", "/moved/python"),
        ("sys_executable", "/moved/python"),
        ("sys_prefix", "/other/.venv"),
        ("sys_base_prefix", "/other/base"),
        ("python_implementation", "PyPy"),
        ("python_version", "3.12.1"),
        ("platform_system", "Windows"),
        ("platform_architecture", "arm64"),
        ("package_inventory_hash", "f" * 64),
    ],
)
def test_environment_fingerprint_mismatch_is_rejected_before_factories(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    recorded_fingerprint = fingerprint()
    live = replace(recorded_fingerprint, **{field: replacement})
    session_factory = Mock()
    backend_factory = Mock()
    local_fallback = Mock()
    delete_container = Mock()

    with pytest.raises(ContinuationEnvironmentError) as raised:
        _ = verify_continuation_environment(
            request(
                project,
                resource_manifest=manifest(
                    environment=environment_record(recorded_fingerprint)
                ),
                observed_environment=live,
            )
        )

    assert raised.value.kind is ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH
    session_factory.assert_not_called()
    backend_factory.assert_not_called()
    local_fallback.assert_not_called()
    delete_container.assert_not_called()


@pytest.mark.parametrize(("available", "executable"), [(False, False), (True, False)])
def test_missing_or_non_executable_interpreter_is_rejected(
    tmp_path: Path,
    available: bool,
    executable: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    recorded = fingerprint(environment_type=EnvironmentType.PROJECT_VENV)
    live = replace(
        recorded,
        interpreter_available=available,
        interpreter_executable=executable,
    )

    with pytest.raises(ContinuationEnvironmentError) as raised:
        _ = verify_continuation_environment(
            request(
                project,
                resource_manifest=manifest(environment=environment_record(recorded)),
                observed_environment=live,
            )
        )

    assert raised.value.kind is ContinuationEnvironmentErrorKind.INTERPRETER_UNAVAILABLE
