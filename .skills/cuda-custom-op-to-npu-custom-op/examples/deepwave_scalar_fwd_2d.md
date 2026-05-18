# Example: Deepwave ScalarFwd2D Migration

This is a non-normative worked example. It shows how one stencil/PML PyTorch project handled migration. Deepwave names, scalar wave equations, `python_backend` modes, CANN paths, and Ascend 910B settings are case-study details, not requirements for other CUDA custom-op migrations.

**Project**: Deepwave (wave propagation for seismic imaging/FWI)
**Op**: ScalarFwd2D: 2D scalar wave equation time-stepping
**Migration**: PyTorch eager on NPU -> custom Ascend C kernel on NPU
**Environment**: CANN 8.5.0 | Ascend 910B | PyTorch 2.6.0 + torch-npu

---

## Overview

Deepwave is a PyTorch-based wave propagation library used for seismic imaging and Full Waveform
Inversion (FWI). Its core time-stepping loop calls `_forward_step` at every time step, which
computes the 2D scalar wave equation update:

```
wfp_out[i,j] = v[i,j]^2 * dt^2 * Lap(wfc) + 2*wfc[i,j] - wfp[i,j]

Lap(wfc) = (wfc[i-1,j] + wfc[i+1,j] - 2*wfc[i,j]) / dy^2
         + (wfc[i,j-1] + wfc[i,j+1] - 2*wfc[i,j]) / dx^2
```

Boundary condition: zero Dirichlet (out-of-bounds neighbors treated as 0).

The original NPU path used `python_backend="eager"`, which runs the Laplacian via PyTorch's
`diff1`/`diff2` ops. The target is `python_backend="npu_kernel"`, which routes interior cells
through a custom Ascend C kernel while PML boundary cells continue using PyTorch.

**Key source files read during this migration:**
- `scalar.py`: main time-stepping loop and `scalar_func` router
- `scalar.cu`: original CUDA kernel (reference for the math)
- `_forward_step`: Python implementation of the wave update formula

---

## Step-by-Step Mapping to Skill

### Step 1: Project Analysis

Identified the hot path: `deepwave.scalar()` -> `scalar_func()` -> `_forward_step()` called
`nt` times per shot. The bottleneck is the 5-point Laplacian inside `_forward_step`. The
original CUDA kernel in `scalar.cu` confirmed the exact formula and boundary handling.

Key files: `src/deepwave/scalar.py` (lines ~1935 for `_forward_step`, ~2154 for `scalar_func`).

### Step 2: Math Recovery

Extracted the 5-point finite difference formula from `scalar.cu`. Wrote a CPU reference
implementation in Python to serve as ground truth:

```python
def run_cpu_reference(wfc_np, wfp_np, v_np, dt2, dx, dy):
    ny, nx = wfc_np.shape
    rdx2 = 1.0 / (dx * dx)
    rdy2 = 1.0 / (dy * dy)
    out = np.zeros_like(wfc_np)
    for i in range(ny):
        for j in range(nx):
            up    = wfc_np[i-1, j] if i > 0 else 0.0
            down  = wfc_np[i+1, j] if i < ny-1 else 0.0
            left  = wfc_np[i, j-1] if j > 0 else 0.0
            right = wfc_np[i, j+1] if j < nx-1 else 0.0
            lap = (up + down - 2*wfc_np[i,j])*rdy2 + (left + right - 2*wfc_np[i,j])*rdx2
            out[i,j] = v_np[i,j]**2 * dt2 * lap + 2*wfc_np[i,j] - wfp_np[i,j]
    return out
```

### Step 3: Strategy Notes

This case used a hybrid approach:
- **Interior cells** (where PML coefficients `b=0, a=0`): custom Ascend C kernel
- **PML boundary cells**: PyTorch `diff1`/`diff2` on NPU tensors (unchanged)
- **Fallback**: when `custom_ops_lib` was unavailable, accuracy was not 2, or the device was
  not NPU, the route returned to the original `_forward_step`

This avoids rewriting PML logic (which is complex) while still accelerating the dominant
interior region.

### Step 4: Kernel Implementation

File: `custom_op_scalar_fwd_2d/op_kernel/scalar_fwd2_d.cpp`

The kernel uses a class `KernelScalarFwd2D` with three pipeline stages: `CopyIn`, `Compute`,
`CopyOut`. The data layout in the local buffer is:

