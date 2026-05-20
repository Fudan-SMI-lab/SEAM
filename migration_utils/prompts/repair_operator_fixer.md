1. 这是 operator 修复任务，只处理当前失败；不要扩展成通用 workplan。
2. 先阅读 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}，结合 {project_dir} 和 {entry_script} 定位当前 operator incompatibility。
3. 做 Ascend NPU 原生修复，不要加 CPU fallback；普通 CUDA 项目的 operator 修复应使用 torch_npu/PyTorch NPU 支持的算子、参数、后端或局部代码改写，不要生成 OPP/custom-op 产物。
4. 只有下方 operator_custom_op_guidance 明确说明存在 active custom-op contract 时，才进入严格 Ascend C/CANN OPP custom-op 修复范围。
5. 只有 active custom-op contract 存在时，缺少每行 `public_api_route_evidence` 或 `framework_integration_route_evidence` 才是 custom-op 合同失败；普通 CUDA/operator 修复不要生成或要求这些 custom-op route evidence。
6. 普通 Transformers attention backend 问题（例如零 custom-op 项目的 FlashAttention2/flash_attn 缺失）不属于 OPP/custom-op 修复范围；应退回 dependency/code 修复，改用 `attn_implementation="sdpa"` 或 `"eager"` 等 NPU 兼容路径。
7. 直接修改目标项目文件并运行验证；不要启动后台检索/后台 agents 后提前返回，不要把 modified_files: []、调研计划、等待后台结果或“下一步再修”当作本轮修复结果。
{operator_custom_op_guidance}
