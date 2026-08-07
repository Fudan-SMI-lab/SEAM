from pathlib import Path
from unittest.mock import MagicMock, patch

from core.resource_retention_finalizer import _authorized_retention_finalization
from core.resource_retention_manifest import RetentionManifestFinalizer
from tests.resource_retention_manifest_cases import _retained_store


def test_terminal_lifecycle_status_is_authenticated(tmp_path: Path) -> None:
    # Given an authorized terminal retention measurement.
    store, backend, finalizer, recorder = _retained_store(tmp_path)
    manifest_finalizer = RetentionManifestFinalizer(store, recorder, backend)
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|immutable-id\n",
            stderr="",
        )
        with _authorized_retention_finalization(finalizer):
            finalizer.run()

    # When the terminal status seals the manifest.
    _ = manifest_finalizer.persist_and_seal("passed")

    # Then continuation-authorizing lifecycle status carries process authority.
    status = tuple(
        fact for fact in store.read().facts if fact.name == "lifecycle.status"
    )[-1]
    assert status.authority_tag is not None
