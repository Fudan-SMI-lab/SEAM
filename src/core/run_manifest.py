from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import final

from core.artifact_store import ArtifactStore
from core.continuation_lock_identity import fsync_parent
from core.owned_directory_lock import (
    close_directory_identity,
    release_owned_directory,
)
from . import run_manifest_access as access
from .run_manifest_io import atomic_write, read_manifest, validated_payload
from .run_manifest_allocation import create_run_directory
from .run_manifest_evidence_seal import seal_evidence as _seal_evidence
from .run_manifest_lock import RunFileLock
from .run_manifest_models import (
    RUN_MANIFEST_FILENAME as RUN_MANIFEST_FILENAME,
    RUN_MANIFEST_SCHEMA as RUN_MANIFEST_SCHEMA,
    RUN_MANIFEST_SCHEMA_VERSION as RUN_MANIFEST_SCHEMA_VERSION,
    CanonicalReference as CanonicalReference,
    EvidenceDigest as EvidenceDigest,
    ManifestErrorKind as ManifestErrorKind,
    ResourceReference as ResourceReference,
    RunId as RunId,
    RunManifest as RunManifest,
    RunManifestError as RunManifestError,
    RunStorageContext as RunStorageContext,
    Sha256Digest as Sha256Digest,
    SharedWorkspaceMarker as SharedWorkspaceMarker,
)
from .run_manifest_directory import require_real_directory
from .run_manifest_inventory import digest_inventory
from .run_manifest_rollback import rollback_owned_allocation
from .run_manifest_validation import (
    manifest_error,
    require,
    validate_initial,
    validate_loaded,
    validate_parent_access,
    validate_parent_lineage,
    validate_update,
)


_ManifestPermission = access.deny_permission_reassignment


