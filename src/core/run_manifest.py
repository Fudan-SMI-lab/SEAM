from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path
from threading import Lock
from typing import NoReturn, final

from typing_extensions import override

from core.artifact_store import ArtifactStore
from . import run_manifest_access as access
from .run_manifest_io import atomic_write, read_manifest, validated_payload
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
from .run_manifest_paths import copy_real_tree, digest_inventory
from .run_manifest_validation import (
    manifest_error,
    require,
    validate_initial,
    validate_loaded,
    validate_parent_access,
    validate_parent_lineage,
    validate_update,
)


@final
class RunManifestStore:
    def __init__(
        self,
        context: RunStorageContext,
        run_id: RunId,
        expected_workflow_digest: Sha256Digest,
    ) -> None:
        self._initialize(context, run_id, expected_workflow_digest)
        raise manifest_error(
            ManifestErrorKind.READ_ONLY,
            "manifest stores must be opened through a public factory",
        )

    def _initialize(
        self,
        context: RunStorageContext,
        run_id: RunId,
        expected_workflow_digest: Sha256Digest,
    ) -> None:
        self._context = context
        self._run_dir = context.authoritative_root / str(run_id)
        self._manifest_path = self._run_dir / RUN_MANIFEST_FILENAME
        self._expected_workflow_digest = expected_workflow_digest
        self._thread_lock = Lock()

    def __copy__(self) -> NoReturn:
        raise access.RunManifestHandleError("RunManifestStore cannot be copied")

    def __deepcopy__(self, _memo: dict[int, RunManifestStore]) -> NoReturn:
        raise access.RunManifestHandleError("RunManifestStore cannot be copied")

    @override
    def __reduce__(self) -> NoReturn:
        raise access.RunManifestHandleError("RunManifestStore cannot be serialized")

    def _registered_writable(self) -> bool:
        return access.store_is_writable(self)

    def _require_registered(self) -> None:
        access.require_store(self)

    @classmethod
    def create(
        cls,
        context: RunStorageContext,
        manifest: RunManifest,
        parent: RunManifestStore | None = None,
    ) -> RunManifestStore:
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
        except OSError as exc:
            raise manifest_error(ManifestErrorKind.WRITE_INTERRUPTED, str(exc)) from exc
        _ = require_real_directory(
            context.authoritative_root, context.authoritative_root.parent
        )
        run_dir = context.authoritative_root / str(candidate.manifest.run_id)
        try:
            run_dir.mkdir()
        except FileExistsError as exc:
            raise manifest_error(
                ManifestErrorKind.DUPLICATE_RUN,
                f"run namespace already exists: {candidate.manifest.run_id}",
            ) from exc
        owner_token = secrets.token_hex(16)
        owner_path = run_dir / ".allocation-owner"
        store = object.__new__(cls)
        store._initialize(
            context,
            candidate.manifest.run_id,
            candidate.manifest.workflow_digest,
        )
        access.register_store(store, True)
        try:
            _ = owner_path.write_text(owner_token, encoding="ascii")
            atomic_write(store._manifest_path, candidate)
        except RunManifestError:
            store._rollback_owned_allocation(owner_path, owner_token)
            raise
        except OSError as exc:
            store._rollback_owned_allocation(owner_path, owner_token)
            raise manifest_error(ManifestErrorKind.WRITE_INTERRUPTED, str(exc)) from exc
        return store

    @classmethod
    def open_readonly(
        cls,
        context: RunStorageContext,
        run_id: RunId,
        expected_workflow_digest: Sha256Digest,
    ) -> RunManifestStore:
        require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(run_id)) is not None,
            ManifestErrorKind.MALFORMED,
            "run_id is not a safe namespace",
        )
        store = object.__new__(cls)
        store._initialize(context, run_id, expected_workflow_digest)
        access.register_store(store, False)
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
                access.register_store(self, False)
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
            sealed_dir = self._run_dir / "sealed-artifacts"
            if not sealed_dir.exists():
                self._copy_seal(artifact_store, sealed_dir)
            inventory = digest_inventory(sealed_dir, self._run_dir)
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "evidence_sealed": True,
                    "sealed_evidence": inventory,
                }
            )
            candidate = validated_payload(updated)
            atomic_write(self._manifest_path, candidate)
            access.register_store(self, False)
            return candidate.manifest

    def _copy_seal(self, artifact_store: ArtifactStore, sealed_dir: Path) -> None:
        staging = self._run_dir / f".sealed-artifacts.{secrets.token_hex(8)}.tmp"
        try:
            copy_real_tree(
                Path(artifact_store.artifact_dir), self._context.workspace_root, staging
            )
            os.rename(staging, sealed_dir)
        except RunManifestError:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        except OSError as exc:
            if staging.exists():
                shutil.rmtree(staging)
            raise manifest_error(
                ManifestErrorKind.WRITE_INTERRUPTED, f"evidence seal interrupted: {exc}"
            ) from exc

    def _rollback_owned_allocation(self, owner_path: Path, owner_token: str) -> None:
        require(
            self._registered_writable(),
            ManifestErrorKind.READ_ONLY,
            "manifest allocation rollback requires its issued writer",
        )
        try:
            owns_allocation = owner_path.read_text(encoding="ascii") == owner_token
        except FileNotFoundError:
            return
        if owns_allocation and not self._manifest_path.exists():
            shutil.rmtree(self._run_dir)
