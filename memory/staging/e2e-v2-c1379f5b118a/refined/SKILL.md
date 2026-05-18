---
name: npu-venv-system-site-packages
description: NPU virtual environment strategy: prefer --system-site-packages over external mirror pip install
tags: ["venv-creation", "system-site-packages", "torch-npu", "npu-environment", "pip-mirror"]
category: environment_issue
subtype: wrong_package_variant_from_mirror
confidence: 0.8
occurrence_count: 1
---

# NPU virtual environment strategy: prefer --system-site-packages over external mirror pip install

## When to Use
- After pip install -r requirements.txt from a domestic mirror (Aliyun, Tsinghua), the script crashes with CUDA kernel not found errors — torch reports version 2.x.x+cuXX with nvidia-* dependencies installed, but the host is an Ascend NPU with no CUDA GPU.

## Root Cause
Standard PyPI mirrors (including domestic ones like Aliyun and Tsinghua) distribute CUDA-native PyTorch builds when you run pip install torch. On Ascend NPU hosts, the system Python typically already has a pre-configured NPU-compatible PyTorch stack (e.g., torch 2.5.1+cpu + torch-npu 2.5.1). Creating an isolated venv without --system-site-packages shadows these pre-installed packages and pulls the wrong CUDA variant.

## How to Use
1. 1. Before creating a new virtual environment on a confirmed NPU host, check if system Python already has torch-npu installed: run `python3 -c "import torch_npu; print(torch_npu.__version__)"` to confirm.
2. 2. Also verify system torch version: run `python3 -c "import torch; print(torch.__version__)"`.
3. 3. If both torch and torch-npu are confirmed present on the system, create the virtual environment with --system-site-packages: `python3 -m venv --system-site-packages <project_dir>/.venv`.
4. 4. Upgrade pip inside the venv if needed: `<venv_path>/bin/pip install --upgrade pip`.
5. 5. Install only non-torch project dependencies (if any) via `<venv_path>/bin/pip install -r requirements.txt`. Do NOT pip install torch or torch-npu separately.
6. 6. Verify the venv inherits the correct packages: `<venv_path>/bin/python -c "import torch; import torch_npu; print(f'torch={torch.__version__}, torch_npu={torch_npu.__version__}')"`.
7. 7. If torch-npu is NOT pre-installed on the system, consult the Ascend/torch-npu installation guide for the correct NPU-native wheel URL rather than falling back to standard PyPI mirrors.

## Do Not
- Do NOT create a standard venv (without --system-site-packages) on an NPU host that has pre-installed torch-npu — it will pull CUDA torch from PyPI.
- Do NOT pip install torch from Aliyun/Tsinghua mirrors on NPU hosts unless you explicitly specify the NPU-compatible wheel URL.
- Do NOT assume CUDA packages will fail to install on NPU — pip will happily install them; the failure occurs at runtime when CUDA kernels cannot execute.
- Do NOT use conda create without checking if the conda environment already has torch-npu in its channels.

## Evidence
- Source runs: e2e-v2-c1379f5b118a
