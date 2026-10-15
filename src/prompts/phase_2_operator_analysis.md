# Phase 1: Operator Analysis

## Operator API policy

The following policy is binding for any adapter or implementation proposal.
Use only the allowlisted APIs and the generated custom-op namespace:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed in the allowlist is
forbidden. If the desired operation is not available in the allowlist, do not
use it as a shortcut; propose an Ascend C implementation or another
allowlisted construction and record the change in the design.

This is a single-operator KernelBench task, not a project migration.

## Context

- Operator task directory: `{operator_task_dir}`
- Definition: `{definition_path}`
- Workloads: `{workload_path}`
- Operator name: `{operator_name}`
- Level: `{level}`
- Target platform: Ascend NPU
- Required implementation type: Ascend C custom-op
- Runtime detection report: `{runtime_report}`
- Runtime configuration result: `{runtime_configuration}`

## Required Actions

1. Use the exact absolute paths supplied above. Phase 0 has already validated
   this task directory and its required files; do not reconstruct the path or
   search for another copy with `find`.
2. Read `definition.json`, `workload.jsonl`, and the reference implementation.
   Use tool calls sequentially: issue at most one tool call per assistant turn,
   then continue after its result until the required inputs have been inspected.
3. Describe the mathematical semantics, input/output contract, dtypes, shapes, scalar attributes, optional inputs, broadcasting, layout, and workload variations.
4. Design an Ascend C implementation: kernel strategy, tiling, block dimension, GM/UB movement, dtype handling, tail handling, and numerical-risk points.
5. Define the adapter entry point that KernelBench will call.
6. Do not modify `definition.json` or `workload.jsonl`.

This run is non-interactive. Do not ask the user questions or invoke a
question/confirmation tool. If a supplied path cannot be read, return the
required JSON with the exact failing path and a clear failure summary; do not
guess another path or claim the analysis succeeded.

## Output Format

Return exactly one JSON object:

```json
{
  "phase_id": "phase_2_operator_analysis",
  "operator_task_dir": "{operator_task_dir}",
  "operator_name": "{operator_name}",
  "level": "{level}",
  "workload_count": 0,
  "operator_kind": "",
  "reference_summary": "",
  "inputs": [],
  "outputs": [],
  "supported_dtypes": [],
  "ascendc_design": {
    "kernel_strategy": "",
    "tiling_strategy": "",
    "block_dim_strategy": "",
    "gm_ub_strategy": "",
    "dtype_strategy": "",
    "tail_strategy": "",
    "numerical_risks": []
  },
  "expected_artifacts": ["solution.json", "op_host", "op_kernel", "adapter", "build_config"]
}
```
