---
name: comprehensive-cuda-to-npu-code-adapter
description: Comprehensive CUDA-to-NPU code adaptation: remove CUDA-exclusive imports and replace torch.cuda.* with torch.npu.*
tags: ["apex", "torch.cuda", "torch.npu", "nccl", "hccl", "code-adapter", "phase-4-gap"]
category: operator_incompat
subtype: cuda_exclusive_import_with_api_mismatch
confidence: 0.9
occurrence_count: 1
---

# Comprehensive CUDA-to-NPU code adaptation: remove CUDA-exclusive imports and replace torch.cuda.* with torch.npu.*

## When to Use
- Phase 4 rule migration performs zero operations (duration <1ms, empty operation field). At Phase 5 validation, the entry script fails with ModuleNotFoundError: No module named 'apex_C' (or similar CUDA-exclusive extension), or silently falls back to CPU via torch.cuda.is_available() returning False.

## Root Cause
Phase 4 rule migration only performs simple string/text replacements (.cuda() → .npu(), 'cuda' → 'npu'). It does NOT catch: (1) CUDA-exclusive third-party imports like apex_C, apex, cupy that have no NPU wheels, and (2) torch.cuda.* API calls (is_available, device_count, amp.autocast) that require torch.npu.* equivalents. Both require actual code adaptation by the code_adapter repair role.

## How to Use
1. 1. Scan all Python source files for CUDA-exclusive imports: grep -rn 'from apex\|import apex\|from apex_C\|import apex_C\|from cupy\|import cupy\|from .*_C import' across the project root. Also look for 'from apex_C import fused_layer_norm' specifically.
2. 2. For each CUDA-exclusive import, remove the entire import line. Check if the imported symbol (e.g., fused_layer_norm) is used elsewhere in the file. If used, replace the call with torch.nn.LayerNorm or torch.nn.functional.layer_norm.
3. 3. Replace torch.cuda.is_available() with torch.npu.is_available() in all files.
4. 4. Replace torch.cuda.device_count() with torch.npu.device_count() in all files.
5. 5. Replace torch.cuda.amp.autocast() with torch.npu.amp.autocast() in all files.
6. 6. Replace torch.cuda.current_device() with torch.npu.current_device() if present.
7. 7. Replace .cuda() and .to('cuda') / .to('cuda:0') with .npu() and .to('npu') / .to('npu:0') respectively.
8. 8. Replace backend='nccl' or backend="nccl" in torch.distributed.init_process_group() with backend='hccl' or backend="hccl".
9. 9. Run the entry script and verify it exits with code 0 on NPU.

## Code Examples
**File: train.py**
# Before
    # [E2E_TEST_INJECTION] Simulate a CUDA-specific import that breaks on NPU
    from apex_C import fused_layer_norm  # noqa: F401 - intentionally broken for E2E test

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if torch.cuda.device_count() > 1:
        torch.distributed.init_process_group(backend="nccl")
    with torch.cuda.amp.autocast():
# After
    # Device setup with NPU
    device = "npu" if torch.npu.is_available() else "cpu"
    if torch.npu.device_count() > 1:
        torch.distributed.init_process_group(backend="hccl")
    with torch.npu.amp.autocast():

## Do Not
- Do NOT attempt to install apex or apex_C on NPU — no wheels exist for Ascend architecture.
- Do NOT leave torch.cuda.is_available() in place without replacement — it will return False and trigger CPU fallback for device selection.
- Do NOT use nccl backend on NPU — it requires HCCL for Ascend distributed training.
- Do NOT use CPU fallback for fused_layer_norm — replace with torch.nn.LayerNorm which is NPU-native and performant.

## References
- https://gitee.com/ascend/pytorch (torch-npu source and API mapping)
- https://ascend.github.io/docs/tutorials/torch_npu/

## Evidence
- Source runs: e2e-v2-bb60c2c5e231
