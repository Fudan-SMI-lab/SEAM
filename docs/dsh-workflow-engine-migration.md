# SEAM 算子工作流：YAML 构建与 dsh 原生执行

更新：2026-11-05。算子路径已改为提前生成固定 JS，并按工作流包版本复用。没有修改 dsh 源码，也没有改变 KernelBench YAML 的业务阶段顺序。真实模型/NPU 验收尚未执行，项目级 workflow 不因此自动兼容。

## 构建与运行

构建时，`workflow-compiler.ts` 校验 YAML，并生成显式阶段函数、顶层 switch、transition 分支、有限 for loop 和停止条件；不再加载固定的 YAML 状态机解释器。表达式求值和状态记录由 `compiled-runtime.ts` 提供，不在辅助库里查找下一阶段。`source-map.json` 保存阶段 ID、YAML 结构路径及生成 JS 行范围，不是完整的标准调试 source map。

工作流包由 `workflow-package.ts` 原子发布到内容摘要目录，包含：

- `workflow.js`：预先生成的流程代码和辅助库。
- `stages.json`：宿主执行叶子阶段所需的配置。
- `definition.json`：归档定义及角色、hooks、backend 配置；不交给 worker 解释。
- `source.yaml`：构建输入的审计副本。
- `source-map.json`：阶段源位置映射。
- `manifest.json`：编译器/阶段协议版本、文件摘要、仓库依赖摘要和包 ID。

相同构建输入产生相同包 ID；包是可信代码部署产物，不是安全沙箱。哈希不提供签名认证。部署者必须保护包目录，不能允许任务模型修改。

任务启动时配置 `workflowPackages: {kernelbench: /path/to/package}` 和 `workflows: {}`，无需读取原 YAML。旧 `workflows` 配置在插件实例首次使用时编译并缓存，不每任务重读定义。修改 YAML 后重建包并更新配置/重启，不热更新运行中流程。

## 执行位置

1. dsh 主进程：加载和缓存包、核对依赖、获取 workspace 租约、建立本次调用身份。
2. dsh Workflow Engine：启动真实 worker，参数只有任务 context 和调用 capability，不传入 YAML 流程对象。
3. worker：执行生成的 JS，决定阶段、条件、循环、转移和终态。
4. dsh 主进程插件：接收已登记阶段 ID，使用包中的阶段配置执行角色/builtin/shell，验证结果和持久化。
5. Python bridge / KernelBench：执行已有领域逻辑、编译、设备评测。

worker 到主进程仍使用 dsh 的正式 `subagentProvider` 扩展点。`WorkflowTransport` 只校验 run capability/parent 和传递 step、artifact、checkpoint、hooks，不决定阶段转移。builtin 不调用模型，角色仍由 DshRuntime 创建或复用独立 session。dsh 子调用计数包含这些 RPC，不等于模型调用次数。

## 阶段输入、输出和验证

叶子阶段新增可选内联 `input_schema`、`output_schema`，由 Ajv strict draft-07 在构建时编译。运行前验证实际输入；角色结束后解析 JSON，再验证输出结构、运行领域 validator，并重新检查输出契约。输入不合法不执行；builtin 输出非法不自动重试。LLM 输出修正有限次且共享阶段 deadline。

未声明契约的旧阶段不自动获得完整业务验证。现有输入映射/条件求值保留，尚未实现跨阶段 schema 推断、引用可达性证明或任意表达式编译。未知定义字段、操作、validator 和不支持策略仍 fail closed。

角色 prompt/skill 和 Python 实现继续来自仓库；包绑定仓库资源摘要以拒绝依赖漂移，并非完整自包含镜像。外部 SDK、provider、模型和硬件状态不在包锁定范围，需运行时检测。并发修改部署代码不受 workspace 锁保护，应采用只读部署及显式升级。

## 状态与清理

每个 run 独立保存 context、状态和证据，快照记录 `executionEngine: dsh-workflow`、`dshWorkflowId`、`workflowPackageId`。相同包可服务多个任务，不共享可变运行状态。任务内共享 workspace，同角色复用 session；workspace 复用仍受租约约束，不表示可恢复旧 workflow。

取消传给 dsh 和操作执行器。worker dispose grace 到期后，主进程仍等待领域 pending 操作清理，再保存终态、释放 workspace。提供聊天恢复，不提供进程崩溃后的工作流续跑。旧 `engine.ts` 留作静态校验和差分测试，生产路径不实例化旧调度器。

## 验收与未覆盖范围

实际测试加载 dsh WorkerThreadWorkflowEngine、Subagent、AgentLoop 和 JSONL，比较旧执行器和生成代码的阶段顺序、循环、停滞、终态与状态。另测工作流包复用、删除原 YAML 后运行、内容损坏/依赖漂移拒绝、输入输出契约、取消清理和真实 Python builtin。

命令与部署配置见 `plugins/dsh-seam/README.md`。项目 orchestration/review、blocks、控制阶段 schema、容器 builtin 等仍未迁移。服务器报告中的 wait/CLI/history 补丁尚未出现在本次拉取的远端；评测实时写盘和超时层级问题也未由本次架构重构解决，不应宣称硬件评测问题已修复。
