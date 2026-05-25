#!/usr/bin/env python3
"""Pre-flight diagnostics for experience memory E2E."""
import sys, os, json
from pathlib import Path

SM_ADAPT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SM_ADAPT))
sys.path.insert(0, str(SM_ADAPT / "tests" / "e2e"))

from core.workflow_executor import WorkflowExecutor
from core.experience_store import ExperienceStore
from core.config import load_workflow, VALID_PHASE_TYPES
from core.validator_engine import ValidatorEngine
from validators.validate_env_detect import validate as validate_env_detect

passes = 0
fails = 0

def check(name, fn):
    global passes, fails
    try:
        fn()
        print(f"  ✅ {name}")
        passes += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        fails += 1

print("=" * 60)
print("  Pre-flight Diagnostics — Experience Memory E2E")
print("=" * 60)
print()

# 1. Validators
def v1():
    v = ValidatorEngine()
    v.register_validator("env_detect", validate_env_detect)
    assert v.validate("env_detect", {}).passed == False, "env_detect data check"
    assert v._validators["env_detect"] == validate_env_detect
check("Validator engine: env_detect registered and called", v1)

# 2. Workflow YAML loads
def v2():
    wf = load_workflow(str(SM_ADAPT / "workflows" / "npu_migration_v2.yaml"))
    ids = [p.id for p in wf.phases]
    assert "phase_7a_evaluate" in ids
    p7a = [p for p in wf.phases if p.id == "phase_7a_evaluate"][0]
    assert p7a.type == "orchestration"
    assert p7a.handler == "experience_evaluator.ExperienceEvaluator.evaluate"
    p7b = [p for p in wf.phases if p.id == "phase_7b_refine"][0]
    assert p7b.type == "orchestration"
    assert p7b.handler == "experience_dispatcher.ExperienceDispatcher.dispatch_and_refine"
check("YAML: Phase 7a/7b loaded + orchestration type", v2)

# 3. Sub-workflow experience fields
def v3():
    wf = load_workflow(str(SM_ADAPT / "workflows" / "npu_migration_v2.yaml"))
    rw = wf.sub_workflows["repair_loop"]
    ae = [p for p in rw.phases if isinstance(p, dict) and p["id"] == "analyze_error"][0]
    assert ae.get("retrieve_experience") == True
    assert ae.get("experience_query") is not None
    # Test _mini_phase propagation
    exec_obj = WorkflowExecutor.__new__(WorkflowExecutor)
    mini = exec_obj._mini_phase(ae)
    assert mini.retrieve_experience == True
    assert mini.experience_query is not None
    assert mini.experience_query.get("source") == "error_analysis"
check("Sub-workflow: analyze_error has retrieve_experience + _mini_phase propagates", v3)

# 4. ExperienceStore
def v4():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ExperienceStore(td)
        store.promote_from_staging("test-run", "skill", {
            "skill_name": "test-skill", "title": "Test", "category": "x",
            "subtype": "y", "tags": ["a", "b", "c"], "confidence": 0.9,
        })
        index = store.read_index()
        promoted = [e for e in index if e.get("status") == "promoted"]
        assert len(promoted) >= 1, f"No promoted entry in index"
check("ExperienceStore: promote writes to index", v4)

# 5. Import chain
def v5():
    from core.experience_refiner import ExperienceRefiner
    from core.experience_dispatcher import ExperienceDispatcher
    from core.experience_injector import ExperienceInjector
    from core.experience_query import ExperienceQuerier
check("All experience modules import", v5)

# 6. E2E test validator registration (simulated)
def v6():
    from validators.validate_env_detect import validate as v_ed
    from validators.validate_project_analysis import validate as v_pa
    from validators.validate_venv import validate as v_venv
    from validators.validate_entry_script import validate as v_es
    from validators.validate_entry_static import validate as v_est
    from validators.validate_rule_migration import validate as v_rm
    from validators.validate_validation_final import validate as v_vf
    from validators.validate_reports import validate as v_rep
    engine = ValidatorEngine()
    for name, fn in [
        ("env_detect", v_ed), ("project_analysis", v_pa),
        ("venv", v_venv), ("entry_script", v_es),
        ("entry_static", v_est), ("rule_migration", v_rm),
        ("validation_final", v_vf), ("reports", v_rep),
        ("repair_classification", lambda d: {"passed": True}),
    ]:
        engine.register_validator(name, fn)
    assert engine.validate("env_detect", {}).passed == False  # validator runs, returns errors for missing data
    assert engine.validate("repair_classification", {}).passed == True
check("E2E validator registration chain", v6)

# 7. Dispatch skip logic (code structure check)
def v7():
    import inspect
    src = inspect.getsource(WorkflowExecutor._run_sub_workflow)
    assert 'elif dispatch_route and phase_id in dispatch_route:' in src, "Sub-workflow dispatch filter missing"
check("Dispatch: sub-workflow has skip logic for un-routed targets", v7)

# 8. Memory directory exists
def v8():
    mem = SM_ADAPT / "memory"
    assert mem.is_dir(), f"{mem} does not exist"
    assert (mem / "index").is_dir()
    assert (mem / "staging").is_dir()
    assert (mem / "cases").is_dir()
check("Memory directories exist", v8)

print()
print(f"{'=' * 60}")
if fails == 0:
    print(f"  ALL {passes}/{passes + fails} CHECKS PASSED")
else:
    print(f"  {passes} PASSED, {fails} FAILED")
    print("  ⚠️  Fix failures before running E2E")
print(f"{'=' * 60}\n")

sys.exit(1 if fails > 0 else 0)
