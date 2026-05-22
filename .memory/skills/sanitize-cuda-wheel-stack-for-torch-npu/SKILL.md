---
name: sanitize-cuda-wheel-stack-for-torch-npu
description: Sanitize CUDA wheel stacks for torch-npu environments
tags: ["torch-npu", "dependencies", "torchaudio", "numpy", "cann", "cuda-wheels"]
category: dependency_issue
subtype: cuda_wheel_stack_replacement
confidence: 0.92
occurrence_count: 1
---

# Sanitize CUDA wheel stacks for torch-npu environments

## When to Use
- A CUDA-oriented requirements stack pins nvidia-* cu12 wheels, triton, flash-attn, torch/torchaudio/torchvision CUDA-linked variants, and numpy 2.x. On Ascend NPU this can install incompatible CUDA binaries or fail imports, including torchaudio attempting to load libtorch_cuda.so.

## Root Cause
The original dependency manifest targets a CUDA PyTorch runtime rather than an Ascend torch-npu runtime. CUDA wheel families such as nvidia-*cu12, CUDA triton, flash-attn, and CUDA-linked torchaudio are not valid runtime dependencies for the NPU environment, while the observed CANN 8.x torch-npu stack required the host-compatible torch CPU wheel family, matching torch-npu, PyYAML, and numpy 1.26.x.

## How to Use
1. Before creating the virtual environment, scan the project dependency files for CUDA-only package families: nvidia-*cu12, triton, flash-attn, and unqualified torch, torchaudio, or torchvision pins.
2. Do not install nvidia-*cu12 packages, CUDA triton, or flash-attn into the torch-npu environment.
3. Create the project virtual environment without modifying the global Python environment; in this run, the working approach used python3 -m venv --system-site-packages .venv to inherit the host NPU stack.
4. Install the NPU-compatible core stack: torch==2.5.1+cpu, torch-npu==2.5.1, torchvision==0.20.1+cpu, torchaudio==2.5.1+cpu, numpy==1.26.4, and PyYAML==6.0.2.
5. Install the non-CUDA application dependencies from the project requirements, preserving needed packages such as transformers==4.46.1, accelerate==1.3.0, gradio==5.13.1, librosa==0.10.2.post1, and soundfile==0.13.1.
6. If torchaudio was installed from a mirror and importing it fails with a CUDA-linked libtorch_cuda.so error, force reinstall the CPU wheel with --no-deps from the PyTorch CPU wheel index: torchaudio==2.5.1+cpu.
7. Probe the environment before Phase 5 validation by importing torch, torch_npu, torchvision, and torchaudio, then checking that torch.npu is available and selects the intended Ascend device.
8. Run the migration validation entry only after dependency import probes pass; in the source run, final validation exercised t2t, t2s, s2t, and s2s on npu:0 with exit_code=0.

## Code Examples
[
  {
    "file": "original_src/requirements.txt",
    "before": "numpy==2.1.3\nnvidia-cuda-runtime-cu12==12.4.127\nnvidia-nccl-cu12==2.21.5\ntorch==2.5.1\ntorchaudio==2.5.1\ntorchvision==0.20.1\ntriton==3.1.0",
    "after": "torch==2.5.1+cpu\ntorch-npu==2.5.1\ntorchvision==0.20.1+cpu\ntorchaudio==2.5.1+cpu\nnumpy==1.26.4\nPyYAML==6.0.2"
  },
  {
    "file": "environment install procedure",
    "before": "pip install -r original_src/requirements.txt",
    "after": "python3 -m venv --system-site-packages .venv\n.venv/bin/python -m pip install non-CUDA project dependencies\n.venv/bin/python -m pip install --force-reinstall --no-deps torchaudio==2.5.1+cpu --index-url https://download.pytorch.org/whl/cpu"
  }
]

## Do Not
- Do NOT install nvidia-*cu12 wheels in a torch-npu runtime environment.
- Do NOT install CUDA triton or flash-attn for Ascend NPU validation.
- Do NOT leave torchaudio as a CUDA-linked wheel if import errors reference libtorch_cuda.so.
- Do NOT silently fall back to CPU for model, codec, tensor, or audio-token computation when the migration target requires NPU execution.
- Do NOT use numpy>=2.0 with this observed CANN 8.x torch-npu stack if it causes ACL/CANN initialization failures.

## References
- validated/phase_1_project_analysis_canonical.json
- validated/phase_1_5_constraint_summary_canonical.json
- validated/phase_2_venv_create_canonical.json
- reports/TOOLS_EXECUTION_REPORT.md
- reports/SUMMARY_REPORT.md

## Evidence
- Source runs: e2e-v2-98ea2e024ee4
