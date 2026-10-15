# Phase 1.5: Operator Runtime Configuration

Review the Phase 1 runtime report and produce a safe, run-scoped configuration plan for the operator workflow. Do not modify global vendor installations, replace torch/torch_npu/CANN, use CPU fallback, or change operator source code.

## Context

- Operator task directory: `{operator_task_dir}`
- KernelBench root: `{kernelbench_root}`
- Target SOC: `{target_soc}`
- Runtime report: `{runtime_report}`

Only propose changes supported by concrete Phase 1 evidence. Prefer environment variables, per-run cache/staging directories, dependency installation in the selected runtime environment, and explicit executable paths. If a blocking issue cannot be safely fixed in scope, mark the configuration as blocked and explain the handoff.

Return exactly one JSON object:

```json
{
  "phase_id": "phase_1_5_operator_runtime_configure",
  "configuration_status": "ready",
  "environment_file": "",
  "changes": [],
  "verified_checks": [],
  "remaining_blocking_issues": [],
  "handoff_role": "",
  "summary": ""
}
```
