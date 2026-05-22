---
name: fail-closed-ascend-npu-validation-entrypoint
description: Fail closed on Ascend NPU availability in validation entrypoints
tags: ["torch-npu", "npu-validation", "no-cpu-fallback", "entry-script", "device-placement"]
category: code_adaptation
subtype: fail_closed_npu_device_gate
confidence: 0.88
occurrence_count: 1
---

# Fail closed on Ascend NPU availability in validation entrypoints

## When to Use
- A validation or inference entrypoint permits CUDA or CPU fallback even though migration success requires actual Ascend NPU execution. This can produce false validation success or large-model CPU/CUDA OOM instead of proving the migrated NPU path works.

## Root Cause
The validation entrypoint used permissive device selection rather than enforcing the migration constraint that `torch_npu.npu.is_available()` must be true. Without a fail-closed gate, the real entry command can run outside Ascend NPU while still appearing to validate the migration.

## How to Use
1. Locate the binding validation entrypoint selected for Phase 3; in this run it was `test_data_and_scripts/run_inference.py`.
2. Add a single device-detection helper in the entrypoint instead of scattering device checks through the script.
3. Inside the helper, import `torch_npu` locally and raise a `RuntimeError` on `ImportError` with a message that CPU/CUDA fallback is not allowed.
4. In the same helper, call `torch_npu.npu.is_available()` and raise a `RuntimeError` if it returns false.
5. Return only the Ascend device path, such as the string `"npu"`; do not return `"cuda"` or `"cpu"` from the validation helper.
6. Call the helper at the start of `main()` before model loading so validation fails before any non-NPU execution path can proceed.
7. Move processor outputs and generated-input tensors to `model.device`, not to a separately guessed CUDA or CPU device, so the full transformers inference path follows the actual model placement.
8. Validate with the real binding entry command, not a wrapper smoke test, and accept success only when the command exits with code 0 and reports Ascend NPU use.

## Code Examples
[
  {
    "file": "test_data_and_scripts/run_inference.py",
    "before": "# Problem pattern from the repair evidence: validation device selection allowed CUDA or CPU fallback, so the entrypoint could count non-NPU execution as migration success.",
    "after": "def get_device():\n    try:\n        import torch_npu\n    except ImportError as exc:\n        raise RuntimeError(\n            \"Ascend NPU validation requires torch_npu; CPU/CUDA fallback is not allowed.\"\n        ) from exc\n\n    npu_backend = getattr(torch_npu, \"npu\")\n    if not npu_backend.is_available():\n        raise RuntimeError(\n            \"Ascend NPU validation requires an available NPU; CPU/CUDA fallback is not allowed.\"\n        )\n\n    print(\"\u2713 \u68c0\u6d4b\u5230 Ascend NPU \u53ef\u7528\")\n    return \"npu\""
  },
  {
    "file": "test_data_and_scripts/run_inference.py",
    "before": "# Problem pattern from the constraint evidence: processor outputs and generated tensors must not resolve to CUDA or CPU during validation.",
    "after": "inputs = processor.apply_chat_template(\n    messages,\n    tokenize=True,\n    add_generation_prompt=True,\n    return_dict=True,\n    return_tensors=\"pt\",\n).to(model.device)"
  }
]

## Do Not
- Do NOT allow `torch.cuda.is_available()` or CPU fallback to count as successful Ascend migration validation.
- Do NOT accept a validation result from a wrapper or smoke script when the binding entry command is `test_data_and_scripts/run_inference.py`.
- Do NOT move processor outputs to an independently selected CUDA or CPU device; use `model.device` for the transformers inference path.
- Do NOT silently downgrade precision or bypass the GLM-4.1V transformers backend to make validation pass.

## References
- validated/phase_1_5_constraint_summary_canonical.json
- validated/phase_fix_code_canonical.json
- validated/phase_5_validation_canonical.json
- reports/SUMMARY_REPORT.md
- reports/OPENCODE_OPERATIONS_LOG.md
- execution_journal.jsonl

## Evidence
- Source runs: e2e-v2-570be999f9f4
