# Kernel Experience Notes

This file is an optional reference for Ascend C kernel implementation and build
observations. It is not a kernel completion checklist.

## Correctness Observations

- Recording the math formula or algorithm before kernel coding made later debugging
  easier.
- CPU reference implementations helped separate math bugs from device or adapter
  bugs.
- Direct CPU-reference smokes gave useful local evidence for kernel behavior.
- Kernel JSON, generated header, and op-info identities explained which generated
  OPP entries were being exercised.

## Build Observations

- Ascend C compile output was the most useful source of truth for device-code
  validity.
- End-to-end `build.sh` or CMake runs exposed package-level issues that single-file
  compile probes missed.
- Compiled `.o` and `.json` artifacts helped confirm which op and SoC were built.
- Build config target chip and `{{SOC_VERSION}}` mismatches produced confusing
  runtime failures in previous migrations.
- Kernel binary caches sometimes caused source edits to appear ineffective.

## Host/Kernal Interface Observations

- `opParaSize` and `sizeof(TilingData)` mismatches caused launch-time confusion.
- `blockDim` consistency between host tiling and direct-launch adapters simplified
  debugging.
- Generated ACLNN header signatures revealed scalar attr order and type.
- Scalar attr strategies were easier to trust after generated-header and runtime
  smoke evidence.
- Data movement choices such as direct GM scalar access, UB staging, `DataCopy`,
  and vectorization depended on semantics, alignment, tails, and reference tests.
- Multi-op generated ACLNN packages exposed resource-registration issues during
  mixed-op framework execution.
- Real OPP op counts were clearer when documented separately from adapter aliases
  or framework registry names.
