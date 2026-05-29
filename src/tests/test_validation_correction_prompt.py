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
from core.phase_runner import PhaseRunner, PhaseSpec
from core.validation_correction import extract_output_format_from_prompt
from core.validator_engine import ValidationResult


# ── Output format extraction ──────────────────────────────────────────

_OUTPUT_FORMAT_PROMPT = """## Goal
Detect environment.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "platform": "npu",
  "npu_detected": true,
  "python_version": "3.10"
}
```

## Field Semantics
- platform: string."""

_EXPECTED_FORMAT_JSON = '{\n  "platform": "npu",\n  "npu_detected": true,\n  "python_version": "3.10"\n}'


def test_extract_output_format_workflow() -> None:
    result = WorkflowExecutor._extract_output_format_from_prompt(_OUTPUT_FORMAT_PROMPT)
    assert result == _EXPECTED_FORMAT_JSON


def test_extract_output_format_empty() -> None:
    assert WorkflowExecutor._extract_output_format_from_prompt("") is None


def test_extract_output_format_none() -> None:
    assert extract_output_format_from_prompt(None) is None


def test_extract_output_format_no_section() -> None:
    assert WorkflowExecutor._extract_output_format_from_prompt("## Goal\nNo format here.") is None


def test_extract_output_format_phase_runner() -> None:
    result = PhaseRunner._extract_output_format_from_prompt(_OUTPUT_FORMAT_PROMPT)
    assert result == _EXPECTED_FORMAT_JSON


def test_extract_output_format_case_insensitive() -> None:
    prompt = "## output format\n```json\n{\"key\": \"val\"}\n```"
    result = WorkflowExecutor._extract_output_format_from_prompt(prompt)
    assert result == '{"key": "val"}'


def test_extract_output_format_no_lang_tag() -> None:
    prompt = "## Output Format\n```\n{\"a\": 1}\n```"
    result = WorkflowExecutor._extract_output_format_from_prompt(prompt)
    assert result == '{"a": 1}'


# ── WorkflowExecutor correction prompt builder ────────────────────────


def test_validation_correction_with_output_format() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "Missing field 'platform'",
        output_format_example='{"platform": "npu"}',
        phase_name="phase_0",
    )
    assert "Your previous output for phase_0 failed validation" in prompt
    assert "Missing field 'platform'" in prompt
    assert "Expected output format" in prompt
    assert '{"platform": "npu"}' in prompt
    assert "You may reason" in prompt
    assert "Do not ask the user" in prompt
    assert "call the question tool" in prompt
    assert "single parseable JSON object" in prompt


def test_validation_correction_without_format() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt("error")
    assert "Your previous output failed validation" in prompt
    assert "Expected output format" not in prompt


def test_parse_failure_prompt_content() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "dummy",
        output_format_example='{"platform": "npu"}',
        is_parse_failure=True,
        phase_name="phase_0",
    )
    assert "did not contain a valid JSON object" in prompt
    assert "prose" in prompt
    assert "Expected output format" in prompt
    assert "last" in prompt.lower()
    assert "do not ask the user" in prompt.lower()
    assert "single parseable json object" in prompt.lower()


def test_parse_failure_no_format() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "dummy",
        is_parse_failure=True,
    )
    assert "did not contain a valid JSON object" in prompt
    assert "Expected output format" not in prompt


def test_custom_op_hint_injected() -> None:
    prompt = WorkflowExecutor._build_validation_correction_prompt(
        "existing file for custom-op contracts is missing",
    )
    assert "custom-op validation script" in prompt
    assert "entry_script_path points to a real file" in prompt


# ── PhaseRunner correction prompt builder ─────────────────────────────