```
inQueue buffer (5*nx floats per entry):
  [0 .. 3*nx-1]   : wfc[y-1,:], wfc[y,:], wfc[y+1,:]  (3 rows, contiguous)
  [3*nx .. 4*nx-1]: wfp[y,:]
  [4*nx .. 5*nx-1]: v[y,:]

outQueue buffer (nx floats per entry):
  [0 .. nx-1]: wfp_out[y,:]
```

`CopyIn` loads 3 rows of `wfc` in a single `DataCopy` call (the GM pointer is offset by `-nx`
so that `wfcGm[row*nx]` points to `wfc[y-1,:]`). `Compute` uses `GetValue`/`SetValue` scalar
loops to apply the 5-point stencil. `CopyOut` writes one row back to GM.

Boundary rows (`y=0`, `y=ny-1`) are intentionally skipped; they stay zero (Dirichlet).

### Step 5: Host Tiling

File: `custom_op_scalar_fwd_2d/op_host/scalar_fwd_2d.cpp`

TilingData fields: `dt2` (float), `ny` (uint32), `nx` (uint32), `rdy2` (float), `rdx2`
(float), plus padding to reach `opParaSize=32` bytes.

Dynamic `blockDim` formula (avoids single-core timeout on large shapes):

```cpp
uint32_t blockDim = (ny >= 64) ? (ny / 16) : 1;
if (blockDim > 8) blockDim = 8;
context->SetBlockDim(blockDim);
```

| Shape | ny | BlockDim | Rows/Core |
|-------|----|----------|-----------|
| 16x16 | 16 | 1 | 16 |
| 64x64 | 64 | 4 | 16 |
| 128x128 | 128 | 8 | 16 |

### Step 6: Build

```bash
# SoC target note from this case study
# CMakePresets.json: "value": "ascend910b"
# op_host/scalar_fwd_2d.cpp: AddConfig("ascend910b")

cd custom_op_scalar_fwd_2d
rm -rf /root/.ascendc/kernel_cache/*   # this case cleared compiler cache
rm -rf build_out/*
source /usr/local/Ascend/cann-8.5.0/set_env.sh
bash build.sh

cd build_out
bash custom_opp_ubuntu_x86_64.run \
  --install-path=/usr/local/Ascend/cann-8.5.0 --nox11 --quiet
```

This case recorded the `.o` file and hash after rebuilds:
```bash
md5sum .../kernel/ascend910b/scalar_fwd2_d/ScalarFwd2D_*.o
```

### Step 7: Python Binding

File: `scalar_fwd_2d_pybind/python_bind_scalar_fwd_2d.cpp`

Structure: pybind11 module exposing `scalar_fwd_2d(wfc, wfp, v, dt2, rdy, rdx)`. The C++
function tries ACLNN first, then falls back to ACLRT direct launch:

```cpp
static at::Tensor scalar_fwd_2d_npu(
    const at::Tensor& wfc, const at::Tensor& wfp,
    const at::Tensor& v, double dt2, double rdy, double rdx)
{
    // Try ACLNN path
    aclnnStatus status = aclnnScalarFwd2DGetWorkspaceSize(..., &workspace_size, &executor);
    if (status != 0) {
        // ACLNN returns 161001 for shape mismatch; fall back to ACLRT
        return LaunchScalarFwd2DViaAclrt(wfc, wfp, v, dt2, rdy, rdx);
    }
    // ACLNN success path ...
}
```

The ACLRT fallback loads the `.o` binary, constructs a `KernelTilingData` struct matching
`opParaSize`, and calls `aclrtLaunchKernel` with a `void*[6]` args array:
`[wfc_ptr, wfp_ptr, v_ptr, out_ptr, nullptr, &tiling]`.

`blockDim` is computed dynamically using the same formula as the host tiling.

Compiled with `setup.py` using `CppExtension`, linking against `libascendcl`,
`libcust_opapi`, and `libopapi`.

### Step 8: Stream Synchronization

NPU execution is asynchronous. The pybind binding creates a dedicated `aclrtStream` per call
and calls `aclrtSynchronizeStream` before returning the output tensor. On the Python side,
`torch.npu.synchronize()` is used around timing measurements to get accurate wall-clock times.

### Step 9: Kernel Tests

File: `scalar_fwd_2d_pybind/test_validation.py`

Each shape runs in an **independent subprocess** (see Problems section for why). Comparison
is done only on interior rows `[1:-1, :]` since boundary rows are intentionally zero.

