---
name: torch-npu-undeclared-deps-validation
description: Validate and install torch-npu undeclared runtime dependencies
tags: ["torch-npu", "undeclared-dependencies", "npu-stack", "decorator", "ml-dtypes", "dependency_validation"]
category: dependency_issue
subtype: multiple_undeclared_dependencies
confidence: 0.8
occurrence_count: 1
---

# Validate and install torch-npu undeclared runtime dependencies

## When to Use
- After installing torch-npu 2.5.1 in a fresh venv, various import or runtime errors occur due to missing dependencies that are not declared in torch-npu's pip metadata. Errors may include ModuleNotFoundError for packages like 'decorator', 'psutil', 'cloudpickle', 'ml_dtypes', or 'tornado'.

## Root Cause
torch-npu 2.5.1 has runtime dependencies on decorator, attrs, psutil, absl-py, cloudpickle, ml-dtypes, scipy, and tornado, but none of these are declared in the package's pip metadata (requires field). In a minimal venv created for migration, these packages may be absent, causing failures when torch-npu features are accessed. The errors manifest unpredictably depending on which torch-npu submodule is first invoked.

## How to Use
1. 1. After installing torch-npu, immediately run a pre-flight import validation: `python -c 'import torch_npu; import torch_npu.npu'`
2. 2. If the pre-flight check fails with ModuleNotFoundError, note the missing package name from the error message.
3. 3. Install the full NPU stack dependency set proactively: `pip install decorator attrs psutil absl-py cloudpickle ml-dtypes scipy tornado`
4. 4. Re-run the pre-flight validation to confirm all dependencies are satisfied.
5. 5. Optionally add these packages to requirements.txt to prevent recurrence in future venv builds.

## Do Not
- Do NOT wait for a specific torch-npu feature to fail before installing deps — proactive validation is faster.
- Do NOT install only the single missing package reported in the error — install the full set of 8 deps to prevent cascading failures on the next missing one.
- Do NOT assume global Python environment packages are available in the venv — each dependency must be installed within the venv.

## References
- https://ascend.github.io/docs/tutorials/torch_npu/

## Evidence
- Source runs: e2e-v2-c3c4a55c9cd4
