import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import load_workflow
from core.paths import execution_root

PASS = 0
FAIL = 0


def _write_yaml(content: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8")
    f.write(textwrap.dedent(content))
    f.close()
    return f.name


def _test(name: str, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  PASS: {name}")
    except Exception as e:
        FAIL += 1
        print(f"  FAIL: {name} — {e}")


def test_valid_yaml():
    VALID = """
    name: test_workflow
    version: "1.0"
    description: "Test workflow"
    globals:
      max_retry_per_phase: 3
      timeout_per_phase: 600
      rollback_on_failure: true
      artifacts_dir: ".sm-artifacts"
    phases:
      - id: phase_1
        name: "Phase One"
        prompt_template: "prompts/p1.md"
        output_schema:
          type: object
        validator: validators/v1.py
        transitions:
          on_success: phase_2
          on_failure: failed
      - id: phase_2
        name: "Phase Two"
        prompt_template: "prompts/p2.md"
        output_schema:
          type: object
        transitions:
          on_success: complete
          on_failure: failed
    terminals:
      complete: "Done"
      failed: "Fail"
    """
    path = _write_yaml(VALID)
    try:
        wf = load_workflow(path)
        assert wf.name == "test_workflow"
        assert wf.version == "1.0"
        assert wf.description == "Test workflow"
        assert wf.globals["max_retry_per_phase"] == 3
        assert wf.globals["timeout_per_phase"] == 600
        assert len(wf.phases) == 2
        assert wf.phases[0].id == "phase_1"
        assert wf.phases[0].transitions["on_success"] == "phase_2"
        assert wf.terminals == ["complete", "failed"]
    finally:
        os.unlink(path)


def test_missing_name():
    BAD = """
    version: "1.0"
    phases:
      - id: p1
        name: "P1"
        prompt_template: "p.md"
        output_schema: {}
        transitions:
          on_success: done
    terminals:
      done: ok
    """
    path = _write_yaml(BAD)
    try:
        try:
            load_workflow(path)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "name" in str(e).lower()
    finally:
        os.unlink(path)


def test_missing_phases():
    BAD = """
    name: test
    version: "1.0"
    terminals:
      done: ok
    """
    path = _write_yaml(BAD)
    try:
        try:
            load_workflow(path)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "phases" in str(e).lower()
    finally:
        os.unlink(path)


def test_missing_terminals():
    BAD = """
    name: test
    version: "1.0"
    phases:
      - id: p1
        name: "P1"
        prompt_template: "p.md"
        output_schema: {}
        transitions:
          on_success: done
    """
    path = _write_yaml(BAD)
    try:
        try:
            load_workflow(path)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "terminals" in str(e).lower()
    finally:
        os.unlink(path)


def test_invalid_transition():
    BAD = """
    name: test
    version: "1.0"
    phases:
      - id: p1
        name: "P1"
        prompt_template: "p.md"
        output_schema: {}
        transitions:
          on_success: nonexistent_phase
          on_failure: done
    terminals:
      done: ok
    """
    path = _write_yaml(BAD)
    try:
        try:
            load_workflow(path)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "nonexistent_phase" in str(e)
    finally:
        os.unlink(path)


def test_file_not_found():
    try:
        load_workflow("/tmp/does_not_exist_12345.yaml")
        raise AssertionError("Expected FileNotFoundError")
    except FileNotFoundError:
        pass


def test_dict_terminals():
    DICT_TERMINALS = """
    name: test
    version: "1.0"
    phases:
      - id: p1
        name: "P1"
        prompt_template: "p.md"
        output_schema: {}
        transitions:
          on_success: complete
    terminals:
      complete: "Done"
      failed: "Fail"
    """
    path = _write_yaml(DICT_TERMINALS)
    try:
        wf = load_workflow(path)
        assert "complete" in wf.terminals
        assert "failed" in wf.terminals
    finally:
        os.unlink(path)


def test_real_yaml_e2e_quick_test():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "workflow", "e2e_quick_test.yaml"
    )
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"  SKIP: {path} not found")
        return
    wf = load_workflow(path)
    assert wf.name == "e2e_quick_test"
    assert len(wf.phases) == 2
    assert wf.globals["timeout_per_phase"] == 120



def test_relative_workflow_path_resolves_against_execution_root():
    workflow_name = "__relative_workflow_test__.yaml"
    workflow_path = execution_root() / workflow_name
    workflow_path.write_text(
        textwrap.dedent(
            """
            name: relative_test
            version: "1.0"
            phases:
              - id: p1
                name: P1
                prompt_template: prompt.md
                output_schema: {}
                transitions:
                  on_success: complete
                  on_failure: failed
            terminals:
              complete: ok
              failed: bad
            """
        ),
        encoding="utf-8",
    )
    old_cwd = os.getcwd()
    temp_cwd = tempfile.mkdtemp(prefix="src-cwd-")
    try:
        os.chdir(temp_cwd)
        wf = load_workflow(workflow_name)
        assert wf.name == "relative_test"
        assert wf.terminals == ["complete", "failed"]
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(temp_cwd, ignore_errors=True)
        workflow_path.unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print("Task T7: load_workflow tests")
    print("=" * 60)

    _test("valid YAML parsing", test_valid_yaml)
    _test("missing 'name' field", test_missing_name)
    _test("missing 'phases' field", test_missing_phases)
    _test("missing 'terminals' field", test_missing_terminals)
    _test("invalid transition target", test_invalid_transition)
    _test("file not found", test_file_not_found)
    _test("dict-style terminals", test_dict_terminals)
    _test("real e2e_quick_test.yaml", test_real_yaml_e2e_quick_test)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
