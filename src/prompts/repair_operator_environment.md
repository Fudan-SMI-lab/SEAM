# Operator Runtime Environment Repair

Repair only the execution environment for the current KernelBench operator run.
Do not modify `definition.json`, `workload.jsonl`, Ascend C source, adapter logic, or numerical algorithms.
Do not use CPU fallback or replace vendor torch/torch_npu/CANN packages.

## Context

- Runtime report: `{runtime_report}`
- Runtime configuration: `{runtime_configuration}`
- Error analysis: `{error_analysis}`
- Operator task directory: `{operator_task_dir}`
- KernelBench root: `{kernelbench_root}`

Inspect the current evidence before changing anything. In scope are run-scoped environment variables, dependency closure, CANN/OPP/TBE paths, writable cache/tmp/staging directories, evaluator process cleanup, and stale artifact cleanup. Prefer a run-scoped configuration file over global shell/profile changes.

After any change, run the supplied runtime/evaluator preflight if available. If the evidence indicates an operator source, compile, or numerical defect rather than an environment defect, make no code change and hand off to `operator_fixer`.

Do not infer an environment root cause from a timeout alone. Identify the current
run, failing stage and supporting artifact before changing anything. Limit process
and cache cleanup to resources proven to belong to this run; if ownership is
unknown, report the uncertainty instead of killing processes or deleting shared
state. Record each check's command, exit status and log path. If no execution host
or preflight is available, report verification as pending/blocked, not successful.
An environment preflight is not operator correctness; the controller must rerun
the candidate's full workload after the repair.

Return exactly one JSON object:

```json
{
  "phase_id": "repair_operator_environment",
  "configuration_status": "ready|changed|blocked|handoff",
  "modified_files": [],
  "environment_changes": [],
  "verification": [],
  "handoff_role": "",
  "summary": ""
}
```
