1. 这是 operator 修复任务，只处理当前失败；不要扩展成通用 workplan。
2. 先阅读 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}，结合 {project_dir} 和 {entry_script} 定位当前 operator incompatibility。
3. 做 Ascend NPU 原生修复，不要加 CPU fallback；若是 custom-op 项目，严格遵守下方 operator_custom_op_guidance。
4. 对 custom-op operator 修复，目标必须是严格 Ascend C/CANN OPP custom operator：生成/构建/安装 op_host、op_kernel、CMakeLists.txt/build.sh 或等价 OPP build、CANN/OPP build-install 日志、install/provenance、generated header/op_info/kernel_meta/producer/package artifacts；不得把 torch_npu.utils.cpp_extension.NpuExtension、torch.utils.cpp_extension.CppExtension、ATen-only npu_ops.cpp、libtorch/torch_cpu/torch_npu-only build 当作 opp_custom_op_artifact_evidence，NpuExtension 只能在另有独立 OPP producer 证据时作为 adapter evidence。
5. 直接修改目标项目文件并运行验证；不要启动后台检索/后台 agents 后提前返回，不要把 modified_files: []、调研计划、等待后台结果或“下一步再修”当作本轮修复结果。
{operator_custom_op_guidance}
