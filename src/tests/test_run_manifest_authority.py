from __future__ import annotations

import copy
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import core.run_manifest as run_manifest
import core.run_manifest_access as manifest_access
from core.run_manifest import ManifestErrorKind, RunManifestError, RunManifestStore
from core.run_manifest_access import (
    ManifestHandleIdentity,
    RunManifestHandleBase,
    RunManifestHandleError,
)
from tests.authority_boundary_attack_support import reclassify_manifest_reader
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
    try:
        object.__setattr__(reader, "_writable", True)
    except (AttributeError, RunManifestHandleError) as error:
        _ = error
    current = reader.read()
    updated = current.model_copy(update={"revision": current.revision + 1})

    with pytest.raises(RunManifestError) as rejected:
        _ = reader.write(updated)

    assert rejected.value.kind is ManifestErrorKind.READ_ONLY


def test_unrelated_module_cannot_reclassify_readonly_manifest_handle(
    tmp_path: Path,
) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    current = writer.read()
    reader = RunManifestStore.open_readonly(
        context, current.run_id, current.workflow_digest
    )
    updated = current.model_copy(update={"revision": current.revision + 1})

    with pytest.raises(RunManifestError) as rejected:
        _ = reclassify_manifest_reader(reader, updated)

    assert rejected.value.kind is ManifestErrorKind.READ_ONLY
    assert writer.read().revision == current.revision


def test_caller_cannot_initialize_writable_manifest_handle(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    manifest = root_manifest(context)
    forged = object.__new__(RunManifestStore)
    initializer = getattr(forged, "_initialize_handle")

    with pytest.raises((TypeError, RunManifestHandleError)):
        initializer(
            ManifestHandleIdentity(context, manifest.run_id, manifest.workflow_digest),
            True,
        )


def test_manifest_write_capability_is_not_caller_addressable(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    current = writer.read()
    forged = object.__new__(RunManifestStore)
    initializer = getattr(forged, "_initialize_handle")

    assert not hasattr(manifest_access, "_WRITE_CAPABILITY")
    assert not hasattr(manifest_access, "_take_write_capability")
    assert not hasattr(manifest_access, "take_write_capability")
    assert not hasattr(manifest_access, "ManifestWriteCapability")
    assert not hasattr(forged, "_register_writer")
    initializer(
        ManifestHandleIdentity(context, current.run_id, current.workflow_digest),
    )
    with pytest.raises(RunManifestError) as rejected:
        _ = forged.write(current.model_copy(update={"revision": current.revision + 1}))
    assert rejected.value.kind is ManifestErrorKind.READ_ONLY


def test_readonly_handle_exposes_no_write_authority_issuer(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    current = writer.read()
    reader = RunManifestStore.open_readonly(
        context, current.run_id, current.workflow_digest
    )
    assert not hasattr(reader, "_create_write_access")

    with pytest.raises(RunManifestError) as rejected:
        _ = reader.write(current.model_copy(update={"revision": current.revision + 1}))

    assert rejected.value.kind is ManifestErrorKind.READ_ONLY


def test_allocation_owner_bytes_cannot_promote_reader(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    current = writer.read()
    reader = RunManifestStore.open_readonly(
        context, current.run_id, current.workflow_digest
    )
    owner = (
        context.authoritative_root / str(current.run_id) / ".allocation-owner"
    ).read_bytes()
    object.__setattr__(reader, "_allocation_owner", owner)

    with pytest.raises(RunManifestError) as rejected:
        _ = reader.write(current.model_copy(update={"revision": current.revision + 1}))

    assert rejected.value.kind is ManifestErrorKind.READ_ONLY


def test_rewritten_allocation_owner_cannot_promote_reader(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    current = writer.read()
    reader = RunManifestStore.open_readonly(
        context, current.run_id, current.workflow_digest
    )
    forged = f"{id(reader):x}:forged-owner".encode("ascii")
    owner_path = context.authoritative_root / str(current.run_id) / ".allocation-owner"
    _ = owner_path.write_bytes(forged)
    object.__setattr__(reader, "_allocation_owner", forged)

    with pytest.raises(RunManifestError) as rejected:
        _ = reader.write(current.model_copy(update={"revision": current.revision + 1}))

    assert rejected.value.kind is ManifestErrorKind.READ_ONLY


def test_access_first_import_exposes_no_manifest_issuer() -> None:
    script = """
import inspect
import core.run_manifest_access as access
assert not hasattr(access, 'take_write_capability')
assert not hasattr(access, 'ManifestWriteCapability')
from core.run_manifest import RunManifestStore
assert tuple(inspect.signature(RunManifestStore.create).parameters) == (
    'context', 'manifest', 'parent'
)
assert not RunManifestStore.create.__func__.__kwdefaults__
"""

    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_manifest_factory_methods_expose_no_authority_closure() -> None:
    create = RunManifestStore.create.__func__
    open_readonly = RunManifestStore.open_readonly.__func__

    assert create.__closure__ is None
    assert open_readonly.__closure__ is None
    assert inspect.signature(RunManifestStore.create).return_annotation in {
        "RunManifestStore",
        RunManifestStore,
    }
    assert inspect.signature(RunManifestStore.open_readonly).return_annotation in {
        "RunManifestStore",
        RunManifestStore,
    }


def test_caller_cannot_subclass_manifest_store() -> None:
    with pytest.raises(TypeError, match="cannot be subclassed"):
        _ = type("ForgedManifestStore", (RunManifestStore,), {})


def test_factory_function_rejects_foreign_handle_class(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    foreign = type("ForeignManifestHandle", (RunManifestHandleBase,), {})
    create = RunManifestStore.create.__func__

    with pytest.raises(RunManifestHandleError, match="foreign handle types"):
        _ = create(foreign, context, root_manifest(context))
