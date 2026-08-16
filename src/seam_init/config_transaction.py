"""Recoverable owner-only config write transactions for seam_init."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum, unique
from pathlib import Path
from typing import final

from core.compat import TypeAlias
from core.secret_redaction import redact_sensitive_text
from seam_init.models import SafeDetail

__all__ = ["AffectedFile", "ConfigTransaction", "TransactionError",
           "TransactionId", "TransactionPhase", "TransactionResult", "TransactionState"]

TransactionId: TypeAlias = str

_OMO_SIDECAR_REL = ".opencode/oh-my-openagent.json.migrations.json"
_FORBIDDEN_RELPATHS = (
    ".sm-artifacts", ".opencode/plugins", ".opencode/package.json"
)


@unique
class TransactionPhase(str, Enum):
    COMMITTING = "committing"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class AffectedFile:
    relative_path: str
    original_sha256: str
    original_size: int


@dataclass(frozen=True, slots=True)
class TransactionState:
    transaction_id: TransactionId
    phase: TransactionPhase
    started_at: str
    updated_at: str
    affected: tuple[AffectedFile, ...]


@dataclass(frozen=True, slots=True)
class TransactionResult:
    transaction_id: TransactionId
    committed: bool
    restored: tuple[str, ...]
    safe_detail: SafeDetail


class TransactionError(Exception):
    reason: SafeDetail

    def __init__(self, *, reason: SafeDetail) -> None:
        super().__init__(str(reason))
        self.reason = reason


def _safe(raw: str) -> SafeDetail:
    return SafeDetail(redact_sensitive_text(raw))


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)
    if os.name == "posix" and stat.S_IMODE(path.lstat().st_mode) != 0o600:
        os.chmod(path, 0o600)


def _validate_relative(path: Path) -> None:
    if path.is_absolute():
        raise TransactionError(reason=_safe(f"absolute path not allowed: {path}"))
    normalized = path.as_posix()
    for forbidden in _FORBIDDEN_RELPATHS:
        if normalized == forbidden or normalized.startswith(forbidden + "/"):
            raise TransactionError(reason=_safe(f"forbidden path prefix: {normalized}"))


def _state_to_json(state: TransactionState) -> bytes:
    affected = [{"relative_path": a.relative_path,
                 "original_sha256": a.original_sha256,
                 "original_size": a.original_size} for a in state.affected]
    data = {"transaction_id": state.transaction_id, "phase": state.phase.value,
            "started_at": state.started_at, "updated_at": state.updated_at,
            "affected": affected}
    return json.dumps(data).encode("utf-8")


def _state_from_json(raw: str) -> TransactionState:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("state root must be an object")
    affected_raw = data.get("affected") or []
    if not isinstance(affected_raw, list):
        raise ValueError("affected must be a list")
    affected = tuple(
        AffectedFile(relative_path=str(i["relative_path"]),
                     original_sha256=str(i["original_sha256"]),
                     original_size=int(i["original_size"]))
        for i in affected_raw if isinstance(i, dict))
    return TransactionState(transaction_id=str(data["transaction_id"]),
                            phase=TransactionPhase(str(data["phase"])),
                            started_at=str(data.get("started_at", "")),
                            updated_at=str(data.get("updated_at", "")),
                            affected=affected)


@final
class ConfigTransaction:
    """Owner-only config write transaction with backup, rollback, recovery."""

    _root: Path
    _state_dir: Path
    _state_file: Path
    _backup_root: Path

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()
        self._state_dir = self._root / ".seam-init"
        self._state_file = self._state_dir / "state.json"
        self._backup_root = self._state_dir / "backups"

    def recover_interrupted(self) -> TransactionResult | None:
        """Restore any incomplete (COMMITTING) transaction left by a prior run."""
        if not self._state_file.is_file():
            return None
        state = self._read_state()
        if state.phase is TransactionPhase.COMMITTING:
            return self._rollback(state)
        self._delete_backup_dir(state.transaction_id)
        return None

    def begin(self, affected_paths: tuple[Path, ...]) -> TransactionId:
        """Back up affected configs plus the OMO sidecar; enter COMMITTING."""
        if self._state_file.is_file():
            existing = self._read_state()
            if existing.phase is TransactionPhase.COMMITTING:
                raise TransactionError(
                    reason=_safe("incomplete transaction exists; recover first"))
        relative_paths = [self._relative_of(p) for p in affected_paths]
        for rel in relative_paths:
            _validate_relative(Path(rel))
        if (self._root / _OMO_SIDECAR_REL).is_file() and _OMO_SIDECAR_REL not in relative_paths:
            relative_paths.append(_OMO_SIDECAR_REL)

        transaction_id = TransactionId(secrets.token_hex(8))
        backup_dir = self._backup_root / transaction_id
        started_at = _now_iso()
        affected = tuple(self._backup_one(backup_dir, rel) for rel in relative_paths)
        self._write_state(TransactionState(
            transaction_id=transaction_id, phase=TransactionPhase.COMMITTING,
            started_at=started_at, updated_at=started_at, affected=affected))
        return transaction_id

    def commit(
        self,
        transaction_id: TransactionId,
        updates: Mapping[Path, bytes],
        validate: Callable[[Mapping[Path, bytes]], bool] | None = None,
    ) -> TransactionResult:
        state = self._read_state()
        if (state.transaction_id != transaction_id
                or state.phase is not TransactionPhase.COMMITTING):
            raise TransactionError(
                reason=_safe("transaction id or phase mismatch"))
        affected_rels = {af.relative_path for af in state.affected}
        for path in updates:
            relative = self._relative_of(path)
            if relative not in affected_rels:
                raise TransactionError(
                    reason=_safe(f"path not in transaction: {relative}"))
        try:
            for path, new_bytes in updates.items():
                target = self._root / self._relative_of(path)
                _write_private(target, new_bytes)
                if target.read_bytes() != new_bytes:
                    raise TransactionError(
                        reason=_safe(f"post-rename readback mismatch: {target.name}"))
        except (OSError, TransactionError):
            return self._rollback(state)
        if validate is not None:
            try:
                ok = validate(updates)
            except Exception:
                ok = False
            if not ok:
                return self._rollback(state)
        self._write_state(replace(state, phase=TransactionPhase.COMMITTED,
                                  updated_at=_now_iso()))
        self._delete_backup_dir(state.transaction_id)
        return TransactionResult(transaction_id=state.transaction_id,
                                 committed=True, restored=(),
                                 safe_detail=_safe("transaction committed"))

    def _relative_of(self, path: Path) -> str:
        resolved = path.resolve() if path.is_absolute() else (self._root / path).resolve()
        return resolved.relative_to(self._root).as_posix()

    def _backup_one(self, backup_dir: Path, relative: str) -> AffectedFile:
        source = self._root / relative
        if not source.is_file():
            return AffectedFile(relative_path=relative, original_sha256="",
                                original_size=-1)
        original = source.read_bytes()
        _write_private(backup_dir / relative, original)
        return AffectedFile(relative_path=relative, original_sha256=_sha256(original),
                            original_size=len(original))

    def _delete_backup_dir(self, transaction_id: TransactionId) -> None:
        directory = self._backup_root / transaction_id
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
            _fsync_dir(self._backup_root)

    def _rollback(self, state: TransactionState) -> TransactionResult:
        restored: list[str] = []
        for af in state.affected:
            if af.original_size < 0:
                (self._root / af.relative_path).unlink(missing_ok=True)
                restored.append(af.relative_path)
                continue
            backup_path = self._backup_root / state.transaction_id / af.relative_path
            if not backup_path.is_file():
                raise TransactionError(reason=_safe(f"missing backup for {af.relative_path}"))
            original = backup_path.read_bytes()
            if _sha256(original) != af.original_sha256:
                raise TransactionError(reason=_safe(f"backup hash mismatch for {af.relative_path}"))
            _write_private(self._root / af.relative_path, original)
            restored.append(af.relative_path)
        self._write_state(replace(state, phase=TransactionPhase.ROLLED_BACK, updated_at=_now_iso()))
        self._delete_backup_dir(state.transaction_id)
        return TransactionResult(transaction_id=state.transaction_id, committed=False,
                                 restored=tuple(restored),
                                 safe_detail=_safe("transaction rolled back; originals restored"))

    def _write_state(self, state: TransactionState) -> None:
        _write_private(self._state_file, _state_to_json(state))

    def _read_state(self) -> TransactionState:
        if not self._state_file.is_file():
            raise TransactionError(reason=_safe("no state file"))
        try:
            return _state_from_json(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TransactionError(reason=_safe(f"malformed state: {exc}")) from exc
