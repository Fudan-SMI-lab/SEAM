# Environment Experience Notes

This file captures optional environment observations from Ascend custom-op
migrations. It is not a preflight policy.

## CANN And OPP Path Observations

- CANN toolkit version, active environment script, target SoC, and vendor package
  name helped explain build and runtime behavior.
- `ASCEND_CUSTOM_OPP_PATH` appeared as a path list in some setups. Entries could be
  vendor package roots or direct dynamic-library directories.
- Empty environment variables were only one clue; generated vendor `bin/set_env.bash`,
  project-local install roots, installer output, manifest evidence, CANN vendor
  config, and producer-library linkage often supplied more context.
- `{{CANN_INSTALL_PATH}}/opp/vendors/{{VENDOR_NAME}}` was one possible layout rather
  than a universal package root.
- Unrelated vendor roots, stale installed libraries, stale site-package adapters,
  filename-only matches, and mtime-only matches caused misleading evidence in prior
  projects.

## Artifact Identity Observations

- `LD_LIBRARY_PATH` entries explained which producer library directories were
  visible to the host process.
- Generated ACLNN headers, `libcust_opapi.so`, op-info entries, kernel artifacts,
  and package metadata tied environment readiness to actual generated operators.
- Dynamic-library directories were useful hints but did not describe package-level
  headers, op-info, or kernel artifacts by themselves.
- Runtime loaded-library proof with adapter path/sha256 and producer path/sha256
  helped resolve stale-library confusion.
- Shared producer libraries could be overwritten by later installs under the same
  vendor package.

## Project-Local Environment Observations

- Isolated virtual environments helped reproduce host-project import, lint, and
  smoke-test behavior.
- Accelerator-specific packages such as torch-npu were separate from CPU-only host
  environment readiness.
- Repository-supported install paths and smoke commands made benchmark failures
  easier to interpret.

## Minimal Runtime Proofs

- A tiny Ascend C vector kernel was useful as a CANN/toolchain proof before a large
  framework migration.
- A local host adapter against the installed accelerator framework helped separate
  toolchain issues from project-integration issues.
- A minimal OPP packaging proof explained whether CANN could generate headers,
  producer libraries, and op implementation artifacts in a non-global workspace.
- Target-op packaging evidence stayed distinct from full framework integration,
  runtime coverage, and measured performance observations.
