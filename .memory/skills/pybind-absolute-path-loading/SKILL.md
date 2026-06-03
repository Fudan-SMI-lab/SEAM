---
name: pybind-absolute-path-loading
description: Pybind .so Loading via Absolute Path: Avoid sys.path.insert() Accumulation to Prevent Wrong Module Resolution
tags: ["ascend-npu", "pybind", "custom-ops", "importlib", "sys-path", "module-loading", "multi-module"]
category: code_adaptation
subtype: pybind_syspath_accumulation_wrong_so
confidence: 0.75
occurrence_count: 1
---

# Pybind .so Loading via Absolute Path: Avoid sys.path.insert() Accumulation to Prevent Wrong Module Resolution

## When to Use
- Multi-module pybind projects producing identically-named `custom_ops_lib.so` files across different directories. When multiple modules each call `sys.path.insert(0, dir)` followed by `import custom_ops_lib`, Python resolves the import to the first match in `sys.path`, not necessarily the intended `.so`. The accumulated `sys.path` entries cause the wrong module to be loaded as more modules are imported, leading to incorrect operator registrations, segmentation faults, or silent correctness errors.

## Root Cause
`sys.path.insert(0, dir)` accumulates paths over multiple module loads without removing previous entries. Since all modules produce identically-named `custom_ops_lib.so` files, Python's import system resolves `import custom_ops_lib` to whichever `.so` appears first in `sys.path` — which may be a module loaded earlier rather than the intended one. The `(0, ...)` insert at position 0 pushes older entries down but never removes them, creating an expanding search list where stale paths shadow new ones.

## How to Use
1. Locate all `sys.path.insert(0, ...)` calls followed by `import custom_ops_lib` in the project (typically in `benchmark_custom_ops.py` or similar entry scripts).
2. Replace each `sys.path.insert()` + `import` pair with `importlib.util.spec_from_file_location()` using the absolute path to the correct `.so` file.
3. Use a unique module name per operator (e.g., `custom_ops_lib_depthwise` instead of the generic `custom_ops_lib`) to avoid name collisions in `sys.modules`.
4. Call `spec.loader.exec_module(module)` to execute the module after creating it with `importlib.util.module_from_spec(spec)`.
5. Remove all `sys.path.insert(0, ...)` calls for `.so` directories — they are no longer needed and would still pollute `sys.path`.

## Code Examples
[
  {
    "file": "benchmark_custom_ops.py",
    "before": "        sys.path.insert(0, op_dir)\n        import custom_ops_lib",
    "after": "        import importlib.util\n        spec = importlib.util.spec_from_file_location(\n            f\"custom_ops_lib_{op_name}\",\n            os.path.join(op_dir, \"custom_ops_lib.so\")\n        )\n        custom_ops_lib = importlib.util.module_from_spec(spec)\n        spec.loader.exec_module(custom_ops_lib)"
  }
]

## Do Not
- Do NOT use `sys.path.insert(0, ...)` for loading pybind `.so` files in multi-module projects — the accumulation causes wrong `.so` resolution.
- Do NOT import pybind modules with the same name (`custom_ops_lib`) from different directories — `sys.modules` caching will return the first loaded module.
- Do NOT rely on `sys.path.remove()` or `sys.path.pop()` to clean up between imports — this is fragile and error-prone across concurrent or nested imports.
- Do NOT use relative imports or `importlib.import_module()` with a shared module name — always use `spec_from_file_location()` with a unique name per operator.

## References
- https://docs.python.org/3/library/importlib.html#importlib.util.spec_from_file_location — Python importlib documentation
- https://pybind11.readthedocs.io/en/stable/ — pybind11 documentation
- https://docs.python.org/3/library/sys.html#sys.path — Python sys.path documentation

## Evidence
- Source runs: e2e-v3-8c8bf406dc7e
