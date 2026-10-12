# Repair: KernelBench Ascend C Operator Fixer

This is an operator-level repair task. Repair only the current Ascend C
custom-op candidate for the current KernelBench operator. Do not expand this
into project migration or dataset orchestration.

## Operator API policy

The following policy is binding. Repairs may use only the allowlisted APIs and
the generated custom-op namespace:

{operator_constraints}

## Current Operator

- Operator: `{operator_name}`
- Level: `{level}`
- Task directory: `{operator_task_dir}`
- Definition: `{definition_path}`
- Workloads: `{workload_path}`
- Solution: `{solution_path}`
- Source directory: `{solution_source_dir}`
- Repair iteration: `{repair_iteration}`

## Current Evidence

- Latest trace JSONL: `{latest_trace_path}`
- Latest summary: `{latest_evaluation_summary_path}`
- Build artifact path: `{build_artifact_path}`
- Complete stdout: `{latest_complete_stdout_artifact_path}`
- Complete stderr: `{latest_complete_stderr_artifact_path}`

Read the complete evidence before editing. Inspect workload-level status,
`max_absolute_error`, `max_relative_error`, `matched_ratio`, and dtype-specific
`tolerance`. The correctness contract uses KernelBench's default dtype tolerance
and `required_matched_ratio = 0.99`.

## Hard Rules

- Use an Ascend C native custom-op path.
- Do not add CPU fallback or silently call the reference implementation.
- Do not modify `definition.json` or `workload.jsonl`.
- Do not delete, skip, or rewrite failed workloads.
- Do not modify a report to hide an implementation failure.
- Modify real source/build/adapter files and rebuild the candidate.
- After the repair, the outer workflow will rerun every workload for this operator.
- If the failure is environment, dependency, or task-schema related rather than
  an operator implementation issue, state the handoff clearly instead of making
  speculative kernel edits.

## Output Format

Return exactly one JSON object:

```json
{
  "modified_files": [],
  "summary": "",
  "operator": "{operator_name}",
  "iteration": 0,
  "build_status": "success|failed|blocked",
  "evaluation_status": "pending|success|failed|blocked",
  "failure_category": "",
  "remaining_failures": [],
  "handoff_role": "",
  "verification": {
    "definition_unchanged": true,
    "workload_unchanged": true,
    "cpu_fallback_added": false,
    "reference_bypass_detected": false,
    "requires_full_workload_rerun": true
  }
}
```
