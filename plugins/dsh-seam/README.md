# SEAM on dsh：迁移实现与边界

截至 2026-11-02，**本插件的算子状态机已由真实 dsh Workflow Engine 的 worker 执行**，已做宿主集成与差分测试，不是全部工作流已经切换的声明。原 Python/OpenCode 启动入口仍保持原样；本插件不调用 OpenCode，也不委托给旧 `WorkflowExecutor`。

迁移方向不变：全面替换 runtime，先保留流程。当前支持现有 `npu_kernelbench_custom_op.yaml` 的执行语义；`npu_ascend_custom_op.yaml` 等项目级流程仍有下面列出的执行策略待移植。未迁移字段会在开始执行前报错，**不静默忽略，不回退 OpenCode**。这不是只替换某一个 LLM phase，也没有重写阶段编号或改成动态流程。

## 实现分工

- `src/plugin.ts`：dsh/Cordis 原生插件，注入 `agents`、`sessions`、`tools`、`workflowEngine`、`subagents`，注册控制服务、工具和专用操作 provider。
- `src/native-workflow.ts`：调用 dsh `workflowEngine.start`，关联原生 run ID，管理取消、检查点、资源清理。
- `src/workflow-compiler.ts`、`src/fixed-script.ts`：严格校验 YAML，打包固定解释器；dsh worker 执行 phase、transition、loop、dispatch、condition 和停滞策略。LLM 不决定流程跳转。
- `src/workflow-transport.ts`、`src/phase-executor.ts`：带 run capability 和 parent 校验的宿主 RPC，执行单阶段和有限重试；不在宿主重新调度流程。
- `src/engine.ts`：旧调度器仅作差分回归基线；静态校验仍复用，插件不实例化它。
- `src/dsh-runtime.ts`：每角色独立 session，同一角色复用聊天；通过 dsh AgentLoop 执行消息与工具调用。临时角色也可配置为 `ephemeral`。
- `src/store.ts`：任务快照、角色 session ID、不可覆盖的递增 artifact；同一 `runId` 不允许重跑。
- `src/bridge.ts`、`python/bridge.py`：独立 Python 子进程复用原领域函数、prompt loader、skill resolver 与 validator。Python 目前保留的是领域实现，不是旧 runtime/流程执行器。

每个任务共享 workspace；不同角色的聊天隔离。每轮输入、输出、工具/消息事件和修正尝试都会保留。prompt 将本轮 artifact、诊断建议和历史轮次一起作为证据注入；超预算显式失败，不偷偷截掉整个历史。领域 prompt 的文件内容摘录仍按既有上限截取，完整路径保留以便读取。

`solution.json` 继续由框架从真实源码打包发布：角色修改源码和 `solution_spec.json`，框架核对 producer 成功后重新读取源码、计算摘要、原子发布；失败不能把旧候选冒充新候选。本迁移没有加入按错误文本匹配后机械改源码的补丁；API 静态约束仍走既有固定策略。

## 构建与 dsh 版本

已验证的 dsh 源码版本：`0.1.2-rc.1`，commit `76fda729799fe9b3848dbe2c211d4b231032b81e`，Node `22.22.0`。不能假定同名旧 npm 发行版具有相同 API；其他提交需要重新跑集成测试。不要为了本插件覆盖你已有的 dsh checkout。

先在兼容的 dsh 源码目录安装并构建宿主包：

```sh
npm exec --yes pnpm@11.7.0 -- install --frozen-lockfile --ignore-scripts
npm run build:lib:host
```

然后在本插件目录执行：

```sh
npm ci
npm run link:dsh -- /absolute/path/to/deepseek-harness
npm run typecheck
npm run build
npm test
npm run audit -- ../../src/workflows/npu_kernelbench_custom_op.yaml
```

`link:dsh` 只建立本插件 `node_modules` 下的链接，不修改 dsh 源码；遇到同名非链接安装会拒绝覆盖。测试依赖该 dsh checkout 的宿主包和 Cordis 包，`npm ci` 后需要重新执行链接。Python 需要安装 SEAM 原有依赖；插件不自动安装 Python 包、不隐式配置 NPU。

测试默认调用 `python3`。多 Python 环境可用 `SEAM_TEST_PYTHON=/absolute/path/to/python3 npm test` 显式指定已安装 SEAM 依赖的解释器，避免 npm 的 PATH 选中其他 Python；生产配置使用下述 `SEAM_PYTHON`，二者独立。

生产 TypeScript 构建检查依赖声明；测试配置单独开启 `skipLibCheck`，避开所验证 dsh 版本的 session projection 声明扩展不完整问题，测试源码仍做类型检查并运行真实宿主集成。

