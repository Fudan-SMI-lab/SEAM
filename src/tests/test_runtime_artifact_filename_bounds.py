# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedCallResult=false, reportUnusedParameter=false

"""Bug #11 regression tests: runtime artifact filenames must never exceed the
filesystem component limit (NAME_MAX = 255 bytes).

Root cause: ``sanitize_project_name`` regex-replaces invalid characters but
never truncates. Four filename construction sites embed the project name::

    runtime_error_{name}.md
    runtimeCard_{name}.md
    operatorRepairContext_{name}.md
    finalGateValidator_{name}.sh

On NPU filesystems an over-long component raises ``OSError: [Errno 36] File
name too long``. These tests lock the fix: UTF-8 byte-safe truncation with a
stable hash suffix, extension preservation, byte-identical short-name output,
and an explicit path-length guard (never silently swallowed).
"""

import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.repair_loop import _write_final_gate_validator_runner
from core.runtime_artifacts import (
    bounded_runtime_filename,
    write_operator_repair_context_artifact,
    write_repair_runtime_artifacts,
)


def _project_dir(tmp_path: Path, name: str) -> str:
    """Return a project_dir path whose resolved dir name is ``name``.

    ``name`` must be a legal directory name (<= 255 bytes); the over-long
    *artifact* filename is the real-world trigger for Bug #11.
    """
    target = tmp_path / name
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _name_byte_len(name: str) -> int:
    return len(name.encode("utf-8"))


def test_short_name_outputs_are_byte_identical(tmp_path: Path) -> None:
    """Short project names must produce exactly the filenames the codebase
    already emits (no truncation, no hash suffix)."""
    artifact_dir = str(tmp_path / "artifacts")
    error_path, card_path = write_repair_runtime_artifacts(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, "demo project "),
        entry_script="python validate.py",
        error_text="boom",
        category="operator",
        root_cause="x",
        suggested_fix="y",
        repair_role="operator_fixer",
    )
    runtime_dir = Path(artifact_dir) / "runtime"
    assert Path(error_path) == (runtime_dir / "runtime_error_demo_project_.md").resolve()
    assert Path(card_path) == (runtime_dir / "runtimeCard_demo_project_.md").resolve()

    operator_context = write_operator_repair_context_artifact(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, "demo custom project!"),
        entry_script="python validate.py",
    )
    assert Path(operator_context) == (
        runtime_dir / "operatorRepairContext_demo_custom_project_.md"
    ).resolve()


def test_long_ascii_project_name_writes_bounded_filename(tmp_path: Path) -> None:
    """A project name that would push the artifact filename past 255 bytes must
    be truncated with a stable hash suffix; the file must actually be written."""
    long_name = "p" * 250  # legal dir name; artifact filename would be 266 bytes
    artifact_dir = str(tmp_path / "artifacts")
    error_path, card_path = write_repair_runtime_artifacts(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, long_name),
        entry_script="python validate.py",
        error_text="boom",
        category="operator",
        root_cause="x",
        suggested_fix="y",
        repair_role="operator_fixer",
    )

    runtime_dir = Path(artifact_dir) / "runtime"
    error_file = Path(error_path)
    card_file = Path(card_path)

    # File must exist (pre-fix this raised Errno 36 and no file was written).
    assert error_file.is_file()
    assert card_file.is_file()

    # Component byte length must respect NAME_MAX.
    assert _name_byte_len(error_file.name) <= 255
    assert _name_byte_len(card_file.name) <= 255

    # Extension preserved.
    assert error_file.name.endswith(".md")
    assert card_file.name.endswith(".md")

    # Stable hash suffix derived from the full sanitized name.
    expected_hash = hashlib.sha256(long_name.encode("utf-8")).hexdigest()[:8]
    assert error_file.name.startswith("runtime_error_")
    assert error_file.name.endswith(f"_{expected_hash}.md")
    assert card_file.name.startswith("runtimeCard_")
    assert card_file.name.endswith(f"_{expected_hash}.md")


def test_bounded_runtime_filename_multibyte_truncation_is_valid_utf8() -> None:
    """Truncation must never split a multi-byte UTF-8 character in half."""
    long_cjk = "中" * 120  # 3 bytes per char = 360 bytes
    filename = bounded_runtime_filename("runtime_error_", long_cjk, ".md")
    assert _name_byte_len(filename) <= 255
    assert "\ufffd" not in filename
    expected_hash = hashlib.sha256(long_cjk.encode("utf-8")).hexdigest()[:8]
    assert filename.startswith("runtime_error_")
    assert filename.endswith(f"_{expected_hash}.md")


