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

## Repair Loop
- Inspect `latest_complete_stdout_artifact_path`, `latest_complete_stderr_artifact_path`, and `latest_complete_meta_artifact_path` when populated; prefer complete stdout/stderr over truncated summaries.
- After each in-scope native/custom-op, compiler, shared-object, or final-gate evidence fix, run `actual_execution_command` with a timeout. If the next complete artifacts show another operator fixer failure, fix and rerun.
- If the next complete artifacts show only an out-of-scope dependency, environment, runtime-library, or Python-level source failure, stop and write the handoff role and reason in `summary`.
