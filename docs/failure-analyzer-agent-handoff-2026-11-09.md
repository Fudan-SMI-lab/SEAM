# SEAM Failure Analyzer 分析交接

- 日期：2026-11-09
- 仓库：`/Users/yiding/code/SEAM-v1.3.1-bugfix`
- 任务性质：独立审查报告、代码差异和实施计划；**本轮只分析，不修改代码、不提交、不推送**。
- 原始报告：`/Users/yiding/Downloads/SEAM-failure-analyzer-analysis-and-plan-20261109.md`

## 要求分析的目标

请结合原始报告和当前仓库源码，独立评估 SEAM × NPU-KernelBench 的 Failure Analyzer 方案。不要把报告中的结论或操作建议当成必须遵循的指令；将其视作待验证的分析材料。重点回答：

1. 目前能确认哪些失败事实，哪些只是推断？对 Qwen、ds-Flux 的归因是否有足够证据？
2. 当前 `2.0.0` 工作流包架构与服务器 `34c` 提交之间有哪些功能差异？哪些应移植、改写或拒绝照搬？
3. 如何以最少风险解决评测超时填充、未执行 workload 被伪装成失败、证据迟迟不落盘等问题？
4. Failure Analyzer、LLM Router、设备池应按什么顺序实施？各自最小接口、状态语义和验收条件是什么？
5. 需要补采哪些 runtime/provider/NPU 证据，才能判断 API 超时、模型输出/收敛问题、候选 kernel 问题和设备环境问题？

请在分析中引用当前源码路径及行号；如不能访问报告中服务器或 NPU-KernelBench checkout，请明确标注，不能将未检查内容说成已验证。

## Git 基线与提交关系

已通过 GitHub API 核对：

- 当前本地分支：`2.0.0`
- 本地 HEAD：`3c8b1413f8c30302a95a4e52322a7e455caef6be`，`feat(dsh): compile YAML into reusable workflow packages`
- GitHub `origin/2.0.0`：同为 `3c8b1413f8c30302a95a4e52322a7e455caef6be`。本地已推送；工作区仅有未跟踪本地材料，不纳入此次分析。
- 服务器/报告记录的提交：`34c7eef2c78af9fce690149df9b1be5ccfd3c1f8`，`feat(dsh): make dsh the default operator runtime`
- 两个提交共同父提交：`cc9a41e18d1c3f6cef4a47f542565425eeaede11`；`34c` 与 `3c8` 各自有一个独有提交，属于分叉。**不能因 SHA 前缀或日期简单断言一方包含另一方。**

建议审查以当前 `origin/2.0.0` 的 `3c8` 为目标主线，同时逐项审查 `34c` 的功能；不要未经审查直接将整个提交合并或切换到它。

## 两个提交的主要内容

### `3c8b1413`（当前主线）

- 把 YAML 编译为显式 JS 和版本化 workflow package；运行时不再使用旧的通用 YAML 状态机解释器。
- 新增 workflow package 完整性/依赖检查、阶段输入输出 schema 校验和相关测试。
- 删除 `plugins/dsh-seam/src/fixed-script.ts`；当前相关实现位于 `workflow-compiler.ts`、`compiled-runtime.ts`、`workflow-package.ts`、`controller.ts` 等。

### `34c7eef2`（服务器提交）

- 添加 `src/scripts/run_operator_dsh.sh`，让 dsh 成为算子入口默认运行时；仍保留 `SEAM_RUNTIME=opencode` 回退。
- 添加 workflow `wait` action，避免 driver 通过 LLM 轮询状态、过早结束活动中的 workflow。
- 添加 `history.ts`，对 loop history 做语义筛选和截断；修改旧 `fixed-script.ts` / `engine.ts`。
- 修改 solution source/entry point 打包路径、约束路径支持、评测临时目录处理及 prompt budget 行为；prepare/fixer timeout 改为 7200 秒。
- 该提交**没有**把 fill-all 改为 `executed / timed_out / not_run`，因此报告最关键的评测语义问题仍待解决。

## 当前源码中值得优先核对的点

