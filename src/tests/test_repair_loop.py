# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedCallResult=false, reportUnusedParameter=false

import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import TextIO, TypedDict, cast
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.artifact_store import ArtifactStore
from core.prompt_loader import PromptLoader
from core.repair_loop import ClassificationDict, RepairLoopEngine, ReviewGateState, _operator_custom_op_target_units, force_custom_op_operator_routing_if_needed
from core.runtime_artifacts import write_operator_repair_context_artifact
from core.types import RepairContext
from core.validator_engine import ValidatorEngine
from validators.validate_validation_final import validate_custom_op_final_gate


class MockSessionManager:
    analyzer_response: dict[str, str]

    def __init__(self, analyzer_response: dict[str, str]) -> None:
        self.analyzer_response = analyzer_response
        self.get_or_create_calls: list[tuple[str, str, str]] = []
        self.send_command_calls: list[tuple[str, str, str, int]] = []
        self._session_ids: dict[tuple[str, str, str], str] = {}
        self._next_id: int = 1

    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        self.get_or_create_calls.append((role, lifecycle, agent))
        key = (role, lifecycle, agent)
        if key not in self._session_ids:
            self._session_ids[key] = f"session-{self._next_id}"
            self._next_id += 1
        return self._session_ids[key]

    def send_command(self, session_id: str, command: str, agent: str = "", timeout: object = None) -> str:
        self.send_command_calls.append((session_id, command, agent, int(timeout) if isinstance(timeout, int) else 600))
        if session_id == "session-1":
            return json.dumps(self.analyzer_response)
        return json.dumps({"status": "ok", "session_id": session_id})


def build_engine(base_dir: Path, session_mgr: MockSessionManager) -> tuple[RepairLoopEngine, ArtifactStore]:
    artifact_store = ArtifactStore(str(base_dir), "testrun")
    engine = RepairLoopEngine(
        session_mgr=session_mgr,
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )
    return engine, artifact_store


def test_operator_custom_op_target_units_reads_nested_expanded_variant_inventory() -> None:
    units = [f"deepwave:axis=a:dtype=float32:variant={index}" for index in range(240)]
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "expanded_variant_inventory": {
            "variant_axes_detected": True,
            "unit_identities": units,
            "expanded_operator_instances_count": 240,
        },
        "operator_inventory_schema": {"fine_grained_operator_units": ["collapsed:fallback"]},
    }

    assert _operator_custom_op_target_units(phase3_contract) == units


def test_repair_loop_extends_stagnation_only_for_custom_op_operator_repair() -> None:
    engine, *_ = build_mocked_engine()
    custom_op_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
    }
    operator_classification: dict[str, object] = {"repair_role": "operator_fixer"}

    assert engine._effective_stagnation_threshold(
        classification=operator_classification,
        phase3_contract=custom_op_contract,
    ) == 100
    assert engine._effective_stagnation_threshold(
        classification={"repair_role": "code_adapter"},
        phase3_contract=custom_op_contract,
    ) == 3
    assert engine._effective_stagnation_threshold(
        classification=operator_classification,
        phase3_contract=None,
    ) == 3


def test_strict_custom_op_final_gate_missing_evidence_reroutes_before_stagnation() -> None:
    engine, *_ = build_mocked_engine()
    error_text = (
        "strict custom-op final gate failed: missing or insufficient per-expanded-variant evidence "
        "for acoustic:backward_cuda:ndim=1d:dtype=float:accuracy=2"
    )
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "expanded_variant_inventory": ["acoustic:backward_cuda:ndim=1d:dtype=float:accuracy=2"],
        "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
    }
    analyzer_classification: dict[str, object] = {
        "category": "communication_error",
        "root_cause": "final gate evidence missing",
        "suggested_fix": "retry validation",
        "repair_role": "dependency_fixer",
    }

    routed = force_custom_op_operator_routing_if_needed(
        analyzer_classification,
        error_text=error_text,
        phase3_contract=phase3_contract,
    )

    assert routed["category"] == "operator"
    assert routed["repair_role"] == "operator_fixer"
    assert engine._effective_stagnation_threshold(
        classification=routed,
        phase3_contract=phase3_contract,
    ) == 100
    assert engine._effective_stagnation_threshold(
        classification=analyzer_classification,
        phase3_contract=None,
    ) == 3


def test_custom_op_operator_repair_uses_non_orchestrator_agent() -> None:
    engine, *_ = build_mocked_engine()
    custom_op_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
    }

    assert engine._repair_agent_for_role(
        repair_role="operator_fixer",
        phase3_contract=custom_op_contract,
    ) == "hephaestus"
    assert engine._repair_agent_for_role(
        repair_role="operator_fixer",
        phase3_contract=None,
    ) == ""
    assert engine._repair_agent_for_role(
        repair_role="dependency_fixer",
        phase3_contract=custom_op_contract,
    ) == ""


def test_custom_op_operator_repair_agent_can_be_configured() -> None:
    engine, *_ = build_mocked_engine()
    engine.config = {"framework": {"custom_op_operator_repair_agent": "hephaestus"}}
    custom_op_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "required_report_paths": ["migration_reports/custom_op_final_gate.json"],
    }

    assert engine._repair_agent_for_role(
        repair_role="operator_fixer",
        phase3_contract=custom_op_contract,
    ) == "hephaestus"


def test_custom_op_repair_allows_nested_task_tool() -> None:
    class NestedTaskSessionManager(MockSessionManager):
        def _http(self, _method: str, _path: str, query: object = None) -> dict[str, object]:
            return {
                "ok": True,
                "data": [{"parts": [{"type": "tool", "tool": "task", "state": {"status": "completed"}}]}],
            }

    session_mgr = NestedTaskSessionManager({})
    engine, _ = build_engine(Path("/tmp"), session_mgr)

    error = engine._repair_session_nested_task_error("session-2")

    assert error == ""


def test_repair_loop_keeps_repair_session_after_nested_task_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_calls: list[str] = []

    class NestedTaskRepairSessionManager(MockSessionManager):
        def cleanup_session(self, session_id: str) -> bool:
            cleanup_calls.append(session_id)
            for key, value in list(self._session_ids.items()):
                if value == session_id:
                    del self._session_ids[key]
            return True

        def _http(self, _method: str, path: str, query: object = None) -> dict[str, object]:
            session_id = path.split("/")[2]
            if session_id == "session-2":
                return {
                    "ok": True,
                    "data": [{"parts": [{"type": "tool", "tool": "task", "state": {"status": "completed"}}]}],
                }
            return {"ok": True, "data": []}

    session_mgr = NestedTaskRepairSessionManager({
        "category": "operator",
        "root_cause": "custom-op final gate evidence is incomplete",
        "suggested_fix": "repair custom-op reports inline",
        "repair_role": "operator_fixer",
    })
    engine, _artifact_store = build_engine(tmp_path, session_mgr)

    outcomes = [
        CompletedProcess(args="python validate.py", returncode=1, stdout="", stderr="gate error one"),
        CompletedProcess(args="python validate.py", returncode=1, stdout="", stderr="gate error two"),
    ]

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: outcomes.pop(0))

    result = cast(RunResult, cast(object, engine.run(
        "python validate.py",
        str(tmp_path),
        max_iterations=2,
        phase3_contract=_custom_op_phase3_contract(tmp_path),
    )))

    assert cleanup_calls == []
    assert result["repair_session_ids"] == {"operator_fixer": "session-2"}
    assert session_mgr.get_or_create_calls == [
        ("error_analyzer", "persistent", ""),
        ("operator_fixer", "persistent", "hephaestus"),
    ]


class SessionManagerMock:
    def __init__(self) -> None:
        self.get_or_create: MagicMock = MagicMock()
        self.send_command: MagicMock = MagicMock()


class ArtifactStoreMock:
    def __init__(self) -> None:
        self.save_phase_output: MagicMock = MagicMock()
        self.write_journal: MagicMock = MagicMock()
        self.save_checkpoint: MagicMock = MagicMock()
        self.mark_validated: MagicMock = MagicMock()


