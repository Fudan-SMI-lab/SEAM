---
name: cuda-extension-to-ascendc-opp-aclnn-bridge
description: Migrate CUDA custom extensions to real Ascend C/CANN OPP with ACLNN PyTorch bridge
tags: ["torch-npu", "ascendc", "cann-opp", "aclnn", "custom-op"]
category: operator_incompat
subtype: cuda_extension_to_ascendc_opp_aclnn_bridge
confidence: 0.95
occurrence_count: 1
---

# Migrate CUDA custom extensions to real Ascend C/CANN OPP with ACLNN PyTorch bridge

## When to Use
- Phase 5 rejects a CUDA custom-op migration when the project only swaps build plumbing to NpuExtension, CppExtension, or ATen-style adapter code. Validation reports missing native OPP producer evidence such as op_host, op_kernel, generated ACLNN headers, kernel_meta, libcust_opapi.so, build/install provenance, or same-run per-operator route evidence.

## Root Cause
A PyTorch NpuExtension adapter is only bridge evidence. It does not prove that the CUDA kernel was migrated to a native Ascend C/CANN custom operator. The migration is production-ready only when a project-local Ascend OPP package provides op_host metadata, AscendC kernels, generated ACLNN APIs, installed runtime artifacts, and a PyTorch bridge that links cust_opapi/opapi/ascendcl and launches ACLNN on the current NPU stream.

## How to Use
1. Inventory every CUDA custom-op unit discovered from the original extension sources and keep a one-to-one manifest entry for each unit that must be closed by native Ascend C/CANN evidence.
2. Create a project-local Ascend OPP package, for example under `pointnet2_ops/ascend_opp`, with separate `op_host` and `op_kernel` directories for the migrated operators.
3. For each custom op, implement an `op_host` definition that declares inputs, outputs, supported dtypes and formats, attributes, shape inference, dtype inference, AICore tiling, and an explicit `AddConfig` target such as `ascend910b`.
4. For each custom op, implement an AscendC `op_kernel` source with an `extern "C" __global__ __aicore__` kernel entry matching the generated custom-op API route.
5. Build and install the OPP package so the validation artifacts include generated ACLNN headers, generated op_api library files, kernel metadata, `libcust_opapi.so`, and CMake/build/install provenance. Do not count the PyTorch adapter itself as the OPP producer.
6. Replace the runtime extension build with a PyTorch `NpuExtension` adapter that includes the generated ACLNN headers, CANN headers, and project helper headers.
7. Link the adapter against `cust_opapi`, `opapi`, and `ascendcl`, and add runtime library paths for the project OPP `op_api/lib`, CANN `lib64`, and torch_npu `lib` directories.
8. In the adapter, validate that inputs are defined NPU tensors with the expected dtype and rank, make them contiguous, allocate outputs on the same NPU device, and use `at::DeviceGuard` before launching the operator.
9. Convert PyTorch tensors to `aclTensor` objects with shape, stride, storage offset, `ACL_FORMAT_ND`, ACL dtype, and the tensor data pointer; release each `aclTensor` after use.
10. Before calling generated ACLNN APIs, register the project OPP resources once, request workspace size and executor from the generated `GetWorkspaceSize` function, allocate byte workspace when needed, launch the generated ACLNN function on `c10_npu::getCurrentNPUStream(...).stream()`, then destroy the executor and check every ACLNN status.
11. Run Phase 5 validation and require same-run, per-operator, non-empty route evidence correlated to each custom-op unit. The final gate must reject fallback, stub, report-only, direct-only, ATen-only, or adapter-only success.
12. Confirm final gate counters close all discovered units: inventory count equals manifest entries, every entry is closed/pass, remaining entries are zero, and full migration status is `FULL_PASS`.

