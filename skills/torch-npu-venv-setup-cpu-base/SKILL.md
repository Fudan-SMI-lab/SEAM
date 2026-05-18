---
name: torch-npu-venv-setup-cpu-base
description: Set up torch-npu virtual environment with CPU-base torch and +cpu variant torchvision
tags: ["torch-npu", "venv-setup", "dependency-resolution", "cpu-base-torch", "torchvision-compatibility"]
category: dependency_issue
subtype: torch_npu_cpu_base_requirement
confidence: 0.95
occurrence_count: 4
---

# Set up torch-npu virtual environment with CPU-base torch and +cpu variant torchvision

## When to Use
- Error involves: pip install -r requirements.txt  # where requirements.txt contains 'torch' and 'torch-npu' without version pins, pip install torch torch-npu torchvision  # resolves to incompatible versions

## Root Cause
The torch-npu package declares a strict dependency on the CPU-only build of PyTorch (e.g., torch==2.5.1+cpu). pip's resolver initially pulls the standard CUDA build of torch from generic PyPI mirrors, causing a version conflict. Additionally, vanilla torchvision expects CUDA-enabled torch and registers operators with the 'CUDA' dispatch key, which conflicts with the CPU-base torch's 'CPU' dispatch key.

## How to Use
1. 1. Probe the system-wide torch-npu version installed by CANN: python -c 'import torch_npu; print(torch_npu.__version__)'. Note the major.minor version (e.g., 2.5.1).
2. 2. Create a virtual environment: python -m venv .venv && source .venv/bin/activate.
3. 3. Install the matching CPU-base torch from PyTorch's official CPU wheel index: pip install torch==<version>+cpu --extra-index-url https://download.pytorch.org/whl/cpu --trusted-host download.pytorch.org.
4. 4. Install the matching torch-npu from a domestic mirror: pip install torch-npu==<version> -i https://mirrors.aliyun.com/pypi/simple/.
5. 5. Install the matching CPU-variant torchvision: pip install torchvision==<derived_version>+cpu --extra-index-url https://download.pytorch.org/whl/cpu --trusted-host download.pytorch.org. Derive torchvision version as: if torch is 2.5.x, torchvision is 0.20.x; if torch is 2.4.x, torchvision is 0.19.x.
6. 6. Install pyyaml if not already present: pip install pyyaml. (torch_npu requires yaml at import time for its memory visualization module.)
7. 7. Verify the stack: python -c 'import torch; import torch_npu; import torchvision; print(torch.__version__); print(torch_npu.__version__); print(torch.npu.is_available())'. Expected: torch shows '+cpu', torch_npu shows the probed version, npu.is_available() returns True.

## Do Not
- Do NOT install torch from a generic PyPI mirror first — it will pull the CUDA build and conflict with torch-npu's torch==X.X.X+cpu dependency.
- Do NOT install vanilla torchvision on top of CPU-base torch — the torchvision::nms operator registration will fail with a dispatch key conflict. Always use the +cpu variant.
- Do NOT skip the version probe step — system torch-npu version must match the torch version exactly (e.g., torch-npu 2.5.1 requires torch 2.5.1+cpu, not 2.6.1+cpu).

## References
- https://gitee.com/ascend/pytorch (torch-npu source)
- https://download.pytorch.org/whl/cpu (PyTorch CPU wheel index)

## Evidence
- Source runs: e2e-v2-39fff3edbbe2
- Source runs: e2e-v2-39fff3edbbe2-exp-20260429192846
- Source runs: e2e-v2-69fe6c6573b0
- Source runs: e2e-v2-c3c4a55c9cd4