class PromptLoaderMock:
    def __init__(self) -> None:
        self.load_prompt: MagicMock = MagicMock()


class ValidatorMock:
    def __init__(self) -> None:
        self.register_validator: MagicMock = MagicMock()
        self.validate: MagicMock = MagicMock()


def build_mocked_engine() -> tuple[
    RepairLoopEngine,
    SessionManagerMock,
    ArtifactStoreMock,
    PromptLoaderMock,
    ValidatorMock,
]:
    session_mgr = SessionManagerMock()
    artifact_store = ArtifactStoreMock()
    prompt_loader = PromptLoaderMock()
    validator = ValidatorMock()

    validator.validate.return_value = SimpleNamespace(passed=True, errors=[], warnings=[])
    artifact_store.save_phase_output.return_value = "phase_5_validation_raw.json"
    artifact_store.write_journal.return_value = "journal.json"
    artifact_store.save_checkpoint.return_value = "checkpoint.json"
    artifact_store.mark_validated.return_value = "phase_5_validation.json"
    prompt_loader.load_prompt.return_value = "prompt"

    engine = RepairLoopEngine(
        session_mgr,
        cast(ArtifactStore, cast(object, artifact_store)),
        cast(PromptLoader, cast(object, prompt_loader)),
        cast(ValidatorEngine, cast(object, validator)),
    )
    return engine, session_mgr, artifact_store, prompt_loader, validator


class SummaryEntry(TypedDict, total=False):
    iteration: int
    exit_code: int
    error_category: str
    repair_role: str
    modified_files: list[str]
    fix_summary: str


class RunResult(TypedDict):
    success: bool
    status: str
    iteration_count: int
    error_history: list[SummaryEntry]
    repair_session_ids: dict[str, str]
    error_analyzer_session_id: str


def test_repair_loop_detects_stagnation_and_reuses_repair_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "dependency",
            "root_cause": "torch_npu is missing",
            "suggested_fix": "Install torch_npu",
            "repair_role": "dependency_fixer",
        }
    )
    engine, artifact_store = build_engine(tmp_path, session_mgr)

    outcomes = [
        CompletedProcess(args="python train.py", returncode=1, stdout="", stderr="ModuleNotFoundError: torch_npu"),
        CompletedProcess(args="python train.py", returncode=1, stdout="", stderr="ModuleNotFoundError: torch_npu"),
        CompletedProcess(args="python train.py", returncode=1, stdout="", stderr="ModuleNotFoundError: torch_npu"),
    ]

    def fake_run(*_args: object, **kwargs: object) -> CompletedProcess[str]:
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["timeout"] is None
        completed = outcomes.pop(0)
        stderr_handle = kwargs.get("stderr")
        if stderr_handle is not None:
            cast(TextIO, stderr_handle).write(completed.stderr)
        stdout_handle = kwargs.get("stdout")
        if stdout_handle is not None:
            cast(TextIO, stdout_handle).write(completed.stdout)
        return completed

    monkeypatch.setattr("subprocess.run", fake_run)

    result = cast(RunResult, cast(object, engine.run("python train.py", str(tmp_path), max_iterations=5)))
    error_history = result["error_history"]

    assert result["success"] is False
    assert result["status"] == "stagnation"
    assert result["iteration_count"] == 3
    assert len(error_history) == 3
    assert result["repair_session_ids"] == {"dependency_fixer": "session-2"}
    assert error_history[0].get("repair_role") == "dependency_fixer"
    assert error_history[1].get("repair_role") == "dependency_fixer"
    assert "error_category" in error_history[0]
    assert "fix_summary" in error_history[0]
    assert session_mgr.get_or_create_calls == [
        ("error_analyzer", "persistent", ""),
        ("dependency_fixer", "persistent", ""),
    ]

    saved = artifact_store.load_phase_output("phase_5_validation")
    assert saved is not None
    assert saved["status"] == "stagnation"

    journal = artifact_store.get_journal()
    assert [entry["status"] for entry in journal] == [
        "repair_dispatched",
        "repair_dispatched",
        "stagnation",
        "stagnation",
    ]


def test_repair_loop_exits_on_max_iterations_with_different_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "code",
            "root_cause": "API mismatch",
            "suggested_fix": "Update the call site",
            "repair_role": "code_adapter",
        }
    )
    engine, artifact_store = build_engine(tmp_path, session_mgr)

    outcomes = [
        CompletedProcess(args="python train.py", returncode=1, stdout="", stderr="TypeError: first"),
        CompletedProcess(args="python train.py", returncode=1, stdout="", stderr="TypeError: second"),
    ]

    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return outcomes.pop(0)

    monkeypatch.setattr("subprocess.run", fake_run)

    result = cast(RunResult, cast(object, engine.run("python train.py", str(tmp_path), max_iterations=2)))
    error_history = result["error_history"]

    assert result["success"] is False
    assert result["status"] == "max_iterations"
    assert result["iteration_count"] == 2
    assert len(error_history) == 2
    assert result["repair_session_ids"] == {"code_adapter": "session-2"}

    journal = artifact_store.get_journal()
    assert [entry["status"] for entry in journal] == [
        "repair_dispatched",
        "repair_dispatched",
        "max_iterations",
    ]


def test_direct_operator_repair_prompt_is_slim_and_writes_runtime_artifacts(tmp_path: Path) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "operator",
            "root_cause": "unsupported custom op",
            "suggested_fix": "port custom op",
            "repair_role": "operator_fixer",
        }
    )
    engine, artifact_store = build_engine(tmp_path, session_mgr)
    project_dir = str(tmp_path / "demo project!")

    prompt = engine._build_repair_prompt(
        entry_script="python main.py",
        project_dir=project_dir,
        iteration=1,
        error_text="RuntimeError: unsupported custom op",
        classification={
            "category": "operator",
            "root_cause": "unsupported custom op",
            "suggested_fix": "port custom op",
            "repair_role": "operator_fixer",
            "raw_response": "{}",
        },
        history=[],
    )

    assert "RuntimeError" not in prompt
    assert "## Analyzer-Selected Experience Action Cards" not in prompt
    assert "This is a generic operator-incompatibility repair" in prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in prompt
    assert "custom_op_final_gate" not in prompt
    assert ".skills" not in prompt

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    runtime_error = runtime_dir / "runtime_error_demo_project_.md"
    runtime_card = runtime_dir / "runtimeCard_demo_project_.md"
    operator_context = runtime_dir / "operatorRepairContext_demo_project_.md"
    assert str(runtime_error.resolve()) in prompt
    assert str(runtime_card.resolve()) in prompt
    assert str(operator_context.resolve()) not in prompt
    assert not operator_context.exists()
    error_text = runtime_error.read_text(encoding="utf-8")
    card_text = runtime_card.read_text(encoding="utf-8")
    assert "# Operator Fixer" in error_text
    assert "## Execution Failure" in error_text
    assert "## Error Classification" in error_text
    assert "RuntimeError: unsupported custom op" in error_text
    assert "Migration Constraints" not in error_text
    assert "Required Actions" not in error_text
    assert "(No analyzer-selected experience cards)" in card_text


def test_direct_operator_repair_prompt_with_custom_op_contract_writes_bounded_context(tmp_path: Path) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "operator",
            "root_cause": "custom op final gate failed",
            "suggested_fix": "close custom op reports",
            "repair_role": "operator_fixer",
        }
    )
    engine, artifact_store = build_engine(tmp_path, session_mgr)
    project_dir = str(tmp_path / "demo custom project!")

    prompt = engine._build_repair_prompt(
        entry_script="python validate.py",
        project_dir=project_dir,
        iteration=1,
        error_text="custom_op_final_gate failed",
        classification={
            "category": "operator",
            "root_cause": "custom op final gate failed",
            "suggested_fix": "close custom op reports",
            "repair_role": "operator_fixer",
            "raw_response": "{}",
        },
        history=[],
        phase3_contract=_custom_op_phase3_contract(Path(project_dir)),
    )

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    operator_context = runtime_dir / "operatorRepairContext_demo_custom_project_.md"
    assert str(operator_context.resolve()) in prompt
    assert "bounded operator context" in prompt
    assert "inventory / manifest / final-gate" in prompt.lower()
    assert "freeze manifest rows" in prompt
    assert "inventory_count == manifest_entries == closed_pass_entries" in prompt
    assert "remaining_entries == 0" in prompt
    assert "full_migration_status == FULL_PASS" in prompt
    assert "same-run runtime coverage > 0" in prompt
    assert "baseline/custom performance evidence" in prompt
    assert "report-only" in prompt
    assert "MVP-only" in prompt
    assert "zero-call" in prompt
    assert "modified_files 必须列出实际修改文件" in prompt
    assert "FAILED/INCOMPLETE" in prompt
    assert "cuda_custom_op_skill_test_prompt.md" not in prompt
    assert ".skills" not in prompt
    context_text = operator_context.read_text(encoding="utf-8")
    assert "# Operator Repair Context" in context_text
    assert "FULL_PASS is required" in context_text


