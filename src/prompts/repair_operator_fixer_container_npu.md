1. 这是 operator 修复任务，只处理当前失败；不要扩展成通用 workplan。
2. 先阅读 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}，结合 {project_dir} 和 {entry_script} 定位当前 operator incompatibility。
3. 做 Ascend NPU 原生修复，不要加 CPU fallback；若是 custom-op 项目，严格遵守下方 operator_custom_op_guidance。
4. 直接修改目标项目文件并运行验证；不要启动后台检索/后台 agents 后提前返回，不要把 modified_files: []、调研计划、等待后台结果或"下一步再修"当作本轮修复结果。
5. {operator_custom_op_guidance}
6. ## Container Execution Context

This workflow uses a container execution backend for Phase 5 validation.

- **Execution backend mode**: `{execution_backend_mode}`
- **Actual execution command**: `{actual_execution_command}`
- **Container name or ID**: `{container_name_or_id}`
- **Container workdir**: `{container_workdir}`
- **Host project directory**: `{host_project_dir}`
- **Container project directory**: `{container_project_dir}`

当你在容器工作流中手动验证修复时，使用 `actual_execution_command` 进行验证执行。
不要直接在宿主机上运行 `{entry_script}`，该脚本需要在容器环境中执行。
如果需要在容器内手动验证，请使用：
`{actual_execution_command}`

## Output Format
Return a JSON code block with this shape:

```json
{
  "modified_files": [],
  "summary": "what changed and why",
  "agent_diagnostics": {
    "native_path_validated": true,
    "final_gate_schema_preserved": true,
    "validated_with_container_execution": true
  }
}
```
