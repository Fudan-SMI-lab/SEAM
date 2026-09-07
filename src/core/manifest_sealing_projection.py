"""Atomic, outcome-neutral projection of the sealing result into summary.json.

This module is the only writer of the optional ``manifest_sealing`` field in
``summary.json``. It reads the authoritative summary written by the ordinary
finalizer, attaches a bounded projection (status, sidecar path,
``continuation_eligible``), and rewrites the file atomically via the
repository's owned-file atomic helper.

The projection is never result authority. Any read/parse/write fault is
recorded in the returned typed outcome and logged independently; the
original summary's ``overall_status`` and authority are never mutated and
the caller's frozen E2E outcome/exit code are never rewritten.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from core.atomic_file import atomic_write_bytes
from core.manifest_sealing_models import ManifestSealingResult

logger = logging.getLogger("core.manifest_sealing_projection")

_MANIFEST_SEALING_KEY = "manifest_sealing"
_MAX_DETAIL_CHARS = 256
_SUMMARY_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


@unique
class ManifestSealingProjectionStatus(str, Enum):
    """The three projection dispositions, all outcome-neutral."""

    PROJECTED = "projected"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class ManifestSealingProjectionOutcome:
    """The typed outcome-neutral result of a single projection attempt."""

    status: ManifestSealingProjectionStatus
    detail: str

    def __post_init__(self) -> None:
        if len(self.detail) > _MAX_DETAIL_CHARS:
            object.__setattr__(
                self, "detail", self.detail[:_MAX_DETAIL_CHARS] + "[truncated]"
            )


def project_manifest_sealing_into_summary(
    *,
    summary_path: Path | None,
    result: ManifestSealingResult,
) -> ManifestSealingProjectionOutcome:
    """Project the sealing result into ``summary.json`` atomically.

    Returns a typed outcome; never raises. A projection fault is recorded
    in the returned outcome and logged, but does not mutate the original
    summary authority or the caller's frozen E2E result.
    """
    if summary_path is None or not summary_path.exists():
        return ManifestSealingProjectionOutcome(
            status=ManifestSealingProjectionStatus.SKIPPED,
            detail="summary.json absent; projection skipped",
        )
    try:
        payload_text = summary_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _failed(f"summary read failed: {type(exc).__name__}: {exc}")
    try:
        payload = _SUMMARY_ADAPTER.validate_json(payload_text.encode("utf-8"))
    except ValidationError as exc:
        return _failed(f"summary parse failed: {type(exc).__name__}: {exc}")
    payload[_MANIFEST_SEALING_KEY] = _projection_payload(result)
    try:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        atomic_write_bytes(
            summary_path, serialized.replace("\n", os.linesep).encode("utf-8")
        )
    except OSError as exc:
        return _failed(f"summary write failed: {type(exc).__name__}: {exc}")
    return ManifestSealingProjectionOutcome(
        status=ManifestSealingProjectionStatus.PROJECTED,
        detail="",
    )


def _projection_payload(result: ManifestSealingResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "sidecar_path": str(result.sidecar_path),
        "continuation_eligible": result.continuation_eligible,
    }


def _failed(detail: str) -> ManifestSealingProjectionOutcome:
    logger.warning("Manifest sealing projection failed: %s", detail)
    return ManifestSealingProjectionOutcome(
        status=ManifestSealingProjectionStatus.FAILED,
        detail=detail,
    )


__all__ = (
    "ManifestSealingProjectionOutcome",
    "ManifestSealingProjectionStatus",
    "project_manifest_sealing_into_summary",
)