def test_operator_repair_context_prefers_phase3_contract_units_over_stale_reports(tmp_path: Path) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "operator",
            "root_cause": "custom op final gate failed",
            "suggested_fix": "close custom op reports",
            "repair_role": "operator_fixer",
        }
    )
    engine, artifact_store = build_engine(tmp_path, session_mgr)
    project_dir = tmp_path / "contract source project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "operator_inventory.json").write_text(
        json.dumps({"total_count": 1, "entries": [{"name": "stale_family", "status": "passed"}]}),
        encoding="utf-8",
    )
    contract = _custom_op_phase3_contract(project_dir)
    contract["operator_inventory_schema"] = {
        "semantic_rows": "one row per fine-grained source-discovered unit",
        "fine_grained_operator_units": ["family:kernel_a", "family:kernel_b"],
    }

    engine._build_repair_prompt(
        entry_script="python validate.py",
        project_dir=str(project_dir),
        iteration=1,
        error_text="custom_op_final_gate failed",
        classification={
            "category": "operator",
            "root_cause": "custom op final gate failed",
            "suggested_fix": "close custom op reports",
            "repair_role": "operator_fixer",
            "raw_response": "{}",
        },
        history=[],
        phase3_contract=contract,
    )

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    operator_context = next(runtime_dir.glob("operatorRepairContext_contract_source_project*.md"))
    context_text = operator_context.read_text(encoding="utf-8")
    assert "Unit Source: Phase 3 contract" in context_text
    assert "Total Count: 2" in context_text
    assert "Unit 1: family:kernel_a" in context_text
    assert "Unit 2: family:kernel_b" in context_text
    assert "stale_family" not in context_text


def test_direct_dependency_repair_prompt_is_slim_and_writes_runtime_artifacts(tmp_path: Path) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "dependency",
            "root_cause": "torch_npu missing",
            "suggested_fix": "install torch_npu",
            "repair_role": "dependency_fixer",
        }
    )
    engine, artifact_store = build_engine(tmp_path, session_mgr)
    project_dir = str(tmp_path / "demo dependency project!")

    prompt = engine._build_repair_prompt(
        entry_script="python main.py",
        project_dir=project_dir,
        iteration=1,
        error_text="ModuleNotFoundError: No module named 'torch_npu'",
        classification={
            "category": "dependency",
            "root_cause": "torch_npu missing",
            "suggested_fix": "install torch_npu",
            "repair_role": "dependency_fixer",
            "raw_response": "{}",
        },
        history=[],
    )

    # Prompt now includes constraint_summary, No CPU Fallback, and Native Operator Handoff sections
    assert "No CPU Fallback (CRITICAL)" in prompt
    assert "Native Operator Handoff" in prompt
    assert "ModuleNotFoundError" not in prompt
    assert "## Execution Failure" not in prompt
    assert "agent_diagnostics" not in prompt
    # operator_fixer is mentioned in handoff guidance for native operator issues

    runtime_dir = Path(artifact_store.artifact_dir) / "runtime"
    runtime_error = runtime_dir / "runtime_error_demo_dependency_project_.md"
    runtime_card = runtime_dir / "runtimeCard_demo_dependency_project_.md"
    assert str(runtime_error.resolve()) in prompt
    assert str(runtime_card.resolve()) in prompt
    error_text = runtime_error.read_text(encoding="utf-8")
    card_text = runtime_card.read_text(encoding="utf-8")
    assert "# Dependency Fixer" in error_text
    assert "## Execution Failure" in error_text
    assert "## Error Classification" in error_text
    assert "ModuleNotFoundError: No module named 'torch_npu'" in error_text
    assert "Migration Constraints" not in error_text
    assert "Required Actions" not in error_text
    assert "# Dependency Fixer Runtime Cards" in card_text
    assert "(No analyzer-selected experience cards)" in card_text


def test_repair_loop_reuses_error_analyzer_session_across_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "operator",
            "root_cause": "unsupported operator",
            "suggested_fix": "Replace the operator",
            "repair_role": "operator_fixer",
        }
    )
    engine, _artifact_store = build_engine(tmp_path, session_mgr)

    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args="python train.py", returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    first = cast(RunResult, cast(object, engine.run("python train.py", str(tmp_path), max_iterations=3)))
    second = cast(RunResult, cast(object, engine.run("python train.py", str(tmp_path), max_iterations=3)))

    assert first["success"] is True
    assert second["success"] is True
    assert first["error_analyzer_session_id"] == second["error_analyzer_session_id"] == "session-1"
    assert session_mgr.get_or_create_calls == [
        ("error_analyzer", "persistent", ""),
        ("error_analyzer", "persistent", ""),
    ]


def test_repair_loop_passes_logger_to_analyzer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager(
        {
            "category": "dependency",
            "root_cause": "missing pkg",
            "suggested_fix": "pip install",
            "repair_role": "dependency_fixer",
        }
    )
    engine, _ = build_engine(tmp_path, session_mgr)

    monkeypatch.setattr("subprocess.run", lambda *_args, **kwargs: CompletedProcess(
        args="", returncode=0, stdout="", stderr="",
    ))

    log_messages: list[str] = []
    result = cast(RunResult, cast(object, engine.run(
        "python train.py", str(tmp_path), max_iterations=1,
        logger=log_messages.append,
    )))

    assert len(log_messages) >= 1
    assert any("SUCCESS" in m for m in log_messages)
    assert result["success"] is True


def test_format_history_summary_empty_and_nonempty() -> None:
    from core.repair_loop import RepairLoopEngine
    assert RepairLoopEngine._format_history_summary([]) == "(No previous repair attempts)"
    history: list[dict[str, object]] = [
        {"iteration": 1, "exit_code": 1, "error_category": "dependency",
         "repair_role": "dependency_fixer", "modified_files": ["requirements.txt"],
         "fix_summary": "Installed torch_npu"},
    ]
    result = RepairLoopEngine._format_history_summary(history)
    assert "Iter 1" in result
    assert "dependency" in result
    assert "requirements.txt" in result
    assert "exit=1" in result


def test_error_analyzer_json_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0
    def mock_send(session_id: str, command: str, agent: str = "", timeout: object = None) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "I will analyze the error..."  # no JSON
        return '{"category": "dependency", "root_cause": "missing", "suggested_fix": "install", "repair_role": "dependency_fixer"}'

    session_mgr = MockSessionManager({})
    session_mgr.send_command = mock_send

    engine, _ = build_engine(tmp_path, session_mgr)
    monkeypatch.setattr("subprocess.run", lambda *_args, **kw: CompletedProcess(
        args="", returncode=1, stdout="", stderr="ImportError",
    ))

    _ = cast(RunResult, cast(object, engine.run("x", str(tmp_path), max_iterations=2)))
    assert call_count >= 2  # first call + retry


def test_repair_loop_run_accepts_review_callable() -> None:
    import inspect

    sig = inspect.signature(RepairLoopEngine.run)
    assert "review_callable" in sig.parameters
    assert "constraint_summary" in sig.parameters


