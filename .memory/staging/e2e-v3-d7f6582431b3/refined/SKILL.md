---
name: npu-torchvision-cuda-build-import-guard
description: Guard torchvision _meta_registrations import against RuntimeError on NPU when using CUDA-built torchvision
tags: ["torch-npu", "torchvision", "venv", "import-guard", "operator-incompat", "cuda-build"]
category: dependency_issue
subtype: cuda_built_package_import_crash
confidence: 0.9
occurrence_count: 1
---

# Guard torchvision _meta_registrations import against RuntimeError on NPU when using CUDA-built torchvision

## When to Use
- RuntimeError: operator torchvision::nms does not exist — torchvision 0.20.1+cu124 (CUDA build) crashes at import time on Ascend NPU because _meta_registrations eagerly registers fake implementations for CUDA C++ custom ops (nms, roi_align, deform_conv2d) that are absent from the NPU PyTorch dispatch table.

## Root Cause
torchvision/__init__.py eagerly executes `from torchvision import _meta_registrations` (lines 10-13), which at line 163 calls `@torch.library.register_fake('torchvision::nms')` without an `_has_ops()` guard. The CUDA-built torchvision C++ extension is not loaded, so these custom ops do not exist in the NPU PyTorch dispatch table. The crash occurs during any transitive import of torchvision (e.g., via transformers or directly), even if the project never uses nms, roi_align, or deform_conv2d.

## How to Use
1. Locate the torchvision __init__.py inside the project virtual environment (typically .venv/lib/python3.10/site-packages/torchvision/__init__.py).
2. Find the `from torchvision import _meta_registrations` statement, usually around lines 10-13, immediately after `from .extension import _HAS_OPS`.
3. Confirm the project does not depend on torchvision C++ custom ops (nms, roi_align, deform_conv2d) by checking imports — most projects only need pure-Python symbols like InterpolationMode, transforms, datasets.
4. Wrap the import in a try/except RuntimeError guard: replace the bare `from torchvision import _meta_registrations` with a try block that catches RuntimeError and passes silently.
5. Run the project entry script to verify all imports succeed and the application starts correctly on NPU with EXIT_CODE=0.
6. If the project actually uses torchvision C++ ops, revert the guard and instead install torchvision from source built against torch-npu — the try/except guard is only safe when those ops are unused.

## Code Examples
[
  {
    "file": ".venv/lib/python3.10/site-packages/torchvision/__init__.py",
    "before": "from .extension import _HAS_OPS  # usort:skip\nfrom torchvision import _meta_registrations",
    "after": "from .extension import _HAS_OPS  # usort:skip\ntry:\n    from torchvision import _meta_registrations\nexcept RuntimeError:\n    pass"
  }
]

## Do Not
- Do NOT attempt to install torchvision from source unless the project actually depends on nms, roi_align, or deform_conv2d C++ ops — the import guard is sufficient for most use cases.
- Do NOT remove the .extension._HAS_OPS import when adding the guard — it must execute before _meta_registrations to load the _C extension.
- Do NOT catch a bare `except:` — only catch RuntimeError, the specific exception raised when a C++ custom op is missing from the dispatch table.
- Do NOT assume this fix survives a torchvision upgrade — the try/except must be re-applied if the venv is rebuilt or torchvision is updated to a newer CUDA build.

## Evidence
- Source runs: e2e-v3-d7f6582431b3
