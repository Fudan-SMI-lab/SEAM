# Operator Benchmark Failure Analysis

Analyze the latest full-workload KernelBench result for one Ascend C operator.
The following operator API policy is binding for any proposed repair:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed is forbidden. If the
latest failure was caused by such an API, classify it as `api_policy_violation`
and recommend replacing the approach rather than adding the API to the source.
Use only the saved traces, summary, build output, and source evidence.

## Critical: Reference Implementation Is Not Ground Truth

The reference implementation is an **incomplete and potentially flawed baseline**,
not absolute correctness. Common reference defects in practice:

1. **Uninitialized memory**: Returns uninitialized tensors for certain parameter
   combinations (e.g., NMS with `pad=False` returning random values).
2. **Undefined edge cases**: Missing handling for corner cases like empty inputs,
   zero dimensions, or special numerical values (NaN, Inf).
3. **Precision inconsistencies**: Uses inconsistent dtypes across workloads or
   returns mixed precision without clear specification.
4. **Contract ambiguity**: Semantics not fully specified in `definition.json`,
   leaving multiple valid interpretations.

**When to suspect reference defects** (not candidate bugs):
- Multiple unrelated workloads fail with **identical patterns** (e.g., all
  `pad=False` cases fail, all `pad=True` pass).
- Candidate output is **semantically valid** but doesn't match reference
  (e.g., valid NMS indices vs. uninitialized memory).
- Reference code contains **TODOs, FIXMEs, or unimplemented branches**.
- Failure correlation with **specific parameter values** not linked to candidate
  implementation logic.

**When it IS a candidate bug**:
- Failures have **diverse patterns** across workloads.
- Candidate produces **invalid outputs** (wrong shapes, NaN where valid values
  expected, crashes, VA faults).
- Source code has **direct bugs** (off-by-one, null dereference, race conditions).

If you suspect a reference defect, state it explicitly in `root_cause` and set
`repair_role: "none"` with `handoff` explaining the reference issue. Do NOT
force the candidate to match a broken reference.

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