## Code Examples
[
  {
    "file": "setup.py",
    "before": "from torch.utils.cpp_extension import CUDAExtension\n\next_modules=[\n    CUDAExtension(\n        name=\"pointnet2_ops._ext\",\n        sources=cuda_sources,\n    )\n]",
    "after": "from torch.utils.cpp_extension import BuildExtension\nimport torch_npu\nfrom torch_npu.utils.cpp_extension import NpuExtension\n\n_opp_root = osp.join(this_dir, \"pointnet2_ops\", \"ascend_opp\")\n_opp_build_out = osp.join(_opp_root, \"build_out\")\n_cann_root = os.environ.get(\"ASCEND_CANN_PACKAGE_PATH\", \"/usr/local/Ascend/ascend-toolkit/latest\")\n_torch_npu_root = osp.dirname(osp.abspath(torch_npu.__file__))\n\next_modules=[\n    NpuExtension(\n        name=\"pointnet2_ops._ext\",\n        sources=[\n            osp.join(_ext_src_root, \"src\", \"pointnet2_aclnn_bind.cpp\"),\n            osp.join(_ext_src_root, \"src\", \"pytorch_npu_helper.cpp\"),\n        ],\n        include_dirs=[\n            osp.join(_ext_src_root, \"include\"),\n            osp.join(_opp_build_out, \"op_api\", \"include\"),\n            osp.join(_cann_root, \"include\"),\n        ],\n        library_dirs=[\n            osp.join(_opp_build_out, \"op_api\", \"lib\"),\n            osp.join(_cann_root, \"lib64\"),\n            osp.join(_torch_npu_root, \"lib\"),\n        ],\n        libraries=[\"cust_opapi\", \"opapi\", \"ascendcl\"],\n        extra_compile_args=[\"-O2\", \"-std=c++17\"],\n    )\n]"
  },
  {
    "file": "pointnet2_ops/_ext-src/src/pointnet2_aclnn_bind.cpp",
    "before": "// Adapter-only or ATen-only route without generated ACLNN headers, OPP resource registration, aclTensor conversion, workspace/executor handling, or current NPU stream launch.",
    "after": "#include <acl/acl.h>\n#include <acl/acl_rt.h>\n#include <torch/extension.h>\n#include <torch_npu/csrc/core/npu/NPUStream.h>\n#include \"pytorch_npu_helper.hpp\"\n#include \"aclnn_pointnet2_gather_points.h\"\n\nextern \"C\" void pointnet2RegisterAllOpResources();\n\nvoid RunAclnn(const at::Tensor& stream_tensor, const char* op_name, WorkspaceFn workspace_fn, LaunchFn launch_fn)\n{\n    EnsurePointnet2ResourcesRegistered();\n    uint64_t workspace_size = 0;\n    aclOpExecutor* executor = nullptr;\n    CheckStatus(workspace_fn(&workspace_size, &executor), (std::string(op_name) + \" GetWorkspaceSize\").c_str());\n    at::Tensor workspace;\n    void* workspace_ptr = nullptr;\n    if (workspace_size > 0) {\n        workspace = at::empty({static_cast<int64_t>(workspace_size)}, stream_tensor.options().dtype(at::kByte));\n        workspace_ptr = workspace.data_ptr();\n    }\n    aclrtStream stream = c10_npu::getCurrentNPUStream(stream_tensor.device().index()).stream();\n    aclnnStatus launch_status = launch_fn(workspace_ptr, workspace_size, executor, stream);\n    aclnnStatus destroy_status = aclDestroyAclOpExecutor(executor);\n    CheckStatus(launch_status, (std::string(op_name) + \" launch\").c_str());\n    CheckStatus(destroy_status, (std::string(op_name) + \" destroy executor\").c_str());\n}"
  },
  {
    "file": "pointnet2_ops/_ext-src/src/pytorch_npu_helper.cpp",
    "before": "// No conversion from at::Tensor to aclTensor for generated ACLNN APIs.",
    "after": "aclTensor* ConvertType(const at::Tensor& tensor)\n{\n    const aclDataType data_type = ToAclDataType(tensor.scalar_type());\n    if (data_type == ACL_DT_UNDEFINED) {\n        return nullptr;\n    }\n    const auto sizes = tensor.sizes();\n    const auto strides = tensor.strides();\n    std::vector<int64_t> shape(sizes.begin(), sizes.end());\n    std::vector<int64_t> stride_vec(strides.begin(), strides.end());\n    int64_t storage_len = tensor.numel();\n    return aclCreateTensor(shape.data(), shape.size(), data_type, stride_vec.data(), tensor.storage_offset(), ACL_FORMAT_ND, &storage_len, 1, tensor.data_ptr());\n}\n\nvoid Release(aclTensor* tensor)\n{\n    if (tensor != nullptr) {\n        aclDestroyTensor(tensor);\n    }\n}"
  },
  {
    "file": "pointnet2_ops/ascend_opp/op_host/pointnet2_gather_points.cpp",
    "before": "// Missing native OPP op_host definition, shape inference, dtype inference, tiling, and AICore config.",
    "after": "namespace ge {\nstatic ge::graphStatus InferShape(gert::InferShapeContext* context)\n{\n    const gert::Shape* points = context->GetInputShape(0);\n    const gert::Shape* idx = context->GetInputShape(1);\n    gert::Shape* y = context->GetOutputShape(0);\n    y->SetDimNum(0);\n    y->AppendDim(points->GetDim(0));\n    y->AppendDim(points->GetDim(1));\n    y->AppendDim(idx->GetDim(1));\n    return GRAPH_SUCCESS;\n}\nstatic ge::graphStatus InferDataType(gert::InferDataTypeContext *context)\n{\n    const auto inputDataType = context->GetInputDataType(0);\n    context->SetOutputDataType(0, inputDataType);\n    return ge::GRAPH_SUCCESS;\n}\n}\n\nnamespace ops {\nclass Pointnet2GatherPoints : public OpDef {\npublic:\n    explicit Pointnet2GatherPoints(const char* name) : OpDef(name)\n    {\n        this->Input(\"points\").ParamType(REQUIRED).DataType({ge::DT_FLOAT16, ge::DT_FLOAT}).Format({ge::FORMAT_ND, ge::FORMAT_ND}).UnknownShapeFormat({ge::FORMAT_ND, ge::FORMAT_ND});\n        this->Input(\"idx\").ParamType(REQUIRED).DataType({ge::DT_INT32, ge::DT_INT32}).Format({ge::FORMAT_ND, ge::FORMAT_ND}).UnknownShapeFormat({ge::FORMAT_ND, ge::FORMAT_ND});\n        this->Output(\"out\").ParamType(REQUIRED).DataType({ge::DT_FLOAT16, ge::DT_FLOAT}).Format({ge::FORMAT_ND, ge::FORMAT_ND}).UnknownShapeFormat({ge::FORMAT_ND, ge::FORMAT_ND});\n        this->SetInferShape(ge::InferShape).SetInferDataType(ge::InferDataType);\n        this->AICore().SetTiling(optiling::TilingFunc);\n        this->AICore().AddConfig(\"ascend910b\");\n    }\n};\nOP_ADD(Pointnet2GatherPoints);\n}"
  },
  {
    "file": "pointnet2_ops/ascend_opp/op_kernel/pointnet2_gather_points.cpp",
    "before": "// Missing AscendC custom-op kernel source.",
    "after": "#include \"kernel_operator.h\"\nusing namespace AscendC;\nextern \"C\" __global__ __aicore__ void pointnet2_gather_points(GM_ADDR points, GM_ADDR idx, GM_ADDR out, GM_ADDR workspace, GM_ADDR tiling) {\n    GET_TILING_DATA(t, tiling);\n    for (uint32_t bb = 0; bb < t.b; ++bb) {\n        for (uint32_t cc = 0; cc < t.c; ++cc) {\n            for (uint32_t mm = 0; mm < t.m; ++mm) {\n                int32_t src = ((__gm__ int32_t*)idx)[bb * t.m + mm];\n                ((__gm__ float*)out)[bb * t.c * t.m + cc * t.m + mm] = ((__gm__ float*)points)[bb * t.c * t.n + cc * t.n + src];\n            }\n        }\n    }\n}"
  }
]

