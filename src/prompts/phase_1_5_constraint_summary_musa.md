# Phase 1.5 - Constraint Summary (MUXI Accelerator Family)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## User Constraints
{user_constraints}

## Phase 1 Context
{phase_1_context}

## Goal
Convert user constraints, Phase 0 environment facts, and Phase 1 project analysis into binding downstream rules. Keep the rules platform-generic within the MUXI accelerator family and do not invent APIs not observed in Phase 0.

## Required Actions
1. Preserve explicit entry scripts, model/data paths, container/local execution constraints, device constraints, and no-fallback requirements.
2. State that migrated execution must use the observed MUXI-family accelerator path, not CPU fallback.
3. If the observed stack is CUDA-compatible vendor PyTorch, state that preserving `torch.cuda` may be correct.
4. If the observed stack is native MUSA, state that `torch_musa` or `torch.musa` may be required.
5. If CPU baseline is requested, record it only as a performance comparison baseline, never as migrated execution success.
6. If native/custom operators are present, require compile, load, run, parity, runtime coverage, performance evidence, and final-gate evidence.

## Hard Rules
- Do not say all `torch.cuda` calls must be converted to `torch.musa` unless Phase 0 and Phase 1 prove native MUSA API is required.
- Do not add host-install or public-PyPI package requirements for vendor accelerator packages.
- Return exactly one JSON object and no other JSON.

## Output Format
```json
{
  "constraint_summary": "Use the observed MUXI-family accelerator stack; preserve CUDA-compatible vendor APIs when Phase 0 shows that is the correct runtime API; no CPU fallback.",
  "constraints": ["Use MUXI-family accelerator execution only"],
  "entry_script_priority": "user_constraints_first",
  "api_policy": "preserve_cuda_when_vendor_cuda_compatible",
  "cpu_baseline_policy": "allowed_only_as_performance_baseline_not_fallback",
  "custom_op_policy": "compile_load_run_and_emit_final_gate_evidence_when_native_custom_ops_exist",
  "challenges_flagged": []
}
```