def test_repair_loop_review_callable_is_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed validation path does not invoke the reviewer."""
    call_args: list[dict[str, object]] = []

    def mock_review(ctx: dict[str, object]) -> dict[str, object]:
        call_args.append(ctx)
        return {
            "verdict": "accept",
            "cpu_fallback_detected": False,
            "cpu_fallback_necessary": False,
            "alternative_suggestions": "",
            "reasoning": "",
        }

    session_mgr = MockSessionManager(
        {
            "category": "dependency",
            "root_cause": "torch_npu is missing",
            "suggested_fix": "Install torch_npu",
            "repair_role": "dependency_fixer",
        }
    )
    runner, _ = build_engine(tmp_path, session_mgr)

    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: CompletedProcess(
            args="python train.py",
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: torch_npu",
        ),
    )

    runner.run(
        entry_script="python train.py",
        project_dir=str(tmp_path),
        max_iterations=1,
        review_callable=mock_review,
        constraint_summary="R1",
    )

    assert call_args == []


def test_repair_loop_constraint_summary_in_prompt() -> None:
    import inspect

    sig = inspect.signature(RepairLoopEngine._analyze_error)
    assert "constraint_summary" in sig.parameters
    assert "last_review" in sig.parameters


def test_repair_loop_review_gate_reject_snapshots_and_tracks_counter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review gate reject snapshots project files and tracks improvement counter."""
    review_reject_count = 0

    def mock_review(ctx: dict[str, object]) -> dict[str, object]:
        nonlocal review_reject_count
        review_reject_count += 1
        return {
            "verdict": "reject",
            "cpu_fallback_detected": True,
            "cpu_fallback_necessary": True,
            "alternative_suggestions": "",
            "reasoning": f"Reject #{review_reject_count}",
        }

    session_mgr = MockSessionManager(
        {
            "category": "code",
            "root_cause": "API usage",
            "suggested_fix": "Fix the call",
            "repair_role": "code_adapter",
        }
    )
    engine, _artifact_store = build_engine(tmp_path, session_mgr)

    outcomes = [
        CompletedProcess(args="python train.py", returncode=1, stdout="", stderr="RuntimeError: test error"),
        CompletedProcess(args="python train.py", returncode=0, stdout="ok", stderr=""),
        CompletedProcess(args="python train.py", returncode=0, stdout="ok", stderr=""),
    ]

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: outcomes.pop(0))

    result = cast(RunResult, cast(object, engine.run(
        "python train.py",
        str(tmp_path),
        max_iterations=5,
        review_callable=mock_review,
        constraint_summary="R1",
        enable_review_gate=True,
        max_review_iterations=2,
    )))

    assert result["success"] is True
    assert result["status"] == "passed_with_reviews"

    gate_summary = cast(dict[str, object], result.get("review_gate_summary", {}))
    assert gate_summary.get("passing_iteration") == 3
    assert gate_summary.get("review_rejections") == 2
    assert gate_summary.get("improvement_iterations") == 2
    assert gate_summary.get("last_passing_version_path") is not None
    assert str(gate_summary.get("last_passing_version_path", "")).endswith("passing_version_iter3.json")


def test_review_gate_disabled_identical_behavior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.return_value = "analyzer-1"
    (tmp_path / "dummy.py").write_text("print('ok')\n", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args="python dummy.py", returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    sig = inspect.signature(RepairLoopEngine.run)
    result = cast(RunResult, cast(object, engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=2,
        enable_review_gate=False,
    )))

    assert cast(bool, sig.parameters["enable_review_gate"].default) is False
    assert result["success"] is True
    assert result["status"] == "success"
    assert "review_gate_summary" not in result
    session_mgr.send_command.assert_not_called()


def test_review_gate_no_rejection_continues_normally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    session_mgr.send_command.return_value = '{"status": "ok"}'
    (tmp_path / "dummy.py").write_text("print('ok')\n", encoding="utf-8")

    outcomes = [
        CompletedProcess(args="python dummy.py", returncode=1, stdout="", stderr="RuntimeError: boom"),
        CompletedProcess(args="python dummy.py", returncode=0, stdout="ok", stderr=""),
    ]

    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return outcomes.pop(0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(engine, "_analyze_error", lambda **_kwargs: {
        "category": "code",
        "root_cause": "bad call",
        "suggested_fix": "update code",
        "repair_role": "code_adapter",
        "raw_response": "{}",
    })
    monkeypatch.setattr(engine, "_build_repair_prompt", lambda **_kwargs: "repair prompt")
    monkeypatch.setattr(engine, "_extract_fix_summary", lambda *_args, **_kwargs: {
        "modified_files": ["dummy.py"],
        "summary": "Adjusted the failing path.",
    })

    review_calls: list[dict[str, object]] = []

    def mock_review(ctx: dict[str, object]) -> dict[str, object]:
        review_calls.append(ctx)
        return {
            "verdict": "accept",
            "cpu_fallback_detected": False,
            "cpu_fallback_necessary": False,
            "alternative_suggestions": "",
            "reasoning": "looks good",
        }

    result = cast(RunResult, cast(object, engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=2,
        enable_review_gate=True,
        review_callable=mock_review,
    )))

    assert result["success"] is True
    assert result["status"] == "success"
    assert result["iteration_count"] == 2
    assert "review_gate_summary" not in result
    assert len(review_calls) == 1
    assert review_calls[0]["repair_role"] == "code_adapter"
    assert review_calls[0]["fix_instruction"] == "repair prompt"
    assert review_calls[0]["fix_response"] == '{"status": "ok"}'
    assert review_calls[0]["fix_metadata"] == {
        "modified_files": ["dummy.py"],
        "summary": "Adjusted the failing path.",
    }
    assert review_calls[0]["classification"] == {
        "category": "code",
        "root_cause": "bad call",
        "suggested_fix": "update code",
        "repair_role": "code_adapter",
        "raw_response": "{}",
    }
    assert cast(list[dict[str, object]], review_calls[0]["history"])[0]["repair_role"] == "code_adapter"


def test_review_gate_session_error_fails_closed_on_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    session_mgr.send_command.return_value = '{"status": "ok"}'
    (tmp_path / "dummy.py").write_text("print('ok')\n", encoding="utf-8")

    outcomes = [
        CompletedProcess(args="python dummy.py", returncode=1, stdout="", stderr="RuntimeError: boom"),
        CompletedProcess(args="python dummy.py", returncode=0, stdout="ok", stderr=""),
    ]

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: outcomes.pop(0))
    monkeypatch.setattr(engine, "_analyze_error", lambda **_kwargs: {
        "category": "code",
        "root_cause": "bad call",
        "suggested_fix": "update code",
        "repair_role": "code_adapter",
        "raw_response": "{}",
    })
    monkeypatch.setattr(engine, "_build_repair_prompt", lambda **_kwargs: "repair prompt")
    monkeypatch.setattr(engine, "_extract_fix_summary", lambda *_args, **_kwargs: {
        "modified_files": ["dummy.py"],
        "summary": "Adjusted the failing path.",
    })

    review_calls: list[dict[str, object]] = []

    def mock_review(ctx: dict[str, object]) -> dict[str, object]:
        review_calls.append(ctx)
        return {
            "verdict": "session_error",
            "session_error": "Compaction response is incomplete",
            "reasoning": "Review session command failed: Compaction response is incomplete",
        }

    result = cast(RunResult, cast(object, engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=2,
        enable_review_gate=True,
        review_callable=mock_review,
    )))

    assert len(review_calls) == 1
    assert result["success"] is False
    assert result["status"] == "review_failed"
    assert result["iteration_count"] == 2
    assert "review_gate_summary" not in result


