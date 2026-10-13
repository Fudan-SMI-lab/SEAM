# Repair: KernelBench Ascend C Operator Fixer

This is an operator-level repair task. Repair only the current Ascend C
custom-op candidate for the current KernelBench operator. Do not expand this
into project migration or dataset orchestration.

## Operator API policy

The following policy is binding. Repairs may use only the allowlisted APIs and
the generated custom-op namespace:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed in the allowlist is
forbidden. When a repair would require an unlisted API, replace the approach
with an allowlisted primitive or move the computation into the Ascend C kernel.
Do not add the unlisted API just to make the current workload pass; report the
repair as blocked if no compliant alternative exists.

## Current Operator (minimal write paths)

- Operator: `{operator_name}`
- Level: `{level}`
- Task directory: `{operator_task_dir}`
- Solution: `{solution_path}`
- Source directory: `{solution_source_dir}`
- Target SoC: `{target_soc}`
- Solution type: `{solution_type}`
- Repair iteration: `{repair_iteration}`

## Current Operator Text Context

The controller already read the relevant task and evaluation evidence. Use
this text first; do not open evidence paths yourself or search for duplicate
task directories.

### Definition
```json
{operator_definition_text}
```

### Workload sample
```jsonl
{operator_workload_text}
```

### Phase 1 analysis
```json
{operator_analysis_text}
```

## Current Evaluation Evidence

### Summary
```json
{operator_evaluation_text}
```

### stdout
```text
{operator_stdout_text}
```

### stderr
```text
{operator_stderr_text}
```

### Error analysis
```json
{operator_error_analysis_text}
```

Use the supplied summary and error text to identify the smallest real source
or build fix. The controller will rerun every workload after the repair.

## Hard Rules

- Use an Ascend C native custom-op path.
- Do not add CPU fallback or silently call the reference implementation.
- Do not modify `definition.json` or `workload.jsonl`.
- Do not delete, skip, or rewrite failed workloads.
- Do not modify a report to hide an implementation failure.
- Modify real source/build/adapter files and rebuild the candidate.
- After the repair, the outer workflow will rerun every workload for this operator.
- Do not spend tool calls reopening definition, workload, trace, stdout, or
  stderr files; their relevant contents are already included above.
- Work sequentially: do not issue parallel tool calls. Prefer one inspection
  call, one edit call, and one focused build/validation call.
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
