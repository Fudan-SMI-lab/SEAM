# CUDA Custom Op To Ascend Custom Op Migration Notes

This directory is an experience reference for converting CUDA or C++ custom
operators into Ascend C / CANN custom operators. It is documentation only. It is
not a task contract, agent instruction set, completion policy, validation gate,
or automatic execution workflow.

The notes below preserve practical migration lessons from previous projects.
Project scope, priorities, acceptance criteria, reports, and stop conditions live
in the user's current request or in the target project's own plan.

## KernelBench (build_method=cmake) — read this FIRST

If the task's `solution.json` uses `build_method: "cmake"` (KernelBench), copy the
structure of KernelBench's own official sample and build template:

- Sample (canonical, correct): `/home/yiding/bench/npu-kernelbench/tests/samples/vector_add_ascendc_cmake/`
  - `op_kernel/add_custom.cpp`, `op_host/my_add.cpp`, `op_host/utils.h`, `main.py`
- Build skeleton: `/home/yiding/bench/npu-kernelbench/src/npu_kernelbench/builder/templates/CMakeLists.txt.j2`
  (it compiles `op_kernel/**` into the kernel lib, links `torch_npu/ascendcl/tiling_api/register/platform`,
  and CANN's `ascendc.cmake` generates the `aclrtlaunch_*.h` headers)

Hard rules confirmed by that sample:
- Device entry MUST be `extern "C" __global__ __aicore__ void <kernel>(GM_ADDR ...)`.
  Never use `__global__ __vector__` (bisheng: "1 error generated").
- Host MUST `#include "aclrtlaunch_<kernel>.h"` and
  `ACLRT_LAUNCH_KERNEL(<kernel>)(blockDim, aclStream, ...)` with
  `c10_npu::getCurrentNPUStream().stream(false)`.
- Files: `op_kernel/<name>.cpp`, `op_host/<name>.cpp` (+ optional `utils.h`), `main.py`.
- Do NOT hand-write CMakeLists (the builder generates it).

### Matrix-multiply / Cube ops (matmul, batched matmul, conv using mmad)

This CANN install has TWO matmul header trees; only one is complete:
- COMPLETE (use this): `/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/ascendc/include/highlevel_api/`
  (`lib/matmul/matmul_intf.h` + `impl/kfc/kfc_register_obj.h` + `impl/matmul/**` all present)
- BROKEN (never use): `/usr/local/Ascend/ascend-toolkit/latest/tools/tikcpp/tikcfw/`
  (its `impl/kfc/` is EMPTY, so `matmul_intf.h` fails with
  `fatal error: '../impl/kfc/kfc_register_obj.h' file not found`)

Rules for matmul:
- Do NOT rely on a bare `#include "matmul_intf.h"` (the compiler may pick the broken
  `tikcfw` copy). Include the COMPLETE tree explicitly, e.g.
  `#include "<highlevel_api>/lib/matmul/matmul_intf.h"`, or copy the entire
  `highlevel_api/{lib,impl}` into the project preserving that layout and include the
  local path — the header's `../impl/kfc/...` includes must resolve inside the copied tree.
- Use `AscendC::Matmul` + `TCubeTiling` with host-side tiling (`tiling_api` is already
  linked by the builder). Do not copy vendor `opp/**` or `tikcfw`.
- Verify with a tiny standalone macro/cube kernel compile before writing the full op.

Read scope:
- USE: KernelBench `tests/samples/**`, `builder/templates/**`, `docs/**`,
  CANN `ascendc/include/**` + tiling headers, and the task's own `definition.json`
  (`definition.json.reference` is the operator spec, provided by design).
- AVOID (cheating / noise): previously solved AscendC solutions under
  `output_projects/**` or `operator_runs/**`, vendor `opp/**` and `tikcfw`
  implementations, and `/home/yiding/opencode_home/**`. Do not copy a finished
  solution; derive from the reference and AscendC public API only.

## Common Placeholders

The examples use placeholders to avoid binding generic notes to one site or
project.

| Placeholder | Meaning |
|-------------|---------|
| `{{PROJECT_NAME}}` | Project being migrated |
| `{{FRAMEWORK_NAME}}` | Host framework or runtime, such as PyTorch, TensorFlow, native C++, or a plugin system |
| `{{CANN_INSTALL_PATH}}` | Active CANN install root |
| `{{VENDOR_NAME}}` | Vendor name registered in the custom OPP package |
| `{{SOC_VERSION}}` | Target SoC string used by CANN build and `AddConfig` |
| `{{BASELINE_MODE}}` | Existing reference mode used for accuracy and speed comparison |
| `{{CUSTOM_OP_MODE}}` | Mode that calls the Ascend custom-op path |
| `{{CUSTOM_OPP_PATH}}` | Active custom OPP vendor package path |
| `{{CUSTOM_OPP_INSTALL_ROOT}}` | Install root passed to the generated custom OPP installer |
| `{{PROJECT_LOCAL_WORKDIR}}` | Project-local scratch directory for generated experiments |
| `{{op_name}}` | Operator-specific source, host, kernel, and artifact stem |
| `{{OP_TYPE}}` | Generated CANN op type identifier |

Concrete paths, vendor names, SoCs, operator inventories, benchmark values, and
project-specific module names are better kept in project-local records or explicit
case studies.

## Migration Mindset

- The skill content is a collection of lessons, probes, and example structures.
- Current-project evidence is more reliable than memory: source registrations,
  generated headers, op-info entries, loaded-library paths, runtime probes, and
  build logs usually explain the real state.
- Operator names, build modes, adapter routes, and artifact locations vary by
  project, framework, generator, and CANN version.
- Framework aliases, wrappers, placeholder modules, and fallback paths often make
  operator counts look larger than the real installed OPP count.
- Framework built-ins are useful as references or baselines, while custom-op
  success claims usually depend on project-defined scope and evidence.

## Operator Mapping Experience

A useful migration record often traces each public entry through this path:

```text
source public entry or framework alias
-> semantic operator
-> generated OPP entry
-> adapter callable
-> coverage key
-> parity scope
```

Several framework aliases can share one semantic operator or one OPP entry. Keeping
aliases visible in the record helps explain accuracy, coverage, and integration
results without confusing aliases with real installed custom ops.

Typical per-operator investigation artifacts:

- Source CUDA/C++ entry points, registration code, and launch sites.
- Plain-language formula or algorithm.
- Shape, dtype, stride, contiguity, storage-offset, alignment, and boundary rules.
- CPU, NumPy, framework, or native reference implementation.
- Ascend C kernel, host tiling, op definition, generated headers, op-info entries,
  kernel artifacts, and producer libraries.
- Adapter or caller identity, import/link evidence, and runtime call observations.
- Project-level accuracy, integration, and timing observations.

## Project Analysis Lessons

Common discovery targets include `.cu`, `.cuh`, `.cpp`, and `.cc` files; build
files for CUDA extensions; framework registries such as `REGISTER_OP`,
`TORCH_LIBRARY`, pybind11 modules, plugin tables, or dispatcher branches; backward
or gradient kernels; and device-specific dependencies such as CUDA streams, events,
atomics, texture memory, cooperative groups, CUB, cuBLAS, or cuDNN.

Inventory tables have been useful when they separate the source of truth for each
count:

| Count Type | Source of Truth | Meaning |
|------------|-----------------|---------|
| CUDA source ops | CUDA source and registration files | Semantic work present in the source project |
| Real NPU OPP ops | Generated ACLNN headers and installed op-info entries | Entries visible to the CANN package |
| Adapter functions | Binding module functions, plugin exports, or native symbols | Host-callable surface |
| Framework aliases | Project-specific registry names | Public names that may wrap or alias real ops |

## Math And Semantics Lessons

Successful migrations usually started by recovering the public contract before
writing Ascend C code. Useful notes per op include the formula, boundary and mask
rules, shape behavior, dtype expectations, reference outputs, numerical tolerance,
and gradient dependencies.

Recurring observations:

- Syntax-level CUDA-to-Ascend translation can preserve scheduling details while
  missing the public semantics.
- Custom-op call counts and successful launches are not mathematical parity proof.
- A deterministic correctness-first Ascend implementation can be valuable before
  recreating the CUDA optimization schedule.
- Backward migration depends on the forward intermediates consumed by gradients.
- Output meaning matters: a kernel returning a full update is different from a
  kernel returning one term of a larger formula.

## Strategy Lessons

Common target strategies include Ascend C custom kernels, generated ACLNN APIs,
ACLRT direct launch, hybrid custom/baseline regions, and framework-native reference
paths. Adapter approaches vary across native C++ callers, framework plugins,
Python C extensions, ctypes, and PyTorch pybind11 with torch-npu streams.

Generated ACLNN headers are a useful signature reference because host adapter
signatures can differ from kernel parameter lists.

## Ascend C Kernel Lessons

The `templates/ascend_custom_op/` directory contains illustrative skeletons. They
are examples, not a prescribed project layout.

Observed kernel-level lessons:

- `DataCopy` through UB is common for vectorized blocks; direct GM scalar access
  has been useful for simple correctness-first kernels.
- `DataCopy` alignment, especially 32-byte alignment for float32 blocks, often
  changes the feasible movement strategy.
- `GetValue(index)` and `SetValue(index, value)` are the common scalar access
  pattern for `LocalTensor`.
- Ascend C device code can reject casts between floating-point values and unsigned
  integer variables in index math.
- Libc-style math helpers may be unavailable in device code depending on CANN
  headers and target version.
- Nondeterministic adjacent scalar outputs have sometimes traced back to block
  partitioning or parallel writes; `blockDim=1` was useful as a correctness probe.
- CANN build output is usually more authoritative than generic language-server
  diagnostics for Ascend device headers.

Package observations:

- Multi-op packages often need inspection under `binary/<soc>/`, `binary/config/`,
  and `binary/dynamic/`.
- Stale per-op work directories and binary output can hide the first compiler
  error after a failed rebuild.
- Generated headers, op-info paths, and producer libraries are best recorded from
  actual CANN build/install outputs rather than recreated manually.

## Host Tiling And Op Definition Lessons

In `op_host/{{op_name}}.cpp`, the important migration facts usually include inputs,
outputs, attrs, tiling data, block dimensions, and SoC configuration.

Useful facts to record:

- Input and output declarations.
- Scalar attr shape, order, and type when attrs are part of the generated header.
- Tiling data layout and `opParaSize`.
- `SetBlockDim()` and kernel launch assumptions.
- `.AICore().AddConfig("{{SOC_VERSION}}")` target.
- Generated ACLNN header path and observed adapter signature.

Scalar `OpDef` attrs worked well only after generated-header inspection and runtime
smoke evidence showed the attr signature, order, type, and lifecycle were stable.
Tiling fields or explicit tensor inputs were simpler in projects where scalar attrs
were unstable through wrappers.

## Build, Install, And Path Lessons

Typical build/install records include the active CANN environment, custom-op source
directory, generated run package, install root, vendor package path, generated
headers, kernel artifacts, and `libcust_opapi.so` identity.

Observed path lessons:

- Some installers place vendor packages under `<install-root>/vendors`, while
  others use `opp/vendors`.
- `ASCEND_CUSTOM_OPP_PATH` can be a path list.
- A dynamic-library directory containing `libcust_opapi.so` can help discover the
  producer library, but package-level headers, op-info entries, and kernel artifacts
  still explain which op package is being observed.
- Sequential installs under one vendor can replace the shared producer library.

## Adapter And Caller Lessons

Adapter records were clearest when they captured the generated ACLNN signature,
adapter library or module path, exported callable, import/link proof, loaded adapter
identity, loaded producer identity, and same-run call observations.

PyTorch pybind11 observations:

- Pybind11 fits projects passing `torch.Tensor` objects and using torch-npu streams.
- Tensor-to-`aclTensor*` conversion depends on active ACLNN shape and stride rules.
- Non-contiguous slices and non-zero storage offsets can affect descriptor and kernel
  indexing assumptions.
- Stream synchronization and repeated-call behavior exposed several stale-handle and
  cache bugs in previous migrations.

Native or C++ caller observations:

- Link identity and packaged op binary resource registration helped explain runtime
  dispatch behavior.
- Direct-launch tiling structs stayed easier to debug when recorded beside host
  tiling definitions.

## Validation And Coverage Lessons

Validation is project-specific. Useful evidence layers have included direct
kernel/reference comparisons, adapter import/link checks, repeated-call tests,
integration tests through the public project entry point, gradient tests, project
test suites, runtime call observations, and comparable baseline/custom timings.

Coverage evidence is easier to interpret when bound to manifest or mapping hash,
run id, workload identity, producer path and sha256, adapter path and sha256,
coverage key, and call count.

Fallback behavior is clearest when documented separately from custom-op coverage
and performance observations.

## Benchmark And Reporting Lessons

Comparable benchmark notes usually include the same environment, same input set,
same dtype and shape scope, isolated baseline behavior, custom path evidence, and
measured speed ratio or slowdown. Slower custom paths are still useful information
when reported as measured slowdowns.

Project-local records often include operator inventory, migration manifest, build
attempt notes, runtime coverage JSON, baseline/custom benchmark JSON, speed report,
reproduction guide, and integration status. These documents are navigational and
evidentiary; this shared directory stays generic.

## Troubleshooting Lessons

- Missing generated-header attrs usually pointed back to op definition issues.
- Kernels that seemed not to update often involved stale Ascend C kernel cache or
  stale installed package artifacts.
- Correct-once then drifting output often involved stream synchronization, cached
  handles, or repeated-call state.
- OPP paths without headers, op-info entries, kernel artifacts, or real ELF producer
  libraries were incomplete artifact records.
- Direct launch failures often involved tiling struct size, binary resource
  registration, `blockDim`, stream, or kernel symbol-name mismatches.
- Framework placeholders raising `NotImplementedError` represented missing adapter
  routes rather than successful custom-op migration.

## Experience Summary

1. Separate semantic operators, generated OPP entries, adapter callables, and
   framework aliases.
2. Recover math before translating kernels.
3. Build references before trusting custom-op call counts.
4. Treat generated ACLNN headers as valuable adapter-signature evidence.
5. Recheck artifact hashes after package installs that can replace producer
   libraries.
6. Validate public routes that the current project cares about, including aliases
   and gradients when they are in scope.
7. Keep fallback paths visible but separate from custom-op success metrics.
8. Bind coverage observations to loaded producer and adapter identities.
9. Keep shared notes generic and store project-specific results in project-local
   reports or explicit examples.

## AscendC Basic API Cheatsheet (CANN 8.3.RC1 — verified against local headers)

Authoritative: `AscendC::` namespace from `kernel_operator.h`. These rules are
GENERAL; never tailor them to a single operator.

### Enums & names — use the exact names below
- Buffer position enum is `AscendC::TPosition` (NOT `QuePosition`, NOT `QuePos`):
  `TPosition::VECIN`, `TPosition::VECOUT`, `TPosition::VECCALC` (and A1/A2/... for
  specialized layouts). `TQue<TPosition::VECIN, 2>` / `TQue<TPosition::VECOUT, 2>`.
- Cast mode is `AscendC::RoundMode` with members `CAST_NONE`, `CAST_RINT`,
  `CAST_FLOOR`, `CAST_CEIL`, `CAST_ROUND`, `CAST_TRUNC`, `CAST_ODD`.
  There is no `RoundMode::Round_NEAREST`.
- Event ids that exist: `EVENT_ID0`, `EVENT_ID1`, `EVENT_ID2` (qualify with
  `AscendC::` like `AscendC::SafeSetFlag<AscendC::HardEvent::M_MTE2>(EVENT_ID1)`).
- `DEFAULT_BLK_STRIDE`, `DEFAULT_REPEAT_STRIDE`, `DEFAULT_DATA_COPY_NBURST` are in
  namespace `AscendC` — write `AscendC::DEFAULT_BLK_STRIDE`, never bare.
- `RoundMode`, `CMPMODE` etc. are in `AscendC` — qualify when out of a `using`.

### Queue / buffer usage
- `TPipe pipe;` then `pipe.InitBuffer(q, N, bytes)` (or `InitBuffer` with element
  count).
- Always `AllocTensor<T>()` -> `EnQue` (= fill) / `DeQue<T>()` -> `FreeTensor`.
- Inside templates, dependent calls need the `template` keyword:
  `buf.template AllocTensor<float>()`, `q.template DeQue<float>()`.
- Scalar access: `float v = src.GetValue(i);` / `dst.SetValue(i, v);`.
  NEVER assign a `LocalTensor` to a scalar (add `.GetValue(i)`).

### Vector intrinsics
- `Add(dst, src1, src2, count)`, `Muls(dst, src, scalar, count)`,
  `Adds/Mul/Sub/Maxs/Mins/Exp/Log/Rsqrt(dst, src[,2], count)`, `ReduceSum(...)`.
- `count` is the ELEMENT count of the tile, and the final argument of these ops.
- dtypes: keep src and dst identical (preferred); bf16 mixes are NOT supported in
  `Muls`/`Adds` (static_assert) — cast to fp32 first, compute, cast back.

### Cast / conversions
- `Cast(dstLocal, srcLocal, RoundMode::CAST_NONE, len)` — src and dst are both
  LocalTensor; pass the rounding mode from the RoundMode list above.
- In aicore functions float<->unsigned conversions are FORBIDDEN. Convert
  float->signed int (int32) via `Cast`, or rework the math; never write
  `uint32_t u = (uint32_t)f` in device code.
- `const float x;` members with no initializer are errors — always initialize.

### DataCopy
- `DataCopy(dstLocal, srcGm[offset], count)` blob copy.
- `DataCopyPad(dstLocal, srcGm, {blockCount, blockLen, srcStride, dstStride, rsv},
  padParms)` for padded/tail tiles; `DataCopyExtParams` field order is
  `{blockCount, blockLen, srcStride, dstStride, rsv}` (blockLen in BYTES).

### Includes / entry
- Device file: `#include "kernel_operator.h"` + `using namespace AscendC;`.
- Entry MUST be `extern "C" __global__ __aicore__ void <op>(...)` (never
  `__global__ __vector__`).
- Math: use `sqrt/rsqrt/exp/pow` (there is no `sqrtf`/`expf` on aicore).

### Matmul / cube (only if the op needs it)
- Use the COMPLETE header tree (see matmul note above): include
  `highlevel_api/...`; do not include from `tools/tikcpp/tikcfw` (kfc missing →
  `kfc_register_obj.h file not found`). Follow KernelBench's own sample/Host glue.