@final
class RunManifestStore(access.RunManifestHandleBase):
    __slots__: tuple[str, ...] = ()

    def __init_subclass__(cls) -> None:
        _ = cls
        raise access.RunManifestHandleError("RunManifestStore cannot be subclassed")

    def __init__(
        self,
        context: RunStorageContext,
        run_id: RunId,
        expected_workflow_digest: Sha256Digest,
    ) -> None:
        super().__init__(context, run_id, expected_workflow_digest)
        raise manifest_error(
            ManifestErrorKind.READ_ONLY,
            "manifest stores must be opened through a public factory",
        )

    @classmethod
    def create(
        cls,
        context: RunStorageContext,
        manifest: RunManifest,
        parent: RunManifestStore | None = None,
    ) -> RunManifestStore:
        if cls is not RunManifestStore:
            raise access.RunManifestHandleError("factory rejects foreign handle types")
        candidate = validated_payload(manifest)
        validate_initial(context, candidate.manifest, parent is not None)
        if parent is not None:
            parent._require_registered()
            validate_parent_access(
                context, parent._context, parent._registered_writable()
            )
            validate_parent_lineage(
                candidate.manifest,
                parent.read(),
            )
        try:
            context.authoritative_root.mkdir(parents=True, exist_ok=True)
            fsync_parent(context.authoritative_root)
        except OSError as exc:
            raise manifest_error(ManifestErrorKind.WRITE_INTERRUPTED, str(exc)) from exc
        _ = require_real_directory(
            context.authoritative_root, context.authoritative_root.parent
        )
        run_dir, run_identity = create_run_directory(
            context.authoritative_root,
            candidate.manifest.run_id,
        )
        store: RunManifestStore | None = None
        owner_content: bytes | None = None
        succeeded = False
        try:
            store = object.__new__(cls)
            store._initialize_handle(
                access.ManifestHandleIdentity(
                    context,
                    candidate.manifest.run_id,
                    candidate.manifest.workflow_digest,
                ),
            )
            owner_content = f"{id(store):x}:{secrets.token_hex(16)}".encode("ascii")
            object.__setattr__(store, "_allocation_owner", owner_content)
            try:
                flags = (
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(run_dir / ".allocation-owner", flags, 0o600)
                allocation_handle = os.fdopen(descriptor, "r+b")
                owner_created = False
                try:
                    _ = allocation_handle.write(owner_content)
                    allocation_handle.flush()
                    os.fsync(allocation_handle.fileno())
                    access.lock_owner_descriptor(allocation_handle)
                    fsync_parent(run_dir / ".allocation-owner")
                    object.__setattr__(store, "_allocation_handle", allocation_handle)
                    owner_created = True
                finally:
                    if not owner_created:
                        allocation_handle.close()
                atomic_write(store._manifest_path, candidate)
            except OSError as exc:
                raise manifest_error(
                    ManifestErrorKind.WRITE_INTERRUPTED, str(exc)
                ) from exc
            succeeded = True
            return store
        finally:
            if succeeded:
                close_directory_identity(run_identity)
            elif store is not None and owner_content is not None:
                store._revoke_write_access()
                rollback_owned_allocation(
                    run_dir,
                    store._manifest_path,
                    run_dir / ".allocation-owner",
                    owner_content,
                    run_identity,
                )
            else:
                try:
                    release_owned_directory(run_dir, run_identity)
                except OSError:
                    close_directory_identity(run_identity)

    @classmethod
    def open_readonly(
        cls,
        context: RunStorageContext,
        run_id: RunId,
        expected_workflow_digest: Sha256Digest,
    ) -> RunManifestStore:
        if cls is not RunManifestStore:
            raise access.RunManifestHandleError("factory rejects foreign handle types")
        require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(run_id)) is not None,
            ManifestErrorKind.MALFORMED,
            "run_id is not a safe namespace",
        )
        store = object.__new__(cls)
        store._initialize_handle(
            access.ManifestHandleIdentity(context, run_id, expected_workflow_digest),
        )
        _ = store.read()
        return store

    def read(self) -> RunManifest:
        self._require_registered()
        _ = require_real_directory(self._run_dir, self._context.authoritative_root)
        manifest = read_manifest(self._manifest_path)
        validate_loaded(
            manifest,
            RunId(self._run_dir.name),
            self._expected_workflow_digest,
            self._context,
        )
        if manifest.evidence_sealed:
            inventory = digest_inventory(
                self._run_dir / "sealed-artifacts", self._run_dir
            )
            require(
                inventory == manifest.sealed_evidence,
                ManifestErrorKind.EVIDENCE_MUTATION,
                "sealed evidence digest inventory changed",
            )
        return manifest

    def write(self, manifest: RunManifest) -> RunManifest:
        with self._thread_lock, RunFileLock(self._run_dir):
            require(
                self._registered_writable(),
                ManifestErrorKind.READ_ONLY,
                "manifest handle is read-only",
            )
            current = self.read()
            require(
                not current.evidence_sealed,
                ManifestErrorKind.SEALED,
                "sealed manifests are immutable",
            )
            require(
                not (self._run_dir / "sealed-artifacts").exists(),
                ManifestErrorKind.SEALED,
                "a seal transition is pending",
            )
            candidate = validated_payload(manifest)
            validate_update(current, candidate.manifest)
            atomic_write(self._manifest_path, candidate)
            return candidate.manifest

    def seal_working_evidence(self, artifact_store: ArtifactStore) -> RunManifest:
        with self._thread_lock, RunFileLock(self._run_dir):
            current = self.read()
            if current.evidence_sealed:
                self._revoke_write_access()
                return current
            require(
                self._registered_writable(),
                ManifestErrorKind.READ_ONLY,
                "manifest handle is read-only",
            )
            workspace = Path(artifact_store.base_dir).resolve(strict=True)
            require(
                workspace == self._context.workspace_root,
                ManifestErrorKind.PARENT_MISMATCH,
                "working evidence is from another workspace",
            )
            require(
                artifact_store.run_id == str(current.run_id),
                ManifestErrorKind.PARENT_MISMATCH,
                "working evidence is from another run",
            )
            inventory = _seal_evidence(artifact_store, workspace, self._run_dir)
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "evidence_sealed": True,
                    "sealed_evidence": inventory,
                }
            )
            candidate = validated_payload(updated)
            atomic_write(self._manifest_path, candidate)
            self._revoke_write_access()
            return candidate.manifest