Results (all 5 cases PASS):

| Shape | dx | dy | max_abs_diff |
|-------|----|----|------------|
| 16x16 | 10.0 | 10.0 | 3.7e-09 |
| 32x32 | 10.0 | 10.0 | 3.7e-09 |
| 32x32 | 5.0 | 5.0 | 3.7e-09 |
| 64x64 | 10.0 | 10.0 | 7.5e-09 |
| 128x128 | 15.0 | 15.0 | 7.5e-09 |

All differences are within float32 machine precision.

### Step 10: Integration into Deepwave

Two files modified in the deepwave source tree:

**`src/deepwave/scalar.py`**: Added `"npu_kernel"` to the `python_backend` type annotation
(3 locations) and added a routing branch in `scalar_func`:

```python
elif mode == "npu_kernel":
    from deepwave.npu_fwd_step import npu_forward_step as _npu_fwd
    _forward_step_opt = lambda *a, **kw: (
        _npu_fwd(*a, **kw) if npu_fwd_is_npu(a) else _forward_step(*a, **kw)
    )
```

**`src/deepwave/npu_fwd_step.py`** (new file): Implements `npu_forward_step`, which:
1. Detects interior bounds from PML coefficients `b[0]`, `b[1]` (squeeze 3D first)
2. Computes full PyTorch baseline `pml_full` (correct for all cells)
3. Extracts interior block with ghost cells: `wfc[g0_y-1 : g1_y+2, g0_x-1 : g1_x+2]`
4. Calls `custom_ops_lib.scalar_fwd_2d` on the block
5. Writes kernel result back into `pml_full` at the interior slice

### Step 11: Autograd Handling

The `npu_kernel` path does not implement custom backward. Instead:
- When `v.requires_grad=True`, PyTorch autograd traces through the `pml_full.clone()` and
  slice-assignment operations normally.
- The kernel call itself (`custom_ops_lib.scalar_fwd_2d`) is not differentiable, but since
  the kernel result is re-expressed as `v**2 * dt2 * kernel_valid + 2*wfc - wfp` before
  writing back, autograd can differentiate through `v` correctly.
- This works because the kernel computes the same math as the PyTorch expression; the
  gradient flows through the PyTorch re-expression, not through the kernel binary.

### Step 12: End-to-End Validation

Three levels of E2E testing:

**Level 1, single step**: `npu_forward_step` vs `_forward_step` on 120x200 padded model
with full PML. All outputs (`new_wfp`, `psi_y`, `psi_x`, `zeta_y`, `zeta_x`) show
`max|diff|=0.00`.

**Level 2, scalar() forward+backward**: 80x120 model, 2 shots, 50 time steps.
`wfc`, receiver data, loss, and gradient all match `eager` with `max|diff|=0.00`.

**Level 3, FWI (Marmousi)**: 300x150 subregion, 5 shots, 150 time steps, 3 epochs.

| Epoch | eager loss | npu_kernel loss |
|-------|-----------|----------------|
| 1 | 0.069256 | 0.069256 |
| 2 | 0.063218 | 0.063218 |
| 3 | 0.057417 | 0.057417 |

### Step 13: Performance

| Scenario | eager | npu_kernel | Speedup |
|----------|-------|------------|---------|
| 200x200, nt=60, 1 shot | 0.219s | 0.044s | ~5x |
| Marmousi 300x150, nt=150, 5 shots | 1.69s/epoch | 1.53s/epoch | ~1.1x |

The 5x speedup on the pure interior case confirms the kernel is actually running. The lower
1.1x on Marmousi reflects that PML computation (still PyTorch) and kernel launch overhead
dominate when the model has significant PML padding. The `torch.npu.synchronize()` call
before/after each kernel launch also adds latency that would not exist in a production
pipeline with pipelined launches.

### Step 14: Documentation

Three documents produced during this migration:
- `5-Point_Laplacian_Implementation_Guide.md`: kernel implementation and all failure modes
- `Python_Invocation_Implementation_Record.md`: pybind11 + ACLRT binding details
- `Phase_E_Implementation_Record.md`: integration, routing, and FWI validation
- `Integration_Status_Report.md`: architecture overview and final verification summary

---

## Key Problems Encountered

### 1. `SetMatrixStrip` API hallucination

**Root cause**: An earlier agent suggested using `SetMatrixStrip` for strided GM access.
This API does not exist in CANN 8.5.0. Grepping all headers returned zero results.