def test_phase_runner_correction_with_output_format() -> None:
    validation = ValidationResult(
        passed=False,
        errors=["field 'python_version' is required"],
    )
    prompt = PhaseRunner._build_correction_prompt(
        phase=PhaseSpec("phase_0", "phase_0_env_detect", "env_detect"),
        validation=validation,
        previous_prompt=_OUTPUT_FORMAT_PROMPT,
    )
    assert "Your previous output for phase_0_env_detect failed validation" in prompt
    assert "field 'python_version' is required" in prompt
    assert "Expected output format" in prompt
    assert '"platform": "npu"' in prompt
    assert "You may reason" in prompt


def test_phase_runner_correction_without_format() -> None:
    validation = ValidationResult(
        passed=False,
        errors=["missing field"],
    )
    prompt = PhaseRunner._build_correction_prompt(
        phase=PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis"),
        validation=validation,
        previous_prompt="No output format section here.",
    )
    assert "Your previous output for phase_1_project_analysis failed validation" in prompt
    assert "Expected output format" not in prompt


def test_phase_runner_correction_missing_fields() -> None:
    validation = ValidationResult(
        passed=False,
        errors=[
            "field 'alpha' is required",
            "field 'beta' is missing",
        ],
    )
    prompt = PhaseRunner._build_correction_prompt(
        phase=PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis"),
        validation=validation,
        previous_prompt="",
    )
    assert "Required or invalid fields called out by validation: alpha, beta" in prompt


def test_phase_runner_correction_custom_op_hint() -> None:
    validation = ValidationResult(
        passed=False,
        errors=["existing file for custom-op contracts not found"],
    )
    prompt = PhaseRunner._build_correction_prompt(
        phase=PhaseSpec("phase_3", "phase_3_entry_script", "entry_script"),
        validation=validation,
        previous_prompt="",
    )
    assert "custom-op validation script" in prompt


# ── PhaseRunner correction preserves reasoning allowance ──────────────


def test_phase_runner_correction_allows_reasoning(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path, session_mgr=NoopSessionManager())
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])
    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})
    second_prompt, _ = session.calls[1]
    assert "You may reason" in second_prompt
    assert "single parseable JSON object" in second_prompt


def test_phase_runner_correction_includes_output_format(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path, session_mgr=NoopSessionManager())
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])
    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})
    second_prompt, _ = session.calls[1]
    assert "Expected output format" in second_prompt
    assert '"platform": "npu"' in second_prompt


def test_phase_runner_parse_retry_accepts_trailing_json_after_prose(tmp_path: Path) -> None:
    runner, _ = build_runner(tmp_path, session_mgr=NoopSessionManager())
    session = MockSession([
        "Phase 0 complete, but I forgot the JSON.",
        "Here is the corrected final object:\n"
        + json.dumps({
            "platform": "npu",
            "npu_detected": True,
            "python_version": "3.10",
            "cann_version": "8.0.RC1",
            "ascendc_available": True,
            "driver_version": "24.1",
        }),
    ])

    result = runner.run_single_phase(session, "phase_0", {"max_retry": 1})

    assert result["platform"] == "npu"
    assert len(session.calls) == 2
    parse_prompt, _ = session.calls[1]
    assert "did not contain a valid JSON object" in parse_prompt
    assert "You may reason" in parse_prompt


# ── Existing tests (preserved) ────────────────────────────────────────


def _build_correction_runner(tmp_path: Path):
    return build_runner(tmp_path, session_mgr=NoopSessionManager())


def test_first_attempt_sends_full_prompt(tmp_path: Path) -> None:
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
    runner, _ = _build_correction_runner(tmp_path)
    session = MockSession([
        json.dumps({"platform": "npu"}),
        json.dumps({"platform": "npu"}),
    ])

    with pytest.raises(ValueError):
        _ = runner.run_single_phase(session, "phase_0", {"max_retry": 2})

    second_prompt, _ = session.calls[1]
    assert "failed validation" in second_prompt
    assert second_prompt.startswith("Your previous output for phase_0_env_detect")


def test_correction_prompt_includes_validation_errors(tmp_path: Path) -> None:
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