def test_truncation_is_deterministic(tmp_path: Path) -> None:
    """Same input must produce identical output across invocations."""
    long_name = "p" * 250
    artifact_dir = str(tmp_path / "artifacts")
    error_path1, _ = write_repair_runtime_artifacts(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, long_name),
        entry_script="python validate.py",
        error_text="boom",
        category="operator",
        root_cause="x",
        suggested_fix="y",
        repair_role="operator_fixer",
    )
    artifact_dir2 = str(tmp_path / "artifacts2")
    error_path2, _ = write_repair_runtime_artifacts(
        artifact_dir=artifact_dir2,
        project_dir=_project_dir(tmp_path, long_name),
        entry_script="python validate.py",
        error_text="boom",
        category="operator",
        root_cause="x",
        suggested_fix="y",
        repair_role="operator_fixer",
    )
    assert Path(error_path1).name == Path(error_path2).name


def test_distinct_long_names_get_distinct_hashes(tmp_path: Path) -> None:
    """Two long names sharing a long prefix must not collide after truncation."""
    shared = "p" * 240
    artifact_dir = str(tmp_path / "artifacts")
    error_path1, _ = write_repair_runtime_artifacts(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, shared + "A"),
        entry_script="python validate.py",
        error_text="boom",
        category="operator",
        root_cause="x",
        suggested_fix="y",
        repair_role="operator_fixer",
    )
    artifact_dir2 = str(tmp_path / "artifacts2")
    error_path2, _ = write_repair_runtime_artifacts(
        artifact_dir=artifact_dir2,
        project_dir=_project_dir(tmp_path, shared + "B"),
        entry_script="python validate.py",
        error_text="boom",
        category="operator",
        root_cause="x",
        suggested_fix="y",
        repair_role="operator_fixer",
    )
    assert Path(error_path1).name != Path(error_path2).name


def test_final_gate_validator_runner_uses_bounded_name(tmp_path: Path) -> None:
    """The finalGateValidator_{name}.sh site must also stay within NAME_MAX."""
    long_name = "v" * 250
    artifact_dir = str(tmp_path / "artifacts")
    runner_path = _write_final_gate_validator_runner(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, long_name),
        platform_policy=None,
    )
    runner = Path(runner_path)
    assert runner.is_file()
    assert _name_byte_len(runner.name) <= 255
    assert runner.name.startswith("finalGateValidator_")
    assert runner.name.endswith(".sh")
    expected_hash = hashlib.sha256(long_name.encode("utf-8")).hexdigest()[:8]
    assert runner.name.endswith(f"_{expected_hash}.sh")


def test_operator_context_runner_uses_bounded_name(tmp_path: Path) -> None:
    """The operatorRepairContext_{name}.md site must also stay within NAME_MAX."""
    long_name = "o" * 250
    artifact_dir = str(tmp_path / "artifacts")
    context_path = write_operator_repair_context_artifact(
        artifact_dir=artifact_dir,
        project_dir=_project_dir(tmp_path, long_name),
        entry_script="python validate.py",
    )
    context_file = Path(context_path)
    assert context_file.is_file()
    assert _name_byte_len(context_file.name) <= 255
    assert context_file.name.startswith("operatorRepairContext_")
    assert context_file.name.endswith(".md")


def test_excessive_directory_length_raises_explicit_error(tmp_path: Path) -> None:
    """When the *directory* portion alone pushes the path past the limit, the
    writer must raise a clear diagnostic instead of silently swallowing it."""
    deep = tmp_path
    for _ in range(60):
        deep = deep / ("d" * 120)
    artifact_dir = str(deep)

    with pytest.raises(RuntimeError) as excinfo:
        write_repair_runtime_artifacts(
            artifact_dir=artifact_dir,
            project_dir=_project_dir(tmp_path, "demo project"),
            entry_script="python validate.py",
            error_text="boom",
            category="operator",
            root_cause="x",
            suggested_fix="y",
            repair_role="operator_fixer",
        )
    message = str(excinfo.value)
    assert "artifact path" in message.lower()
    assert "byte" in message.lower()