**Solution**: Use 3-row contiguous `DataCopy` with a GM pointer offset of `-nx`, then
access individual elements with `GetValue`/`SetValue`.

**Lesson**: Grepping CANN SDK headers before using an unfamiliar API name saved debugging time.

### 2. `LocalTensor[]` returns sub-tensor, not scalar

**Root cause**: `LocalTensor<T>::operator[](uint32_t)` returns `LocalTensor<T>`, not `T`.
Assigning it to `float` causes a compile error.

**Solution**: Use `src[offset].GetValue(col)` to read a scalar, and `dst.SetValue(col, val)`
to write one. The SDK header confirms this at `kernel_tensor.h:147`.

### 3. ACLNN returns 161001 for all shapes

**Root cause**: The CANN `customize` template compiles a static-shape kernel. The shape key
baked into the `.o` file doesn't match runtime shapes, so ACLNN rejects the dispatch.

**Solution**: Implement an ACLRT fallback (`LaunchScalarFwd2DViaAclrt`) that loads the `.o`
directly via `aclrtBinaryLoadFromFile` + `aclrtLaunchKernel`, bypassing ACLNN's shape check.

### 4. SoC mismatch: `ascend910_93` vs `ascend910b`

**Root cause**: `CMakePresets.json` had `ASCEND_COMPUTE_UNIT = ascend910_93` (the default
template value). The actual hardware is Ascend 910B. The compiled kernel binary has an
incompatible instruction set, causing a Vector Core Exception (error 507035) at runtime.

**Solution**: Change `CMakePresets.json` and `AddConfig(...)` in the host file to
`ascend910b`, then rebuild and reinstall.

### 5. `torch.where` merge failed (max|diff|=2.34)

**Root cause**: The first integration attempt used `torch.where(pml_sum > 1e-30, pml_full,
kernel_out)` to select between PyTorch and kernel results. But `pml_sum` is the Laplacian
value, which is non-zero everywhere, so the condition never selects the kernel output.

**Solution**: Detect interior bounds from PML coefficients `b[dim]` (which are exactly zero
in the interior and non-zero in PML zones). Extract a block with ghost cells, run the kernel,
and write the result back via slice assignment.

### 6. PML profiles have 3D shape `[1, ny, 1]`

**Root cause**: `set_pml_profiles()` returns tensors shaped `[1, ny, 1]` (with a batch dim).
Calling `.nonzero()` on a 3D tensor returns multi-dimensional indices; the first index was
0 (the batch dim), not the spatial row index.

**Solution**: Call `b.squeeze()` (and `reshape(-1)` as a safety net) before computing
`nonzero()` to get the correct 1D spatial indices.

### 7. Stream desync causes incorrect timing and occasional errors

**Root cause**: NPU ops are asynchronous. Without `torch.npu.synchronize()`, wall-clock
timing captures only the kernel launch time, not execution time. Also, the ACLRT stream
needed synchronization before the output tensor was read in this binding.

**Solution**: Call `aclrtSynchronizeStream(stream)` inside the C++ binding after
`aclrtLaunchKernel`. Use `torch.npu.synchronize()` around Python-level timing blocks.

### 8. CANN kernel cache prevents recompilation

**Root cause**: The Ascend C compiler caches compiled kernels by source hash. Even after
clearing `build_out/`, the compiler reuses the cached `.o` if the source hash matches.
This means code changes appear to have no effect.

**Solution**: This migration cleared `/root/.ascendc/kernel_cache/*` before rebuilding.

### 9. Same-process kernel cache causes shape interference

**Root cause**: `aclrt` caches loaded kernel binaries within a process. Running multiple
shapes in the same Python process means the second shape executes the first shape's binary.

**Solution**: Run each shape in an independent subprocess via `subprocess.run(...)`.

---

## File Manifest

