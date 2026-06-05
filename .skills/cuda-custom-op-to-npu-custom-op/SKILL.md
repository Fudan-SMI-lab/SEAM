---
target_phases: ["fix_operator", "phase_5_validate"]
target_roles: ["operator_fixer"]
---
# CUDA Custom Op To Ascend Custom Op Migration Notes

This directory is an experience reference for converting CUDA or C++ custom
operators into Ascend C / CANN custom operators. It is documentation only. It is
not a task contract, agent instruction set, completion policy, validation gate,
or automatic execution workflow.

The notes below preserve practical migration lessons from previous projects.
Project scope, priorities, acceptance criteria, reports, and stop conditions live
in the user's current request or in the target project's own plan.

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
