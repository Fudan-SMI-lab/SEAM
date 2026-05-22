# pyright: reportUnknownMemberType=false

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e import e2e_test, e2e_test_v2


def test_direct_e2e_harness_passes_phase3_contract_to_repair_loop() -> None:
    source = inspect.getsource(e2e_test.run_e2e)

    assert 'phase3_output = phase_outputs.get("phase_3_entry_script")' in source
    assert 'phase3_contract = dict(phase3_output) if isinstance(phase3_output, dict) else None' in source
    assert 'phase3_contract=phase3_contract' in source


def test_v2_summary_keeps_custom_op_fail_closed_status_as_overall_fail() -> None:
    summary = e2e_test_v2.build_v2_summary(
        run_id="run-1",
        base_url="http://127.0.0.1:4096",
        output_dir="/tmp/out",
        temp_dir="/tmp/project",
        keep_temp_dir=True,
        max_phase5_iter=5,
        phase_results=[
            e2e_test_v2.PhaseStatus(
                phase_number=5,
                phase_id="phase_5_validation",
                label="phase_5_validation",
                status="stagnation_fail_closed_missing_strict_opp_evidence",
                error="missing strict Ascend C/CANN OPP evidence",
            ),
            e2e_test_v2.PhaseStatus(
                phase_number=6,
                phase_id="phase_6_report",
                label="phase_6_report",
                status="passed",
            ),
        ],
        session_count=2,
        command_count=4,
        total_duration_seconds=1.25,
        artifact_dir="/tmp/out/.sm-artifacts",
        telemetry_paths={},
        before_snapshot_path=None,
        after_snapshot_path=None,
        entry_script=None,
        errors=[],
    )

    assert summary.overall_status == "FAIL"


def test_v2_project_dir_resolution_falls_back_to_unique_cuda_project(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    missing = repo_root / "original_projects" / "pointnet2_ops"
    candidate = repo_root / "cuda_projects" / "pointnet2_ops"
    candidate.mkdir(parents=True)

    with patch.object(e2e_test_v2, "REPO_ROOT", repo_root), patch.object(
        e2e_test_v2,
        "PROJECT_FALLBACK_ROOTS",
        (repo_root / "cuda_projects", repo_root / "original_projects"),
    ):
        resolved, warning = e2e_test_v2.resolve_project_dir(missing)

    assert resolved == candidate.resolve()
    assert warning is not None
    assert "Using unique matching source candidate" in warning


def test_v2_project_dir_resolution_rejects_ambiguous_fallbacks(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    missing = repo_root / "original_projects" / "sample_project"
    candidate_a = repo_root / "cuda_projects" / "sample_project"
    candidate_b = repo_root.parent / "cuda_projects" / "sample_project"
    candidate_a.mkdir(parents=True)
    candidate_b.mkdir(parents=True)

    with patch.object(e2e_test_v2, "REPO_ROOT", repo_root), patch.object(
        e2e_test_v2,
        "PROJECT_FALLBACK_ROOTS",
        (repo_root / "cuda_projects", repo_root.parent / "cuda_projects"),
    ):
        try:
            _ = e2e_test_v2.resolve_project_dir(missing)
        except FileNotFoundError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected ambiguous project dir resolution to fail")

    assert "ambiguous" in message
    assert str(candidate_a.resolve()) in message
    assert str(candidate_b.resolve()) in message