def test_review_gate_rejection_saves_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    session_mgr.send_command.return_value = '{"status": "ok"}'
    (tmp_path / "dummy.py").write_text("x = 1\n", encoding="utf-8")

    outcomes = [
        CompletedProcess(args="python dummy.py", returncode=1, stdout="", stderr="RuntimeError: boom"),
        CompletedProcess(args="python dummy.py", returncode=0, stdout="ok", stderr=""),
    ]

    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return outcomes.pop(0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(engine, "_analyze_error", lambda **_kwargs: {
        "category": "code",
        "root_cause": "bad call",
        "suggested_fix": "update code",
        "repair_role": "code_adapter",
        "raw_response": "{}",
    })
    monkeypatch.setattr(engine, "_build_repair_prompt", lambda **_kwargs: "repair prompt")
    monkeypatch.setattr(engine, "_extract_fix_summary", lambda *_args, **_kwargs: {
        "modified_files": ["dummy.py"],
        "summary": "Adjusted the failing path.",
    })
    improvement_mock = MagicMock(return_value={
        "status": "success",
        "repair_role": "code_adapter",
        "improvement_area": "device placement",
        "suggested_direction": "avoid CPU fallback",
    })
    monkeypatch.setattr(engine, "_run_improvement_iteration", improvement_mock)

    review_calls = [0]

    def mock_review(_ctx: dict[str, object]) -> dict[str, object]:
        review_calls[0] += 1
        return {
            "verdict": "reject",
            "cpu_fallback_detected": True,
            "cpu_fallback_necessary": True,
            "alternative_suggestions": "",
            "reasoning": "test rejection",
        }

    result = engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=2,
        enable_review_gate=True,
        max_review_iterations=1,
        review_callable=mock_review,
    )

    gate_summary = cast(dict[str, object], result["review_gate_summary"])
    snapshot_path = Path(cast(str, gate_summary["last_passing_version_path"]))

    assert result["status"] == "passed_with_reviews"
    assert result["iteration_count"] == 2
    assert review_calls[0] == 1
    assert gate_summary["passing_iteration"] == 2
    assert gate_summary["review_rejections"] == 1
    assert gate_summary["improvement_iterations"] == 1
    assert snapshot_path.exists()
    assert "dummy.py" in snapshot_path.read_text(encoding="utf-8")
    improvement_mock.assert_called_once()


def test_review_gate_max_iterations_reached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    session_mgr.send_command.return_value = '{"status": "ok"}'
    (tmp_path / "dummy.py").write_text("x = 1\n", encoding="utf-8")

    run_calls = [0]

    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        run_calls[0] += 1
        return CompletedProcess(args="python dummy.py", returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(engine, "_run_improvement_iteration", lambda **_kwargs: {
        "status": "success",
        "repair_role": "operator_fixer",
        "improvement_area": "operator coverage",
        "suggested_direction": "stay on accelerator",
    })

    review_calls = [0]

    def mock_review(_ctx: dict[str, object]) -> dict[str, object]:
        review_calls[0] += 1
        return {
            "verdict": "reject",
            "cpu_fallback_detected": True,
            "cpu_fallback_necessary": True,
            "alternative_suggestions": "",
            "reasoning": "reject once",
        }

    result = engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=5,
        enable_review_gate=True,
        max_review_iterations=1,
        review_callable=mock_review,
    )

    assert result["success"] is True
    assert result["status"] == "passed_with_reviews"
    assert result["iteration_count"] == 1
    assert run_calls[0] == 1
    assert review_calls[0] == 1


def test_review_gate_improvement_iteration_returns_result(tmp_path: Path) -> None:
    from core.repair_loop import ReviewGateState

    engine, session_mgr, _artifact_store, prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = lambda role="error_analyzer", lifecycle="persistent": (
        "analyzer-1" if role == "error_analyzer" else "repair-1"
    )
    prompt_loader.load_prompt.return_value = "improvement prompt"
    session_mgr.send_command.side_effect = [
        """analysis
```json
{"repair_role": "operator_fixer", "improvement_area": "fallback handling", "suggested_direction": "keep tensors on NPU", "priority": "high"}
```
""",
        """Modified the file to fix the fallback.
```json
{"modified_files": ["backend_utils.py"], "summary": "Removed CPU fallback"}
```
""",
    ]

    result = engine._run_improvement_iteration(
        gate_state=ReviewGateState(review_reject_reasons=["CPU fallback detected"]),
        project_dir=str(tmp_path),
        entry_script="python dummy.py",
        constraint_summary="stay on accelerator",
    )

    assert result["status"] == "success"
    assert result["repair_role"] == "operator_fixer"
    assert result["improvement_area"] == "fallback handling"
    assert result["suggested_direction"] == "keep tensors on NPU"


def test_review_gate_result_structure() -> None:
    from core.repair_loop import ReviewGateState

    engine, _session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    gate_state = ReviewGateState(
        best_passing_version={"iteration": 3, "snapshot_path": "/tmp/passing.json"},
        review_reject_reasons=["first", "second"],
        improvement_iterations=2,
    )
    context = RepairContext(
        repair_role="code_adapter",
        max_iterations=5,
        iteration_count=3,
        history=[{"iteration": 1, "exit_code": 1}],
    )

    result = engine._build_result(
        status="passed_with_reviews",
        analyzer_session_id="analyzer-1",
        repair_session_ids={"code_adapter": "repair-1"},
        context=context,
        final_stdout="ok",
        final_stderr="",
        final_exit_code=0,
        gate_state=gate_state,
    )

    gate_summary = cast(dict[str, object], result["review_gate_summary"])

    assert result["success"] is True
    assert result["status"] == "passed_with_reviews"
    assert gate_summary == {
        "passing_iteration": 3,
        "review_rejections": 2,
        "improvement_iterations": 2,
        "last_passing_version_path": "/tmp/passing.json",
    }


def test_analyze_error_timeout_returns_default_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    """_analyze_error with TimeoutError retries 3 times then returns communication_error default."""
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.return_value = "analyzer-1"

    call_count = [0]

    def failing_send(*_args: object, **_kwargs: object) -> str:
        call_count[0] += 1
        raise TimeoutError("analysis timed out")

    session_mgr.send_command.side_effect = failing_send
    monkeypatch.setattr("time.sleep", MagicMock())

    result = engine._analyze_error(
        analyzer_session_id="x",
        entry_script="t.py",
        project_dir="/tmp",
        iteration=1,
        error_text="ImportError: blah",
        history=[],
    )

    assert call_count[0] == 3  # max_send_retries=2 → 3 total attempts
    assert result["category"] == "communication_error"
    assert result["repair_role"] == "dependency_fixer"


def test_analyze_error_connection_refused_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """_analyze_error with ConnectionRefusedError retries 3 times then returns default classification."""
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.return_value = "analyzer-1"

    call_count = [0]

    def failing_send(*_args: object, **_kwargs: object) -> str:
        call_count[0] += 1
        raise ConnectionRefusedError("connection refused")

    session_mgr.send_command.side_effect = failing_send
    monkeypatch.setattr("time.sleep", MagicMock())

    result = engine._analyze_error(
        analyzer_session_id="x",
        entry_script="t.py",
        project_dir="/tmp",
        iteration=1,
        error_text="RuntimeError: boom",
        history=[],
    )

    assert call_count[0] == 3
    assert result["category"] == "communication_error"
    assert "connection refused" in result["root_cause"]


def test_analyze_error_success_no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """_analyze_error with valid JSON response succeeds on first call, no retries needed."""
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.return_value = "analyzer-1"

    call_count = [0]

    def success_send(*_args: object, **_kwargs: object) -> str:
        call_count[0] += 1
        return '{"category": "dependency", "root_cause": "missing pkg", "suggested_fix": "pip install", "repair_role": "dependency_fixer"}'

    session_mgr.send_command.side_effect = success_send
    monkeypatch.setattr("time.sleep", MagicMock())

    result = engine._analyze_error(
        analyzer_session_id="x",
        entry_script="t.py",
        project_dir="/tmp",
        iteration=1,
        error_text="ModuleNotFoundError: no module named 'foo'",
        history=[],
    )

    assert call_count[0] == 1  # No retries needed
    assert result["category"] == "dependency"
    assert result["repair_role"] == "dependency_fixer"
    assert result["root_cause"] == "missing pkg"


