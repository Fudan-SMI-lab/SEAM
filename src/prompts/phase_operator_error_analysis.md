# Operator Benchmark Failure Analysis

Analyze the latest full-workload KernelBench result for one Ascend C operator.
The following operator API policy is binding for any proposed repair:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed is forbidden. If the
latest failure was caused by such an API, classify it as `api_policy_violation`
and recommend replacing the approach rather than adding the API to the source.
Use only the saved traces, summary, build output, and source evidence.

Classify the primary problem as one of:

```text
task_schema
build
link
install
adapter
runtime
numerical
fallback
environment
unknown
```

Decide whether `operator_fixer` should modify the candidate. Do not change
`definition.json` or `workload.jsonl`. Return exactly one JSON object:

```json
{
  "category": "numerical",
  "root_cause": "",
  "repair_role": "operator_fixer",
  "suggested_fix": "",
  "failed_workloads": [],
  "evidence": [],
  "handoff": ""
}
```
