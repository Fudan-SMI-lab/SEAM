# Operator Benchmark Failure Analysis

Analyze the latest full-workload KernelBench result for one Ascend C operator.
The following operator API policy is binding for any proposed repair:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed is forbidden. If the
latest failure was caused by such an API, classify it as `api_policy_violation`
and recommend replacing the approach rather than adding the API to the source.
Use only the saved traces, summary, build output, and source evidence.

Runtime detection report: {runtime_report}
Runtime configuration result: {runtime_configuration}
Latest evaluator result: {evaluation_result}
Deterministic evaluation classification: {evaluation_classification}

Treat `failure_category`, `timeout_classification`, `resource_before`, and
`resource_after` as framework evidence. A `device_memory_fault`,
`device_sync_hang`, `host_crash`, `resource_exhausted`, `candidate_missing`,
or `stale_candidate` result must not be misclassified as a numerical or
operator-code failure without direct source evidence.

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

Decide whether `operator_fixer` should modify the candidate. Treat evaluator
timeouts, incomplete traces, stale aggregates, overlapping evaluator
processes, and missing staging files as environment/evaluator failures, not
operator defects. Do not change `definition.json` or `workload.jsonl`.
Return exactly one JSON object:

```json
{
  "category": "numerical",
  "root_cause": "",
  "repair_role": "operator_fixer|operator_environment_fixer|none",
  "evaluation_valid": true,
  "requires_clean_rerun": false,
  "operator_code_evidence": true,
  "suggested_fix": "",
  "failed_workloads": [],
  "evidence": [],
  "handoff": ""
}
```