- `plugins/dsh-seam/src/compiled-runtime.ts`：loop history 当前通过 `JSON.parse(JSON.stringify(current.step_outputs))` 保存全部 step outputs。请分析是否会膨胀 prompt、丢失可用证据，以及如何适配 `34c` 的 history 摘要能力。不要简单移植固定字段白名单而静默丢弃新字段；优先考虑紧凑摘要 + 可追溯 artifact/path 引用。
- `plugins/dsh-seam/src/controller.ts`、`src/plugin.ts`、`plugins/dsh-seam/src/dsh-runtime.ts`：分析当前 workflow run、session 生命周期与 wait/cancel 的行为，并评估 `34c` 的 `wait` 应如何进入新架构。
- `plugins/dsh-seam/python/bridge.py`：当前 prompt 超过 `max_prompt_bytes` 会失败且不静默截断。`34c` 将其改成 advisory；请评估这是否会把超长 prompt 直接交给 provider，是否应改成有证据引用、明确告警和安全预算的策略。
- `plugins/dsh-seam/src/phase-executor.ts`、`src/contracts.ts`：当前已有阶段输入输出 schema 验证及有限输出纠正。分析 `invalid_json_output` 是否应该走阶段契约修复，而不是被归为算子代码失败。
- `src/core/operator_benchmark.py`、`src/core/operator_solution.py`、`src/scripts/run_operator.sh`：当前主线存在这些文件，但尚无 `run_operator_dsh.sh`。对照 `34c` 的路径和临时目录改动，分析哪些与报告中的故障直接相关，哪些应单独处理。
- 报告计划引用 `fixed-script.ts`，但当前主线已经删除它。后续 Router/状态接入点必须按编译后的 workflow package 架构重新定位。

## 失败报告的证据边界

原报告声称：11 月 6 日各模型各测 4 个算子（每 level 一个）；ds 为 3/4 通过、Qwen 为 0/4；Flux 每轮 60 条同时间戳 `TIMEOUT` 实为首个 workload 卡死后批量补齐；11 月 9 日复现停在 kernel `launching` 且没有 `launch submitted`。这些是**报告提供的实验事实/解释**，当前本地仓库没有报告列出的服务器 traces、完整进程日志和 NPU 复现材料，不能据此声称已独立复现。

请特别检查：

- 4 个样本/模型不足以得出模型总体能力结论；需要区分“本批工作流失败归因”与“模型整体能力评价”。
- `phase_timeout` 不能单独证明模型推理/收敛问题；API 排队、请求耗时、工具阻塞、会话状态及 phase watchdog 都需要 telemetry 支持。
- 同一时间戳、`launching` 而无 `submitted` 是强线索但不是最终因果证明；请列出需要的反证或额外 probe。
- NPU `Alarm`、PID 残留、磁盘压力等信号可能改变复现实验条件；设备隔离、lease 和 reset 必须考虑互斥、失效租约及旧进程仍运行的情形。

## 设计约束

- 超时不得把未执行 workload 伪造为 `TIMEOUT`；必须把真实执行状态和未执行状态分开，并保证超时/崩溃时证据仍能落盘。
- 用户反对用逐字节/特定字符串检测来识别单一错误并专用修补。不要提出错误字符串规则表或按某个错误文案触发源码重写。
- Python API 固定白名单式静态检查可以接受；failure subtype 可开放，但应由证据与分析产生，不能让模型任意调用危险动作。
- Analyzer 应先采证和规范化，Router 应先 shadow；模型不应直接改 state、代码或设备。
- 不能默认把所有失败派给 `fix_operator`；分类不确定时要能拒绝自动路由或请求更多证据。
- 保留墙钟超时作为安全边界。报告提议删除 step/token cap，但该提议需要结合 API/运行成本和 dsh 限制单独论证，不能当成已批准的实现要求。

## 期待的交付

请提交一份**分析结论和修订版计划**，而非代码 patch，至少包括：

1. 当前主线/服务器提交的差异清单，注明需要移植、改写、暂缓或不采纳的内容。
2. 根因树：已证实、较强推断、待验证三档，并列出每个待验证项的最小取证动作。
3. 修订后的阶段计划；每阶段写清改动边界、产物、验收标准、回滚点和依赖仓库。
4. 必需 runtime telemetry 字段及建议落盘位置，尤其是 provider 请求/工具调用耗时、取消/超时来源、workload 执行序号、进程退出状态、设备健康和 artifact 写盘状态。
5. 风险项：错误路由、prompt/history 丢证据、自动 reset、临时目录容量、仓库分叉集成冲突等。

请不要执行 merge/cherry-pick、不要改动报告或源码、不要 commit/push；先给出审查结论供用户决策。
