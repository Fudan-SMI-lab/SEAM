# Integration Experience Notes

This file captures optional reference notes for wiring custom NPU kernels into a
host framework execution path. It is not an integration policy.

## Routing Observations

- New custom-op branches were easier to review when added alongside existing paths
  rather than replacing the default path during investigation.
- Placeholders such as `{{BASELINE_MODE}}` and `{{CUSTOM_OP_MODE}}` kept baseline
  and candidate routes clear in reports.
- Fallback conditions often included non-NPU device, unsupported dtype/shape/layout,
  unsupported algorithm option, adapter load failure, and disabled custom op.
- Fallback behavior was clearer when recorded separately from custom-op coverage
  and benchmark observations.

## Operator Semantics Observations

- Stencil and hybrid-region ops benefited from structural region metadata rather
  than computed physical values.
- Halo or ghost cells mattered when extracting blocks for stencil kernels.
- Batch, channel, and broadcast dimensions followed the original op contract.
- Gradient strategy was a separate topic from forward custom-op routing when the
  framework supported differentiable execution.
- Profile tensors for absorbing-boundary or PML-style ops often needed rank
  normalization before indexing.
- Tensor blocks passed to adapters were easier to reason about when compact or when
  descriptors explicitly covered storage offsets and strides.

## Benchmark And Coverage Observations

- Baseline/custom comparisons were clearer when baseline runs were isolated from
  custom adapter imports, custom OPP paths, preloaded producers, and custom-op
  coverage counters.
- Workload hashes or normalized command hashes helped explain comparable inputs.
- Framework-level call counters identified which kernels ran in the current process.
- Public entries or framework aliases were easier to track when mapped to semantic
  op, generated OPP entry, adapter callable, coverage key, and parity scope.
- Direct per-op smokes gave local evidence; pairing them with coverage, project
  tests, benchmark parity, validation, and migration-scope reporting gave a fuller
  picture.

## Adapter Work Notes

- Missing host adapters usually led to discovery of framework registries, build
  files, generated ACLNN headers, and manifest mappings.
- Useful implementation notes included attempted build/import/link commands,
  output or error summaries, missing ABI or dependency, failing artifact,
  remediation attempted, and next callable or library to inspect.
- Adapter registration and runtime call proof were separate observations: declaring
  an alias or loading a plugin did not by itself show that the current run invoked
  the intended custom op.
