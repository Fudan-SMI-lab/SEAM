# Phase 1: Operator Runtime Detection

Inspect the target Ascend/KernelBench runtime for this single-operator task. Use the supplied paths and target SOC; do not modify the operator implementation or global vendor installation.

## Context

- Operator task directory: `{operator_task_dir}`
- KernelBench root: `{kernelbench_root}`
- Target SOC: `{target_soc}`
- Operator level: `{operator_level}`

## Required checks

Collect concrete evidence for:

1. Python interpreter and version.
2. PyTorch and torch_npu import/version and NPU availability.
3. NPU device count/name and target SOC compatibility.
4. Driver, firmware, CANN, AscendC compiler, OPP/TBE paths.
5. KernelBench/uv command availability.
6. Required runtime imports: decorator, scipy, attrs, psutil.
7. Writable output, cache, temporary, and staging directories.
8. A minimal NPU runtime smoke test when available.

Classify every issue as `blocking`, `warning`, or `info`. Do not infer success from environment variables alone; verify commands/imports where possible.

Return exactly one JSON object:

```json
{
  "phase_id": "phase_1_operator_runtime_detect",
  "runtime_status": "ready",
  "python_executable": "",
  "python_version": "",
  "torch_version": "",
  "torch_npu_version": "",
  "npu_available": false,
  "device_count": 0,
  "device_names": [],
  "target_soc": "",
  "driver_version": "",
  "firmware_version": "",
  "cann_version": "",
  "ascendc_available": false,
  "opp_path": "",
  "kernelbench_available": false,
  "dependency_checks": {},
  "path_checks": {},
  "smoke_test": {"status": "not_run", "summary": ""},
  "blocking_issues": [],
  "warnings": [],
  "evidence": []
}
```
