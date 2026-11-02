# SEAM 算子流程迁入 dsh Workflow Engine

更新：2026-11-02。**插件的算子执行入口已切到真实 `ctx.workflowEngine`，不再实例化 SEAM 自己的调度 Engine。** 已完成宿主集成和固定流程回归；尚未做真实模型/NPU 验收，也未替换项目级 Python/OpenCode 启动入口。

## 谁控制流程

```text
seam_workflow(run) → Controller → strict YAML preflight
  → dsh workflowEngine.start(fixed script, YAML args, parent, signal)
    → dsh worker 执行固定解释器：condition / transition / dispatch / loop
      → agent(JSON command) → SEAM 专用 SubagentProvider
        → step：单阶段执行器 → dsh 角色 session 或 Python 领域 builtin
        → artifact / checkpoint / hooks：宿主持久化操作
```

YAML 不变成提示词，LLM 不参与编排。`workflow-compiler.ts` 校验 YAML 并加载构建好的 `fixed-script.bundle.js`；脚本是固定的解释器，YAML 作为 `args` 传入，不是让模型生成 JavaScript。**循环、分支、停止条件和状态推进实际运行在 dsh workflow worker 中**，宿主只执行叶子阶段、持久化和管理资源。

dsh 的 workflow 脚本 API 不直接提供 SEAM builtin/artifact hook，因此使用其正式 `subagentProvider` 扩展点桥接。`agent()` 在这里是版本化 JSON RPC，不意味着每次都调用模型。provider 以 `action` 分发，不靠提示词 label 猜测；每次 run 使用随机 capability，绑定调用方 parent 身份，拒绝过期或跨 parent 请求。角色仍由 `DshRuntime` 创建独立 session，同角色重试/后续阶段复用聊天；builtin 不调用模型。

## 已迁移的状态机语义

- 顶层阶段顺序、transition、terminal、output_as、输入映射和条件表达式。
- repair loop、角色 dispatch、stop conditions、评测字段提升、历史、停滞与迭代上限。
- 单阶段 deadline、同角色 JSON 修正、validator 重试；重试不重置整个阶段预算。
- workflow_start/workflow_end hooks；关键收尾 hook 失败不能留下成功状态。
- 9 个算子 builtin、算子 validator、框架打包 solution 与失效候选保护保持原有领域实现。
- `run/status/cancel/restore_chat/close_chat` 统一由插件控制；恢复聊天不等于续跑 workflow。

`engine.ts` 的旧调度类仅作为差分测试基线保留；生产路径会复用其静态 YAML 校验，但不会执行旧调度器。项目级 orchestration/review、blocks、phase hooks 等未迁移能力仍在启动前明确拒绝，不能据此宣称全仓库工作流已经迁完。

## 生命周期与运行证据

- 原生 dsh run 的 stopReason/result 映射为 SEAM 状态；native error、非法返回或取消均不能冒充完成。
- 运行快照有 `executionEngine: dsh-workflow` 和 `dshWorkflowId`；递增 artifact 保存 `dsh-script`（含摘要和 YAML）、`dsh-run`、`dsh-result`，以及逐阶段证据。脚本 capability 不写进这些脚本 artifact。
- 取消同时传给 dsh workflow、角色 Agent 和领域操作。无论 dsh worker 的 dispose grace 是否已结束，都等待宿主 pending 操作、角色和子进程清理后才释放 workspace/run 锁。
- LLM/provider 取消仍是协作式，不能保证失联工具在固定时间内退出。清理未完成时不提前开放 workspace。
- dsh 的 `agentsStarted` 包含 RPC artifact/checkpoint 等调用，**不是 LLM 调用数**。当前使用引擎默认调用预算；若扩大 loop/工作流，需复核 dsh 的 maxTotalAgents 上限。
- 进程重启后可以恢复本插件持久化的角色聊天，但没有执行中 workflow 的断点续跑。

## 配置与验证

见 `plugins/dsh-seam/README.md`。使用 `examples/seam.patch.yml` 叠加在兼容 dsh profile 上；overlay 显式启用 host `workflow-worker-thread` 和角色需要的基本工具，不启用让模型自由编排的 `tool-workflow`。这会增加 host 工具可见性，部署时仍需审查其他 Agent 的权限和每角色 allowlist。

```sh
cd plugins/dsh-seam
npm run typecheck
npm run build
npm test
npm run audit -- ../../src/workflows/npu_kernelbench_custom_op.yaml
```

回归直接加载真实 dsh WorkerThreadWorkflowEngine/Subagent/AgentLoop/JSONL，验证固定算子 YAML 两轮评测失败→修复→通过的调度结果与旧执行器一致，以及环境修复分流、停滞、迭代上限、关键 hooks、超时/取消 drain、会话复用和恢复。全流程用模拟阶段结果验证状态机；另有真实 Python builtin 桥接测试。**这些不能替代真实 API、编译器和 NPU 正确性/性能验收。**

后续设备验收应保存模型/provider 配置、runtime 检测结果、源码与 solution 摘要、完整编译/评测 artifact，以及取消后无残留任务的证据；验收通过后再决定是否替换旧 launcher 和移除旧 runtime 依赖。
