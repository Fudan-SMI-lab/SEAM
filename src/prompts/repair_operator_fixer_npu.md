1. 这是 operator 修复任务，只处理当前失败；不要扩展成通用 workplan。
2. 先阅读 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}，结合 {project_dir} 和 {entry_script} 定位当前 operator incompatibility。
3. 做所选目标平台/后端的原生修复，不要加 CPU fallback；只有在平台策略明确为 Ascend/NPU 时才使用 Ascend/CANN/torch_npu 假设。若是 custom-op 项目，严格遵守下方 operator_custom_op_guidance。
4. 直接修改目标项目文件并运行验证；不要启动后台检索/后台 agents 后提前返回，不要把 modified_files: []、调研计划、等待后台结果或"下一步再修"当作本轮修复结果。
5. 对 custom-op/operator assignments，INCOMPLETE 是需要解决的目标状态，不是提前返回理由。必须持续实现或修复被分配算子/变体的 NPU host/kernel/adapter/build/report producer；缺 build helper、缺 adapter、缺 OPP 源码、缺报告生成脚本都属于本轮要修的项目文件。
6. 如果验收仍未通过，继续把剩余 assigned units 拆成更小实现切片，在同一 session 内继续写 NPU 实现、修 build、跑 {entry_script}。只有在已经产生真实源码/构建/验证修改并记录证据后，才允许报告剩余 blocker；禁止无代码修改地返回 INCOMPLETE。
7. {operator_custom_op_guidance}
8. ## 关键区分：源码修复 vs 输出文件补丁
9. - 如果 analyze_error 报告的问题是 **operator 代码本身的不兼容**（编译错误、API 缺失、算子实现 bug），则修改对应的算子源码（.cpp/.cu/.py 等）。
10. - 如果 analyze_error 报告的问题是 **entry script 输出缺少证据字段**（如 `cpu_baseline`、`npu_custom`、`project_api_invoked`、`custom_op_route_executed`、`public_api_invoked` 等布尔标志），则**必须修改 entry script 源码**（从 `{entry_script}` 命令提取脚本文件名，如 `python validate.py` -> 修改 `validate.py`）来生成这些字段。**严禁只修改输出文件**（如 `migration_reports/*.json`），因为输出文件会在下轮运行时被 entry script 重新生成覆盖。
11. - 验证时使用 `{entry_script}` 命令重新运行（该命令即为 entry script 的执行方式），确认源码修改生效且输出文件自动包含所需字段。
12. {parallel_dispatch_guidance}
13.
14. ## Assigned Operator Scope
15. Assigned unit count: {assigned_unit_count}
16. Assigned units: {assigned_units}
17. Only repair, validate, and report on the assigned units above. Do not claim closure for other global custom-op units unless they are listed in this assigned scope or share source files with an assigned unit and are required to avoid a source conflict.
18. Current assigned-scope progress:
19. {operator_repair_progress_block}
20.
21. ## Output Format
22. Return a JSON code block with this shape. For active custom-op/operator repair, `modified_files` must list concrete project files changed in this call unless the assigned final gate was already FULL_PASS before this call and you verified that no-op state with {entry_script}.
23.
24. ```json
25. {
26.   "modified_files": ["path/to/changed_file.cpp", "path/to/changed_file.py"],
27.   "summary": "what changed and why",
28.   "agent_diagnostics": {
29.     "native_path_validated": true,
30.     "final_gate_schema_preserved": true,
31.     "validated_with_entry_script": true
32.   }
33. }
34. ```
