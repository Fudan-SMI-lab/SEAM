"""Failing-first red proofs: Phase-2 records must not fabricate observed facts.

These characterization tests lock the *desired* Phase-2 evidence contract
for Wave-2 Todo 4 of the v1.2.1 remote-update remediation workplan:

    ``build_phase2_environment`` must record only facts the agent actually
    reported, mark every unobserved interpreter/platform value as unknown
    (``None``), keep ``phase2.base_alias`` as a derived exact key, and
    restore ``packages.inventory_sha256`` from the sorted reported package
    inventory.

At the c6cbed3 baseline ``build_phase2_environment`` fabricates several
Phase-2 facts: it aliases ``interpreter.sys_base_prefix`` to the reported
``venv_path`` (same value as ``sys_prefix``), hardcodes ``CPython`` /
``Linux`` / ``x86_64`` strings as if the agent had reported them, and
records ``packages.inventory_sha256`` as ``None`` even though the sorted
package inventory is available. Every assertion below therefore fails for
its intended contract rather than for import/setup error.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from core.execution_env_context import (
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
)
from core.resource_manifest import (
    EnvironmentRecord,
    FactProvenance,
    build_phase2_environment,
)


def _fact_value(environment: EnvironmentRecord, name: str) -> object:
    for fact in environment.facts:
        if fact.name == name:
            return fact.value
    raise AssertionError(f"missing fact {name!r} on environment record")


def _phase2_request(env_type: Literal["base_env", "venv"]) -> Phase2EnvironmentRequest:
    return Phase2EnvironmentRequest(
        environment_id="phase2-project-venv",
        namespace="container:cid-123",
        container_id="cid-123",
        report=Phase2EnvironmentReport(
            env_type=env_type,
            venv_path="/workspace/.venv",
            python_path="/workspace/.venv/bin/python3.10",
            installed_packages=("numpy==1.26.4", "torch==2.1.0"),
        ),
    )


def test_phase2_sys_base_prefix_is_not_fabricated_from_sys_prefix() -> None:
    """``interpreter.sys_base_prefix`` must remain unknown unless a probe saw it.

    Given a Phase-2 venv report where the agent supplied only ``venv_path``.
    When ``build_phase2_environment`` records the report without a probe.
    Then ``interpreter.sys_base_prefix`` must be ``None`` (no observation),
    not a copy of the reported ``sys_prefix`` value.
    """
    environment = build_phase2_environment(_phase2_request("venv"))

    sys_prefix = _fact_value(environment, "interpreter.sys_prefix")
    sys_base_prefix = _fact_value(environment, "interpreter.sys_base_prefix")

    assert sys_base_prefix is None, (
        "Phase-2 sys_base_prefix must remain unknown when no framework probe "
        "observed it; it must not alias the reported venv_path/sys_prefix."
    )
    assert sys_prefix == "/workspace/.venv"


def test_phase2_python_implementation_is_not_hardcoded() -> None:
    """``python.implementation`` must remain unknown unless a probe observed it.

    Given a Phase-2 report that did not include interpreter implementation.
    When ``build_phase2_environment`` records the report without a probe.
    Then ``python.implementation`` must be ``None`` (not the hardcoded
    string ``"CPython"``), because the agent did not observe CPython.
    """
    environment = build_phase2_environment(_phase2_request("venv"))

    implementation = _fact_value(environment, "python.implementation")

    assert implementation is None, (
        "Phase-2 python.implementation must be unknown unless a framework "
        "probe observed it; hardcoding CPython fabricates an observation."
    )


def test_phase2_platform_system_is_not_hardcoded() -> None:
    """``platform.system`` must remain unknown unless a probe observed it.

    Given a Phase-2 report that did not include platform system.
    When ``build_phase2_environment`` records the report without a probe.
    Then ``platform.system`` must be ``None`` (not the hardcoded ``Linux``
    string), because the agent did not observe the platform.
    """
    environment = build_phase2_environment(_phase2_request("venv"))

    platform_system = _fact_value(environment, "platform.system")

    assert platform_system is None, (
        "Phase-2 platform.system must be unknown unless a framework probe "
        "observed it; hardcoding Linux fabricates an observation."
    )


def test_phase2_platform_architecture_is_not_hardcoded() -> None:
    """``platform.architecture`` must remain unknown unless a probe observed it.

    Given a Phase-2 report that did not include platform architecture.
    When ``build_phase2_environment`` records the report without a probe.
    Then ``platform.architecture`` must be ``None`` (not the hardcoded
    ``x86_64`` string), because the agent did not observe the architecture.
    """
    environment = build_phase2_environment(_phase2_request("venv"))

    architecture = _fact_value(environment, "platform.architecture")

    assert architecture is None, (
        "Phase-2 platform.architecture must be unknown unless a framework "
        "probe observed it; hardcoding x86_64 fabricates an observation."
    )


def test_phase2_package_inventory_hash_is_restored_from_sorted_report() -> None:
    """``packages.inventory_sha256`` must be derived from the sorted inventory.

    Given a Phase-2 report whose ``installed_packages`` is unsorted.
    When ``build_phase2_environment`` records the report without a probe.
    Then ``packages.inventory_sha256`` must equal the SHA-256 of the
    newline-joined sorted package inventory (the deterministic value that
    a 4fe0d84-era record produced), not ``None``.
    """
    request = _phase2_request("venv")
    environment = build_phase2_environment(request)

    expected = hashlib.sha256(
        "\n".join(sorted(request.report.installed_packages)).encode()
    ).hexdigest()
    inventory_hash = _fact_value(environment, "packages.inventory_sha256")

    assert inventory_hash == expected, (
        "Phase-2 packages.inventory_sha256 must be restored from the sorted "
        "reported inventory; recording None drops a deterministically "
        "computable fact."
    )


def test_phase2_base_alias_remains_derived_for_base_env() -> None:
    """``phase2.base_alias`` must remain a derived exact key for base environments.

    Given a Phase-2 report whose ``env_type`` is ``base_env``.
    When ``build_phase2_environment`` records the report.
    Then ``phase2.base_alias`` must be present with the value ``"true"`` and
    its provenance must remain ``DERIVED`` (an exact key, not a heuristic).
    """
    environment = build_phase2_environment(_phase2_request("base_env"))

    base_alias = None
    base_alias_provenance = None
    for fact in environment.facts:
        if fact.name == "phase2.base_alias":
            base_alias = fact.value
            base_alias_provenance = fact.provenance
            break

    assert base_alias == "true"
    assert base_alias_provenance is FactProvenance.DERIVED
