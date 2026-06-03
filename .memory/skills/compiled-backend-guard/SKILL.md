---
name: compiled-backend-guard
description: MISSING_COMPILED_BACKEND Guard: Return None When Compiled C/CUDA Extensions Absent on NPU
tags: ["ascend-npu", "compiled-extension", "backend-utils", "npu-migration", "cuda-to-npu"]
category: code_adaptation
subtype: missing_compiled_backend_early_return
confidence: 0.90
occurrence_count: 1
---

# MISSING_COMPILED_BACKEND Guard: Return None When Compiled Extensions Absent

## When to Use
- `get_backend_function()` or similar dispatch functions crash with `SystemError` or `ImportError` when a compiled C/CUDA shared library (.so) is missing or incompatible on NPU.
- The project has both a compiled backend (CUDA .so) and a pure-Python or torch.npu fallback, but the dispatch function crashes before reaching the fallback.
- Error: `SystemError: <built-in function ...> returned NULL without setting an error` or `ImportError: cannot import name '...' from 'custom_ext'`.

## Root Cause
Projects that dispatch between compiled C/CUDA extensions and interpreted backends crash when the compiled .so is absent on NPU. The `get_backend_function()` attempts to load the .so unconditionally, without checking whether it's available on the current platform.

## How to Use
1. Set a flag `MISSING_COMPILED_BACKEND = True` when the compiled .so is absent.
2. At the top of `get_backend_function()`, add: `if MISSING_COMPILED_BACKEND: return None`.
3. All callers already handle `None` returns by falling back to the Python/NPU implementation.
4. One-line change, no caller modification needed.

## Code Examples
[
  {
    "file": "output_projects/04_Deepwave_20260602_213331/deepwave/backend_utils.py",
    "before": "    if dtype == torch.float32:\n        dtype_str = \"float\"\n    elif dtype == torch.float64:\n        dtype_str = \"double\"\n    else:\n        raise TypeError(f\"Unsupported dtype {dtype}\")\n\n    device_str = device.type\n    func_name = f\"{propagator}_iso_{ndim}d_{accuracy}_{dtype_str}_{pass_name}{extra}_{device_str}\"\n    try:\n        return getattr(dll, func_name)\n    except AttributeError as e:\n        raise AttributeError(f\"Backend function {func_name} not found.\") from e",
    "after": "    if dtype == torch.float32:\n        dtype_str = \"float\"\n    elif dtype == torch.float64:\n        dtype_str = \"double\"\n    else:\n        raise TypeError(f\"Unsupported dtype {dtype}\")\n\n    if MISSING_COMPILED_BACKEND:\n        return None\n\n    device_str = device.type\n    func_name = f\"{propagator}_iso_{ndim}d_{accuracy}_{dtype_str}_{pass_name}{extra}_{device_str}\"\n    try:\n        return getattr(dll, func_name)\n    except AttributeError as e:\n        raise AttributeError(f\"Backend function {func_name} not found.\") from e"
  }
]

## Do Not
- Do NOT remove the compiled backend import — only guard it.
- Do NOT assume `try/except ImportError` is sufficient — some extensions fail with SystemError after loading.
- Do NOT suppress the error silently without a fallback path.

## References
- Deepwave backend_utils.py: `output_projects/04_Deepwave_20260602_213331/deepwave/backend_utils.py`
- Standard pattern for multi-backend dispatch in PyTorch ecosystem

## Evidence
- Source run: e2e-v3-8c8bf406dc7e
- backend_utils patched for MISSING_COMPILED_BACKEND guard
- Pure-Python fallback correctly engaged after guard return None
