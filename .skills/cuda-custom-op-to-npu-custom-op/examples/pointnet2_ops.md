# PointNet2 Ops Migration Notes

This is a non-normative project-specific case study. It records PointNet2-specific
lessons for converting the CUDA extension `pointnet2_ops` to Ascend custom
operators. The exact operator inventory, vendor name, entry count, artifact
expectations, and benchmark coverage expectations are case-study details.

## Source Inventory

The CUDA extension exposes 9 pybind entries:

| Python/adapter entry | CUDA source family | Role |
| --- | --- | --- |
| `furthest_point_sampling` | `sampling_gpu.cu` | forward, non-differentiable |
| `gather_points` | `sampling_gpu.cu` | forward |
| `gather_points_grad` | `sampling_gpu.cu` | backward scatter-add |
| `three_nn` | `interpolate_gpu.cu` | forward, non-differentiable |
| `three_interpolate` | `interpolate_gpu.cu` | forward |
| `three_interpolate_grad` | `interpolate_gpu.cu` | backward scatter-add |
| `ball_query` | `ball_query_gpu.cu` | forward, non-differentiable |
| `group_points` | `group_points_gpu.cu` | forward |
| `group_points_grad` | `group_points_gpu.cu` | backward scatter-add |

## Example Ascend Names

This case used one vendor package, for example `pointnet2`, with one real OPP op per entry:

- `Pointnet2FurthestPointSampling`
- `Pointnet2GatherPoints`
- `Pointnet2GatherPointsGrad`
- `Pointnet2ThreeNn`
- `Pointnet2ThreeInterpolate`
- `Pointnet2ThreeInterpolateGrad`
- `Pointnet2BallQuery`
- `Pointnet2GroupPoints`
- `Pointnet2GroupPointsGrad`

The Python adapter may expose the original lowercase names. The case study kept
real OPP op count, adapter callable count, and Python aliases separate.

## Empty OPP Path Lesson

`ASCEND_CUSTOM_OPP_PATH` being empty was treated as environment/setup context, not proof
that custom ops were absent or present. Sourcing the generated vendor
`bin/set_env.bash` after building the PointNet2 OPP package resolved the path in this case.
Before the package existed, a project-local placeholder path was only intermediate evidence.

## Benchmark Evidence Lesson

PointNet2 project-level acceleration was interpreted only after all 9 entries had
nonzero call observations. A direct kernel smoke test or a reference PyTorch
implementation described partial evidence rather than full replacement.

## Multi-op ACLNN Lifecycle Lesson

Generated ACLNN wrappers may keep static executor state per wrapper. A shared
executor space across different PointNet2 op signatures caused problems in this
case. The observed failure mode was that one op succeeded, then a later
different op reused stale descriptors and failed. The generalized fix is to
pre-register all PointNet2 binary resources before the first executor space is
created, while preserving per-wrapper executor state.

## Exact Artifact Lesson

This PointNet2 case tracked the exact 9 generated ACLNN headers and exact 9 kernel
directories that corresponded to the Python entries. Counting arbitrary headers or
directories under the vendor root gave weaker evidence. The benchmark report was
refreshed from the current JSON result after reruns.
