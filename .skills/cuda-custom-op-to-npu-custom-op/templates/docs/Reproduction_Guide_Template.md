# {{PROJECT_NAME}} NPU Custom Op Reproduction Guide

This guide is a project-local reproduction template. It records the typical
sequence used to reproduce a migration and the observations that came from it.

## Companion Documents

| Document | Purpose |
|----------|---------|
| `Reproduction_Guide.md` | Step-by-step reproduction notes |
| `Migration_Record.md` | Implementation record and observations |
| `Integration_Status_Report.md` | Architecture and verification summary |

## Prerequisites

- Hardware: {{NPU_HARDWARE}}
- OS: Linux ({{ARCH}})
- CANN: {{CANN_VERSION}} at `{{CANN_INSTALL_PATH}}`
- Framework: {{FRAMEWORK_NAME}} {{FRAMEWORK_VERSION}}
- Adapter: {{ADAPTER_TYPE}} {{ADAPTER_VERSION}}
- Disk space: about {{DISK_SPACE}} for build artifacts
- Test data: {{TEST_DATA_DESCRIPTION}} for end-to-end verification

## Project Structure

```text
{{project_root}}/
|-- {{kernel_source_dir}}/
|   |-- op_kernel/{{kernel_file}}
|   |-- op_proto/
|   |-- op_host/
|   `-- op_api/
|
|-- {{adapter_dir}}/
|   |-- {{adapter_entry_file}}
|   |-- {{adapter_build_file}}
|   `-- {{adapter_artifact}}
|
|-- {{project_integration_dir}}/
|   |-- {{custom_op_route_file}}
|   `-- {{dispatch_file}}
|
`-- {{test_script}}
```

## Typical Reproduction Sequence

### Step 1: Environment setup

```bash
source {{CANN_INSTALL_PATH}}/set_env.sh
export LD_PRELOAD="{{LD_PRELOAD_PATH}}"
export ASCEND_CUSTOM_OPP_PATH="{{CUSTOM_OPP_PATH}}"
export LD_LIBRARY_PATH="{{VENDOR_LIB_PATH}}:$LD_LIBRARY_PATH"
```

Observed notes often included whether `ASCEND_CUSTOM_OPP_PATH` was empty, whether
`LD_PRELOAD` was needed, and whether the host process had already started.

### Step 2: Kernel build

```bash
cd {{kernel_source_dir}}/
mkdir -p build_out && cd build_out

cmake .. \
  -DCMAKE_CXX_COMPILER={{CXX_COMPILER}} \
  -DTARGET_CHIP={{SOC_VERSION}} \
  -DCMAKE_BUILD_TYPE=Release

make -j$(nproc)
make install
```

Typical notes after this step included where the kernel binary landed and which
manifest artifacts were visible.

### Step 3: Package install and artifact checks

```bash
export ASCEND_CUSTOM_OPP_PATH="{{CUSTOM_OPP_PATH}}"
python templates/validation/validate_manifest_artifacts.py {{MIGRATION_MANIFEST_JSON}}
```

This step usually recorded which generated headers, producer libraries, op-info
entries, and kernel binaries were present in the active package.

### Step 4: Adapter or caller build

```bash
cd {{adapter_dir}}/
{{ADAPTER_CLEAN_COMMAND}}
{{ADAPTER_BUILD_COMMAND}}
```

Typical notes after this step included adapter artifact location, loaded identity,
and whether the callable matched the manifest mapping.

### Step 5: Host framework path setup

```bash
{{HOST_FRAMEWORK_PATH_EXPORTS}}
```

### Step 6: Environment verification

```bash
{{FRAMEWORK_ENV_VERIFY_COMMAND}}
```

Typical output notes described the framework version, adapter load behavior, and
whether the custom-op route was visible.

## Run Experiments

### Experiment 1: Kernel vs reference

```bash
{{KERNEL_REFERENCE_TEST_COMMAND}}
```

Common observations: max diff, tolerance, and whether the kernel behaved the same
as the CPU reference.

### Experiment 2: Forward pass end-to-end

```bash
{{FORWARD_TEST_COMMAND}}
```

### Experiment 3: Backward or gradient check

```bash
{{BACKWARD_TEST_COMMAND}}
```

Common notes here described loss and grad behavior, or forward-only behavior when
gradient behavior was outside scope.

### Experiment 4: Performance benchmark

```bash
{{PERFORMANCE_BENCHMARK_COMMAND}}
```

Common notes here described coverage, measured timings, and whether the
custom path was faster or slower than baseline.

### Experiment 4b: Whole-package report

```bash
python templates/validation/validate_manifest_artifacts.py {{MIGRATION_MANIFEST_JSON}}
```

```bash
{{FRAMEWORK_BENCHMARK_COMMAND}}
```

The report usually captured installed OPP count, framework op count,
called custom-op count, unavailable or unsupported cases, accuracy diff, runtime,
and measured speed ratio or slowdown.

```bash
python templates/validation/compare_report_json_parity.py \
  {{BENCHMARK_RESULT_JSON}} {{INTEGRATION_STATUS_REPORT}} \
  --json-path status \
  --json-path benchmark.seconds_per_iter
```

### Experiment 5: Full pipeline test

```bash
{{E2E_TEST_COMMAND}}
```

## Common Questions Seen In Reproduction

### Q1: `lib{{SOME_LIB}}.so: undefined symbol` during adapter or framework load

```bash
export LD_PRELOAD="{{LD_PRELOAD_PATH}}"
```

### Q2: Framework or adapter import changes after environment setup

Environment variables were typically set before the host process started, because
some frameworks load CANN or vendor libraries during import or startup.

### Q3: `{{ADAPTER_ARTIFACT}}` import, link, or load fails

```bash
ls {{adapter_dir}}/{{adapter_artifact}}
cd {{adapter_dir}} && {{ADAPTER_BUILD_COMMAND}}
{{HOST_FRAMEWORK_PATH_EXPORTS}}
```

### Q4: Kernel binary not found at runtime

```bash
ls {{KERNEL_INSTALL_PATH}}/
cd {{kernel_source_dir}}/build_out && make install
```

### Q5: CPU fallback behaves unexpectedly

This note usually points to the need for an NPU-native backend or an adapter path
that matches the intended custom-op route.

### Q6: Input shape issues

Common patterns included batch dimension placement, missing `.contiguous()`, and
shape mismatches between host tiling and tensor dimensions.

## Evidence To Record

- Environment variables used for the run.
- Adapter artifact identity and load behavior.
- Kernel binary location.
- Manifest validation observations.
- Reference comparison observations.
- Forward, backward, benchmark, and end-to-end notes.
- Any unsupported, missing, or fallback-routed cases that stayed open.

The set of notes above is usually enough to reconstruct a reproducible migration
run without turning the template into a gate.
