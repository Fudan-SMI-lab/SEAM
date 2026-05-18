# Example: PointNet2 Ops Migration Notes

This is a non-normative project-specific note set. It is retained as a case study
for projects with the same public operator contract. The exact operator inventory,
vendor name, shapes, and benchmark expectations are case-study details.


These notes came from migrating `pointnet2_ops` CUDA extension projects to Ascend
custom operators.

## Operator Contract

- The case reconciled 9 framework entries: `furthest_point_sampling`, `gather_points`, `gather_points_grad`, `three_nn`, `three_interpolate`, `three_interpolate_grad`, `ball_query`, `group_points`, `group_points_grad`.
- Python wrapper semantics stayed separate from kernel semantics: `three_nn` custom op returned squared distances, while the Python wrapper returned `sqrt(dist2)`.
- Index dtype was `torch.int32`/`DT_INT32` for FPS, gather/group indices, three-NN indices, and ball-query indices.
- Input layout contracts were points as `(B, N, 3)`, features as `(B, C, N)`, three-NN indices/weights as `(B, n, 3)`, grouped indices as `(B, npoint, nsample)`.

## Gradient Policy

- Custom backward kernels were part of the training-acceleration story for `gather_points_grad`, `three_interpolate_grad`, and `group_points_grad`.
- Duplicate-index accumulation was an important gradient-kernel case; scatter duplicates accumulated rather than overwrote.
- Framework fallback gradients were recorded separately from custom-op acceleration observations.

## Ascend Custom OPP Package

- A project vendor such as `pointnet2` kept generated package paths easy to recognize.
- Generated ACLNN headers, `libcust_opapi.so`, kernel JSONs, and kernel binaries were tracked for the PointNet2 ops.
- Exact PointNet2 artifact identities were more meaningful than matching any 9 headers or any 9 kernel directories.
- Generated ACLNN wrappers behaved better when all 9 PointNet2 binary resources were registered before mixed-op execution while executor state stayed per wrapper.
- Empty `ASCEND_CUSTOM_OPP_PATH` was treated as path-discovery context until PointNet2 artifacts existed.
- Python dispatch-layer call-count observations explained which entries ran in a benchmark.

## Correctness Cases

- FPS: deterministic start index `0`, `npoint=0`, `npoint=N`, and tie behavior.
- Gather/group forward: duplicate and repeated indices, all batches/channels.
- Three-NN: `unknown == known`, deterministic top-3 ordering, and at least 3 known points.
- Interpolate: weights sum to 1, duplicate indices, and gradient accumulation.
- Ball query: `d2 < radius^2`, fewer than `nsample` neighbors filled with first found neighbor, no-neighbor case stayed zero.

## Benchmark Rules

- Baseline and custom-op runs used identical shapes, seeds, dtype, device class, warmup, and repeat counts.
- NPU synchronization before and after timing helped stabilize measurements.
- Missing `_npu_ext` or OPP artifacts were recorded as implementation context rather than speedup evidence.
- A single-process sequence covering all 9 ops caught descriptor leakage that one-op smokes missed.
- The Markdown result report was refreshed from the current benchmark JSON after reruns.
