# SEAM → dsh 迁移执行计划

状态更新日：2026-11-02。目标是把 OpenCode runtime 契约替换为 dsh 插件执行，并将固定 YAML 状态机迁到 dsh Workflow Engine 执行。迁移期间不得把不兼容 YAML 静默忽略、改写成 prompt，或仅为某个错误增加字符串匹配补丁。新迁移设计见 `docs/dsh-workflow-engine-migration.md`。

## 已完成

- 算子插件入口已经使用真实 dsh `ctx.workflowEngine`：严格校验 YAML，加载固定脚本，将流程状态机放到 dsh worker 执行。旧 TypeScript `WorkflowEngine` 仅留作静态校验与差分回归基线，不再承担插件调度。
- dsh 角色 session 建立、复用、清理、持久化、取消、状态查看及仅聊天恢复。
- 共享 workspace 租约、不可变 artifact、phase 超时/取消边界、KernelBench 算子领域桥接。
- `npu_kernelbench_custom_op.yaml` 的本地执行链已通过真实 YAML 的状态机回归：环境检测、runtime 检测/配置、分析、生成/评测修复 loop、报告；phase 0 规范化的 level/solution type 会传入 loop。
- workflow_start/workflow_end hooks：`snapshot_project`、`copy_artifacts`、`write_summary`、`save_telemetry`、`noop`；关键 hook 失败会失败关闭，非关键失败写入状态。
- Local shell phase：字符串或 argv 命令、cwd/env、output capture、真实退出码、8 MiB 输出限制以及协作取消/进程组清理。
- Container 执行适配：Docker/Podman image 与 existing-container、任务 workspace bind mount、容器 cwd/env、必需 host env/device 校验；Agent scope 内的 `bash` shadow 与 shell phase 共用同一个后端，run 完成/取消后按 `cleanup` 策略回收本次创建的容器。容器命令要求 `sh` 与 `setsid`，取消时按进程组终止，不依赖杀宿主 Docker 客户端。
- 固定 validator 名称清单扩展到现有 `src/validators` 项目和算子 validator，并由 Python `ValidatorEngine` 执行；未登记 validator 在 preflight 拒绝。
- dsh 原生插件提供唯一控制服务 `seamWorkflow` 与工具 `seam_workflow`。当前还不能作为全项目统一入口，因为项目 YAML 的执行语义尚未齐备。

## 执行顺序

1. **公共状态机语义**：补齐 phase-level hooks、所有 phase 类型、嵌套 blocks/transition、review gate、experience/rule migration 的 Python 对照实现；每类语义先加旧 Python 差分 fixture，再实现 TS，不以“放宽 preflight”代替支持。
2. **执行环境适配器**：已接通 Local/Container runtime、Agent scoped `bash` 与 shell phase；read/write/edit/search 通过共享 workspace 的宿主 bind-mount 路径访问同一批文件。仍需验证这些文件工具的路径权限与容器 mount 的一致性；未完成审计前不能把容器当成完整安全沙箱。
3. **领域操作与 validator**：按 operation/validator 注册清单移植项目级 builtin 与校验器；输入输出 schema、artifact 和失败状态做差分测试。固定 Python API 白名单静态检查允许保留；不得根据报错文本机械改源码。
4. **Selector 与统一入口**：解析 `kind: workflow_selector`、候选列表、fallback 和 overrides；selector 只能返回已配置且通过 preflight 的 workflow ID。所有启动、状态、取消、聊天恢复走 `seam_workflow`，旧 OpenCode launcher 在切换前显式标为 legacy，不做隐式双 runtime fallback。
5. **切换验收**：逐个已部署 workflow 输出 capability 审计；dsh 宿主集成、fake-provider 回归、真实 provider smoke、目标环境/NPU correctness 回归、产物对比均通过后，才修改默认 launcher。保留一版可回滚配置，随后才移除 OpenCode 依赖。

## 当前阻塞与验收门

- 容器适配目前覆盖 LLM + shell phase 的工作流；`builtin` phase 仍会执行宿主领域逻辑，带 builtin 的容器 workflow fail-closed。`execution_backend.mode: auto` 的选择/回退语义也仍未迁移。
- KernelBench 算子 YAML 当前不声明 `auto` backend；本地模式下 9 个领域 builtin 已桥接。若改成容器 backend，builtin 仍在宿主运行并因此 fail-closed；容器内执行领域 builtin 尚未迁移。真实模型、KernelBench/NPU correctness 与性能验收仍未运行。项目流程还有 orchestration/review、项目 validator、rule migration 等独立 blocker。
- `seam_auto_default.yaml` 和 `workflow_selector_smoke.yaml` 是 selector 元配置，不是普通 workflow；需要独立 selector 解析和安全覆盖策略。
- 兼容审计与 native 编译器共用 preflight，包括跨子流程 phase ID 唯一性检查；不能用检查通过代替真实模型、设备与容器验收。

## 不变量

- 未知字段、未知 operation/validator、未支持 phase type 在执行前 fail closed。
- 一个角色在一个任务中复用自己的 session；不同角色不共享聊天上下文；任务共享 workspace 和版本化 artifact。
- run/cancel/status/restore_chat 都由同一生命周期控制器持有资源；聊天恢复不续跑 workflow。
- 工作流执行结果由验证器、shell exit/result schema 和框架状态机决定，不由 LLM 自报“成功”决定。
