from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union
from typing_extensions import TypeAlias, override

from .finalization_contract import RunArtifactUpdate
from .models import FinalizationStage, RunArtifacts
from .artifact_paths import (
    ArtifactPathKind,
    ArtifactReceipt,
    ReportSnapshot,
    SidecarValidationError,
    validate_path_receipt,
)


@dataclass(frozen=True)
class ArtifactProvenanceError(ValueError):
    path: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"


ArtifactReceiptError: TypeAlias = Union[SidecarValidationError, ArtifactProvenanceError]


@dataclass(frozen=True)
class ArtifactReceiptUpdate:
    artifact_dir: ArtifactReceipt | None = None
    telemetry: tuple[tuple[str, ArtifactReceipt], ...] = ()
    before_snapshot: ArtifactReceipt | None = None
    after_snapshot: ArtifactReceipt | None = None
    entry_script: str | None = None


@dataclass(frozen=True)
class ArtifactReceipts:
    artifact_dir: ArtifactReceipt | None = None
    telemetry: tuple[tuple[str, ArtifactReceipt], ...] = ()
    before_snapshot: ArtifactReceipt | None = None
    after_snapshot: ArtifactReceipt | None = None
    entry_script: str | None = None

    def overlay(self, update: ArtifactReceiptUpdate) -> ArtifactReceipts:
        telemetry = dict(self.telemetry)
        telemetry.update(update.telemetry)
        return ArtifactReceipts(
            artifact_dir=update.artifact_dir or self.artifact_dir,
            telemetry=tuple(telemetry.items()),
            before_snapshot=update.before_snapshot or self.before_snapshot,
            after_snapshot=update.after_snapshot or self.after_snapshot,
            entry_script=update.entry_script or self.entry_script,
        )

    def to_artifacts(self) -> RunArtifacts:
        return RunArtifacts(
            artifact_dir=self.artifact_dir.path if self.artifact_dir else None,
            telemetry_paths=tuple(
                (key, receipt.path) for key, receipt in self.telemetry
            ),
            before_snapshot_path=self.before_snapshot.path
            if self.before_snapshot
            else None,
            after_snapshot_path=self.after_snapshot.path
            if self.after_snapshot
            else None,
            entry_script=self.entry_script,
        )


@dataclass(frozen=True)
class ArtifactReceiptValidation:
    receipts: ArtifactReceipts
    errors: tuple[ArtifactReceiptError, ...]


@dataclass(frozen=True)
class ArtifactUpdateValidation:
    update: ArtifactReceiptUpdate
    errors: tuple[ArtifactReceiptError, ...]


def validate_initial_artifacts(
    report_dir: Path,
    artifacts: RunArtifacts,
) -> ArtifactReceiptValidation:
    update = RunArtifactUpdate(
        artifact_dir=artifacts.artifact_dir,
        telemetry_paths=artifacts.telemetry_paths,
        before_snapshot_path=artifacts.before_snapshot_path,
        after_snapshot_path=artifacts.after_snapshot_path,
        entry_script=artifacts.entry_script,
    )
    validation = validate_artifact_update(
        report_dir, update, None, FinalizationStage.INITIAL_ARTIFACTS
    )
    receipts = ArtifactReceipts().overlay(validation.update)
    return ArtifactReceiptValidation(receipts, validation.errors)


def validate_artifact_update(
    report_dir: Path,
    update: RunArtifactUpdate,
    before: ReportSnapshot | None,
    stage: FinalizationStage,
) -> ArtifactUpdateValidation:
    errors: list[ArtifactReceiptError] = []

    def accepted(
        raw_path: str | None, kind: ArtifactPathKind
    ) -> ArtifactReceipt | None:
        if raw_path is None:
            return None
        try:
            receipt = validate_path_receipt(report_dir, raw_path, kind, stage)
        except SidecarValidationError as exc:
            errors.append(exc)
            return None
        if (
            before is not None
            and before.fingerprint_for(receipt.canonical_path) == receipt.fingerprint
        ):
            errors.append(
                ArtifactProvenanceError(
                    raw_path, "artifact was not created or materially updated by hook"
                )
            )
            return None
        return receipt

    telemetry: list[tuple[str, ArtifactReceipt]] = []
    for key, raw_path in update.telemetry_paths:
        receipt = accepted(raw_path, ArtifactPathKind.FILE)
        if receipt is not None:
            telemetry.append((key, receipt))
    for key, raw_path in update.directory_paths:
        receipt = accepted(raw_path, ArtifactPathKind.DIRECTORY)
        if receipt is not None:
            telemetry.append((key, receipt))
    return ArtifactUpdateValidation(
        ArtifactReceiptUpdate(
            artifact_dir=accepted(update.artifact_dir, ArtifactPathKind.DIRECTORY),
            telemetry=tuple(telemetry),
            before_snapshot=accepted(
                update.before_snapshot_path, ArtifactPathKind.FILE
            ),
            after_snapshot=accepted(update.after_snapshot_path, ArtifactPathKind.FILE),
            entry_script=update.entry_script,
        ),
        tuple(errors),
    )


def freeze_artifacts(
    report_dir: Path,
    receipts: ArtifactReceipts,
) -> ArtifactReceiptValidation:
    errors: list[ArtifactReceiptError] = []

    def current(receipt: ArtifactReceipt | None) -> ArtifactReceipt | None:
        if receipt is None:
            return None
        try:
            observed = validate_path_receipt(
                report_dir,
                receipt.path,
                receipt.kind,
                FinalizationStage.ARTIFACT_FREEZE,
            )
        except SidecarValidationError as exc:
            errors.append(exc)
            return None
        if (
            observed.canonical_path != receipt.canonical_path
            or observed.fingerprint != receipt.fingerprint
        ):
            errors.append(
                ArtifactProvenanceError(receipt.path, "artifact changed after claim")
            )
            return None
        return receipt

    telemetry: list[tuple[str, ArtifactReceipt]] = []
    for key, receipt in receipts.telemetry:
        valid = current(receipt)
        if valid is not None:
            telemetry.append((key, valid))
    frozen = ArtifactReceipts(
        artifact_dir=current(receipts.artifact_dir),
        telemetry=tuple(telemetry),
        before_snapshot=current(receipts.before_snapshot),
        after_snapshot=current(receipts.after_snapshot),
        entry_script=receipts.entry_script,
    )
    return ArtifactReceiptValidation(frozen, tuple(errors))
