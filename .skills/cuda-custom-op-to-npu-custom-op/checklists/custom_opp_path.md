# Custom OPP Path Experience Notes

This file is an optional reference for path discovery issues seen in Ascend custom
OPP migrations. It is not a prescribed preflight checklist.

## Candidate Path Observations

- Base CANN environment scripts often provided useful defaults.
- Explicit inputs such as `opp_path` or `--custom-opp-path` reduced ambiguity in
  project-local validation scripts.
- `ASCEND_CUSTOM_OPP_PATH` behaved as a path list in some environments, with entries
  representing vendor package roots or direct dynamic-library directories.
- Generated vendor `bin/set_env.bash` files often contained more reliable package
  paths than hand-written assumptions.
- Common installer roots included `{{CUSTOM_OPP_INSTALL_ROOT}}/vendors/{{VENDOR_NAME}}`
  and `{{CUSTOM_OPP_INSTALL_ROOT}}/opp/vendors/{{VENDOR_NAME}}`.
- Layouts under `{{CANN_INSTALL_PATH}}/opp/vendors/{{VENDOR_NAME}}` and
  `{{CANN_INSTALL_PATH}}/vendors/{{VENDOR_NAME}}` were useful hypotheses, not proof
  by themselves.

## Package Identity Observations

- Generated `aclnn_*.h` headers under `{{CUSTOM_OPP_PATH}}/op_api/include` explained
  adapter signatures.
- `op_api/lib/libcust_opapi.so` under a vendor package root, or `libcust_opapi.so`
  inside a direct dynamic-library entry, identified the producer candidate.
- ELF identity and sha256 helped distinguish real producers from placeholders.
- Op-info entries, kernel directories, config JSON, and kernel binaries linked path
  discovery to actual generated operators.
- Host-process loaded-library proof was useful when multiple vendors or stale site
  packages existed.

## Preflight Fields That Were Useful In Reports

- `custom_opp_path_source`
- `active_custom_opp_path`
- `ascend_custom_opp_path_env`
- Low-level OPP coverage by generated entry
- Framework alias coverage by public source entry
- Per-entry artifact paths and sha256 values
- Runtime callable readiness
- Loaded adapter and producer identity
- Open implementation notes for missing artifacts, unsupported aliases, stale
  producers, disabled ops, fallback routes, baseline contamination, or workload
  mismatch

## Reporting Notes

- A dynamic-library directory helped find the producer library, while headers,
  op-info entries, and kernel artifacts explained the package root.
- Non-empty paths from another project were clearer when reported as mismatched
  evidence rather than reused as current-project proof.
- Missing adapter modules often led to adapter ABI discovery and adapter build work
  for the current framework.
- Speedup or slowdown observations were easier to interpret when linked to measured
  benchmark evidence and custom-op routing observations.
