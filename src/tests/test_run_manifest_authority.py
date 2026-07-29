from __future__ import annotations

import copy
from pathlib import Path

import pytest

import core.run_manifest as run_manifest
from core.run_manifest import ManifestErrorKind, RunManifestError, RunManifestStore
from tests.run_manifest_test_support import root_manifest, storage_context


@pytest.mark.parametrize(
    "name",
    ["_ConstructionAuthority", "_CONSTRUCTION_AUTHORITY", "_StoreState"],
)
def test_run_manifest_exposes_no_construction_primitive(name: str) -> None:
    assert not hasattr(run_manifest, name)


def test_shallow_copy_cannot_duplicate_writable_store(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))

    with pytest.raises(TypeError, match="cannot be copied"):
        _ = copy.copy(writer)


def test_readonly_flag_mutation_does_not_grant_write(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    reader = RunManifestStore.open_readonly(
        context, writer.read().run_id, writer.read().workflow_digest
    )
    object.__setattr__(reader, "_writable", True)
    current = reader.read()
    updated = current.model_copy(update={"revision": current.revision + 1})

    with pytest.raises(RunManifestError) as rejected:
        _ = reader.write(updated)

    assert rejected.value.kind is ManifestErrorKind.READ_ONLY