## Do Not
- Do NOT treat `NpuExtension` build success as proof that the CUDA kernel was migrated to native Ascend C/CANN OPP.
- Do NOT accept CppExtension, ATen-only, direct-only, fallback, stub, or report-only routes as production-ready custom-op migration evidence.
- Do NOT skip project-local OPP producer artifacts: `op_host`, `op_kernel`, generated ACLNN headers, `kernel_meta`, `libcust_opapi.so`, and build/install provenance are required.
- Do NOT launch generated ACLNN APIs without using the current NPU stream from torch_npu.
- Do NOT provide route evidence that is stale, cross-run, empty, or not correlated to each custom-op unit identity.

## References
- .sm-artifacts/e2e-v2-ec26de5f95b9/reports/OPENCODE_OPERATIONS_LOG.md
- validated/phase_5_validation_canonical.json
- setup.py
- pointnet2_ops/_ext-src/src/pointnet2_aclnn_bind.cpp
- pointnet2_ops/_ext-src/src/pytorch_npu_helper.cpp
- pointnet2_ops/ascend_opp/op_host/pointnet2_gather_points.cpp
- pointnet2_ops/ascend_opp/op_kernel/pointnet2_gather_points.cpp

## Evidence
- Source runs: e2e-v2-ec26de5f95b9
