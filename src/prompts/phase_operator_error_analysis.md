# Operator Benchmark Failure Analysis

Analyze the latest full-workload KernelBench result for one Ascend C operator.
The following operator API policy is binding for any proposed repair:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed is forbidden. If the
latest failure was caused by such an API, classify it as `api_policy_violation`
and recommend replacing the approach rather than adding the API to the source.
Use only the saved traces, summary, build output, and source evidence.

## Evidence Integrity and Causal Diagnosis

Keep each diagnosis tied to the current iteration and candidate. From the saved
artifacts record, when available: run/iteration identifier, candidate identity
or source hash, executed command, exit status, first failing stage, workload ID,
source location and log/trace path. Mark absent or truncated evidence explicitly;
never invent provenance or treat a log tail as a complete compiler diagnostic.
Read the relevant original artifact when the supplied excerpt is insufficient.
Do not combine an older candidate's success with the current candidate's failure.

Compiler errors require inspection of the failing expression, declarations and
active API signature, not keyword matching or predetermined source replacement.
For runtime failures distinguish an observed device fault from a suspected source
cause. A timeout, zero traces or failed synchronization alone does not identify
whether the candidate, runtime, evaluator or API transport caused the failure.
Separate confirmed observations, hypotheses and missing evidence; specify the
smallest next check that could discriminate between plausible causes.

Treat the reference as the comparison baseline, not as proof of semantics. Only
report a suspected reference defect with a concrete contract/source/workload
conflict. Correlated failures, parameter patterns or TODOs alone are insufficient.
Do not change benchmark inputs, the reference or acceptance criteria.

Runtime detection report: {runtime_report}
Runtime configuration result: {runtime_configuration}
Latest evaluator result: {evaluation_result}
Deterministic evaluation classification: {evaluation_classification}

## Repair Routing

Use `failure_category`, `timeout_classification`, `resource_before`, and
`resource_after` as observations from the framework, not infallible root causes.
A device fault or hang may originate in candidate code; source evidence is needed
before assigning a specific kernel defect. Likewise, a timeout is not automatically
an environment failure.

Classify `category` as `task_schema`, `build`, `link`, `install`, `adapter`,
`runtime`, `numerical`, `api_policy_violation`, `fallback`, `environment`, or
`unknown`, and choose the role from the evidence:

- `operator_fixer`: a candidate source, adapter, build configuration or API-policy
  defect is supported by a source location and causal explanation.
- `operator_environment_fixer`: execution paths, dependencies, staging, resource
  ownership or evaluator lifecycle are supported as the problem. Do not prescribe
  killing unrelated processes or changing global/vendor installations.
- `none`: evidence cannot yet establish a repair, or an external task/reference
  issue requires escalation. State the missing evidence and next action rather
  than requesting speculative source edits.

Incomplete traces, stale aggregates or a missing staging file make evaluation
incomplete; they do not by themselves identify who caused it. Keep
`evaluation_valid` distinct from `operator_code_evidence`: an incomplete run can
still contain direct compiler/source evidence. Use `requires_clean_rerun` when
current evidence cannot establish full-workload correctness. In `handoff`, explain
any disagreement with the deterministic classification and the supporting evidence.
Do not change `definition.json` or `workload.jsonl`.

Return exactly one JSON object:

```json
{
  "category": "unknown",
  "root_cause": "",
  "repair_role": "operator_fixer|operator_environment_fixer|none",
  "evaluation_valid": false,
  "requires_clean_rerun": false,
  "operator_code_evidence": false,
  "suggested_fix": "",
  "failed_workloads": [],
  "evidence": [],
  "missing_evidence": [],
  "hypotheses": [],
  "next_verification": [],
  "handoff": ""
}
```
