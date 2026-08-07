from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.resource_manifest import (
    FactProvenance,
    FactStatus,
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
    ProvenanceFact,
    build_phase2_environment,
    capture_launcher_facts,
)


def test_facts_require_bounded_value_provenance_and_exact_namespace() -> None:
    # Given a fact from a framework probe in an exact container namespace.
    fact = ProvenanceFact(
        name="python.version",
        value="3.10.12",
        provenance=FactProvenance.FRAMEWORK_OBSERVED,
        namespace="container:cid-123",
        status=FactStatus.KNOWN,
    )

    # When the immutable record is inspected.
    payload = fact.model_dump(mode="json")

    # Then source and namespace remain first-class values.
    assert payload["provenance"] == "framework_observed"
    assert payload["namespace"] == "container:cid-123"
    with pytest.raises(ValidationError):
        _ = ProvenanceFact(
            name="python.version",
            value="x" * 1025,
            provenance=FactProvenance.CONFIGURED,
            namespace="host",
        )
    with pytest.raises(ValidationError):
        _ = ProvenanceFact.model_validate(
            {**payload, "namespace": "container:../escape"}
        )


def test_phase2_report_is_bounded_and_remains_agent_reported() -> None:
    # Given a concise Phase 2 response from the Agent.
    request = Phase2EnvironmentRequest(
        environment_id="project-venv",
        namespace="container:user-dev",
        container_id="user-dev",
        report=Phase2EnvironmentReport(
            venv_path="/workspace/.venv",
            python_path="/workspace/.venv/bin/python",
            installed_packages=("torch==2.1.0", "numpy==1.26.4"),
        ),
    )

    # When the report is captured without a framework probe.
    environment = build_phase2_environment(request)

    # Then reported values are not flattened into observed facts.
    python_facts = tuple(
        fact for fact in environment.facts if fact.name == "interpreter.sys_executable"
    )
    assert tuple(fact.provenance for fact in python_facts) == (
        FactProvenance.AGENT_REPORTED,
    )
    assert not any(
        fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        for fact in environment.facts
    )
    with pytest.raises(ValidationError):
        _ = Phase2EnvironmentReport(
            venv_path="../escape",
            python_path="/workspace/python",
            installed_packages=(),
        )
    with pytest.raises(ValidationError):
        _ = Phase2EnvironmentReport(
            venv_path="/workspace/.venv",
            python_path="/workspace/.venv/bin/python",
            installed_packages=tuple(f"pkg-{index}" for index in range(513)),
        )


def test_launcher_capture_is_bounded_and_host_observed() -> None:
    # Given a real temporary launcher working directory.
    # When launcher facts are captured by the framework.
    facts = capture_launcher_facts()

    # Then the required Python/platform/cwd facts are observed on the host.
    names = {fact.name for fact in facts}
    assert names == {
        "launcher.architecture",
        "launcher.cwd",
        "launcher.platform",
        "launcher.python_executable",
        "launcher.python_implementation",
        "launcher.python_realpath",
        "launcher.python_version",
    }
    assert all(fact.namespace == "host" for fact in facts)
    assert all(fact.provenance is FactProvenance.FRAMEWORK_OBSERVED for fact in facts)
