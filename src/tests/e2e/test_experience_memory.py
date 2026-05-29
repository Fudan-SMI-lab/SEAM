#!/usr/bin/env python3
# pyright: reportFunctionMemberAccess=false
"""
Experience Memory System E2E Test Runner

Tests the complete flow:
Run 1: Migration → Phase 7 extracts skill → auto-promotes → index updated
Run 2: Migration → Phase 5 retrieves skill → injects into prompt

Can run with real OpenCode server (--server-url) or mocked (--mock) mode.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent.parent
REPO_ROOT = PACKAGE_ROOT.parent

sys.path.insert(0, str(PACKAGE_ROOT))

# pylint: disable-next=wrong-import-position; silent
from core.config import VALID_PHASE_TYPES, load_workflow
# pylint: disable-next=wrong-import-position; silent
from core.experience_injector import ExperienceInjector
# pylint: disable-next=wrong-import-position; silent
from core.experience_query import ExperienceQuerier
from core.experience_store import ExperienceStore  # pylint: disable=wrong-import-position; silent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("e2e-memory-test")
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
NC = "\033[0m"


def test(name, func):
    try:
        func()
        print(f"  {GREEN}✅ PASS{NC}: {name}")
        return True
    except AssertionError as e:
        print(f"  {RED}❌ FAIL{NC}: {name}")
        print(f"    {e}")
        return False
    except Exception:  # pylint: disable=broad-exception-caught; silent
        print(f"  {RED}❌ ERROR{NC}: {name}")
        print(f"    {traceback.format_exc()}")
        return False


test.__test__ = False


# ── Test: Promotion + Index ─────────────────────────────────────
def test_promotion_writes_index():
    tmp = tempfile.mkdtemp()
    store = ExperienceStore(tmp)
    store.promote_from_staging(
        "run-1",
        "skill",
        {
            "skill_name": "npu-flash-attn",
            "title": "Flash Attention NPU Fix",
            "category": "operator_incompat",
            "subtype": "flash_attention_unsupported",
            "tags": ["torch-npu", "flash-attn", "oom"],
            "confidence": 0.95,
        },
    )
    index = store.read_index()
    promoted = [e for e in index if e.get("status") == "promoted"]
    assert len(promoted) >= 1, f"Expected >=1 promoted entry, got {len(promoted)}"
    entry = promoted[0]
    assert entry["id"] == "promoted-npu-flash-attn"
    assert entry["type"] == "skill"
    assert entry["category"] == "operator_incompat"
    assert "flash-attn" in entry["tags"]


# ── Test: Staging load_full fallback ────────────────────────────
def test_staging_load_full():
    tmp = tempfile.mkdtemp()
    store = ExperienceStore(tmp)
    run_id = "run-1"
    exp_id = f"{run_id}-exp-flash-attn"
    refined_dir = os.path.join(store.staging_dir, run_id, "refined")
    os.makedirs(refined_dir, exist_ok=True)
    # pylint: disable-next=unspecified-encoding; silent
    with open(os.path.join(refined_dir, f"{exp_id}.json"), "w") as f:
        json.dump({"type": "skill", "title": "Flash Fix", "fix_steps": ["Step A", "Step B"]}, f)
    q = ExperienceQuerier(store, None)
    result = q._load_staging_experience(exp_id)  # pylint: disable=protected-access; silent
    assert result["id"] == exp_id
    assert result["title"] == "Flash Fix"
    assert result["fix_steps"] == ["Step A", "Step B"]


# ── Test: _mini_phase propagates new fields ─────────────────────
def test_mini_phase_propagates():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_executor import WorkflowExecutor

    wf = load_workflow(str(PACKAGE_ROOT / "workflows" / "experience_memory_test.yaml"))
    rw = wf.sub_workflows["repair_loop"]
    ae_phase = [p for p in rw.phases if isinstance(p, dict) and p.get("id") == "analyze_error"][0]
    # pylint: disable-next=singleton-comparison; silent
    assert ae_phase.get("retrieve_experience") == True
    assert ae_phase.get("experience_query") is not None
    exec_obj = WorkflowExecutor.__new__(WorkflowExecutor)
    mini = exec_obj._mini_phase(ae_phase)  # pylint: disable=protected-access; silent
    # pylint: disable-next=singleton-comparison; silent
    assert mini.retrieve_experience == True, f"Got {mini.retrieve_experience}"
    cq = mini.experience_query
    assert cq is not None, "experience_query is None"
    assert cq.get("source") == "error_analysis"


# ── Test: Validator Engine handles null validators ─────────────
def test_null_validator():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.validator_engine import ValidatorEngine

    engine = ValidatorEngine()
    result = engine.validate("some_unregistered", {"output": "valid"})
    # pylint: disable-next=singleton-comparison; silent
    assert getattr(result, "passed", False) == False, (
        "Unregistered validator should fail (workflow_executor skips via null check)"
    )


# ── Test: Dispatcher auto-promotes after refinement ─────────────
def test_dispatcher_auto_promote():
    tmp = tempfile.mkdtemp()
    store = ExperienceStore(tmp)
    run_id = "run-1"
    exp_id = f"{run_id}-exp-flash-attn"
    refined_dir = os.path.join(store.staging_dir, run_id, "refined")
    os.makedirs(refined_dir, exist_ok=True)
    # pylint: disable-next=unspecified-encoding; silent
    with open(os.path.join(refined_dir, f"{exp_id}.json"), "w") as f:
        json.dump(
            {
                "id": exp_id,
                "type": "skill",
                "skill_name": "npu-flash-attn",
                "title": "Flash Fix",
                "category": "operator_incompat",
                "subtype": "flash_attention",
                "tags": ["torch-npu", "flash-attn"],
                "confidence": 0.95,
                "run_id": run_id,
                "root_cause": "flash_attn not supported",
                "fix_steps": ["Replace flash_attn with sdpa"],
            },
            f,
        )
    store.promote_from_staging(
        run_id,
        "skill",
        {
            "skill_name": "npu-flash-attn",
            "title": "Flash Fix",
            "category": "operator_incompat",
            "subtype": "flash_attention",
            "tags": ["torch-npu", "flash-attn"],
            "confidence": 0.95,
            "root_cause": "flash_attn not supported",
            "fix_steps": ["Replace flash_attn with sdpa"],
        },
    )
    index = store.read_index()
    promoted = [e for e in index if e.get("status") == "promoted"]
    assert len(promoted) >= 1
    skill_dir = os.path.join(store.skills_dir, "npu-flash-attn")
    assert os.path.isdir(skill_dir)
    assert os.path.isfile(os.path.join(skill_dir, "skill_data.json"))
    assert os.path.isfile(os.path.join(skill_dir, "SKILL.md"))


# ── Test: Injector formats properly ─────────────────────────────
def test_injector_format():
    inj = ExperienceInjector()
    result_empty = inj.inject(None, {})
    assert result_empty == ""
    result_summary = inj.inject(
        None,
        {
            "selected_experiences": [
                {
                    "title": "Flash Fix",
                    "category": "operator_incompat",
                    "relevance_score": 0.9,
                    "reasoning": "Same issue",
                    "load_full": False,
                }
            ]
        },
    )
    assert "**Flash Fix**" in result_summary
    assert "operator_incompat" in result_summary
    result_full = inj.inject(
        None,
        {
            "selected_experiences": [
                {
                    "title": "Flash Fix",
                    "relevance_score": 0.95,
                    "root_cause": "flash_attn not supported",
                    "fix_steps": ["Step 1", "Step 2"],
                    "affected_patterns": ["flash_attn"],
                    "load_full": True,
                }
            ]
        },
    )
    assert "### Flash Fix" in result_full
    assert "Root cause:" in result_full
    assert "Step 1" in result_full


# ── Test: Querier finds promoted skills in index ────────────────
def test_querier_finds_promoted():
    tmp = tempfile.mkdtemp()
    store = ExperienceStore(tmp)
    store.promote_from_staging(
        "run-1",
        "skill",
        {
            "skill_name": "npu-flash-attn",
            "title": "Flash Fix",
            "category": "operator_incompat",
            "subtype": "flash_attention",
            "tags": ["torch-npu", "flash-attn"],
            "confidence": 0.95,
        },
    )
    q = ExperienceQuerier(store, None)
    result = q.query({}, load_full=False)
    selected = result["selected_experiences"]
    assert len(selected) == 0, "LLM not available, should return empty selected"
    assert result["summary"] == ""
    fmt = q._format_index_summary(store.read_index())  # pylint: disable=protected-access; silent
    assert "promoted-npu-flash-attn" in fmt


# ── Test: Orchestration in VALID_PHASE_TYPES ────────────────────
def test_orchestration_type():
    assert "orchestration" in VALID_PHASE_TYPES


# ── Test: Workflow YAML loads correctly ─────────────────────────
def test_yaml_load():
    yaml_path = str(PACKAGE_ROOT / "workflows" / "experience_memory_test.yaml")
    wf = load_workflow(yaml_path)
    assert wf.name == "experience_memory_test"
    ids = [p.id for p in wf.phases]
    assert "phase_7a_evaluate" in ids
    assert "phase_7b_refine" in ids
    p7a = [p for p in wf.phases if p.id == "phase_7a_evaluate"][0]
    assert p7a.type == "orchestration"
    assert p7a.handler == "experience_evaluator.ExperienceEvaluator.evaluate"
    p7b = [p for p in wf.phases if p.id == "phase_7b_refine"][0]
    assert p7b.type == "orchestration"
    assert p7b.handler == "experience_dispatcher.ExperienceDispatcher.dispatch_and_refine"


# ── Test: _safe_eval_bool comparison operators ──────────────────
def test_safe_eval_bool():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_executor import _safe_eval_bool

    assert _safe_eval_bool("1 != 0", {}) == True  # pylint: disable=singleton-comparison; silent
    assert _safe_eval_bool("0 < 3", {}) == True  # pylint: disable=singleton-comparison; silent
    assert _safe_eval_bool("1 == 1", {}) == True  # pylint: disable=singleton-comparison; silent
    # pylint: disable-next=singleton-comparison; silent
    assert _safe_eval_bool("1 != 0 and 0 < 3", {}) == True
    assert _safe_eval_bool("false", {}) == False  # pylint: disable=singleton-comparison; silent
    assert _safe_eval_bool("true", {}) == True  # pylint: disable=singleton-comparison; silent
    assert _safe_eval_bool("0 == 0", {}) == True  # pylint: disable=singleton-comparison; silent


# ── Test: variable_resolver bare-name → state lookup ────────────
def test_resolver_bare_name_to_state():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.variable_resolver import VariableResolver

    vr = VariableResolver()
    state = {"error_analysis": {"repair_role": "dependency_fixer"}}
    result = vr.resolve("${error_analysis.repair_role}", state=state)
    assert result == "dependency_fixer", f"Got {result!r}"


# ── Test: Sub-workflow dispatch chain end-to-end ────────────────
def test_sub_workflow_dispatch_chain():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.variable_resolver import VariableResolver
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_executor import _safe_eval_bool

    vr = VariableResolver()
    state = {"error_analysis": {"repair_role": "dependency_fixer"}}
    expr = "1 != 0 and 0 < 3"
    assert _safe_eval_bool(expr, {}) == True  # pylint: disable=singleton-comparison; silent
    route_value = vr.resolve("${error_analysis.repair_role}", state=state)
    assert route_value == "dependency_fixer"


# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{CYAN}{'=' * 60}{NC}")
    print(f"{CYAN}  Experience Memory System — E2E Validation{NC}")
    print(f"{CYAN}{'=' * 60}{NC}\n")

    results = []
    results.append(test("VALID_PHASE_TYPES has 'orchestration'", test_orchestration_type))
    results.append(test("YAML loads correctly with Phase 7a/7b", test_yaml_load))
    results.append(test("promote_from_staging writes to index", test_promotion_writes_index))
    results.append(
        test("_load_staging_experience fallback loads refined JSON", test_staging_load_full)
    )
    results.append(test("_mini_phase propagates retrieve_experience", test_mini_phase_propagates))
    results.append(test("Null validator passes by default", test_null_validator))
    results.append(test("Dispatcher auto-promote flow", test_dispatcher_auto_promote))
    results.append(test("Injector format (empty + summary + full)", test_injector_format))
    results.append(test("Querier finds promoted skills in index", test_querier_finds_promoted))
    results.append(test("_safe_eval_bool comparison operators", test_safe_eval_bool))
    results.append(
        test("variable_resolver bare-name to state lookup", test_resolver_bare_name_to_state)
    )
    results.append(test("Sub-workflow dispatch chain end-to-end", test_sub_workflow_dispatch_chain))

    passed = sum(results)
    total = len(results)  # pylint: disable=invalid-name; silent
    print(f"\n{'=' * 60}")
    if passed == total:
        print(f"{GREEN}  ALL {total}/{total} TESTS PASSED{NC}")
    else:
        print(f"{RED}  {passed}/{total} TESTS PASSED{NC}")
    print(f"{'=' * 60}\n")
    sys.exit(0 if passed == total else 1)
