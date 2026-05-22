---
name: close-custom-op-migrations-by-fine-grained-unit
description: Close custom-op migrations by fine-grained operator unit
tags: ["custom-op-inventory", "fine-grained-units", "manifest-closure", "runtime-coverage", "pointnet2"]
category: code_adaptation
subtype: fine_grained_custom_op_unit_closure
confidence: 0.9
occurrence_count: 1
---

# Close custom-op migrations by fine-grained operator unit

## When to Use
- A custom-op migration can appear closed when tracked by broad families such as sampling, interpolate, group_points, and ball_query, while distinct exported/native units and autograd backward paths remain unproven.

## Root Cause
Family-level closure collapses multiple public/native operator boundaries. In the PointNet2 evidence, four families contain nine required units, each with its own unit_identity, native symbols, kernel functions, launch sites, public route mapping, source evidence, runtime coverage, parity, performance, and no-fallback proof.

## How to Use
1. Search source files, bindings, Python wrappers, autograd forward/backward paths, aliases, launch sites, setup/build files, and tests before declaring custom-op discovery complete.
2. Enumerate public/native boundary units, not families. For PointNet2 this means nine units: gather_points, gather_points_grad, furthest_point_sampling, three_nn, three_interpolate, three_interpolate_grad, group_points, group_points_grad, and ball_query.
3. Assign each unit a stable unit_identity that includes its family and full signature, then reuse that exact identity across operator_inventory.json, migration_manifest.json, runtime_coverage.json, performance.json, and custom_op_final_gate.json.
4. For every inventory row, record family, variant_or_signature, native_operator_symbols, kernel_functions, kernel_launch_sites, source_evidence, and public_entry_mapping with python_public_api, autograd_function, and high_level_route where applicable.
5. Build migration_manifest.json required_units from the fine-grained unit identities, not from broad family names.
6. Preserve the Python public APIs and extension symbol names while replacing or adapting the backend implementation, so calls through pointnet2_ops.pointnet2_utils and pointnet2_ops._ext still hit the intended unit.
7. Execute validation through public/framework routes, including backward/autograd routes, and record same-run runtime_coverage for every unit with executed=true, runtime_device=npu, and cpu_or_cuda_routed=false.
8. Write performance.json with a performance entry for every unit_identity plus an overall public API route proof showing all units were replaced.
9. Require each final-gate row to carry adapter evidence, OPP/custom-op artifact evidence, parity evidence, integration evidence, runtime coverage, performance evidence, and no_fallback_no_zero_call_no_builtin_contamination evidence.
10. Fail closed unless inventory_count, manifest_entries, and closed_pass_entries match, remaining_entries is 0, full_migration_status is FULL_PASS, and all required units are present, executed, and have performance evidence.

