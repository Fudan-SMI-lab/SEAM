1. 这是 operator 修复任务，只处理当前失败；不要扩展成通用 workplan。
2. 先阅读 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}，结合 {project_dir} 和 {entry_script} 定位当前 operator incompatibility。
3. 做所选目标平台/后端的原生修复，不要加 CPU fallback；只有在平台策略明确为 Ascend/NPU 时才使用 Ascend/CANN/torch_npu 假设。若是 custom-op 项目，严格遵守下方 operator_custom_op_guidance。
4. 直接修改目标项目文件并运行验证；不要启动后台检索/后台 agents 后提前返回，不要把 modified_files: []、调研计划、等待后台结果或“下一步再修”当作本轮修复结果。
{operator_custom_op_guidance}