def test_analyze_error_session_error_returns_communication_error(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.return_value = "analyzer-1"
    session_mgr.send_command.return_value = '{"ok": false, "error": "Compaction response is incomplete"}'
    monkeypatch.setattr("time.sleep", MagicMock())

    result = engine._analyze_error(
        analyzer_session_id="analyzer-1",
        entry_script="t.py",
        project_dir="/tmp",
        iteration=1,
        error_text="RuntimeError: boom",
        history=[],
    )

    assert result["category"] == "communication_error"
    assert result["repair_role"] == "dependency_fixer"
    assert "Compaction response is incomplete" in result["root_cause"]
    assert "Compaction response is incomplete" in result["raw_response"]
    session_mgr.send_command.assert_called_once()


def test_repair_call_timeout_retries_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run() repair send_command with TimeoutError retries 3 times per iteration, then continues to next."""
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    (tmp_path / "dummy.py").write_text("x = 1\n", encoding="utf-8")

    repair_call_count = [0]

    def mock_send(session_id: str, command: str, agent: str = "", timeout: object = None) -> str:
        # Analyzer calls succeed; repair calls always timeout
        if session_id == "repair-1":
            repair_call_count[0] += 1
            raise TimeoutError("repair timed out")
        return "{}"

    session_mgr.send_command.side_effect = mock_send

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(
        args="python dummy.py", returncode=1, stdout="", stderr="RuntimeError: boom",
    ))
    monkeypatch.setattr("time.sleep", MagicMock())
    monkeypatch.setattr(engine, "_analyze_error", lambda **_kwargs: {
        "category": "code",
        "root_cause": "bad call",
        "suggested_fix": "update code",
        "repair_role": "code_adapter",
        "raw_response": "{}",
    })

    result = cast(RunResult, cast(object, engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=2,
    )))

    # 3 retries per iteration × 2 iterations = 6 repair calls total
    assert repair_call_count[0] == 6
    assert result["success"] is False
    assert result["status"] == "max_iterations"
    # Both iterations should have communication_error status from repair failure
    error_history = result["error_history"]
    assert len(error_history) == 2
    for entry in error_history:
        assert str(entry.get("repair_role")) == "code_adapter"


def test_repair_session_error_marks_fix_attempt_communication_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    (tmp_path / "dummy.py").write_text("x = 1\n", encoding="utf-8")

    def mock_send(session_id: str, _command: str, agent: str = "", timeout: int = 600) -> str:
        if session_id == "repair-1":
            return '{"ok": false, "error": "Compaction response is incomplete"}'
        return "{}"

    session_mgr.send_command.side_effect = mock_send
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(
        args="python dummy.py", returncode=1, stdout="", stderr="RuntimeError: boom",
    ))
    monkeypatch.setattr(engine, "_analyze_error", lambda **_kwargs: {
        "category": "operator",
        "root_cause": "bad op",
        "suggested_fix": "fix op",
        "repair_role": "operator_fixer",
        "raw_response": "{}",
    })
    extract_summary = MagicMock(return_value={"modified_files": ["dummy.py"], "summary": "should not run"})
    monkeypatch.setattr(engine, "_extract_fix_summary", extract_summary)

    result = cast(RunResult, cast(object, engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=1,
    )))

    assert result["success"] is False
    assert result["status"] == "max_iterations"
    assert result["error_history"][0].get("repair_role") == "operator_fixer"
    assert "Compaction response is incomplete" in str(result["error_history"][0].get("fix_summary", ""))
    assert result["repair_session_ids"] == {}
    extract_summary.assert_not_called()


def test_repair_call_success_unchanged_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run() with successful subprocess exit 0 → no repair calls made, status is 'success'."""
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    (tmp_path / "dummy.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(
        args="python dummy.py", returncode=0, stdout="ok", stderr="",
    ))

    result = cast(RunResult, cast(object, engine.run(
        entry_script="python dummy.py",
        project_dir=str(tmp_path),
        max_iterations=3,
    )))

    # No repair session should be created, no send_command calls
    session_mgr.send_command.assert_not_called()
    assert result["success"] is True
    assert result["status"] == "success"
    assert result["iteration_count"] == 1
    assert result["error_history"] == []
    assert result["repair_session_ids"] == {}


def _custom_op_phase3_contract(project_dir: Path, *, revision_allowed: bool = False) -> dict[str, object]:
    reports_dir = project_dir / "migration_reports"
    return {
        "entry_script_path": str(project_dir / "validate.py"),
        "run_command": f"{sys.executable} {project_dir / 'validate.py'}",
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_discovery_sources": [
            "source",
            "bindings",
            "wrappers",
            "autograd",
            "aliases",
            "launch",
            "setup",
            "tests",
        ],
        "validation_obligations": [
            "project_local_artifact",
            "runtime_project_api",
            "numeric_performance",
            "no_fallback",
        ],
        "phase5_entry_script_revision_allowed": revision_allowed,
    }


def _custom_op_gate_report() -> dict[str, object]:
    return {
        "inventory_count": 1,
        "manifest_entries": 1,
        "closed_pass_entries": 1,
        "remaining_entries": 0,
        "full_migration_status": "FULL_PASS",
        "project_e2e_passed": True,
        "report_parity_passed": True,
        "performance_report": {
            "complete": True,
            "unit_count": 1,
            "path": "migration_reports/performance.json",
            "project_api_invoked": True,
            "baseline_device": "cpu",
            "custom_device": "ascend_opp",
            "overall_baseline_seconds": 0.05,
            "overall_custom_seconds": 0.04,
            "overall_speedup_vs_baseline": 1.25,
            "overall_project_api_invoked": True,
            "overall_all_units_replaced": True,
            "overall_baseline_device": "cpu",
            "overall_custom_device": "ascend_opp",
            "entries": [
                {
                    "unit_identity": "op_1",
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cpu",
                    "custom_device": "ascend_opp",
                }
            ],
        },
        "source_inventory": {
            "discovery_complete": True,
            "discovery_sources_checked": [
                "source",
                "bindings",
                "wrappers",
                "autograd",
                "aliases",
                "launch",
                "setup",
                "tests",
            ],
            "out_of_scope_source_groups": [],
            "entries": [
                {
                    "name": "op_1",
                    "unit_identity": "op_1",
                    "variant_or_signature": "op_1(float32)",
                    "inventory_granularity": "fine_grained",
                    "native_operator_symbols": ["op_1_forward"],
                    "kernel_functions": ["op_1_kernel"],
                    "kernel_launch_sites": ["csrc/op_1.cpp:launch"],
                    "public_entry_mapping": {"python_api": "pkg.op_1"},
                    "source_evidence": ["csrc/op_1.cpp"],
                    "source_path": "csrc/op_1.cpp",
                }
            ],
        },
        "rows": [{
            "row_id": "op_1",
            "unit_identity": "op_1",
            "variant_or_signature": "op_1(float32)",
            "inventory_granularity": "fine_grained",
            "status": "PASS",
            "native_operator_symbols": ["op_1_forward"],
            "kernel_functions": ["op_1_kernel"],
            "kernel_launch_sites": ["csrc/op_1.cpp:launch"],
            "public_entry_mapping": {"python_api": "pkg.op_1"},
            "source_evidence": ["csrc/op_1.cpp"],
            "opp_custom_op_artifact_evidence": {
                "path": "opp/op_1/libop_1.so",
                "runtime_loaded_artifact_path": "opp/op_1/libop_1.so",
                "project_local": True,
                "built": True,
                "native_artifact": True,
                "compiled_extension": True,
                "build_provenance": {
                    "command": "bash opp/op_1/build.sh",
                    "log_path": "migration_reports/build.log",
                },
            },
            "adapter_evidence": {"imported": True},
            "parity_evidence": {"passed": True},
            "integration_e2e_evidence": {
                "passed": True,
                "project_api_invoked": True,
                "custom_op_route_executed": True,
                "native_custom_op_route_executed": True,
            },
            "same_run_runtime_coverage": {
                "custom_call_count": 2,
                "same_run": True,
                "project_api_route": True,
                "native_custom_op_route_executed": True,
            },
            "performance_evidence": {
                "baseline_seconds": 0.02,
                "custom_seconds": 0.01,
                "speedup_vs_baseline": 2.0,
                "project_api_invoked": True,
                "baseline_device": "cpu",
                "custom_device": "ascend_opp",
            },
            "no_fallback_no_zero_call_no_builtin_contamination": {
                "passed": True,
                "fallback_detected": False,
                "zero_call_detected": False,
                "builtin_contamination_detected": False,
                "baseline_only_detected": False,
                "stub_detected": False,
            },
        }],
    }


