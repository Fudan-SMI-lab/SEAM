---
name: importlib-module-patching
description: Python Import Shadowing Fix: Use importlib.import_module() for NPU Custom-Op Patching
tags: ["python-import", "importlib", "ascend-npu", "npu-patching", "init-py", "module-shadowing", "code-adaptation"]
category: code_adaptation
subtype: import_shadowing_init_py
confidence: 0.85
occurrence_count: 1
---

# Python Import Shadowing Fix: Use importlib.import_module() for NPU Custom-Op Patching

## When to Use
- When a Python package's __init__.py exports a function via `from .submodule import func`, the statement `import pkg.submodule` resolves to the function instead of the module object, making monkey-patching impossible. The fix uses `importlib.import_module('pkg.submodule')` to obtain the actual module object for patching.

## Root Cause
Python's `__init__.py` can export names from submodules into the package namespace using `from .submodule import func`. This causes direct attribute access (`pkg.submodule`) to resolve to `func` (the exported function) rather than the module object. On NPU platforms like Ascend NPU, custom operator files need to monkey-patch the module's functions (e.g., replacing CUDA ops with NPU ops), but the shadowed module reference prevents this.

## How to Use
1. Identify the file where `import pkg.submodule as _mod` is used, and the `__init__.py` exports the name `submodule` (e.g., `from .submodule import func` in `__init__.py`).
2. Add `import importlib` at the top of the file.
3. Replace `import pkg.submodule as _mod` with `_mod = importlib.import_module('pkg.submodule')`.
4. The rest of the monkey-patching code (e.g., `_mod.func = npu_func`) remains unchanged and now correctly patches the module object.

## Code Examples
[
  {
    "file": "output_projects/04_Deepwave_20260602_213331/deepwave/acoustic_npu.py",
    "before": "import deepwave.acoustic as _mod",
    "after": "_mod = importlib.import_module('deepwave.acoustic')"
  }
]

## Do Not
- Do NOT use `import pkg.submodule` when `__init__.py` shadows the submodule name with an exported function — the import will resolve to the function, not the module.
- Do NOT try to patch the function directly as if it were a module — `_mod.func = replacement` will fail because `_mod` is already the function.
- Do NOT rename the exported name in `__init__.py` without understanding downstream dependencies — other code may rely on the current import behavior.

## References
- Python importlib documentation: https://docs.python.org/3/library/importlib.html
- Deepwave acoustic_npu.py: output_projects/04_Deepwave_20260602_213331/deepwave/acoustic_npu.py
- Python Module Shadowing: When __init__.py exports hide submodules

## Evidence
- Source runs: e2e-v3-8c8bf406dc7e
