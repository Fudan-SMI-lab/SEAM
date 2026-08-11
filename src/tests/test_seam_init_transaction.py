"""Given/When/Then tests for owner-only config write transactions.

Covers atomic two-file commit with mode 0600 backups + targets, byte-identical
rollback on validation failure (False return or raised exception), deterministic
recovery after an injected process interruption, OMO sidecar preservation,
secret-free state.json, forbidden-path guards (``.sm-artifacts``,
``.opencode/plugins/``, ``.opencode/package.json``), and corrupted-state
typed errors. Mode-bit equality is asserted on POSIX only — Windows cannot
enforce per-user POSIX mode bits.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path

import pytest

from seam_init.config_transaction import (
    AffectedFile,
    ConfigTransaction,
    TransactionError,
    TransactionPhase,
    TransactionResult,
)

_CANARY = "sk-test-canary-0123456789abcdef"


def _is_posix() -> bool:
    return os.name == "posix"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _assert_owner_only(path: Path) -> None:
    """Assert 0600 on POSIX; on Windows per-user bits are not representable."""
    if not _is_posix():
        return
    mode = _mode(path)
    assert mode == 0o600, f"{path} mode={oct(mode)} (want 0600)"


def _opencode(tmp_path: Path) -> Path:
    cfg = tmp_path / ".opencode" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    return cfg


# --- begin + commit happy path ---------------------------------------------


class TestBeginAndCommit:
    def test_commit_two_files_atomic_success(self, tmp_path: Path) -> None:
        # Given: two existing configs and an unrelated file under .opencode/.
        cfg_a = _opencode(tmp_path)
        original_a = b'{"model": "old-a"}\n'
        _ = cfg_a.write_bytes(original_a)

        cfg_b = tmp_path / ".opencode" / "oh-my-openagent.json"
        original_b = b'{"auth": "legacy"}\n'
        _ = cfg_b.write_bytes(original_b)

        unrelated = tmp_path / ".opencode" / "unrelated.txt"
        unrelated_bytes = b"do-not-touch-me\n"
        _ = unrelated.write_bytes(unrelated_bytes)

        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg_a, cfg_b))

        # When: commit two updates atomically with a passing validator.
        result = tx.commit(
            tx_id,
            {cfg_a: b'{"model": "new-a"}\n', cfg_b: b'{"auth": "fresh"}\n'},
            validate=lambda _u: True,
        )

        # Then: committed; content correct; unrelated bytes preserved; backups gone.
        assert result.committed is True
        assert result.restored == ()
        assert cfg_a.read_bytes() == b'{"model": "new-a"}\n'
        assert cfg_b.read_bytes() == b'{"auth": "fresh"}\n'
        assert unrelated.read_bytes() == unrelated_bytes
        _assert_owner_only(cfg_a)
        _assert_owner_only(cfg_b)
        backup_root = tmp_path / ".seam-init" / "backups"
        assert not backup_root.joinpath(tx_id).exists()

    def test_state_file_is_mode_0600(self, tmp_path: Path) -> None:
        # Given / When
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"a": 1}')
        tx = ConfigTransaction(tmp_path)
        _ = tx.begin((cfg,))

        # Then
        _assert_owner_only(tmp_path / ".seam-init" / "state.json")

    def test_begin_rejects_when_incomplete_transaction_exists(
        self, tmp_path: Path
    ) -> None:
        # Given: a transaction left incomplete (mid-commit) by a prior process.
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"a": 1}')
        tx1 = ConfigTransaction(tmp_path)
        _ = tx1.begin((cfg,))

        # When / Then: a second begin without recovery is rejected.
        tx2 = ConfigTransaction(tmp_path)
        with pytest.raises(TransactionError):
            _ = tx2.begin((cfg,))


# --- validation failure rollback -------------------------------------------


class TestValidationFailureRollback:
    def test_validate_false_restores_byte_identical_originals(
        self, tmp_path: Path
    ) -> None:
        # Given: two configs with distinct original bytes.
        cfg_a = _opencode(tmp_path)
        original_a = b'{"model": "old-a", "n": 1}\n'
        _ = cfg_a.write_bytes(original_a)

        cfg_b = tmp_path / ".opencode" / "oh-my-openagent.json"
        original_b = b'{"model": "old-b", "n": 2}\n'
        _ = cfg_b.write_bytes(original_b)

        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg_a, cfg_b))

        # When: validate returns False.
        def reject(_updates: Mapping[Path, bytes]) -> bool:
            return False

        result = tx.commit(
            tx_id,
            {cfg_a: b'{"model": "new"}', cfg_b: b'{"model": "newer"}'},
            validate=reject,
        )

        # Then: rollback restored byte-identical originals.
        assert result.committed is False
        assert cfg_a.read_bytes() == original_a
        assert cfg_b.read_bytes() == original_b
        rel_a = cfg_a.relative_to(tmp_path).as_posix()
        rel_b = cfg_b.relative_to(tmp_path).as_posix()
        assert set(result.restored) == {rel_a, rel_b}

    def test_validate_exception_triggers_rollback(self, tmp_path: Path) -> None:
        # Given
        cfg = _opencode(tmp_path)
        original = b'{"model": "keep"}'
        _ = cfg.write_bytes(original)
        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg,))

        # When: validate raises.
        def explode(_updates: Mapping[Path, bytes]) -> bool:
            raise RuntimeError("schema error")

        result = tx.commit(tx_id, {cfg: b'{"model": "wrong"}'}, validate=explode)

        # Then
        assert result.committed is False
        assert cfg.read_bytes() == original


# --- recovery --------------------------------------------------------------


class TestRecovery:
    def test_recover_interrupted_returns_none_when_clean(
        self, tmp_path: Path
    ) -> None:
        # Given: no .seam-init directory.
        tx = ConfigTransaction(tmp_path)
        # When
        result = tx.recover_interrupted()
        # Then
        assert result is None

    def test_recover_after_injected_interruption_restores_originals(
        self, tmp_path: Path
    ) -> None:
        # Given: an existing config plus OMO migration sidecar.
        cfg = _opencode(tmp_path)
        original_cfg = b'{"model": "old"}\n'
        _ = cfg.write_bytes(original_cfg)

        sidecar = (
            tmp_path / ".opencode" / "oh-my-openagent.json.migrations.json"
        )
        sidecar_original = b'{"appliedMigrations": []}\n'
        _ = sidecar.write_bytes(sidecar_original)

        # Begin a transaction (backs up originals, marks phase committing).
        tx1 = ConfigTransaction(tmp_path)
        _ = tx1.begin((cfg,))

        # When: process is "killed" mid-commit — partial write to cfg, and
        # the OMO plugin meanwhile wrote a new migration to the sidecar.
        _ = cfg.write_bytes(b'{"model": "PARTIAL-CRASH-STATE"}')
        _ = sidecar.write_bytes(
            b'{"appliedMigrations": ["added-during-crash"]}\n'
        )

        # A new process instance detects the incomplete transaction + restores.
        tx2 = ConfigTransaction(tmp_path)
        result = tx2.recover_interrupted()

        # Then: deterministic restore of both files from the 0600 backups.
        assert result is not None
        assert result.committed is False
        assert cfg.read_bytes() == original_cfg
        assert sidecar.read_bytes() == sidecar_original
        rel_cfg = cfg.relative_to(tmp_path).as_posix()
        rel_side = sidecar.relative_to(tmp_path).as_posix()
        assert set(result.restored) == {rel_cfg, rel_side}

    def test_recover_after_commit_returns_none(self, tmp_path: Path) -> None:
        # Given: a fully committed transaction.
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"a": 1}')
        tx1 = ConfigTransaction(tmp_path)
        tx_id = tx1.begin((cfg,))
        _ = tx1.commit(tx_id, {cfg: b'{"a": 2}'})

        # When: next session recovers.
        tx2 = ConfigTransaction(tmp_path)
        result = tx2.recover_interrupted()

        # Then: nothing to recover; committed state preserved.
        assert result is None
        assert cfg.read_bytes() == b'{"a": 2}'

    def test_recover_after_rolled_back_returns_none(self, tmp_path: Path) -> None:
        # Given: a transaction that rolled back.
        cfg = _opencode(tmp_path)
        original = b'{"a": 1}'
        _ = cfg.write_bytes(original)
        tx1 = ConfigTransaction(tmp_path)
        tx_id = tx1.begin((cfg,))
        _ = tx1.commit(tx_id, {cfg: b'{"a": 2}'}, validate=lambda _u: False)

        # When: next session recovers.
        tx2 = ConfigTransaction(tmp_path)
        result = tx2.recover_interrupted()

        # Then: nothing to recover; rollback state preserved.
        assert result is None
        assert cfg.read_bytes() == original


# --- secret hygiene --------------------------------------------------------


class TestSecretHygiene:
    def test_state_json_has_no_plaintext_secret(self, tmp_path: Path) -> None:
        # Given: a config whose CONTENT contains a canary secret.
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"apiKey": "' + _CANARY.encode() + b'"}')

        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg,))

        # When
        state_bytes = (
            tmp_path / ".seam-init" / "state.json"
        ).read_bytes()

        # Then: state.json carries hashes + paths only — never plaintext key.
        assert _CANARY.encode() not in state_bytes
        state = json.loads(state_bytes)
        assert state["transaction_id"] == tx_id
        assert "affected" in state
        for entry in state["affected"]:
            assert "original_sha256" in entry
            assert "original_size" in entry
            assert _CANARY not in entry["relative_path"]
            assert _CANARY not in entry["original_sha256"]

        # Cleanup so the test session leaves no incomplete tx.
        _ = tx.commit(tx_id, {cfg: b"{}"})


# --- forbidden paths -------------------------------------------------------


class TestForbiddenPaths:
    @pytest.mark.parametrize(
        "forbidden_rel",
        [
            ".sm-artifacts/run1/x.json",
            ".opencode/plugins/foo.txt",
            ".opencode/package.json",
        ],
    )
    def test_begin_rejects_forbidden_path(
        self, tmp_path: Path, forbidden_rel: str
    ) -> None:
        # Given
        target = tmp_path / forbidden_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(b"{}")

        tx = ConfigTransaction(tmp_path)
        # When / Then
        with pytest.raises(TransactionError):
            _ = tx.begin((target,))

    def test_commit_never_touches_forbidden_paths(self, tmp_path: Path) -> None:
        # Given: pre-existing forbidden paths with sentinel content.
        sm_dir = tmp_path / ".sm-artifacts"
        _ = sm_dir.mkdir(parents=True)
        sm_file = sm_dir / "checkpoint.json"
        sm_before = b'{"run": "x"}'
        _ = sm_file.write_bytes(sm_before)

        plugins_dir = tmp_path / ".opencode" / "plugins"
        _ = plugins_dir.mkdir(parents=True)
        plugin_file = plugins_dir / "my-plugin.txt"
        plugin_before = b"plugin-data"
        _ = plugin_file.write_bytes(plugin_before)

        pkg_dir = tmp_path / ".opencode"
        pkg = pkg_dir / "package.json"
        pkg_before = b'{"name": "no-touch"}'
        _ = pkg.write_bytes(pkg_before)

        # And a legitimate update target.
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"a": 1}')

        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg,))
        result = tx.commit(tx_id, {cfg: b'{"a": 2}'})

        # Then: forbidden paths are untouched.
        assert result.committed is True
        assert sm_file.read_bytes() == sm_before
        assert plugin_file.read_bytes() == plugin_before
        assert pkg.read_bytes() == pkg_before


# --- OMO sidecar backup ----------------------------------------------------


class TestSidecarBackup:
    def test_omo_sidecar_backed_up_alongside_configs(self, tmp_path: Path) -> None:
        # Given: a config + the OMO migration sidecar.
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"model": "old"}')

        sidecar = (
            tmp_path / ".opencode" / "oh-my-openagent.json.migrations.json"
        )
        sidecar_bytes = b'{"appliedMigrations": ["m1"]}\n'
        _ = sidecar.write_bytes(sidecar_bytes)

        # When: begin() runs.
        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg,))
        backup_dir = tmp_path / ".seam-init" / "backups" / tx_id

        # Then: sidecar backup exists with byte-identical content + 0600.
        sidecar_backup = backup_dir / sidecar.relative_to(tmp_path)
        assert sidecar_backup.is_file()
        assert sidecar_backup.read_bytes() == sidecar_bytes
        _assert_owner_only(sidecar_backup)

        # Cleanup.
        _ = tx.commit(tx_id, {cfg: b'{"model": "old"}'})

    def test_backup_files_are_mode_0600(self, tmp_path: Path) -> None:
        # Given: a config whose content carries an obvious plaintext key.
        cfg = _opencode(tmp_path)
        _ = cfg.write_bytes(b'{"apiKey": "sk-leak-me"}')

        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((cfg,))

        # When
        backup_dir = tmp_path / ".seam-init" / "backups" / tx_id
        files = [p for p in backup_dir.rglob("*") if p.is_file()]

        # Then: every backup file is owner-only.
        assert files, "expected at least one backup file"
        for path in files:
            _assert_owner_only(path)

        # Cleanup.
        _ = tx.commit(tx_id, {cfg: b"{}"})


# --- corrupted / adversarial state ----------------------------------------


class TestCorruptedState:
    def test_corrupted_state_json_raises_typed_error(self, tmp_path: Path) -> None:
        # Given: a malformed state.json.
        state_dir = tmp_path / ".seam-init"
        _ = state_dir.mkdir(parents=True)
        _ = (state_dir / "state.json").write_text("{not-json", encoding="utf-8")

        tx = ConfigTransaction(tmp_path)
        # When / Then
        with pytest.raises(TransactionError):
            _ = tx.recover_interrupted()

    def test_missing_backup_during_recovery_raises_typed_error(
        self, tmp_path: Path
    ) -> None:
        # Given: state.json references a backup that does not exist.
        state_dir = tmp_path / ".seam-init"
        _ = state_dir.mkdir(parents=True)
        fake_state = {
            "transaction_id": "deadbeef",
            "phase": TransactionPhase.COMMITTING.value,
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "affected": [
                {
                    "relative_path": ".opencode/config.json",
                    "original_sha256": "0" * 64,
                    "original_size": 0,
                }
            ],
        }
        _ = (state_dir / "state.json").write_text(
            json.dumps(fake_state), encoding="utf-8"
        )

        tx = ConfigTransaction(tmp_path)
        # When / Then: rollback cannot complete without the backup bytes.
        with pytest.raises(TransactionError):
            _ = tx.recover_interrupted()


# --- invariants on data model ---------------------------------------------


class TestDataModel:
    def test_phase_values_are_terminal_or_recoverable(self) -> None:
        # Then: every phase value is one of the three documented states.
        assert {p.value for p in TransactionPhase} == {
            "committing", "committed", "rolled_back",
        }

    def test_affected_file_is_frozen_and_slots(self) -> None:
        # Given
        af = AffectedFile(
            relative_path=".opencode/config.json",
            original_sha256="abc",
            original_size=42,
        )
        # Then
        assert not hasattr(af, "__dict__")

    def test_transaction_result_is_frozen_and_slots(self) -> None:
        # Given
        from seam_init.models import SafeDetail

        result = TransactionResult(
            transaction_id="deadbeef",
            committed=True,
            restored=(),
            safe_detail=SafeDetail("ok"),
        )
        # Then
        assert not hasattr(result, "__dict__")
        assert _CANARY not in result.safe_detail


# --- absent target creation + rollback (Task 10 defect-1 fix) ------------


class TestAbsentTargetHandling:
    """ConfigTransaction natively records absent targets and restores
    absence on rollback/recovery — no unowned placeholder, no heuristic
    cleanup that could delete a legitimate user file."""

    def test_begin_with_absent_target_does_not_raise(self, tmp_path: Path) -> None:
        tx = ConfigTransaction(tmp_path)
        target = tmp_path / "new-config.json"
        tx_id = tx.begin((target,))
        assert isinstance(tx_id, str)

    def test_commit_creates_originally_absent_target(self, tmp_path: Path) -> None:
        tx = ConfigTransaction(tmp_path)
        target = tmp_path / "subdir" / "new.json"
        assert not target.exists()
        tx_id = tx.begin((target,))
        result = tx.commit(tx_id, {target: b'{"ok":true}'})
        assert result.committed is True
        assert target.read_bytes() == b'{"ok":true}'

    def test_validate_failure_restores_absence_for_fresh_target(
        self, tmp_path: Path,
    ) -> None:
        tx = ConfigTransaction(tmp_path)
        target = tmp_path / "fresh.json"
        assert not target.exists()
        tx_id = tx.begin((target,))
        result = tx.commit(tx_id, {target: b"{}"}, validate=lambda _: False)
        assert result.committed is False
        assert not target.exists()

    def test_recovery_restores_absence_after_partial_write(
        self, tmp_path: Path,
    ) -> None:
        tx1 = ConfigTransaction(tmp_path)
        target = tmp_path / "recovery.json"
        _ = tx1.begin((target,))
        target.write_bytes(b'{"partial":true}')
        assert target.exists()
        tx2 = ConfigTransaction(tmp_path)
        result = tx2.recover_interrupted()
        assert result is not None
        assert not result.committed
        assert not target.exists()

    def test_pre_existing_empty_config_survives_rollback(
        self, tmp_path: Path,
    ) -> None:
        target = tmp_path / "config.json"
        target.write_bytes(b"{}\n")
        original = target.read_bytes()
        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((target,))
        result = tx.commit(tx_id, {target: b'{"x":1}'}, validate=lambda _: False)
        assert not result.committed
        assert target.read_bytes() == original
