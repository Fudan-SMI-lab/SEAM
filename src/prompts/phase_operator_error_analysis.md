# Operator Benchmark Failure Analysis

Analyze the latest full-workload KernelBench result for one Ascend C operator.
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
