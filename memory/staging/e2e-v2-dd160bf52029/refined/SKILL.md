---
name: apex-cuda-extension-removal
description: Replace NVIDIA Apex (apex_C) with NPU-native alternatives
tags: ["apex", "fused-layer-norm", "cuda-extension", "module-not-found", "npu-native", "optimizer", "mixed-precision"]
category: dependency_issue
subtype: apex_cuda_extension_no_npu_equivalent
confidence: 0.95
occurrence_count: 1
---

# Replace NVIDIA Apex (apex_C) with NPU-native alternatives

## When to Use
- ModuleNotFoundError: cannot import name 'apex_C' (or 'fused_layer_norm', 'FusedAdam', 'FusedSGD', etc.) — NVIDIA Apex library is imported but has no Ascend NPU build or pip package available.

## Root Cause
NVIDIA Apex is a CUDA-only extension library compiled specifically for NVIDIA GPU architectures. Its C-library (apex_C) and fused operators (fused_layer_norm, FusedAdam, FusedSGD, FusedLAMB, etc.) have no Ascend NPU equivalent. No third-party apex package or NPI variant exists for NPU. The entire Apex dependency must be removed and replaced with NPU-native PyTorch APIs.

## How to Use
1. 1. Search all Python source files for Apex-related imports: `grep -rn 'from apex' --include='*.py' .` and `grep -rn 'from apex_C' --include='*.py' .`. Also check for `import apex` and any reference to `apex.` in code.
2. 2. Remove ALL `from apex import ...`, `from apex_C import ...`, and `import apex` lines. Do NOT attempt to conditionally import or wrap Apex — it cannot work on NPU.
3. 3. If `apex_C.fused_layer_norm` or `apex.normalization.FusedLayerNorm` was used, replace with `torch.nn.LayerNorm(normalized_shape, eps=1e-5)`. NPU-native LayerNorm provides equivalent semantics. Verify the `normalized_shape` parameter matches the last dimension of the input tensor.
4. 4. If Apex AMP (Automatic Mixed Precision) was used (`from apex import amp`, `amp.initialize()`, `amp.scale_loss()`), replace with PyTorch native autocast: `with torch.autocast(device_type='npu', dtype=torch.float16):` or `with torch.npu.amp.autocast():`. Replace `amp.scale_loss(loss, optimizer)` with `scaler.scale(loss).backward()`, `scaler.step(optimizer)`, `scaler.update()` using `torch.npu.amp.GradScaler()`.
5. 5. If Apex fused optimizers were used (`apex.optimizers.FusedAdam`, `FusedSGD`, `FusedLAMB`, `FusedNovoGrad`), replace with standard PyTorch equivalents: `torch.optim.Adam`, `torch.optim.SGD`, etc. Pass identical learning rate, weight_decay, eps, and betas parameters. Note: Fused optimizers may have marginally different performance but produce functionally equivalent results.
6. 6. If Apex DistributedDataParallel (`apex.parallel.DistributedDDP`) was used, replace with `torch.nn.parallel.DistributedDataParallel`.
7. 7. Verify no remaining references to `apex`, `apex_C`, `amp` (Apex variant), or `Fused*` classes remain in any Python file. Run: `grep -rn 'apex' --include='*.py' .` to confirm.
8. 8. Verify output tensor shapes and dtypes match the original implementation by comparing forward pass outputs with a small test input batch.

## Code Examples
**File: model/normalization.py (or wherever FusedLayerNorm is instantiated)**
# Before
from apex.normalization import FusedLayerNorm

layer_norm = FusedLayerNorm(normalized_shape=config.hidden_size, eps=1e-5)
# After
import torch.nn as nn

layer_norm = nn.LayerNorm(normalized_shape=config.hidden_size, eps=1e-5)

**File: optimizer.py (or wherever Apex optimizer is instantiated)**
# Before
from apex.optimizers import FusedAdam

optimizer = FusedAdam(model.parameters(), lr=lr, eps=1e-8, weight_decay=weight_decay)
# After
import torch.optim as optim

optimizer = optim.Adam(model.parameters(), lr=lr, eps=1e-8, weight_decay=weight_decay)

**File: train.py (or wherever Apex AMP is used)**
# Before
from apex import amp

model, optimizer = amp.initialize(model, optimizer, opt_level='O1')

with amp.scale_loss(loss, optimizer) as scaled_loss:
    scaled_loss.backward()

optimizer.step()
# After
scaler = torch.npu.amp.GradScaler()

# Forward pass
with torch.autocast(device_type='npu', dtype=torch.float16):
    output = model(input_ids)
    loss = criterion(output, labels)

# Backward pass
scaler.scale(loss).backward()
scaler.step(optimizer)
scaler.update()

**File: distributed_setup.py (or wherever Apex DDP is used)**
# Before
from apex.parallel import DistributedDataParallel as ApexDDP

model = ApexDDP(model)
# After
from torch.nn.parallel import DistributedDataParallel as DDP

model = DDP(model, device_ids=[local_rank], output_device=local_rank)

## Do Not
- Do NOT attempt to install apex via pip or build from source — it will fail with CUDA compilation errors on NPU.
- Do NOT attempt to use a third-party 'apex-npu' or 'apex-ascend' package — no such package exists.
- Do NOT use CPU fallback for LayerNorm or optimizers — it will OOM on large models.
- Do NOT wrap Apex imports in try/except to suppress errors — the code will silently fail at runtime.
- Do NOT attempt to compile Apex C++ extensions manually for NPU — the kernel implementations are GPU-specific (CUDA PTX) and have no NPU (BIC/AICORE) equivalent.

## References
- https://pytorch.org/docs/stable/notes/amp_examples.html
- https://pytorch.org/docs/stable/generated/torch.nn.LayerNorm.html
- https://gitee.com/ascend/ascend-toolkit

## Evidence
- Source runs: e2e-v2-dd160bf52029
