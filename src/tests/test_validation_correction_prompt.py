# pyright: reportAny=false
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

HELPERS_SPEC = importlib.util.spec_from_file_location(
    "test_phase_runner_helpers", TESTS_ROOT / "test_phase_runner.py"
)
if HELPERS_SPEC is None or HELPERS_SPEC.loader is None:  # pragma: no cover - import guard
    raise RuntimeError("Unable to load test_phase_runner helpers")

HELPERS_MODULE = importlib.util.module_from_spec(HELPERS_SPEC)
HELPERS_SPEC.loader.exec_module(HELPERS_MODULE)

MockSession = HELPERS_MODULE.MockSession
NoopSessionManager = HELPERS_MODULE.NoopSessionManager
build_runner = HELPERS_MODULE.build_runner

from core.workflow_executor import WorkflowExecutor


def _build_correction_runner(tmp_path: Path):
    return build_runner(tmp_path, session_mgr=NoopSessionManager())


def test_first_attempt_sends_full_prompt(tmp_path: Path) -> None:
    """First attempt uses the original phase prompt template."""
    runner, _ = _build_correction_runner(tmp_path)
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])

    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    assert len(session.calls) == 2
    first_prompt, _ = session.calls[0]
    assert "Phase 0" in first_prompt


def test_second_attempt_sends_correction_prompt(tmp_path: Path) -> None:
    """Retry attempt uses validation feedback instead of the full prompt."""
    runner, _ = _build_correction_runner(tmp_path)
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])

    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    second_prompt, _ = session.calls[1]
    assert "failed validation" in second_prompt
    assert second_prompt.startswith("Your previous response for phase_0_env_detect")


def test_correction_prompt_includes_validation_errors(tmp_path: Path) -> None:
    """Retry prompt includes the concrete validator error details."""
    runner, _ = _build_correction_runner(tmp_path)
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])

    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    second_prompt, _ = session.calls[1]
    assert "npu_detected must be a boolean" in second_prompt


def test_second_attempt_correct_json_passes(tmp_path: Path) -> None:
    """A corrected retry response passes validation and returns the result."""
    runner, _ = _build_correction_runner(tmp_path)
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({
            "platform": "npu",
            "npu_detected": True,
            "python_version": "3.10",
            "cann_version": "8.0.RC1",
            "ascendc_available": True,
            "driver_version": "24.1",
        }),
    ])

    result = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    assert result["platform"] == "npu"
    assert result["npu_detected"] is True
    assert result["python_version"] == "3.10"
    assert len(session.calls) == 2


def test_project_analysis_variant_correction_prompt_includes_inventory_repair_rules() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "custom_op_surface.expanded_operator_instances_count must equal expanded_operator_variants length; "
        "custom_op_surface.expanded_operator_variants axis_values.accuracy missing source-enumerated axis values: 4, 6, 8",
        phase_id="phase_1_project_analysis",
        validator_name="project_analysis",
    )

    assert "Return one complete replacement JSON object for phase 'phase_1_project_analysis' only" in prompt
    assert "full replacement Phase 1 JSON" in prompt
    assert "expanded_operator_instances_count` exactly to the number of objects listed" in prompt
    assert "do not claim a Cartesian-product count unless every concrete row is actually present" in prompt
    assert "observes every source-enumerated `variant_axes` value at least once" in prompt
    assert "concrete `source_evidence` plus public or framework route evidence" in prompt
    assert "heterogeneous base units" in prompt
    assert "do not omit a source-required axis from rows that use that axis" in prompt


def test_project_analysis_variant_omission_correction_prompt_includes_variant_rules() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "custom_op_surface describes source-required semantic generated axes (accuracy, dtype, ndim) "
        "but does not provide expanded variant metadata; set variant_axes_detected=true and enumerate concrete per-axis expanded_operator_variants",
        phase_id="phase_1_project_analysis",
        validator_name="project_analysis",
    )

    assert "full replacement Phase 1 JSON" in prompt
    assert "variant_axes` includes every source-enumerated target value" in prompt
    assert "expanded_operator_variants` into separate concrete rows" in prompt
    assert "variant_axes_detected" in prompt


def test_project_analysis_per_base_variant_correction_prompt_includes_combination_rules() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "custom_op_surface.expanded_operator_variants missing source-required per-base axis combinations "
        "for acoustic:forward_cuda: accuracy=6, dtype=double, ndim=3d",
        phase_id="phase_1_project_analysis",
        validator_name="project_analysis",
    )

    assert "full concrete combination set for each affected `base_unit_identity`" in prompt
    assert "source-required axes that apply to that base" in prompt
    assert "representative samples" in prompt
    assert "cover each axis value globally" in prompt
