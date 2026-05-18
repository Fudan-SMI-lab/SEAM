---
name: cuda-extension-import-removal
description: Remove broken CUDA extension imports before NPU execution
tags: ["torch-npu", "cuda-extensions", "apex", "import-compatibility", "module-removal"]
category: code_adaptation
subtype: cuda_extension_import_incompatible
confidence: 0.9
occurrence_count: 1
---

# Remove broken CUDA extension imports before NPU execution

## When to Use
- ModuleNotFoundError: No module named 'apex_C' (or apex, fused_kernels, xformers, etc.) — CUDA-only extension import executes at module load time, preventing the entire script from running before any NPU code paths are reached.

## Root Cause
Import statements for CUDA-only extension libraries execute during Python's import phase, before any application code runs. These libraries (apex_C, custom fused kernels, etc.) are compiled for CUDA and have no NPU-compatible wheels. The import itself is a hard blocker regardless of whether the functionality is used downstream.

## How to Use
1. 1. Scan all Python files for import statements of CUDA-only packages: apex, apex_C, fused_kernels, xformers, flash_attn, and any custom CUDA extensions.
2. 2. For each CUDA-only import, check if any symbol from that import is referenced elsewhere in the codebase (grep the imported names across all .py files).
3. 3. If NO downstream usage exists: remove the import line entirely.
4. 4. If downstream usage EXISTS: identify what functionality is consumed and replace with NPU-compatible equivalents — apex.optimizers → torch.optim, apex.parallel.DistributedDataParallel → torch.nn.parallel.DistributedDataParallel, apex.normalization.FusedLayerNorm → torch.nn.LayerNorm, fused CUDA kernels → native torch operations.
5. 5. After removal/replacement, verify no orphaned references remain: grep the removed package names across all .py files.
6. 6. Validate import succeeds: `python -c 'import <module>'` must exit with code 0.

## Do Not
- Do NOT wrap CUDA-only imports in try/except — this defers failure and creates silent CPU fallback behavior.
- Do NOT leave dead import statements even if you believe they won't execute — Python evaluates all top-level imports at module load.
- Do NOT remove an import without first checking for downstream references — this will cause NameError at runtime.
- Do NOT attempt to pip install apex on NPU — the fused kernels are CUDA-specific and have no NPU equivalent.
- Do NOT replace apex FusedLayerNorm with a CPU fallback — torch.nn.LayerNorm is natively supported by torch-npu and is sufficient.

## References
- https://ascend.github.io/docs/tutorials/torch_npu/migration.html

## Evidence
- Source runs: e2e-v2-595d762cf30b
