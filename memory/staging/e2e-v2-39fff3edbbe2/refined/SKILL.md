---
name: cann-toolkit-venv-implicit-deps
description: Install implicit CANN toolkit Python dependencies in clean venv
tags: ["cann", "venv", "implicit-dependencies", "pyyaml", "torch-npu-import"]
category: environment_issue
subtype: cann_toolkit_missing_deps
confidence: 0.8
occurrence_count: 1
---

# Install implicit CANN toolkit Python dependencies in clean venv

## When to Use
- ModuleNotFoundError when importing torch_npu or running NPU scripts in a clean venv. Common missing modules: 'yaml', 'decorator', 'attrs', 'psutil', 'scipy', 'cloudpickle', 'ml-dtypes', 'tornado', 'absl-py'.

## Root Cause
CANN toolchain Python wheels (auto_tune, te, hccl, opc_tool) shipped with the system installation declare runtime dependencies that are not satisfied in a clean virtual environment. Additionally, torch_npu requires pyyaml at import time for its memory visualization module. Without these packages, torch_npu initialization fails and blocks all NPU operations.

## How to Use
1. 1. Create and activate the project's virtual environment: python -m venv .venv && source .venv/bin/activate.
2. 2. Install torch and torch-npu first, ensuring version compatibility (e.g., torch==X.X.X+cpu for torch-npu==X.X.X).
3. 3. Scan system CANN packages to identify their required dependencies: pip show auto_tune te hccl opc_tool 2>/dev/null | grep 'Requires:'.
4. 4. Install the implicit dependencies required by CANN toolchain and torch_npu: pip install decorator attrs psutil scipy cloudpickle ml-dtypes tornado absl-py pyyaml.
5. 5. Verify the environment by importing torch_npu: python -c 'import torch; import torch_npu; import torchvision; print(torch.npu.is_available())'.
6. 6. If a ModuleNotFoundError occurs during step 5, install the missing module immediately (pip install <missing_module>) and retry the import.

## Do Not
- Do NOT wait for the first import torch_npu to fail before installing dependencies — this wastes execution cycles.
- Do NOT install CANN dependencies globally; always isolate them in the project's virtual environment.
- Do NOT ignore pyyaml — torch_npu imports it at the top level of npu._memory_viz, so its absence crashes torch_npu import.

## References
- /usr/local/Ascend/ascend-toolkit/latest/ (CANN toolchain root)
- https://mirrors.aliyun.com/pypi/simple/ (Domestic PyPI mirror)

## Evidence
- Source runs: e2e-v2-39fff3edbbe2
