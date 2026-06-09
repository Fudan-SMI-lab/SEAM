1. 这是 operator 修复任务，只处理当前失败；不要扩展成通用 workplan。
2. 先阅读 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}，结合 {project_dir} 和 {entry_script} 定位当前 operator incompatibility。
3. 做所选目标平台/后端的原生修复，不要加 CPU fallback；只有在平台策略明确为 Ascend/NPU 时才使用 Ascend/CANN/torch_npu 假设。若是 custom-op 项目，严格遵守下方 operator_custom_op_guidance。
4. 直接修改目标项目文件并运行验证；不要启动后台检索/后台 agents 后提前返回，不要把 modified_files: []、调研计划、等待后台结果或"下一步再修"当作本轮修复结果。
5. {operator_custom_op_guidance}
6. ## 关键区分：源码修复 vs 输出文件补丁
7. - 如果 analyze_error 报告的问题是 **operator 代码本身的不兼容**（编译错误、API 缺失、算子实现 bug），则修改对应的算子源码（.cpp/.cu/.py 等）。
8. - 如果 analyze_error 报告的问题是 **entry script 输出缺少证据字段**（如 `cpu_baseline`、`npu_custom`、`project_api_invoked`、`custom_op_route_executed`、`public_api_invoked` 等布尔标志），则**必须修改 entry script 源码**（从 `{entry_script}` 命令提取脚本文件名，如 `python validate.py` → 修改 `validate.py`）来生成这些字段。**严禁只修改输出文件**（如 `migration_reports/*.json`），因为输出文件会在下轮运行时被 entry script 重新生成覆盖。
9. - 验证时使用 `{entry_script}` 命令重新运行（该命令即为 entry script 的执行方式），确认源码修改生效且输出文件自动包含所需字段。
10. {parallel_dispatch_guidance}
11.
12. ## Assigned Operator Scope
13. Assigned unit count: {assigned_unit_count}
14. Assigned units: {assigned_units}
15. Only repair, validate, and report on the assigned units above. Do not claim closure for other global custom-op units unless they are listed in this assigned scope or share source files with an assigned unit and are required to avoid a source conflict.
16. Current assigned-scope progress:
17. {operator_repair_progress_block}
18.
19. ## Output Format
20. Return a JSON code block with this shape:
21.
22. ```json
23. {
24.   "modified_files": [],
25.   "summary": "what changed and why",
26.   "agent_diagnostics": {
27.     "native_path_validated": true,
28.     "final_gate_schema_preserved": true,
29.     "validated_with_entry_script": true
30.   }
31. }
32. ```
