# Phase 1.5 - Constraint Summary (MUSA/MUXI)

You are executing `{phase_name}` for `{project_dir}`.

## Inputs
User constraints:
{user_constraints}

## Goal
Convert user constraints into concrete migration rules for downstream phases.

## Required Actions
1. Preserve explicit entry scripts, model/data paths, container constraints, device constraints, and no-fallback requirements.
2. State that migrated execution must use MUSA/MUXI accelerator paths, not CPU fallback.
3. If CPU baseline is requested, record it only as performance comparison baseline; it is not a migrated execution fallback.
4. If native/custom operators are present, require compile, load, run, parity, runtime coverage, performance evidence, and final-gate evidence.

## Output Format
Return exactly one JSON object:

```json
{
  "constraints": ["Use MUSA/MUXI accelerator execution only"],
  "entry_script_priority": "user_constraints_first",
  "cpu_baseline_policy": "allowed_only_as_performance_baseline_not_fallback",
  "custom_op_policy": "compile_load_run_and_emit_final_gate_evidence_when_native_custom_ops_exist"
}
```