def _write_native_custom_op_gate_artifacts(project_dir: Path) -> None:
    artifact_path = project_dir / "opp" / "op_1" / "libop_1.so"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00libascendcl aclrt native-op")
    build_log = project_dir / "migration_reports" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    _ = build_log.write_text("g++ op_kernel.o -lascendcl -o libop_1.so\n", encoding="utf-8")
    _ = (project_dir / "migration_reports" / "migration_manifest.json").write_text(
        json.dumps({"required_units": ["op_1"]}),
        encoding="utf-8",
    )


def test_custom_op_final_gate_rejects_hardware_limitation_accepted_status() -> None:
    gate = _custom_op_gate_report()
    rows = cast(list[dict[str, object]], gate["rows"])
    rows[0]["status"] = "HARDWARE_LIMITATION_ACCEPTED"

    result = validate_custom_op_final_gate(gate)

    assert result["passed"] is False
    assert any("HARDWARE_LIMITATION_ACCEPTED" in error for error in result["errors"])


def test_operator_repair_context_artifact_includes_inventory_goal_and_entry_rules(tmp_path: Path) -> None:
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    (reports_dir / "operator_inventory.json").write_text(json.dumps({
        "operators": [
            {
                "unit_identity": "nms_custom:float32",
                "name": "nms_custom:float32",
                "variant_or_signature": "nms(float32)",
                "inventory_granularity": "fine_grained",
                "native_operator_symbols": ["nms_custom_forward"],
                "kernel_functions": ["nms_kernel"],
                "kernel_launch_sites": ["csrc/nms.cpp:launch_nms"],
                "public_entry_mapping": {"python_api": "ops.nms"},
                "source_file": "csrc/nms.cpp",
                "status": "OPEN",
            },
            {
                "unit_identity": "roi_align:float32",
                "name": "roi_align:float32",
                "variant_or_signature": "roi_align(float32)",
                "inventory_granularity": "fine_grained",
                "native_operator_symbols": ["roi_align_forward"],
                "kernel_functions": ["roi_align_kernel"],
                "kernel_launch_sites": ["csrc/roi.cpp:launch_roi"],
                "public_entry_mapping": {"python_api": "ops.roi_align"},
                "source_file": "csrc/roi.cpp",
                "status": "PASS",
            },
        ]
    }), encoding="utf-8")
    (reports_dir / "migration_manifest.json").write_text(json.dumps({
        "manifest_entries": 2,
        "entries": [{"op_name": "nms_custom:float32"}, {"op_name": "roi_align:float32"}],
    }), encoding="utf-8")
    gate = _custom_op_gate_report()
    gate["inventory_count"] = 2
    gate["manifest_entries"] = 2
    gate["closed_pass_entries"] = 1
    gate["remaining_entries"] = 1
    (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(gate), encoding="utf-8")

    path = write_operator_repair_context_artifact(
        artifact_dir=str(tmp_path / "artifacts"),
        project_dir=str(tmp_path),
        entry_script=f"{sys.executable} {tmp_path / 'validate.py'}",
        phase3_contract=_custom_op_phase3_contract(tmp_path, revision_allowed=True),
    )

    text = Path(path).read_text(encoding="utf-8")
    assert "Total Count: 2" in text
    assert "nms_custom" in text
    assert "roi_align" in text
    assert "variant_or_signature=nms(float32)" in text
    assert "inventory_granularity=fine_grained" in text
    assert "kernel_launch_sites=csrc/nms.cpp:launch_nms" in text
    assert "public_entry_mapping=python_api" in text
    assert "Inventory / Manifest / Final-Gate Closure" in text
    assert "inventory → manifest → final gate" in text.lower() or "inventory / manifest / final-gate" in text.lower()
    assert "source_inventory is the authoritative source-discovery proof" in text
    assert "re-discover or re-close instead of passing" in text
    assert "FULL_PASS is required" in text
    assert "remaining_entries must be 0" in text
    assert "Phase 5 Entry Script Revision Allowed: True" in text
    assert "inventory_manifest_equality" in text
    assert "remaining_entries: 1" in text
    assert "Independent operator units may be split into bounded sub-tasks" in text
    assert "source_inventory" in text
    assert "migration_manifest" in text
    assert "custom_op_final_gate" in text


