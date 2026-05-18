---
name: remove-cuda-only-extensions
description: Remove CUDA-only Apex extensions with no NPU equivalent
tags: ["apex", "fused-layer-norm", "cuda-extension", "import-error", "dependency-issue", "code-adapter"]
category: dependency_issue
subtype: cuda_only_extension_incompatible
confidence: 0.7
occurrence_count: 1
---

# Remove CUDA-only Apex extensions with no NPU equivalent

## When to Use
- ModuleNotFoundError: No module named 'apex_C' — CUDA-specific Apex extension has no NPU equivalent. The script fails immediately on import with exit code 1, preventing any execution.

## Root Cause
NVIDIA Apex provides compiled CUDA extensions (apex, apex_C, apex_C.contrib) for fused operations like fused_layer_norm, FusedAdam, and distributed training. These are compiled against CUDA toolchains and have no Ascend NPU binary or wheel. When a CUDA-to-NPU migration project retains these imports, the entry script crashes with ModuleNotFoundError at Phase 5 validation. Phase 4 rule migration does NOT catch this because it performs syntactic string replacements (e.g., torch.cuda → torch.npu), not import-level dependency analysis.

## How to Use
1. 1. Scan all Python source files for imports from CUDA-only extension packages: apex, apex_C, apex.contrib, cupy, and any package with _C suffix (e.g., apex_C, deepwave_C). Use: grep -rn 'from apex\|import apex\|from .*_C import\|import cupy' <project_dir>/
2. 2. For each found import, check if the imported symbol is referenced anywhere else in the file. Search for the symbol name (e.g., fused_layer_norm) in the same file using: grep -n 'fused_layer_norm' <file.py>. Exclude comment lines and noqa markers.
3. 3. If the imported symbol is UNUSED (appears only on the import line or only in comments/noqa): Remove the entire import line from the file.
4. 4. If the imported symbol IS USED downstream: Replace with the torch-native equivalent. For fused_layer_norm, replace with torch.nn.LayerNorm. For FusedAdam, replace with torch.optim.Adam. For other Apex ops, search for torch-native equivalents.
5. 5. After modifying the file, verify no other references to the removed import remain. Check with: grep -n '<symbol_name>' <file.py>.
6. 6. Re-run the entry script to confirm the ModuleNotFoundError is resolved. Check exit_code == 0.
7. 7. If additional CUDA-only imports surface (e.g., custom CUDA extensions), repeat steps 1-6 for each one.

## Code Examples
**File: train.py**
# Before
def main():
    # [E2E_TEST_INJECTION] Simulate a CUDA-specific import that breaks on NPU
    # The repair agent should remove this line after analyzing the error
    from apex_C import fused_layer_norm  # noqa: F401 - intentionally broken for E2E test

    # Device setup with CUDA string literals
    device = "cuda" if torch.cuda.is_available() else "cpu"
# After
def main():
    # Device setup with CUDA string literals
    device = "npu" if torch.npu.is_available() else "cpu"

## Do Not
- Do NOT attempt to `pip install apex` on NPU — Apex is CUDA-only and will fail or install broken wheels.
- Do NOT redirect to CPU fallback for fused operations — it will OOM on large models.
- Do NOT rely on Phase 4 rule migration to catch import-level incompatibilities — it only performs syntactic replacements, not dependency analysis.
- Do NOT blindly remove fused_layer_norm if it is actually used in forward() — replace with torch.nn.LayerNorm instead.

## Evidence
- Source runs: e2e-v2-8cb0205b098b