| File | Status | Description |
|------|--------|-------------|
| `custom_op_scalar_fwd_2d/op_kernel/scalar_fwd2_d.cpp` | Created | Ascend C kernel: 5-point Laplacian, GetValue/SetValue scalar loop, 3-row DataCopy |
| `custom_op_scalar_fwd_2d/op_host/scalar_fwd_2d.cpp` | Modified | TilingData (dt2/ny/nx/rdy2/rdx2), dynamic blockDim, AddConfig("ascend910b") |
| `custom_op_scalar_fwd_2d/CMakePresets.json` | Modified | ASCEND_COMPUTE_UNIT: ascend910_93 -> ascend910b |
| `scalar_fwd_2d_pybind/python_bind_scalar_fwd_2d.cpp` | Created | pybind11 entry, ACLNN try -> ACLRT fallback, dynamic blockDim, RAII handles |
| `scalar_fwd_2d_pybind/pytorch_npu_helper.hpp` | Created | ConvertType / Release declarations |
| `scalar_fwd_2d_pybind/pytorch_npu_helper.cpp` | Created | torch::Tensor -> aclTensor* conversion (9-arg aclCreateTensor, 1D storageDims) |
| `scalar_fwd_2d_pybind/setup.py` | Created | CppExtension linking ascendcl + cust_opapi + opapi |
| `scalar_fwd_2d_pybind/test_validation.py` | Created | CPU reference + subprocess-per-shape NPU validation |
| `src/deepwave/npu_fwd_step.py` | Created | Interior detection, ghost cell extraction, kernel dispatch, result writeback |
| `src/deepwave/scalar.py` | Modified | "npu_kernel" type annotation (3 places), scalar_func routing branch, npu_fwd_is_npu helper |

---

## Verification Results Summary

### Kernel Unit Tests (5-point Laplacian vs CPU reference)

| Shape | dx | dy | max_abs_diff | Status |
|-------|----|----|------------|--------|
| 16x16 | 10.0 | 10.0 | 3.7e-09 | PASS |
| 32x32 | 10.0 | 10.0 | 3.7e-09 | PASS |
| 32x32 | 5.0 | 5.0 | 3.7e-09 | PASS |
| 64x64 | 10.0 | 10.0 | 7.5e-09 | PASS |
| 128x128 | 15.0 | 15.0 | 7.5e-09 | PASS |

### Integration Tests (npu_kernel vs eager)

| Test | Metric | Value | Status |
|------|--------|-------|--------|
| Single step (120x200, full PML) | max\|diff\| new_wfp | 0.00 | PASS |
| Single step (120x200, full PML) | max\|diff\| psi/zeta | 0.00 | PASS |
| scalar() forward (80x120, 2 shots) | max\|diff\| wfc | 0.00 | PASS |
| scalar() forward (80x120, 2 shots) | max\|diff\| receiver | 0.00 | PASS |
| scalar() backward (80x120, 2 shots) | max\|diff\| loss | 0.00 | PASS |
| scalar() backward (80x120, 2 shots) | max\|diff\| grad | 0.00 | PASS |
| FWI epoch 1 (Marmousi 300x150) | loss | 0.069256 | PASS |
| FWI epoch 2 (Marmousi 300x150) | loss | 0.063218 | PASS |
| FWI epoch 3 (Marmousi 300x150) | loss | 0.057417 | PASS |

---

## Lessons for Future Migrations

- **Verify API names against CANN headers before writing code.** The SDK in this case was at
  `/usr/local/Ascend/cann-8.5.0/`. A quick `grep -r "FunctionName" /usr/local/Ascend/cann-8.5.0/include/`
  saves hours of debugging hallucinated APIs.

- **SoC target consistency mattered.** A wrong `ASCEND_COMPUTE_UNIT` in `CMakePresets.json`
  produces a kernel that compiles cleanly but crashes at runtime with a cryptic Vector Core
  Exception. Check `npu-smi info` and match the SoC name exactly.

- **Kernel compiler cache affected rebuilds.** `rm -rf /root/.ascendc/kernel_cache/*`
  helped avoid stale binaries in this case. Without it, source changes appeared to have no effect.

- **Use `GetValue`/`SetValue` for scalar element access in Ascend C.** `LocalTensor::operator[]`
  returns a sub-tensor, not a scalar. This is a common source of compile errors for developers
  coming from CUDA.

- **ACLNN shape mismatch (error 161001) appeared with dynamic shapes.** The ACLRT direct
  launch fallback worked around that case. The ACLNN path remained useful as a primary attempt
  when dynamic shape registration is available.

- **PML-style hybrid kernels need ghost cells.** When the kernel only handles interior cells
  but uses a stencil, extract a block that includes one row/column of padding on each side.
  Without ghost cells, the interior boundary rows compute against zero neighbors.

- **Run each test shape in a separate subprocess.** The ACLRT runtime caches loaded kernel
  binaries within a process. Testing multiple shapes in one process will silently reuse the
  first shape's binary for all subsequent shapes.
