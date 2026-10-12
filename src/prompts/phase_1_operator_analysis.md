# Phase 1: Operator Analysis

This is a single-operator KernelBench task, not a project migration.

## Context

- Operator task directory: `{operator_task_dir}`
- Definition: `{definition_path}`
- Workloads: `{workload_path}`
- Operator name: `{operator_name}`
- Level: `{level}`
- Target platform: Ascend NPU
- Required implementation type: Ascend C custom-op

## Required Actions

1. Read `definition.json`, `workload.jsonl`, and the reference implementation.
2. Describe the mathematical semantics, input/output contract, dtypes, shapes, scalar attributes, optional inputs, broadcasting, layout, and workload variations.
3. Design an Ascend C implementation: kernel strategy, tiling, block dimension, GM/UB movement, dtype handling, tail handling, and numerical-risk points.
4. Define the adapter entry point that KernelBench will call.
5. Do not modify `definition.json` or `workload.jsonl`.

## Output Format

Return exactly one JSON object:

```json
{
  "phase_id": "phase_1_operator_analysis",
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
