"""Runner integration for the outcome-neutral direct-run manifest sealing.

This module is the single place the direct-run sealing result is composed
with its independent observability channels: the typed in-memory result
from :mod:`core.manifest_sealing`, the ``manifest-sealing.v1.json``
sidecar (written by the service), the ``Manifest sealing:`` console line,
and the bounded projection into ``summary.json``.

It is invoked AFTER the ordinary finalization result is frozen so the E2E
PASS/FAIL headline, ``RunOutcome``, and pre-sealing exit code are never
mutated by any sealing disposition or any observability fault. The runner
snapshots the finalization exit code before calling
:func:`run_direct_manifest_sealing` and returns exactly that code
afterward, ignoring every field of the returned report for control flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.atomic_file import atomic_write_bytes
from core.compat import assert_never
from core.manifest_sealing import record_not_requested, seal_root_manifest
from core.manifest_sealing_models import (
    MANIFEST_SEALING_FILENAME,
    ManifestSealingError,
    ManifestSealingErrorKind,
    ManifestSealingFaultHooks,
    ManifestSealingResult,
    ManifestSealingStatus,
)
from core.manifest_sealing_projection import (
    ManifestSealingProjectionOutcome,
    project_manifest_sealing_into_summary,
)
from core.run_outcome import TerminalAnchor

logger = logging.getLogger("core.manifest_sealing_runner")


@dataclass(frozen=True)
class DirectManifestSealingReport:
    """The complete outcome-neutral sealing report returned to the runner."""

    result: ManifestSealingResult
    projection: ManifestSealingProjectionOutcome


def run_direct_manifest_sealing(
    *,
    seal_requested: bool,
    is_continuation: bool,
    report_dir: Path,
    run_id: str,
    project_dir: Path | None,
    workflow_path: Path | None,
    artifact_store: ArtifactStore | None,
    terminal_anchor: TerminalAnchor,
    summary_path: Path | None,
    hooks: ManifestSealingFaultHooks | None = None,
) -> DirectManifestSealingReport:
    """Run direct-run sealing and emit its independent observability.

    Produces an in-memory result for every disposition (``not_requested``,
    ``succeeded``, ``failed``), ensures the sidecar is written, emits
    exactly one ``Manifest sealing:`` console line after the E2E
    headline, and projects into ``summary.json``. The frozen E2E outcome
    is never mutated and any observability fault stays independently
    observable without ever reaching ``RunOutcome``.
    """
    result = _resolve_sealing_result(
        seal_requested=seal_requested,
        is_continuation=is_continuation,
        report_dir=report_dir,
        run_id=run_id,
        project_dir=project_dir,
        workflow_path=workflow_path,
        artifact_store=artifact_store,
        terminal_anchor=terminal_anchor,
        hooks=hooks,
    )
    _emit_manifest_sealing_line(result)
    projection = project_manifest_sealing_into_summary(
        summary_path=summary_path,
        result=result,
    )
    return DirectManifestSealingReport(result=result, projection=projection)


def _resolve_sealing_result(
    *,
    seal_requested: bool,
    is_continuation: bool,
    report_dir: Path,
    run_id: str,
    project_dir: Path | None,
    workflow_path: Path | None,
    artifact_store: ArtifactStore | None,
    terminal_anchor: TerminalAnchor,
    hooks: ManifestSealingFaultHooks | None,
) -> ManifestSealingResult:
    if not seal_requested or is_continuation:
        return record_not_requested(report_dir=report_dir, run_id=run_id)
    if project_dir is None or workflow_path is None:
        return _record_failed_inputs_unavailable(
            report_dir=report_dir, run_id=run_id
        )
    return seal_root_manifest(
        report_dir=report_dir,
        run_id=run_id,
        project_dir=project_dir,
        workflow_path=workflow_path,
        artifact_store=artifact_store,
        terminal_anchor=terminal_anchor,
        hooks=hooks,
    )


def _record_failed_inputs_unavailable(
    *,
    report_dir: Path,
    run_id: str,
) -> ManifestSealingResult:
    sidecar_path = report_dir / MANIFEST_SEALING_FILENAME
    result = ManifestSealingResult(
        status=ManifestSealingStatus.FAILED,
        requested=True,
        continuation_eligible=False,
        run_id=run_id,
        sidecar_path=sidecar_path,
        error=ManifestSealingError(
            ManifestSealingErrorKind.SEALED_INPUTS_UNAVAILABLE,
            "direct-run seal requested but required runner inputs (project_dir/workflow_path) are unavailable",
        ),
    )
    try:
        atomic_write_bytes(sidecar_path, result.to_sidecar_json().encode("utf-8"))
    except OSError:
        logger.debug(
            "Sidecar write suppressed for unavailable-inputs sealing fault",
            exc_info=True,
        )
    return result


def _emit_manifest_sealing_line(result: ManifestSealingResult) -> None:
    line = format_manifest_sealing_line(result)
    try:
        print(line)
    except OSError:
        logger.debug(
            "Manifest sealing console line suppressed by OSError", exc_info=True
        )


def format_manifest_sealing_line(result: ManifestSealingResult) -> str:
    """Format the single independent ``Manifest sealing:`` observability line.

    Pure function; the caller owns stdout. Exposed so the runner and tests
    format the line identically. Exactly one user-visible line is emitted
    per run (no separate INFO log) to avoid duplicate output under the
    runner's ``logging.basicConfig(level=INFO)`` configuration.
    """
    status = result.status
    sidecar_name = result.sidecar_path.name
    if status is ManifestSealingStatus.SUCCEEDED:
        return (
            f"Manifest sealing: succeeded "
            f"(continuation_eligible=true, sidecar={sidecar_name})"
        )
    if status is ManifestSealingStatus.FAILED:
        kind = result.error.kind.value if result.error is not None else "unknown"
        return (
            f"Manifest sealing: failed "
            f"({kind}, continuation_eligible=false, sidecar={sidecar_name})"
        )
    if status is ManifestSealingStatus.NOT_REQUESTED:
        return (
            f"Manifest sealing: not_requested "
            f"(continuation_eligible=false, sidecar={sidecar_name})"
        )
    assert_never(status)


__all__ = (
    "DirectManifestSealingReport",
    "format_manifest_sealing_line",
    "run_direct_manifest_sealing",
)
