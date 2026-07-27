from __future__ import annotations

import typing
from pathlib import Path

from core.resource_manifest import (
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestStore,
)

from .finalization_contract import RunArtifactUpdate, TerminalOutcome


@typing.final
class _ResourceManifestFinalizationHook:
    __slots__ = ("_artifact_path", "_store")

    def __init__(self, store: ResourceManifestStore, artifact_path: Path) -> None:
        self._store = store
        self._artifact_path = artifact_path

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        current = self._store.read()
        _ = self._store.seal(
            expected_revision=current.revision,
            terminal_status=outcome.value,
        )
        return RunArtifactUpdate(
            telemetry_paths=(("resource_manifest_json", str(self._artifact_path)),),
        )


def resource_manifest_finalization_hook(
    store: ResourceManifestStore,
    report_dir: typing.Optional[Path] = None,
) -> typing.Callable[[TerminalOutcome], RunArtifactUpdate]:
    selected = store.context.report_dir if report_dir is None else report_dir
    try:
        canonical = selected.resolve(strict=True)
    except OSError as exc:
        raise ResourceManifestError(
            ResourceManifestErrorKind.UNSAFE_PATH,
            f"finalizer report directory is unavailable: {exc}",
        ) from exc
    if canonical != store.context.report_dir:
        raise ResourceManifestError(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "finalizer and resource manifest use different report directories",
        )
    return _ResourceManifestFinalizationHook(store, selected / store.path.name)