def test_repair_loop_custom_op_gate_blocks_exit_zero_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager({
        "category": "validation",
        "root_cause": "final gate missing",
        "suggested_fix": "write full custom-op evidence",
        "repair_role": "code_adapter",
    })
    engine, _artifact_store = build_engine(tmp_path, session_mgr)
    (tmp_path / "validate.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(args="", returncode=0))

    result = engine.run(
        f"{sys.executable} {tmp_path / 'validate.py'}",
        str(tmp_path),
        max_iterations=1,
        phase3_contract=_custom_op_phase3_contract(tmp_path),
    )

    assert result["success"] is False
    assert result["status"] == "max_iterations"
    assert "Custom-op final evidence gate failed" in str(result["final_stderr"])
    assert session_mgr.send_command_calls


def test_repair_loop_valid_custom_op_gate_allows_exit_zero_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager({})
    engine, _artifact_store = build_engine(tmp_path, session_mgr)
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _write_native_custom_op_gate_artifacts(tmp_path)
    (reports_dir / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_report()), encoding="utf-8")
    (tmp_path / "validate.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(args="", returncode=0))

    result = engine.run(
        f"{sys.executable} {tmp_path / 'validate.py'}",
        str(tmp_path),
        max_iterations=1,
        phase3_contract=_custom_op_phase3_contract(tmp_path),
    )

    assert result["success"] is True
    assert result["status"] == "success"
    assert session_mgr.send_command_calls == []


def test_repair_loop_custom_op_gate_blocks_oversized_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager({
        "category": "validation",
        "root_cause": "final gate report too large",
        "suggested_fix": "rewrite bounded final gate evidence",
        "repair_role": "code_adapter",
    })
    engine, _artifact_store = build_engine(tmp_path, session_mgr)
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()
    _ = (reports_dir / "custom_op_final_gate.json").write_text("{" + " " * (5 * 1024 * 1024), encoding="utf-8")
    (tmp_path / "validate.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(args="", returncode=0))

    result = engine.run(
        f"{sys.executable} {tmp_path / 'validate.py'}",
        str(tmp_path),
        max_iterations=1,
        phase3_contract=_custom_op_phase3_contract(tmp_path),
    )

    assert result["success"] is False
    assert "custom-op final gate report too large" in str(result["final_stderr"])


def test_extract_fix_summary_session_error_skips_followup() -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()

    result = engine._extract_fix_summary(
        "repair-1",
        '{"ok": false, "error": "Compaction response is incomplete"}',
        max_retries=2,
    )

    assert result.get("modified_files") == []
    assert "Compaction response is incomplete" in str(result.get("summary", ""))
    session_mgr.send_command.assert_not_called()


def test_extract_fix_summary_followup_session_error_returns_summary() -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.send_command.return_value = '{"ok": false, "error": "Compaction response is incomplete"}'

    result = engine._extract_fix_summary("repair-1", "not json", max_retries=1)

    assert result.get("modified_files") == []
    assert "Compaction response is incomplete" in str(result.get("summary", ""))
    session_mgr.send_command.assert_called_once()


def test_improvement_analyzer_session_error_fails() -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.return_value = "analyzer-1"
    session_mgr.send_command.return_value = '{"ok": false, "error": "Compaction response is incomplete"}'

    result = engine._run_improvement_iteration(
        gate_state=ReviewGateState(review_reject_reasons=["bad"]),
        project_dir="/tmp/project",
        entry_script="python main.py",
        constraint_summary="",
    )

    assert result["status"] == "improvement_failed"
    assert "Compaction response is incomplete" in str(result["error"])


def test_improvement_repair_session_error_fails() -> None:
    engine, session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    session_mgr.get_or_create.side_effect = ["analyzer-1", "repair-1"]
    session_mgr.send_command.side_effect = [
        '{"repair_role": "code_adapter", "improvement_area": "device", "suggested_direction": "fix fallback"}',
        '{"ok": false, "error": "Compaction response is incomplete"}',
    ]

    result = engine._run_improvement_iteration(
        gate_state=ReviewGateState(review_reject_reasons=["bad"]),
        project_dir="/tmp/project",
        entry_script="python main.py",
        constraint_summary="",
    )

    assert result["status"] == "improvement_repair_failed"
    assert result["repair_role"] == "code_adapter"
    assert "Compaction response is incomplete" in str(result["error"])


def test_repair_loop_entry_script_action_blocked_without_phase3_flag(tmp_path: Path) -> None:
    engine, _session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    classification = {
        "category": "validation",
        "root_cause": "wrong entry",
        "suggested_fix": "revise",
        "repair_role": "code_adapter",
        "raw_response": "{}",
        "entry_script_action": {
            "needed": True,
            "action": "modify",
            "reason": "switch",
            "entry_script_path": "new.py",
            "run_command": "python new.py",
        },
    }

    result = engine._maybe_apply_entry_script_action(
        classification=cast(ClassificationDict, cast(object, classification)),
        active_contract={"entry_script_path": "old.py", "run_command": "python old.py"},
        project_dir=str(tmp_path),
        revision_count=0,
        max_revisions=2,
    )

    assert result is not None
    assert result["applied"] is False
    assert result["blocked_reason"] == "revision_not_allowed"


def test_repair_loop_entry_script_action_safe_revision_recomputes_command(tmp_path: Path) -> None:
    engine, _session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    revised = tmp_path / "new.py"
    revised.write_text("print('new')\n", encoding="utf-8")
    classification = {
        "category": "validation",
        "root_cause": "wrong entry",
        "suggested_fix": "revise",
        "repair_role": "code_adapter",
        "raw_response": "{}",
        "entry_script_action": {
            "needed": True,
            "action": "modify",
            "reason": "switch",
            "entry_script_path": str(revised),
            "run_command": f"{sys.executable} {revised}",
        },
    }

    result = engine._maybe_apply_entry_script_action(
        classification=cast(ClassificationDict, cast(object, classification)),
        active_contract={
            "entry_script_path": str(tmp_path / "old.py"),
            "run_command": f"{sys.executable} {tmp_path / 'old.py'}",
            "phase5_entry_script_revision_allowed": True,
        },
        project_dir=str(tmp_path),
        revision_count=0,
        max_revisions=2,
    )

    assert result is not None
    assert result["applied"] is True
    cwd, env_vars, cmd_argv, use_shell = engine._prepare_entry_command(str(result["run_command"]), str(tmp_path))
    assert cwd == str(tmp_path)
    assert env_vars == {}
    assert cmd_argv[-1] == str(revised)
    assert use_shell is False


@pytest.mark.parametrize(
    "run_command",
    [
        "python new.py && rm -rf /tmp/nope",
        "python new.py; touch /tmp/pwned",
        "python new.py | tee /tmp/pwned",
        "python new.py || touch /tmp/pwned",
        "python `touch /tmp/pwned`.py",
        "python $(touch /tmp/pwned).py",
        "python new.py > /tmp/pwned",
        "python new.py 2>/tmp/pwned",
        "python new.py< /tmp/input",
        "python new.py\npython other.py",
        "python new.py\rpython other.py",
        "python new.py & python other.py",
    ],
)
def test_repair_loop_entry_script_action_blocks_unsafe_command(tmp_path: Path, run_command: str) -> None:
    engine, _session_mgr, _artifact_store, _prompt_loader, _validator = build_mocked_engine()
    classification = {
        "category": "validation",
        "root_cause": "wrong entry",
        "suggested_fix": "revise",
        "repair_role": "code_adapter",
        "raw_response": "{}",
        "entry_script_action": {
            "needed": True,
            "action": "modify",
            "reason": "shell control",
            "entry_script_path": "new.py",
            "run_command": run_command,
        },
    }

    result = engine._maybe_apply_entry_script_action(
        classification=cast(ClassificationDict, cast(object, classification)),
        active_contract={
            "entry_script_path": "old.py",
            "run_command": "python old.py",
            "phase5_entry_script_revision_allowed": True,
        },
        project_dir=str(tmp_path),
        revision_count=0,
        max_revisions=2,
    )

    assert result is not None
    assert result["applied"] is False
    assert result["blocked_reason"] == "unsafe_run_command"



def test_repair_loop_forces_custom_op_final_gate_evidence_to_operator_fixer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_mgr = MockSessionManager({
        "category": "pathing",
        "root_cause": "Path.relative_to(PROJECT_DIR) is stale",
        "suggested_fix": "Adjust path handling",
        "repair_role": "code_adapter",
    })
    engine, _artifact_store = build_engine(tmp_path, session_mgr)
    (tmp_path / "validate.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(args="", returncode=0))

    result = cast(RunResult, cast(object, engine.run(
        f"{sys.executable} {tmp_path / 'validate.py'}",
        str(tmp_path),
        max_iterations=1,
        phase3_contract=_custom_op_phase3_contract(tmp_path),
    )))

    assert result["success"] is False
    assert result["repair_session_ids"] == {"operator_fixer": "session-2"}
    assert ("operator_fixer", "persistent", "hephaestus") in session_mgr.get_or_create_calls
    assert not any(call[0] == "code_adapter" for call in session_mgr.get_or_create_calls)
    assert result["error_history"][0].get("error_category") == "operator"
    assert result["error_history"][0].get("repair_role") == "operator_fixer"


def test_analyze_error_plain_import_pathing_is_not_forced_to_operator(tmp_path: Path) -> None:
    session_mgr = MockSessionManager({
        "category": "pathing",
        "root_cause": "PROJECT_DIR relative import is wrong",
        "suggested_fix": "Fix sys.path setup",
        "repair_role": "code_adapter",
    })
    engine, _artifact_store = build_engine(tmp_path, session_mgr)

    classification = engine._analyze_error(
        analyzer_session_id="session-1",
        entry_script="python train.py",
        project_dir=str(tmp_path),
        iteration=1,
        error_text="ModuleNotFoundError: No module named 'torch_npu'",
        history=[],
    )

    assert classification["category"] == "pathing"
    assert classification["repair_role"] == "code_adapter"


def test_custom_op_negative_evidence_without_contract_does_not_force_operator() -> None:
    classification: dict[str, object] = {
        "category": "dependency",
        "root_cause": "vendor torch is missing",
        "suggested_fix": "select the container base environment",
        "repair_role": "dependency_fixer",
    }

    routed = force_custom_op_operator_routing_if_needed(
        classification,
        error_text="No custom operators exist; the custom-op evidence gate is not activated.",
        history=[],
        phase3_contract=None,
    )

    assert routed["category"] == "dependency"
    assert routed["repair_role"] == "dependency_fixer"

def test_repair_loop_custom_op_gate_ignores_outside_reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "custom_op_final_gate.json").write_text(json.dumps(_custom_op_gate_report()), encoding="utf-8")
    session_mgr = MockSessionManager({
        "category": "validation",
        "root_cause": "final gate missing",
        "suggested_fix": "write canonical evidence",
        "repair_role": "code_adapter",
    })
    engine, _artifact_store = build_engine(tmp_path, session_mgr)
    contract = _custom_op_phase3_contract(tmp_path)
    contract["reports_dir"] = str(outside)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: CompletedProcess(args="", returncode=0))

    result = engine.run(
        str(contract["run_command"]),
        str(tmp_path),
        max_iterations=1,
        phase3_contract=contract,
    )

    assert result["success"] is False
    assert str(tmp_path / "migration_reports" / "custom_op_final_gate.json") in str(result["final_stderr"])
