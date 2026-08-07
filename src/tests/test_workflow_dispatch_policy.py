"""RED tests for bug #15 — 3-chain dispatch deadlock.

The deadlock chain (all three links currently broken at HEAD 6018b85):

1. ``repair_loop.RepairLoopEngine._validate_classification`` (L2901-2911) validates
   ``repair_role`` against the **global** ``_REPAIR_ROLES`` set (4 roles) instead of
   the **current workflow's dispatch route map**. A workflow whose route map only has
   3 routes (e.g. ``src/workflows/ppu_sglang.yaml`` L267-270) will happily accept
   ``repair_role: final_gate_report_fixer`` even though it has no route for it.

2. ``workflow_dispatch_policy.select_dispatch_route`` (L18-28) silently returns
   ``target=None`` for an unknown ``route_key`` (``routes.get(route_key)``), so
   ``WorkflowExecutor._execute_dispatch_phase`` (L4027-4067) returns ``None`` with no
   explicit error marker.

3. ``WorkflowExecutor._run_sub_workflow`` (L5208-5222) treats the falsy ``next_id``
   from the dispatch phase as "dispatch executed, no active target": in the ``else``
   branch it still populates ``dispatch_route = dispatch_targets.get(phase_id)`` with
   **ALL** fix phase ids. On the next loop pass every fix phase then hits
   L4934 ``elif dispatch_route and phase_id in dispatch_route: continue`` and is
   **silently skipped** — no fixer ever runs, the repair loop re-diagnoses forever and
   eventually dies by stagnation. That is the 3-chain dispatch deadlock.

These tests assert the NEW fail-closed contract. They must FAIL (AssertionError) on
current code and PASS once T6 makes each link explicit/route-map-scoped.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from core.repair_loop import RepairLoopEngine
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_dispatch_policy import select_dispatch_route
from core.workflow_executor import WorkflowExecutor

# Route map declared by ppu_sglang.yaml L267-270 (exactly 3 routes, NO final_gate_report_fixer).
PPU_SGLANG_ROUTE_MAP = {
    "dependency_fixer": "fix_dependency",
    "code_adapter": "fix_code",
    "operator_fixer": "fix_operator",
}

# Route map declared by musa_muxi_custom_op.yaml L275 + musa_muxi_migration_v2_... L278
# (control workflows that DO declare final_gate_report_fixer -> fix_report).
MUSA_MUXI_ROUTE_MAP = {
    "dependency_fixer": "fix_dependency",
    "code_adapter": "fix_code",
    "operator_fixer": "fix_operator",
    "final_gate_report_fixer": "fix_report",
}


def _classification_payload(repair_role: str) -> dict[str, object]:
    return {
        "category": "validation",
        "root_cause": "final gate evidence incomplete",
        "suggested_fix": "complete the final gate report",
        "repair_role": repair_role,
    }


def _classify_with_route_map(
    payload: dict[str, object], route_map: dict[str, str]
) -> dict[str, object]:
    """Call _validate_classification under the NEW route-map-scoped contract.

    The current signature only takes ``data`` (route-map blind). Once T6 makes it
    route-map-aware (``route_map`` kwarg), the extra kwarg path is exercised. Until
    then we fall back to the current signature so the assertion targets the actual
    (broken) behavior rather than a TypeError.
    """
    try:
        return RepairLoopEngine._validate_classification(payload, route_map=route_map)
    except TypeError:
        return RepairLoopEngine._validate_classification(payload)


def _dispatch_executor(tmp_path: Path) -> WorkflowExecutor:
    session_mgr = MagicMock()
    session_mgr.get_or_create.side_effect = lambda role, lifecycle: f"session:{role}"
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.side_effect = lambda template, _ctx: template
    validator = MagicMock()
    return WorkflowExecutor(
        WorkflowDefinition(
            name="dispatch-policy-red",
            version="1.0",
            phases=[],
            terminals=["complete"],
        ),
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )


def _deadlock_mini_workflow() -> tuple[SubWorkflowDefinition, list[dict]]:
    """Mini repair_loop with the ppu_sglang-style 3-route dispatch map."""
    sub_wf_phases = [
        {
            "id": "analyze_error",
            "type": "llm",
            "prompt_template": "analyze_prompt",
            "agent": "error_analyzer",
            "output_as": "error_analysis",
        },
        {
            "id": "repair_dispatch",
            "type": "dispatch",
            "route_field": "${error_analysis.repair_role}",
            "routes": dict(PPU_SGLANG_ROUTE_MAP),
        },
        {
            "id": "fix_dependency",
            "type": "llm",
            "prompt_template": "fix_dependency_prompt",
            "agent": "dependency_fixer",
        },
        {
            "id": "fix_code",
            "type": "llm",
            "prompt_template": "fix_code_prompt",
            "agent": "code_adapter",
        },
        {
            "id": "fix_operator",
            "type": "llm",
            "prompt_template": "fix_operator_prompt",
            "agent": "operator_fixer",
        },
    ]
    return (
        SubWorkflowDefinition(
            id="repair_loop",
            type="loop",
            max_iterations=1,
            phases=sub_wf_phases,
        ),
        sub_wf_phases,
    )


# ── (a) select_dispatch_route: unknown repair_role must NOT silently return None ──


def test_select_dispatch_route_unknown_role_must_raise_or_return_explicit_error() -> (
    None
):
    """select_dispatch_route must fail closed for a role absent from the route map.

    Current (buggy) behavior: ``routes.get(route_key)`` returns ``None`` with no
    exception and no error marker — the caller cannot distinguish "no route" from a
    genuine dispatch decision, so the loop deadlocks. NEW contract: raise or return an
    explicit error marker.
    """
    try:
        decision = select_dispatch_route(
            "final_gate_report_fixer", PPU_SGLANG_ROUTE_MAP, {}
        )
    except (KeyError, ValueError, RuntimeError):
        return  # explicit raise — new contract satisfied

    assert decision.target is not None, (
        "BUG #15 chain 2: select_dispatch_route('final_gate_report_fixer', "
        f"{sorted(PPU_SGLANG_ROUTE_MAP)}) silently returned target=None. "
        "New contract: an unknown repair_role must raise or return an explicit error "
        "marker so dispatch fails closed instead of producing a no-op route that "
        "deadlocks the repair loop."
    )


# ── (b) + regression: dispatch failure must NOT populate dispatch_route with all
#        fix phases (which then all get skipped via L4934) ──


def test_run_sub_workflow_dispatch_failure_does_not_skip_all_fix_phases(
    tmp_path: Path,
) -> None:
    """Deadlock regression: a failed dispatch must not silently skip every fix phase.

    Current (buggy) behavior: dispatch resolves to ``None`` (unknown repair_role), the
    ``else`` branch at L5220-5222 sets ``dispatch_route`` to ALL fix phases, and the
    next loop pass skips fix_dependency/fix_code/fix_operator via L4934. No fixer ever
    runs → the loop repeats forever → stagnation deadlock.
    """
    sub_workflow, sub_wf_phases = _deadlock_mini_workflow()
    executor = _dispatch_executor(tmp_path)

    # analyze_error reports a repair_role that is valid per the GLOBAL _REPAIR_ROLES
    # but has NO route in this workflow's 3-route map → dispatch must fail closed.
    executor.session_mgr.send_command.side_effect = [
        json.dumps(_classification_payload("final_gate_report_fixer")),
        json.dumps({"fixed": True}),
        json.dumps({"fixed": True}),
        json.dumps({"fixed": True}),
    ]

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={},
        state={},
        context={},
        sub_wf_phases=sub_wf_phases,
        step_outputs={},
        loop_state={},
    )
    step_outputs = result["step_outputs"]

    ran_fix_phase = any(
        fix_id in step_outputs
        for fix_id in ("fix_dependency", "fix_code", "fix_operator")
    )
    assert ran_fix_phase, (
        "BUG #15 chain 3: dispatch route resolution failed "
        "(repair_role='final_gate_report_fixer' not in the 3-route map) → "
        "_run_sub_workflow populated dispatch_route with ALL fix phases (L5221-5222) "
        "→ L4934 skipped every fix phase. No fixer ran → repair loop deadlocks. "
        f"step_outputs keys = {sorted(step_outputs)}"
    )


def test_run_sub_workflow_dispatch_failure_must_be_recorded_explicitly(
    tmp_path: Path,
) -> None:
    """A failed dispatch must be recorded as an explicit error, not dispatched_to=None.

    Current (buggy) behavior: ``phase_output = {"dispatched_to": None}`` — the failure
    is indistinguishable from a skipped dispatch and nothing downstream can react.
    NEW contract: an explicit error marker (raised, or a non-None error field).
    """
    sub_workflow, sub_wf_phases = _deadlock_mini_workflow()
    executor = _dispatch_executor(tmp_path)
    executor.session_mgr.send_command.side_effect = [
        json.dumps(_classification_payload("final_gate_report_fixer")),
        json.dumps({"fixed": True}),
    ]

    result = executor._run_sub_workflow(
        sub_workflow,
        loop_vars={},
        state={},
        context={},
        sub_wf_phases=sub_wf_phases,
        step_outputs={},
        loop_state={},
    )
    dispatch_output = result["step_outputs"].get("repair_dispatch", {})
    dispatched_to = dispatch_output.get("dispatched_to") if isinstance(
        dispatch_output, dict
    ) else None
    assert dispatched_to is not None, (
        "BUG #15 chain 3: dispatch failure was recorded as {'dispatched_to': None}, "
        "i.e. silently. New contract: the failure must be explicit (raise or an "
        "explicit error marker) so the loop can stop instead of deadlocking."
    )


def test_dispatch_phase_failure_must_be_explicit_not_silent(tmp_path: Path) -> None:
    """_execute_dispatch_phase must not silently return None for an unknown route.

    Current (buggy) behavior: unknown ``repair_role`` → ``select_dispatch_route``
    returns target=None → ``_execute_dispatch_phase`` returns ``None`` (L4061-4067).
    NEW contract: raise or return an explicit error marker.
    """
    executor = _dispatch_executor(tmp_path)
    phase = PhaseDefinition(
        id="repair_dispatch",
        name="Repair Dispatch",
        prompt_template="",
        output_schema={},
        type="dispatch",
    )
    setattr(
        phase,
        "params",
        {
            "route_field": "${error_analysis.repair_role}",
            "routes": dict(PPU_SGLANG_ROUTE_MAP),
        },
    )
    state = {"error_analysis": {"repair_role": "final_gate_report_fixer"}}

    try:
        target = executor._execute_dispatch_phase(
            phase,
            state=state,
            context={},
            loop_vars={},
            loop_state={},
            step_outputs={},
        )
    except (KeyError, ValueError, RuntimeError):
        return  # explicit raise — new contract satisfied

    assert target is not None, (
        "BUG #15 chain 2/3: _execute_dispatch_phase silently returned None for "
        "repair_role='final_gate_report_fixer' which has no route in the 3-route map. "
        "New contract: the dispatch failure must be explicit so _run_sub_workflow "
        "fails closed instead of skipping all fix phases."
    )


# ── (c) _validate_classification must be route-map-scoped, not global ──


def test_validate_classification_rejects_role_absent_from_workflow_route_map() -> None:
    """repair_role must be validated against the workflow's route map, not the global
    _REPAIR_ROLES set.

    ppu_sglang.yaml declares only 3 routes (dependency_fixer/code_adapter/operator_fixer)
    → ``final_gate_report_fixer`` must be REJECTED. Current (buggy) behavior: the global
    4-role check accepts it, which lets the LLM pick a role with no dispatch route and
    triggers the deadlock.
    """
    result = _classify_with_route_map(
        _classification_payload("final_gate_report_fixer"), PPU_SGLANG_ROUTE_MAP
    )
    assert result["passed"] is False, (
        "BUG #15 chain 1: _validate_classification ACCEPTED repair_role="
        "'final_gate_report_fixer' even though ppu_sglang.yaml's route map "
        f"({sorted(PPU_SGLANG_ROUTE_MAP)}) has no such route. Validation must be "
        "scoped to the current workflow's route map (NOT the global _REPAIR_ROLES "
        "4-role set) so a role with no dispatch route is rejected up front."
    )


def test_validate_classification_route_map_scoped_control_accepts_declared_role() -> (
    None
):
    """CONTROL: workflows that DO declare final_gate_report_fixer → fix_report must
    keep accepting the role. Guards against over-tightening to a global disable.

    musa_muxi_custom_op.yaml L275 and
    musa_muxi_migration_v2_container_baseaware_entryfix.yaml L278 both declare
    ``final_gate_report_fixer: fix_report`` — the role must remain valid there.
    (PASSES on current code; must keep passing after T6's route-map-scoped fix.)
    """
    result = _classify_with_route_map(
        _classification_payload("final_gate_report_fixer"), MUSA_MUXI_ROUTE_MAP
    )
    assert result["passed"] is True, (
        "Route-map scoping must be per-workflow: musa_muxi_* workflows declare "
        "final_gate_report_fixer: fix_report, so the role must still be accepted "
        "for those route maps. A global disable of the role is NOT the fix."
    )
