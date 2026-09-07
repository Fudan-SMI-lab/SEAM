from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run_with_cpython38(script: str) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None
    environment = dict(os.environ)
    environment.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    return subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--python",
            "3.8",
            "--with",
            "pydantic==2.8.2",
            "--with",
            "typing_extensions>=4.12",
            "--with",
            "PyYAML>=6,<7",
            "python",
            "-B",
            "-c",
            script,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.parametrize(
    "module",
    (
        "core.phase5_attempt_receipt",
        "core.terminal_continuation",
        "tests.e2e.e2e_test_v3",
        "tests.e2e.e2e_observer",
    ),
)
def test_active_v3_modules_import_on_cpython38(module: str) -> None:
    completed = _run_with_cpython38(f"import {module}")

    assert completed.returncode == 0, completed.stderr


@pytest.mark.integration
@pytest.mark.slow
def test_active_string_normalization_executes_on_cpython38() -> None:
    script = """
import tempfile
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.continuation_hydration import _evidence_for
from core.phase_runner import PhaseSpec
from core.run_manifest import EvidenceDigest
from core.run_outcome import PhaseId

with tempfile.TemporaryDirectory() as temporary:
    store = ArtifactStore(temporary, "run-py38")
    store.save_phase_output("phase_1", {"ok": True})
    store.mark_validated("phase_1", {"ok": True})
    assert store.load_phase_output("phase_1") == {"ok": True}

assert PhaseSpec("phase_1", "phase_1_project_analysis", "project").artifact_id == "1_project_analysis"
evidence = EvidenceDigest(
    relative_path="validated/phase_1_canonical.json",
    digest="a" * 64,
    size_bytes=2,
)
assert _evidence_for(PhaseId("phase_1"), (evidence,)) == evidence
"""
    completed = _run_with_cpython38(script)

    assert completed.returncode == 0, completed.stderr
