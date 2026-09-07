from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import core.continuation_resolver as continuation_resolver
from core.continuation import ContinuationErrorKind
from core.resource_manifest import ResourceManifestContext, ResourceManifestIdentity
from tests.terminal_run_continuation_test_support import (
    claim_rejection,
    create_parent_run,
    read_json_payload,
    read_summary,
    write_summary,
)


def test_eligibility_rejects_forged_resource_authority(tmp_path: Path) -> None:
    # Given an authenticated resource manifest changed after sealing.
    parent = create_parent_run(tmp_path)
    resource_path = parent.report_dir / "resource-manifest.v1.json"
    payload = read_json_payload(resource_path)
    facts = payload["facts"]
    assert isinstance(facts, list)
    first_fact = facts[0]
    assert isinstance(first_fact, dict)
    first_fact["value"] = "forged-runtime-value"
    _ = resource_path.write_text(json.dumps(payload), encoding="utf-8")

    # When continuation eligibility is claimed.
    kind = claim_rejection(parent)

    # Then structural plausibility cannot replace Task 16 authority.
    assert kind is ContinuationErrorKind.AUTHORITY_INVALID


def test_eligibility_rejects_linked_output_project(tmp_path: Path) -> None:
    # Given a summary that names the exact project through a link or junction.
    parent = create_parent_run(tmp_path)
    alias = tmp_path / "project-alias"
    if os.name == "nt":
        _ = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(parent.project_dir)],
            check=True,
            capture_output=True,
        )
    else:
        alias.symlink_to(parent.project_dir, target_is_directory=True)
    payload = read_summary(parent)
    payload["temp_dir"] = str(alias)
    write_summary(parent, payload)

    # When continuation eligibility is claimed.
    kind = claim_rejection(parent)

    # Then canonical equivalence does not authorize an ambiguous project spelling.
    assert kind is ContinuationErrorKind.OUTPUT_PROJECT_MISMATCH


def test_resolve_uses_read_only_existing_resource_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an eligible parent and a guard against Task 16 load-or-create binding.
    parent = create_parent_run(tmp_path)

    def forbid_bind(
        _cls: type[ResourceManifestContext],
        _report_dir: Path,
        _identity: ResourceManifestIdentity,
    ) -> ResourceManifestContext:
        raise AssertionError("eligibility must not invoke load-or-create authority")

    monkeypatch.setattr(
        ResourceManifestContext,
        "bind",
        classmethod(forbid_bind),
    )

    # When the explicit parent is resolved.
    resolved = continuation_resolver.resolve_terminal_parent(parent.summary_path)

    # Then existing authority is verified without a resource-creation seam.
    assert resolved.resource_manifest.sealed is True
