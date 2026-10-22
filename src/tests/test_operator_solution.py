"""Framework-owned source packaging and publication regression tests."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.operator_benchmark import evaluate_and_summarize_operator, validate_operator_artifact
from core.operator_solution import initialize_operator_solution, pack_operator_solution
from core.config import load_workflow
from core.types import WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def write_candidate(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "adapter.py").write_text("def run(value):\n    return value\n", encoding="utf-8")
    (root / "solution_spec.json").write_text(json.dumps({
        "name": "candidate",
        "entry_point": "sources/adapter.py::run",
        "sources": [{"path": "sources/adapter.py", "language": "python"}],
    }), encoding="utf-8")
    return root / "solution.json"


def test_pack_reads_real_sources_and_preserves_metadata(tmp_path: Path) -> None:
    solution = write_candidate(tmp_path)
    (tmp_path / "unlisted.cpp").write_text("not part of the candidate", encoding="utf-8")

    result = pack_operator_solution(solution, tmp_path)

    assert result["packed"] is True
    assert result["source_count"] == 1
    packed = json.loads(solution.read_text(encoding="utf-8"))
    assert packed["name"] == "candidate"
    assert packed["entry_point"] == "sources/adapter.py::run"
    assert packed["sources"] == [{
        "path": "sources/adapter.py", "language": "python",
        "content": "def run(value):\n    return value\n",
    }]
    assert result["solution_sha256"] == hashlib.sha256(solution.read_bytes()).hexdigest()
    assert result["source_sha256"]["sources/adapter.py"] == hashlib.sha256(
        (tmp_path / "sources" / "adapter.py").read_bytes()
    ).hexdigest()
    assert "content" not in json.loads((tmp_path / "solution_spec.json").read_text())["sources"][0]


def test_repaired_source_is_repacked_without_model_writeback(tmp_path: Path) -> None:
    solution = write_candidate(tmp_path)
    first = pack_operator_solution(solution, tmp_path)
    repaired = "def run(value):\n    return value + 1\n"
    (tmp_path / "sources" / "adapter.py").write_text(repaired, encoding="utf-8")
    assert validate_operator_artifact(solution, tmp_path)["pack_required"] is True
    result = pack_operator_solution(solution, tmp_path)

    assert result["packed"] is True
    assert result["solution_sha256"] != first["solution_sha256"]
    assert json.loads(solution.read_text())["sources"][0]["content"] == repaired
    assert validate_operator_artifact(solution, tmp_path)["solution_ready"] is True


@pytest.mark.parametrize("path", ["missing.cpp", "../outside.cpp", "/tmp/outside.cpp", "solution.json", "solution_spec.json", "definition.json", "workload.jsonl", "."])
def test_invalid_source_preserves_previous_solution(tmp_path: Path, path: str) -> None:
    solution = write_candidate(tmp_path)
    assert pack_operator_solution(solution, tmp_path)["packed"]
    before = solution.read_bytes()
    (tmp_path / "solution_spec.json").write_text(json.dumps({"sources": [{"path": path}]}))

    result = pack_operator_solution(solution, tmp_path)

    assert result["packed"] is False
    assert result["errors"]
    assert solution.read_bytes() == before


@pytest.mark.parametrize("entries", [
    [{"path": "sources/adapter.py"}, {"path": "sources/./adapter.py"}],
    [{"path": "sources/adapter.py", "content": "stale embedded content"}],
    [{"path": 123}],
    ["sources/adapter.py"],
    [],
])
def test_invalid_spec_cannot_publish(tmp_path: Path, entries: list) -> None:
    solution = write_candidate(tmp_path)
    assert pack_operator_solution(solution, tmp_path)["packed"]
    before = solution.read_bytes()
    (tmp_path / "solution_spec.json").write_text(json.dumps({"sources": entries}))

    result = pack_operator_solution(solution, tmp_path)

    assert result["packed"] is False
    assert solution.read_bytes() == before


@pytest.mark.parametrize("link_parent", [False, True])
def test_source_symlinks_cannot_publish(tmp_path: Path, link_parent: bool) -> None:
    solution = write_candidate(tmp_path)
    if link_parent:
        (tmp_path / "linked").symlink_to(tmp_path / "sources", target_is_directory=True)
        source_path = "linked/adapter.py"
    else:
        (tmp_path / "linked.py").symlink_to(tmp_path / "sources" / "adapter.py")
        source_path = "linked.py"
    (tmp_path / "solution_spec.json").write_text(json.dumps({"sources": [{"path": source_path}]}))

    result = pack_operator_solution(solution, tmp_path)

    assert result["packed"] is False
    assert not solution.exists()


def test_legacy_import_preserves_existing_source_edits(tmp_path: Path) -> None:
    solution = tmp_path / "solution.json"
    solution.write_text(json.dumps({"name": "legacy", "sources": [
        {"path": "adapter.py", "content": "old adapter"},
        {"path": "kernel/main.cpp", "content": "kernel content"},
    ]}), encoding="utf-8")
    (tmp_path / "adapter.py").write_text("repaired adapter", encoding="utf-8")

    result = initialize_operator_solution(solution)

    assert result["initialized"] is True
    assert (tmp_path / "adapter.py").read_text() == "repaired adapter"
    assert (tmp_path / "kernel" / "main.cpp").read_text() == "kernel content"
    spec_before = (tmp_path / "solution_spec.json").read_bytes()
    assert all("content" not in entry for entry in json.loads(spec_before)["sources"])
    assert initialize_operator_solution(solution)["initialized"] is False
    assert (tmp_path / "solution_spec.json").read_bytes() == spec_before
    assert pack_operator_solution(solution, tmp_path)["packed"] is True
    assert json.loads(solution.read_text())["sources"][0]["content"] == "repaired adapter"


def test_missing_legacy_content_does_not_create_partial_sources(tmp_path: Path) -> None:
    solution = tmp_path / "solution.json"
    solution.write_text(json.dumps({"sources": [
        {"path": "adapter.py", "content": "adapter"}, {"path": "missing.cpp"},
    ]}))

    result = initialize_operator_solution(solution)

    assert result["errors"]
    assert not (tmp_path / "adapter.py").exists()
    assert not (tmp_path / "solution_spec.json").exists()


def test_solution_symlink_cannot_overwrite_another_file(tmp_path: Path) -> None:
    solution = write_candidate(tmp_path)
    destination = tmp_path / "keep.json"
    destination.write_text("keep this file")
    solution.symlink_to(destination)

    assert pack_operator_solution(solution, tmp_path)["packed"] is False
    assert destination.read_text() == "keep this file"


@pytest.fixture
def executor(tmp_path: Path) -> WorkflowExecutor:
    return WorkflowExecutor(
        WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"]),
        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
        project_dir=str(tmp_path), output_dir=str(tmp_path),
    )


def run_builtin(executor: WorkflowExecutor, operation: str, context: dict, *, state: dict | None = None, outputs: dict | None = None, producer: str = "") -> tuple[str, dict]:
    phase = executor._mini_phase({
        "id": operation, "type": "builtin", "operation": operation,
        "params": {"producer": producer},
    })
    return executor._execute_builtin_phase(phase, state or {}, context, loop_state=outputs or {})


def test_existing_spec_is_packaged_before_evaluation(executor: WorkflowExecutor, tmp_path: Path) -> None:
    solution = write_candidate(tmp_path)
    context = {"OPERATOR_TASK_DIR": str(tmp_path), "OPERATOR_SOLUTION_PATH": str(solution)}

    status, initialized = run_builtin(executor, "operator_artifact_initialize", context)
    assert status == "success"
    assert initialized["initialized"] is False
    status, packed = run_builtin(executor, "operator_artifact_pack", context)

    assert status == "success"
    assert packed["packed"] is True
    status, validated = run_builtin(executor, "operator_artifact_validate", context)
    assert status == "success"
    assert validated["rebuild_required"] is False


def test_failed_producer_blocks_publication_across_iterations(executor: WorkflowExecutor, tmp_path: Path) -> None:
    solution = write_candidate(tmp_path)
    assert pack_operator_solution(solution, tmp_path)["packed"]
    before = solution.read_bytes()
    (tmp_path / "sources" / "adapter.py").write_text("partially edited source")
    context = {"OPERATOR_TASK_DIR": str(tmp_path), "OPERATOR_SOLUTION_PATH": str(solution)}

    status, failed_pack = run_builtin(executor, "operator_artifact_pack", context,
        outputs={"fix_operator": {"framework_execution_status": "failure"}}, producer="fix_operator")
    assert status == "failure"
    assert solution.read_bytes() == before
    state = {"operator_artifact_pack": failed_pack}
    status, initialized = run_builtin(executor, "operator_artifact_initialize", context, state=state)
    assert status == "failure"
    status, repack = run_builtin(executor, "operator_artifact_pack", context, state=state)
    assert status == "failure"
    status, evaluation = run_builtin(executor, "operator_kernelbench_eval", context, outputs={
        "initialize_operator_artifact": initialized, "operator_artifact_pack": repack,
    })
    assert status == "failure"
    assert evaluation["failure_category"] == "artifact_writeback_failed"
    assert evaluation["operator_passed"] is False
    assert solution.read_bytes() == before

    status, repaired = run_builtin(executor, "operator_artifact_pack", context, state=state,
        outputs={"fix_operator": {"framework_execution_status": "success"}}, producer="fix_operator")
    assert status == "success"
    assert repaired["packed"] is True
    assert solution.read_bytes() != before


def test_evaluation_uses_frozen_packed_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_dir = tmp_path / "task"
    solution = write_candidate(task_dir)
    assert pack_operator_solution(solution, task_dir)["packed"]
    before = solution.read_bytes()
    (task_dir / "workload.jsonl").write_text('{"uuid": "case-001"}\n')

    def evaluate(task, snapshot, kernelbench_root, traces, **kwargs):
        assert snapshot != solution
        assert snapshot.read_bytes() == before
        assert snapshot.stat().st_mode & 0o222 == 0
        solution.write_text('{"sources": []}')
        assert snapshot.read_bytes() == before
        traces.write_text(json.dumps({"uuid": "case-001", "evaluation": {"status": "PASSED"}}) + "\n")
        return {"evaluation_status": "completed", "traces_path": str(traces)}

    monkeypatch.setattr("core.operator_benchmark.run_kernelbench_eval", evaluate)
    result = evaluate_and_summarize_operator(task_dir, solution, tmp_path, tmp_path / "output")

    assert result["operator_passed"] is True
    assert result["evaluated_solution_sha256"] == hashlib.sha256(before).hexdigest()
    manifest = json.loads(Path(result["failure_manifest_path"]).read_text())
    assert manifest["solution_sha256"] == result["evaluated_solution_sha256"]
    assert manifest["evaluated_solution_path"] == result["evaluated_solution_path"]


@pytest.mark.parametrize("fail_first_repair", [False, True])
def test_workflow_generates_and_packs_then_evaluates_repaired_sources(
    executor: WorkflowExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    fail_first_repair: bool,
) -> None:
    workflow = load_workflow("src/workflows/npu_kernelbench_custom_op.yaml")
    sub_workflow = workflow.sub_workflows["operator_repair_loop"]
    solution = tmp_path / "candidate" / "solution.json"
    task = tmp_path / "task"
    task.mkdir()
    (task / "workload.jsonl").write_text('{"uuid": "case-001"}\n')
    loop_vars = {
        "OPERATOR_TASK_DIR": str(task), "OPERATOR_SOLUTION_PATH": str(solution),
        "KERNELBENCH_ROOT": str(tmp_path), "OPERATOR_OUTPUT_DIR": str(tmp_path / "output"),
    }
    executor.artifact_store.artifact_dir = str(tmp_path / "artifacts")
    executor.artifact_store.raw_dir = str(tmp_path / "raw")
    executor.prompt_loader.load_prompt.side_effect = lambda template, context: template
    monkeypatch.setattr(executor, "_append_explicit_runtime_skill_markdown", lambda prompt, *args: (prompt, None))
    generations = []
    repairs = []
    repaired = "def run(value):\n    return value + 1\n"

    def generate(**kwargs):
        if kwargs["phase_id"] == "prepare_operator":
            generations.append(kwargs["phase_id"])
            write_candidate(solution.parent)
            return '{"generated": true}'
        if kwargs["phase_id"] == "analyze_operator_failure":
            return '{"repair_role": "operator_fixer", "root_cause": "candidate needs repair"}'
        assert kwargs["phase_id"] == "fix_operator"
        repairs.append(kwargs["phase_id"])
        if fail_first_repair and len(repairs) == 1:
            (solution.parent / "sources" / "adapter.py").write_text("partial repair")
            raise TimeoutError("producer did not complete")
        (solution.parent / "sources" / "adapter.py").write_text(repaired)
        return '{"fixed": true}'

    evaluated_contents = []

    def evaluate(task_dir, snapshot, kernelbench_root, traces, **kwargs):
        evaluated_contents.append(json.loads(snapshot.read_text())["sources"][0]["content"])
        status = "COMPILE_ERROR" if len(evaluated_contents) == 1 else "PASSED"
        traces.write_text(json.dumps({"uuid": "case-001", "evaluation": {"status": status}}) + "\n")
        return {"evaluation_status": "completed", "traces_path": str(traces)}

    monkeypatch.setattr(executor, "_send_sub_workflow_llm_command", generate)
    monkeypatch.setattr("core.operator_benchmark.run_kernelbench_eval", evaluate)
    state = {}
    first = executor._run_sub_workflow(sub_workflow, loop_vars, state, {}, sub_workflow.phases,
        step_outputs={}, loop_history=[], loop_state={"iteration": 1})

    assert first["step_outputs"]["prepare_operator"]["framework_execution_status"] == "success"
    assert first["step_outputs"]["operator_passed"] is False
    assert len(evaluated_contents) == 1
    assert len(generations) == 1
    assert first["step_outputs"]["operator_artifact_pack"]["packed"] is not fail_first_repair
    second = executor._run_sub_workflow(sub_workflow, loop_vars, state, {}, sub_workflow.phases,
        step_outputs={}, loop_history=[], loop_state={"iteration": 2})

    if fail_first_repair:
        assert second["step_outputs"]["evaluate_operator"]["failure_category"] == "artifact_writeback_failed"
        assert len(evaluated_contents) == 1
        assert second["step_outputs"]["operator_artifact_pack"]["packed"] is True
        second = executor._run_sub_workflow(sub_workflow, loop_vars, state, {}, sub_workflow.phases,
            step_outputs={}, loop_history=[], loop_state={"iteration": 3})

    assert second["step_outputs"]["operator_passed"] is True
    assert "prepare_operator" not in second["step_outputs"]
    assert evaluated_contents[-1] == repaired
    assert len(generations) == 1
