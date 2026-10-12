# Phase 4: Operator Benchmark Report

Produce a detailed report for one KernelBench operator.

## Required facts

Use the phase outputs and saved artifacts. Do not invent values. Preserve:

- workload_pass_rate;
- operator_pass_rate;
- max_absolute_error;
- max_relative_error;
- matched_ratio;
- dtype-specific tolerance;
- per-workload status;
- repair history;
- build/runtime failures;
- unresolved work.

Return exactly one JSON object with:

```json
{
  "phase_id": "phase_4_operator_report",
  "operator_name": "",
  "workload_pass_rate": 0.0,
  "operator_pass_rate": 0.0,
  "max_absolute_error": null,
  "max_relative_error": null,
  "min_matched_ratio": null,
  "required_matched_ratio": 0.99,
  "final_status": "",
  "report_paths": [],
  "unresolved_failures": [],
  "summary": ""
}
```
