---
name: torch-npu-pyyaml-preinstall
description: Pre-install PyYAML to prevent torch-npu transitive dependency failure
tags: ["torch-npu", "pyyaml", "transitive-dependency", "undeclared-requirement", "dependency_issue"]
category: dependency_issue
subtype: undeclared_transitive_dependency
confidence: 0.95
occurrence_count: 1
---

# Pre-install PyYAML to prevent torch-npu transitive dependency failure

## When to Use
- Error involves: import torch  # fails with ModuleNotFoundError: No module named 'yaml' when torch-npu is installed but PyYAML is not, import torch_npu  # fails at torch_npu/npu/_memory_viz.py line 11

## Root Cause
torch-npu 2.5.1's memory visualization submodule (torch_npu/npu/_memory_viz.py) imports yaml unconditionally at module load time. However, PyYAML is not declared in torch-npu's pip metadata (requires field), so a fresh virtual environment with only torch-npu installed will lack it. The error is misleading because the traceback originates from `import torch` (not `import torch_npu`), making the actual missing dependency hard to diagnose.

## How to Use
1. 1. Before or alongside installing torch-npu, explicitly install PyYAML: `pip install PyYAML` (use domestic mirror if needed: `pip install PyYAML -i https://mirrors.aliyun.com/pypi/simple/`).
2. 2. Alternatively, add PyYAML to the project's requirements.txt alongside torch, torch-npu, and torchvision.
3. 3. After installing torch-npu, run a pre-flight validation: `python -c 'import torch_npu; import torch_npu.npu'` — if this exits cleanly without ModuleNotFoundError, the dependency is resolved.
4. 4. If the error still occurs, verify the yaml module is accessible: `python -c 'import yaml; print(yaml.__version__)'`.

## Code Examples
**File: requirements.txt**
# Before
torch
torch-npu
torchvision
# After
torch
torch-npu
torchvision
PyYAML

## Do Not
- Do NOT attempt to patch torch_npu internal files (_memory_viz.py) — the file is overwritten on package upgrade.
- Do NOT confuse this error with a torch installation issue — the traceback starting at 'import torch' is a red herring; the actual failure is in torch_npu backend loading.
- Do NOT use CPU fallback as a workaround — this is a hard dependency failure, not a performance issue.

## References
- https://ascend.github.io/docs/tutorials/torch_npu/

## Evidence
- Source runs: e2e-v2-69fe6c6573b0
- Source runs: e2e-v2-c3c4a55c9cd4-exp-20260429211243
