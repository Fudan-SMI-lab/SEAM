# Phase 2: Ascend C Custom-Op Preparation

Prepare the current single KernelBench operator for evaluation.

## Context

- Operator workspace: `{operator_task_dir}`
- Operator name: `{operator_name}`
- Definition: `{definition_path}`
- Workloads: `{workload_path}`
- Target SoC: `{target_soc}`
- Analysis output: `{previous_outputs}`

## Required Actions

Create or update the real Ascend C candidate implementation in the operator workspace.

Required artifacts:

- `solution.json` describing the candidate implementation;
- Ascend C host tiling/source;
- Ascend C device kernel/source;
- KernelBench adapter entry point;
- build configuration required by the chosen Ascend C builder;
- an operator manifest describing the generated implementation.

Do not modify `definition.json` or `workload.jsonl`. Do not add CPU fallback, reference bypass, stub success, or report-only evidence. The implementation must be the candidate evaluated by KernelBench.

Run the narrowest available build or structural validation and report actual results.

## Output Format

Return exactly one JSON object:

```json
{
  "phase_id": "phase_2_custom_op_prepare",
  "operator": "{operator_name}",
  "operator_task_dir": "{operator_task_dir}",
  "solution_type": "ascendc",
  "solution_path": "{operator_task_dir}/solution.json",
  "source_dir": "{operator_task_dir}/sources",
  "manifest_path": "",
  "build_config_path": "",
  "prepared": true,
  "build_status": "not_run|success|failed",
  "modified_files": [],
  "summary": ""
}
```