`npm run audit` 不带参数会只读检查所有现有 workflow，输出逐文件 JSON 与第一个阻塞原因。有不兼容文件时退出码为 1，这是兼容审查结果，不代表执行了这些任务。审查检查的是已迁移语义，不证明环境、模型或 NPU 可以运行。

## 加载插件

`examples/seam.patch.yml` 是加入现有 dsh profile 的 overlay，不是完整部署。宿主须已加载 AgentRegistry、AgentLoop、持久化 SessionStore、SubagentRuntime、WorkerThreadWorkflowEngine、LLM provider 和工具。所验证版本的 Web profile 默认关闭 host workflow worker 和文件/bash 工具，因此 overlay 显式重新启用它们；不启用动态 `tool-workflow`。示例角色使用全局 `read/write/edit/glob/grep/bash`。启用全局工具也可能影响其他 Agent 可见性，部署时必须复核宿主及 preset 权限。

配置环境变量后，把该 overlay 传给**兼容版本**的 dsh profile，例如：

```sh
export SEAM_REPOSITORY=/absolute/path/to/SEAM
export SEAM_DSH_PLUGIN="$SEAM_REPOSITORY/plugins/dsh-seam/dist/plugin.js"
export SEAM_RUN_ROOT="$HOME/.local/state/seam-dsh/runs"
export SEAM_PYTHON=/absolute/path/to/python3
export SEAM_PROVIDER=your-configured-provider
export SEAM_MODEL=your-configured-model
dsh --profile web --patch "$SEAM_REPOSITORY/plugins/dsh-seam/examples/seam.patch.yml"
```

provider/model 应是该 dsh profile 已配置的名称；凭证与 API 地址留在 dsh 原有 provider 配置中，不写入 workflow/artifact。没有全局 `dsh` 时，在已构建的 dsh checkout 内以 `corepack pnpm dsh` 替代命令。可附加 `--dump-config` 检查 overlay 合成，但这不代表插件已启动。示例不是对真实模型或 Web UI 已通过验收的声明；已经验证的是真实 Cordis Loader + dsh worker + AgentLoop + JSONL 的宿主级集成。

配置要点：

- `repository` 指 SEAM 源码；`runRoot` 指所有插件实例共用的持久化目录；建议均用绝对路径。
- `workflows` 是可运行 YAML 的显式映射，调用方不能传任意 workflow 路径。
- `roleTools` 每个角色必须显式设置，`[]` 表示无全局工具；不要给 worker 角色开放 `seam_workflow` 以递归启动任务。
- `roleOptions` 透传给 dsh agent 创建/恢复选项，目前示例只指定 provider/model；聊天恢复时也会应用当前配置。
- `defaultTimeoutSeconds` 是整个 phase 的预算，包含 prompt、LLM 修正和 validator。若 builtin 内层评测预算更长，外层预算也必须相应增大。
- `maxPromptBytes` 是最终 prompt 的 UTF-8 字节上限，不是模型 token 上限；需要结合 provider 的上下文容量设置。

工具 allowlist **不是安全沙箱**：开放 `bash` 就允许相应 shell 权限。cwd 只校验 session workspace，不限制进程不能访问其他路径。作用域工具、PTC transport、MCP 等能力需要另行审计；本实现不能宣称已经封闭全部能力通道。

## 控制接口

`seam_workflow` 接受 `action` 与 `runId`。示例启动：

```json
{
  "action": "run",
  "runId": "operator-001",
  "workflow": "kernelbench",
  "workspace": "/absolute/path/to/task",
  "context": {
    "OPERATOR_TASK_DIR": "/absolute/path/to/task",
    "OPERATOR_SOLUTION_PATH": "/absolute/path/to/task/solution.json",
    "OPERATOR_OUTPUT_DIR": "/absolute/path/to/task/evaluation",
    "KERNELBENCH_ROOT": "/home/yiding/npu-kernelbench",
    "SOLUTION_TYPE": "ascendc",
    "TARGET_SOC": "your-target-soc",
    "OPERATOR_LEVEL": "level4",
    "OPERATOR_CONSTRAINTS": "the actual binding API policy"
  }
}
```

这里的路径仅为调用示例，不是运行时默认路径。`OPERATOR_CONSTRAINTS` 必须来自实际任务策略，不能把示例文字当有效约束。任务目录须已有真实 `definition.json` 和 `workload.jsonl`。

| action | 行为 |
| --- | --- |
| `run` | 校验配置后异步启动，返回 `starting`；随后查询状态 |
| `status` | 返回状态、阶段、结果、角色 session ID、executionEngine 和 dshWorkflowId；本实例清理完成前不提前报告完成 |
| `cancel` | 取消当前实例拥有的任务，等待 AgentLoop/sidecar 清理和最终快照 |
| `restore_chat` | 恢复已停止任务的角色聊天，校验 cwd、应用工具策略；不重新发送 prompt、不重跑 phase |
| `close_chat` | 释放恢复的角色会话与 workspace 租约，保留持久化日志 |