## Code Examples
[
  {
    "file": "validate_custom_ops_full.py",
    "before": "Family-level closure by broad groups such as sampling, interpolate, group_points, and ball_query was insufficient.",
    "after": "REQUIRED_UNITS = [\n  \"sampling:gather_points(points[B,C,N], idx[B,npoint])->out[B,C,npoint]\",\n  \"sampling:gather_points_grad(grad_out[B,C,npoint], idx[B,npoint], N)->grad_features[B,C,N]\",\n  \"sampling:furthest_point_sampling(xyz[B,N,3], npoint)->idx[B,npoint]\",\n  \"interpolate:three_nn(unknown[B,n,3], known[B,m,3])->dist[B,n,3],idx[B,n,3]\",\n  \"interpolate:three_interpolate(features[B,C,m], idx[B,n,3], weight[B,n,3])->out[B,C,n]\",\n  \"interpolate:three_interpolate_grad(grad_out[B,C,n], idx[B,n,3], weight[B,n,3], m)->grad_features[B,C,m]\",\n  \"group_points:group_points(features[B,C,N], idx[B,npoint,nsample])->out[B,C,npoint,nsample]\",\n  \"group_points:group_points_grad(grad_out[B,C,npoint,nsample], idx[B,npoint,nsample], N)->grad_features[B,C,N]\",\n  \"ball_query:ball_query(new_xyz[B,npoint,3], xyz[B,N,3], radius, nsample)->idx[B,npoint,nsample]\"\n]"
  },
  {
    "file": "migration_reports/operator_inventory.json",
    "before": "A family-only sampling row would merge gather_points, gather_points_grad, and furthest_point_sampling.",
    "after": "{\n  \"family\": \"sampling\",\n  \"inventory_granularity\": \"fine_grained\",\n  \"native_operator_symbols\": [\"gather_points_grad\"],\n  \"kernel_functions\": [\"gather_points_grad_kernel\", \"gather_points_grad_kernel_wrapper\"],\n  \"public_entry_mapping\": {\n    \"python_public_api\": \"pointnet2_ops.pointnet2_utils.gather_operation backward\",\n    \"autograd_function\": \"pointnet2_ops.pointnet2_utils.GatherOperation.backward\",\n    \"high_level_route\": \"pointnet2_ops.pointnet2_modules.PointnetSAModule backward\"\n  },\n  \"unit_identity\": \"sampling:gather_points_grad(grad_out[B,C,npoint], idx[B,npoint], N)->grad_features[B,C,N]\"\n}"
  },
  {
    "file": "migration_reports/migration_manifest.json",
    "before": "A manifest built from family names would not prove closure for the nine PointNet2 operator units.",
    "after": "{\n  \"required_units\": [\n    \"sampling:gather_points(points[B,C,N], idx[B,npoint])->out[B,C,npoint]\",\n    \"sampling:gather_points_grad(grad_out[B,C,npoint], idx[B,npoint], N)->grad_features[B,C,N]\",\n    \"sampling:furthest_point_sampling(xyz[B,N,3], npoint)->idx[B,npoint]\",\n    \"interpolate:three_nn(unknown[B,n,3], known[B,m,3])->dist[B,n,3],idx[B,n,3]\",\n    \"interpolate:three_interpolate(features[B,C,m], idx[B,n,3], weight[B,n,3])->out[B,C,n]\",\n    \"interpolate:three_interpolate_grad(grad_out[B,C,n], idx[B,n,3], weight[B,n,3], m)->grad_features[B,C,m]\",\n    \"group_points:group_points(features[B,C,N], idx[B,npoint,nsample])->out[B,C,npoint,nsample]\",\n    \"group_points:group_points_grad(grad_out[B,C,npoint,nsample], idx[B,npoint,nsample], N)->grad_features[B,C,N]\",\n    \"ball_query:ball_query(new_xyz[B,npoint,3], xyz[B,N,3], radius, nsample)->idx[B,npoint,nsample]\"\n  ],\n  \"validation_contract\": {\n    \"fail_closed\": true,\n    \"inventory_granularity\": \"fine_grained\",\n    \"cpu_fallback_allowed\": false,\n    \"public_api_routes_required\": true\n  }\n}"
  }
]

## Do Not
- Do NOT close a custom-op migration at the broad family level when multiple exported/native units exist behind that family.
- Do NOT let a forward path imply its backward/autograd unit is migrated; validate backward units independently.
- Do NOT accept manifest rows that cannot be matched back to source-discovered inventory rows by exact unit_identity.
- Do NOT accept CPU fallback, CUDA routing, zero custom-call coverage, builtin contamination, direct-only calls, benchmark-only evidence, or report-only evidence.
- Do NOT mark FULL_PASS unless inventory_count, manifest_entries, closed_pass_entries, and remaining_entries prove complete per-unit closure.

## References
- output_projects/pointnet2_ops_20260518_082637/validate_custom_ops_full.py
- output_projects/pointnet2_ops_20260518_082637/migration_reports/operator_inventory.json
- output_projects/pointnet2_ops_20260518_082637/migration_reports/migration_manifest.json
- output_projects/pointnet2_ops_20260518_082637/migration_reports/runtime_coverage.json
- output_projects/pointnet2_ops_20260518_082637/migration_reports/performance.json
- output_projects/pointnet2_ops_20260518_082637/migration_reports/custom_op_final_gate.json
- output_projects/pointnet2_ops_20260518_082637/migration_reports/evidence_validation.json
- output_projects/pointnet2_ops_20260518_082637/pointnet2_ops/pointnet2_utils.py
- output_projects/pointnet2_ops_20260518_082637/pointnet2_ops/pointnet2_modules.py

## Evidence
- Source runs: e2e-v2-4d249a6c856d
