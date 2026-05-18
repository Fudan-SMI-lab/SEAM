# Verification Experience Notes

This file is an optional reference for evidence that has been useful in CUDA to
Ascend custom-op migrations. It is not a completion checklist or validation gate.

## Per-Operator Evidence That Helped

- Mapping from public entry or framework alias to semantic operator.
- CPU or framework reference for the dtype and shape range being discussed.
- Ascend C output compared with the reference under a documented tolerance.
- Generated ACLNN headers, op-info/config entries, kernel artifacts, and producer
  library identity.
- Adapter or caller import/link observations and callable identity.
- Repeated-call behavior on the same input.
- Forward behavior compared with the baseline or reference path.
- Backward or gradient behavior notes when the source project exposed gradients.
- Runtime coverage observations keyed to the operator mapping.

## Package And Adapter Evidence That Helped

- Installed OPP op count recorded separately from adapter functions and framework
  aliases.
- Actual loaded adapter path and sha256 when an adapter existed.
- Actual loaded custom-op producer path and sha256.
- Notes about stale installed libraries, stale site-package adapters, unrelated
  vendor roots, and filename-only matches.
- Revalidation notes after installs that replaced a shared custom-op producer
  library.
- Package-level artifact notes for op-proto, op-tiling, vendor environment,
  op-info/config, generated ACLNN, kernel, producer, adapter, and loaded-library
  evidence.

## Benchmark Evidence That Helped

- Pre-benchmark environment and artifact notes separated from timing numbers.
- Baseline run isolation notes, especially around custom adapters and active custom
  OPP paths.
- Baseline/custom workload identity, command hash, or input equivalence notes.
- Same-run call counts tied to manifest sha, producer identity, adapter identity,
  run id, and workload identity.
- Measured speedup or slowdown reported only as an observation from comparable
  baseline/custom timings.

## Report Evidence That Helped

- Operator inventory with semantic source ops, generated OPP entries, adapter
  callables, and framework aliases separated.
- Missing, unsupported, disabled, fallback-routed, stale, zero-call, and report
  mismatch cases listed separately from successful custom-op observations.
- Markdown reports kept consistent with machine-readable benchmark JSON when both
  existed.
- Open implementation notes that named the attempted stage, command or probe,
  output or error summary, remediation attempted, and next useful artifact or fact.
- Standalone OPP build evidence separated from full framework replacement evidence.