状态查询、取消、聊天恢复与关闭只需例如 `{"action":"status","runId":"operator-001"}`。`resume` 执行流程刻意没有提供：用户已选择恢复聊天，而非自动续跑状态机。恢复出来的 agent 由宿主维护；本工具不是聊天 UI 的会话选择器。

持久化恢复要求 dsh JSONL/等价后端实际启用；本实现要求 flush 成功，不把纯内存会话标成可恢复。`runRoot` 与 dsh session store 都要持久化、适当控制访问权限，里面可能含源码、提示词、输出和诊断。

## YAML 兼容边界

| 能力 | 当前状态 |
| --- | --- |
| 顺序 phase、终态、transition、output_as | 已实现；真实 KernelBench YAML 通过审查 |
| 变量作用域、类型保持、legacy/enhanced phase condition、stop condition | 已移植，和原 Python 策略做差分测试；不是任意 Python 表达式执行器 |
| loop、角色 dispatch、历史、停滞、迭代上限、评测字段提升 | 已实现算子路径；不改变现有流程设计 |
| 9 个 operator builtin | 已桥接并用于 `npu_kernelbench_custom_op.yaml` 本地执行：task_validate、runtime_detect/configure、artifact_initialize/pack/validate、kernelbench_eval、evaluation_classify、trace_summary；真实 YAML 状态机回归覆盖环境检测到最终报告 |
| operator_analysis/report/env_detect/runtime_detect validators、runtime_skills | 已桥接 |
| workflow_start/workflow_end builtin hooks | 已实现 snapshot_project、copy_artifacts、write_summary、save_telemetry、noop；支持 critical/save_as |
| local/container shell phase | 已支持字符串/argv、cwd/env、capture、超时/取消和真实退出码；容器仅支持 LLM + shell，builtin 容器执行仍拒绝 |
| 项目 orchestration/review、blocks、phase hooks、auto backend | **未迁移，执行前拒绝** |
| rule migration、experience、project final gate、phase6 fallback/context recovery | **未迁移，执行前拒绝相关字段/operation** |
| OpenCode 历史会话导入、执行断点续跑 | 未实现；只恢复本插件产生的 dsh 聊天 |
| 真实模型/NPU 编译、正确性和性能验收 | 尚未运行；宿主状态机回归不能替代硬件验收 |

需要明确的行为差异：

1. 最终输出须是完整 JSON 对象（可为整个 JSON fenced block）。不会从任意说明文字里猜测截取一个对象；截断/异常 turn 即使带有看似完整 JSON 也不算成功。
2. 同角色 JSON 修正最多额外 2 次；validator 最多检查 3 次。整个 phase 共享一个 deadline，不像旧路径那样对每次修正重新计满 timeout。
3. 停滞证据保留结构化字段和有界输出；不迁入旧 stdout 诊断关键词筛选规则，不据此机械修复源码。
4. phase condition 与 stop condition 各自沿用旧求值策略，并记录求值错误；不存在用解析失败代替成功的路径。
5. 不自动接受尚未迁移的扩展字段，避免“YAML 能读但含义已变”。

## 超时、锁与尚未完成的工作

- dsh LLM 取消是协作式：发送 `agent.cancel` 后等待 idle/flush/dispose。若 provider/工具不响应取消，仍可能等待超过 phase deadline，**不是硬隔离 watchdog**。
- Python bridge 有输出限额，取消时追踪子进程并 TERM→KILL、等待退出。它不等于容器隔离；`ps` 不可用、晚生或已脱离追踪的子进程仍是边界。
- workspace 租约只协调同一 `runRoot` 的插件实例。不同 `runRoot`、旧 SEAM 进程或人工操作不受该锁保护；不允许它们同时写同一任务目录。
- 崩溃后锁不会自动删除，`running` 快照不允许直接恢复。先核实旧进程和设备任务已停止，再人工处置锁；不要用无条件删锁脚本规避竞争。
- 尚未替换旧 CLI/服务入口，也没有删除 OpenCode 依赖。后续步骤和验收门见 `docs/dsh-runtime-migration.md`；必须先移植项目级执行语义并接入统一启动入口，再做真实模型/NPU 回归，最后才可切默认并清理旧 runtime。

插件的算子执行器已切换为 dsh 原生 workflow，不是把 YAML 塞进提示词。版本化 artifact 包含 `dsh-script`（脚本及摘要）、`dsh-run`、`dsh-result`，可核实原生执行链。dsh `agentsStarted` 也统计这些宿主 RPC，不等于模型调用次数。当前支持聊天恢复，不支持进程重启后续跑 workflow。详细架构与硬件验收要求见 `docs/dsh-workflow-engine-migration.md`。
