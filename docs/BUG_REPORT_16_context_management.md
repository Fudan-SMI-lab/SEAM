# SEAM Bug #16 修复报告：Phase-5 迭代用尽 LLM 上下文

## 元信息

| 项目 | 内容 |
|---|---|
| Bug 编号 | #16 |
| Bug 标题 | Phase-5 迭代用尽 LLM 上下文 |
| 严重级别 | 高（依据影响评估：迭代中断、命令重发、上下文浪费，直接影响 Phase-5 主流程） |
| 修复提交 | `ee4f4e6`（单一提交，父提交 `4092762`） |
| 提交信息 | fix(context): implement Phase 5 context budget/snapshot/rotation for #16 |
| 作者 | ZihangZ |
| 日期 | 2026-08-06 02:14:25 +0800 |
| 变更规模 | 17 个文件，+2523/-83 |
| 涉及模块 | context_management.py（新增）、config_loader.py、framework_defaults.yaml、session_registry.py、harness/session/manager.py、workflow_executor.py、repair_loop.py、4 个 repair_dependency_fixer*.md 提示模板、6 个测试文件 |
| 审查状态 | 四路并行审查全部 APPROVE（F1 Plan Compliance / F2 Code Quality / F3 Real Manual QA / F4 Scope Fidelity） |

**概述**：SEAM 的 Phase-5 修复循环在多轮迭代中把循环分析、会话历史与 repair 尝试记录不断追加进发给 LLM 的消息序列，全程没有预算控制、快照落盘与会话轮换，最终把上下文窗口撑满。本次修复引入"预算-快照-轮换"三层保护，用单一提交 `ee4f4e6` 落在 `4092762` 之上，共改动 17 个文件、净增 2523 行、净删 83 行。修复后全量测试保持 2913 passed / 14 failed 的环境性预存基线，无新增失败，四路并行审查全部通过。

---

## 一、问题描述

Phase-5 的修复循环依赖 LLM 进行多轮迭代。每一轮迭代都会把上一轮的循环分析结果、会话历史消息以及此前的 repair 尝试记录追加到发送给模型的消息序列中。这些消息在轮次之间只增不减，因为流程里不存在任何对上下文用量的评估、限制或清理机制。轮次一多，累积量便逼近甚至超过上下文窗口容量，模型调用随即失败。

原始症状可归纳为三点：

- **上下文窗口耗尽**：多轮迭代后，循环分析与会话历史消息的总量超过 LLM 上下文窗口上限，调用失败；
- **迭代中断**：处于中段的迭代被迫中止，后续轮次无法继续执行；
- **命令重发与上下文浪费**：中断后需要人工重新下发命令，而此前累积的历史消息仍占据窗口，模型可用的有效工作空间被进一步压缩。

一句话概括：Phase-5 在没有"用量预算、状态快照、会话轮换"任何一层保护的情况下无限累积上下文，最终把窗口撑爆。

## 二、根因分析

**根因**：Phase-5 迭代流程缺少上下文管理机制。具体而言，三件必要的事一件都没做：没有在每次迭代前估算当前上下文用量，没有在用量接近上限时压缩消息或把会话状态落盘为快照，也没有在达到上限时轮换到新会话。循环分析与完整会话历史因此无限累积，直接导致窗口耗尽。

**触发条件**（满足其一即可）：

- 迭代轮次足够多，历史消息逐轮堆积；
- 单轮产生的分析文本或会话历史体量过大；
- compaction 恢复场景下反复重发同一命令，放大了用量累积。

**影响**：

- 迭代中断：上下文耗尽使 Phase-5 迭代无法完成，核心工作流受阻；
- 命令重发：中断后的命令需人工重发，操作成本上升；
- 上下文浪费：大量陈旧历史占据窗口，模型可用的有效上下文被挤占，输出质量随之下降。

## 三、修复方案（架构设计）

设计思路：把上下文用量从"完全失控"变成"可估算、可降级、可轮换"。预算（budget）负责决策，快照（snapshot）负责保状态，轮换（rotation）负责腾空间，三者串成一条闭环保护链。

### 3.1 六个核心组件

| 组件 | 变更 | 职责 |
|---|---|---|
| context_management.py | 新增 352 行 | 快照与预算核心：ContextSnapshot、write_snapshot_atomic、ContextBudgetEstimator |
| config_loader.py + framework_defaults.yaml | +162 / +11 | ContextManagementConfig 冻结数据类与严格字段校验 |
| session_registry.py | +187 | ContextExhaustedError 与 SessionRegistry.rotate 双层原子轮换 |
| harness/session/manager.py | +128 | 有界恢复与结构化 context_exhausted 终止 |
| workflow_executor.py | +276 | 工作流侧接入预算/快照/轮换/恢复/历史 6 个方法 |
| repair_loop.py | +183 | 功能门控与修复提示模板的有界历史注入 |

各组件职责详述如下。

**1. context_management.py（新增 352 行）**

- `ContextSnapshot`：快照数据结构，集合类字段使用 `default_factory`，携带 `schema_version`；写入受 `SNAPSHOT_MAX_BYTES=100_000` 上限保护；
- `write_snapshot_atomic`：快照原子写入，避免落盘半截文件；
- `ContextBudgetEstimator`：预算估算器，compact 阈值 72000、rotate 阈值 88000，即 0.72/0.88；`tokens_used` 按 input + output + reasoning 合计；数据缺失时 `estimated=True` 有界降级，永不抛异常；
- 定义 `CONTEXT_SNAPSHOT_FILENAME` 与 `LOOP_HISTORY_FILENAME`；
- 导入仅限 stdlib 与现有 core 模块（atomic_file、secret_redaction、config_loader），零新增第三方依赖。

**2. config_loader.py + framework_defaults.yaml**

`ContextManagementConfig` 冻结数据类；严格字段强制：`_bool_field`、`_positive_int_field`（拒绝 bool-as-int）、`_ratio_field`；并做 compact < rotate 关系校验。配置错误在加载阶段即被拒绝，而不是运行到半途才暴露。

**3. session_registry.py（+187 行）**

`ContextExhaustedError` 异常，共 7 个属性，包含 old/new session_id；`SessionRegistry.rotate` 双层原子轮换，用一把 `threading.Lock` 同时保证 registry._cache 与 manager._sessions 两层一致，杜绝半轮换状态。

**4. harness/session/manager.py（+128 行）**

`_max_recoveries_per_command` 有界恢复上限，默认 0，支持环境变量 `SEAM_MAX_RECOVERIES_PER_COMMAND` 覆盖；compaction 中间态 → 有界等待 → 单次恢复，保证命令执行恰一次；达上限后以结构化 `context_exhausted` 终止；非 compaction 错误（URLError/Timeout/RuntimeError/ValueError）的重试行为保持不变。

**5. workflow_executor.py（+276 行）**

新增 6 个方法：`_enforce_loop_context_budget`、`_persist_loop_context_snapshot`、`_rotate_loop_analyzer_session`、`_recover_exhausted_sub_workflow_command`、`_persist_loop_history`、`_bounded_loop_history`。3 处 `_format_*` 调用点接入有界历史，函数体本身 0 改动；`max_iterations` 0 改动。T7 附带修复 3 处 `isinstance(artifact_dir, (str, PurePath))` 严格类型检查，均带 rationale 注释。

**6. repair_loop.py（+183 行）**

`_context_budget_active` 功能门控，仅在配置含 `"context_management"` 时启用；4 个 repair_dependency_fixer*.md 提示模板各加入恰 3 行 `## Previous Repair Attempts\n{history_summary}` 占位符并填充；按角色轮换；二次溢出时结构化终止。

### 3.2 协作方式与保护链

| 阶段 | 动作 | 主导组件 |
|---|---|---|
| 每次迭代前 | 估算上下文用量 | ContextBudgetEstimator（context_management.py） |
| 用量低于 0.72 | 正常继续（NORMAL） | workflow_executor._enforce_loop_context_budget |
| 用量达到 0.72 | 压缩（COMPACT），先原子落盘快照 | ContextSnapshot + write_snapshot_atomic |
| 用量达到 0.88 | 轮换（ROTATE），快照交接，新 session 首条消息即快照 | SessionRegistry.rotate |
| 压缩后命令执行 | compaction 中间态 → 有界等待 → 单次恢复 | manager._recover_from_compaction |
| 轮换后再次溢出 | 结构化 context_exhausted 终止，不再轮换 | ContextExhaustedError |
| 修复循环 | 有界历史注入提示模板 | repair_loop._context_budget_active |

**预算-快照-轮换的配合方式**：

- **预算**：每次迭代前由 `ContextBudgetEstimator` 估算用量，按两级阈值决策。低于 72000 正常继续（NORMAL）；达到 72000 触发压缩（COMPACT）；达到 88000 触发轮换（ROTATE）。阈值可配置，估算数据缺失时降级为 `estimated=True`，不会抛异常中断流程。
- **快照**：压缩或轮换之前，`ContextSnapshot` 先把关键会话状态原子落盘。轮换后新 session 的第一条消息就是快照交接内容，状态不因轮换而丢失。`SNAPSHOT_MAX_BYTES=100_000` 把单次快照写入钳制在安全上限内。
- **轮换**：`SessionRegistry.rotate` 用一把锁同时更新注册表与管理器两层视图，保证原子一致。轮换后若新 session 再次溢出（二次溢出），不再无限轮换，直接以结构化 `context_exhausted` 终止，把异常变成可识别、可捕获的信号。
- **三者配合成链**：估算决定动作 → 快照保住状态 → 压缩/轮换释放空间 → 命令经有界恢复保证执行恰一次。保护链首尾相接，窗口耗尽从"不可预测的中断"变为"可预测、可恢复、可终止"。

## 四、实施明细（T1 至 T11）

### 4.1 变更文件清单

| 文件 | 变更 | 说明 |
|---|---|---|
| context_management.py | 新增 352 行 | 快照与预算核心模块 |
| config_loader.py | +162 | ContextManagementConfig 与严格字段校验 |
| framework_defaults.yaml | +11 | 默认配置项 |
| session_registry.py | +187 | ContextExhaustedError 与双层原子轮换 |
| harness/session/manager.py | +128 | 有界恢复与结构化终止 |
| workflow_executor.py | +276 | 预算/快照/轮换/恢复/历史 6 个方法 |
| repair_loop.py | +183 | 功能门控与提示模板占位符填充 |
| 4 个 repair_dependency_fixer*.md | 各 +3 行 | `## Previous Repair Attempts\n{history_summary}` 占位符 |
| review_gate_state_cases.py | 改造 | guard 相关断言更新（含 rationale 注释） |
| test_phase_runner.py | 改造 | 断言更新 |
| test_repair_loop.py | 新增 6 个测试 | task8 用例 + 15 处 LOCK 注释 |
| test_session_event_review_cases.py | 新增 3 个测试 | task1 复现与 task6 恢复用例 |
| test_session_manager_guard.py | 改造 | task4/task6 相关断言 |
| test_workflow_executor.py | 新增 5 个测试 | task7 用例 |

### 4.2 任务执行情况

| 任务 | 内容 | 结果 |
|---|---|---|
| T1 | 记录基线并编写复现/恢复测试。全量 pytest 记录基线；test_session_event_review_cases.py 新增 test_reproduction_compaction_recovers_without_reposting_same_session、test_compaction_recovery_does_not_consume_configured_retries、test_env_override_increases_compaction_recovery_budget | 基线 2913 passed / 14 failed（环境性预存） |
| T2 | 配置接入。framework_defaults.yaml +11 行（9 个 key）；config_loader.py +162 行，新增 ContextManagementConfig 冻结数据类与严格字段强制（_bool_field、_positive_int_field 拒绝 bool-as-int、_ratio_field）及 compact < rotate 关系校验 | 现场验证：valid 加载 / invalid ratio 抛错 / compact>=rotate 抛错 |
| T3 | 快照模块。新建 context_management.py（352 行），ContextSnapshot（default_factory 集合、SNAPSHOT_MAX_BYTES=100_000、schema_version）与 write_snapshot_atomic 原子写 | 快照 roundtrip 通过 |
| T4 | 会话轮换。session_registry.py +187 行，ContextExhaustedError（7 属性含 old/new session_id）与 SessionRegistry.rotate 双层原子轮换 + threading.Lock | 轮换后 _active 为空，双层一致 |
| T5 | 预算估算器。ContextBudgetEstimator，compact 72000 / rotate 88000（0.72/0.88），tokens_used = input+output+reasoning，数据缺失 estimated=True 有界降级 | 边界 71999/72000/88000 全部符合预期 |
| T6 | 有界恢复。manager.py +128 行，_max_recoveries_per_command（默认 0，SEAM_MAX_RECOVERIES_PER_COMMAND 可覆盖）；compaction 中间态 → 有界等待 → 单次恢复，命令执行恰一次；结构化 context_exhausted 终止；非 compaction 错误重试不变 | 3 个新增测试通过 |
| T7 | 工作流接入。workflow_executor.py +276 行，新增 6 个方法；3 处 _format_* 调用点接入有界历史（函数体 0 改动）；3 处 isinstance(artifact_dir, (str, PurePath)) 严格类型检查 + rationale 注释；max_iterations 0 改动 | 5 个新 test_task7_* 通过 |
| T8 | 修复循环。repair_loop.py +183 行，_context_budget_active 功能门控（配置含 "context_management"）；4 个提示模板各加 3 行占位符并填充；按角色轮换；二次溢出结构化终止 | 6 个新 test_task8_* 通过 |
| T9 | 脱敏与证据纪律。.gitignore 已预覆盖 /.sm-artifacts/、**/.sm-artifacts/、*.log；脱敏复用 core/secret_redaction.redact_json_value；17 个提交文件 secrets 扫描 0 命中 | 0 命中 |
| T10 | 全量验证。全量 pytest、compileall、collect-only、grep 扫查，定向测试证据收集 | 详见第五章 |
| T11 | 单提交收口。单一提交 `ee4f4e6` 直接落在父提交 `4092762` 上；4 个 .bak（dashboard.py.bak、ui_events.py.bak、workflow_executor.py.bak、e2e_observer.py.bak）保持未跟踪未提交 | 17 文件 +2523/-83 |

## 五、验证与测试

### 5.1 全量 pytest

执行命令：`cd src && python -m pytest -q -m "not opencode and not docker and not e2e"`

结果：**2913 passed / 14 failed**。14 个失败全部为环境性预存基线，与修复前基线一致，无新增失败：

| 失败分组 | 数量 |
|---|---|
| py38 imports | 5 |
| resource_manifest | 4 |
| review_observability | 3 |
| sqlite_provider | 1 |
| v3_environment_output | 1 |

### 5.2 定向测试统计

| 测试项 | 结果 |
|---|---|
| 6 个变更测试文件 | 367 passed（5.05s） |
| QA03 repair_loop | 70/70 |
| QA04 compaction | 6/6 |
| QA05 guard + phase_runner | 103/103 |
| QA07 估算器边界（71999 NORMAL / 72000 COMPACT / 88000 ROTATE） | 4/4 |
| QA08 快照 roundtrip（190,675B 压缩到 383B，recent_turns 丢弃） | PASS |
| QA09 secrets 扫描（17 个提交文件） | 0 命中 |
| QA10 边界用例（默认值、auto → 128000、再耗尽结构化终止、max_recoveries=1） | 4/4 |
| QA11 集成（估算器 / 快照 / 双层注册） | 3/3 |

断言注释统计：新增/改造断言共含 **34 处单行理由注释**（rationale 19 处 + LOCK 15 处），确保每处行为变更都有可追溯的一行解释。

### 5.3 静态检查

| 检查项 | 结果 |
|---|---|
| python -m compileall -q src | exit 0 |
| pytest --collect-only | 3056 tests clean |
| grep：type:ignore | 0 |
| grep：bare except | 0 |
| grep：except-pass | 0 |
| grep：stray print | 0 |
| grep：TODO/FIXME | 0 |
| grep：未用 import | 0 |

### 5.4 验收矩阵

**Must Have（7/7 全部满足）**：

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | compaction 中间态 → 有界等待 → 单次恢复，命令执行恰一次 | PASS |
| 2 | 恢复上限 → 结构化 context_exhausted | PASS |
| 3 | 轮换原子性双层一致 | PASS |
| 4 | 新 session 首条消息 = 快照交接 | PASS |
| 5 | token 缺失时估算器有界降级 | PASS |
| 6 | 34 处断言更新含一行理由注释 | PASS |
| 7 | 非 compaction retry 行为不变 | PASS |

**Must NOT Have（10/10 全部守住）**：

| # | 禁区 | 结果 |
|---|---|---|
| 1 | .bak 未提交 | PASS |
| 2 | 无新增第三方依赖 | PASS |
| 3 | _is_compaction_payload 语义不改 | PASS |
| 4 | 不因非 compaction 原因轮换 | PASS |
| 5 | 不改 max_iterations / 迭代递减 / _sqlite_message_completion_state | PASS |
| 6 | 不建通用系统 | PASS |
| 7 | send_command 非 compaction 返回契约不变 | PASS |
| 8 | _format_* 逻辑不改（仅调用点接入有界历史） | PASS |
| 9 | opencode_contract_* 不触碰 | PASS |
| 10 | 不修无关 flaky / 不更新文档 | PASS |

## 六、审查

修复提交 `ee4f4e6` 经受四路并行审查，全部 APPROVE：

| 审查路 | 审查要点 | 结论 |
|---|---|---|
| F1 Plan Compliance（oracle） | Must Have [7/7] \| Must NOT Have [10/10] \| Tasks [11/11] | APPROVE |
| F2 Code Quality | Build [PASS] \| Collect [PASS] \| Tests [5 pass/0 fail] \| Files [16 clean/1 issues] | APPROVE |
| F3 Real Manual QA | Scenarios [12/12 pass] \| Integration [5/5] \| Edge Cases [12 tested] | APPROVE |
| F4 Scope Fidelity | Tasks [11/11 compliant] \| Contamination [CLEAN] \| Unaccounted [CLEAN] | APPROVE |

F2 的 1 个 issues 即遗留问题 1（repair_loop.py:982 死参数），经评估为非阻塞项，详见第七章。

约束遵守情况：

- 4 个 .bak 保持未跟踪未提交；
- 无新增第三方依赖；
- 未触碰 opencode_contract_*、max_iterations、_sqlite_message_completion_state、_is_compaction_payload 语义；
- security_audit 0 命中（无凭据、无权重、无未脱敏日志）。

## 七、已知遗留问题

以下 3 项均非阻塞，不影响交付：

1. **死参数**：repair_loop.py:982 `_rotate_base_session` 的 `current_session_id` 参数在函数体内未使用（新会话由角色与计数器即可完全确定），3 处调用点仍在传参。建议后续移除该参数，纯清理项。
2. **性能观察**：`redact_sensitive_text` 对单字符串为 O(n²)，20k 字符约需 14.9s。单个超大 turn 字符串会拖慢快照序列化，属性能问题而非正确性失败。`SNAPSHOT_MAX_BYTES=100_000` guard 仍生效，写入仍被钳制在上限内。
3. **证据缺口**：task-2-* 证据文件未落盘。config 校验改为现场验证（valid 加载 / invalid ratio 抛错 / compact >= rotate 抛错），功能本身验证充分。MINOR 缺口。

## 八、回滚说明

在 /home/yiding/SEAM 下执行：

```bash
git revert ee4f4e6
```

注意事项：

- 执行前先确认工作树状态。当前仅 4 个计划内的 .bak 未跟踪文件，revert 不受其影响；
- `git revert` 会生成一个新的反向提交，不修改既有历史。若确需丢弃历史可执行 `git reset --hard 4092762`，但该操作会连带移除 `4092762` 之后的所有提交，请谨慎使用；
- revert 后 context_management.py 等新增文件被删除、各模块改动被还原，Phase-5 上下文保护随之失效。回归前请对照 .sm-artifacts/bug16-20260805-170012/full_pytest 基线（2913 passed / 14 failed 环境基线）确认无新增失败；
- 若只是临时停用功能，优先走配置而非代码回滚：repair_loop 的 `_context_budget_active` 以配置中是否含 `"context_management"` 门控，移除该配置项即可停用，无需 revert。

## 附录：证据位置

| 位置 | 说明 |
|---|---|
| /home/yiding/.omo/notepads/bug16-context-management/ | 完整审查记录（issues.md、decisions.md、learnings.md、problems.md） |
| /home/yiding/.omo/evidence/ | 任务证据，30 个落盘文件（计划口径 31，task-2-* 未落盘，见遗留问题 3） |
| /home/yiding/.omo/evidence/final-qa/ | 最终 QA 证据，12 个文件 |
| /home/yiding/SEAM/.sm-artifacts/bug16-20260805-170012/ | manifest、full_pytest、security_audit（0 命中）、reproduction_evidence.md |

evidence/ 下按任务分布：

| 任务 | 文件 |
|---|---|
| task-1 | task-1-baseline-pytest.txt、task-1-reproduction-pass.txt |
| task-3 | task-3-snapshot-atomic.txt、task-3-snapshot-guard.txt、task-3-snapshot-roundtrip.txt、task-3-snapshot-validation.txt、task-3-snapshot-write.txt |
| task-4 | task-4-exhausted.txt、task-4-invalidate.txt、task-4-rotate.txt |
| task-5 | task-5-estimator-estimated.txt、task-5-estimator-malformed.txt、task-5-estimator-states.txt |
| task-6 | task-6-compaction-completes.txt、task-6-context-exhausted.txt、task-6-noncompaction-retry.txt |
| task-7 | task-7-compact-snapshot.txt、task-7-exhausted-terminate.txt、task-7-rotate-handoff.txt |
| task-8 | task-8-analyzer-rotate.txt、task-8-exhausted-terminate.txt、task-8-history-bounded.txt |
| task-9 | task-9-gitignore.txt、task-9-redaction-fidelity.txt、task-9-secrets-clean.txt |
| task-10 | task-10-full-pytest.txt、task-10-static.txt、task-10-targeted.txt |
| task-11 | task-11-commit.txt、task-11-staging.txt |

final-qa/ 下 12 个文件：01_full_checklist.txt、02_task7_budget_snapshot_rotate.txt、02b_no_magicmock_recreation.txt、03_repair_loop.txt、04_compaction.txt、05_rotate_invalidate.txt、06_config_validation.txt、07_estimator_boundaries.txt、08_snapshot_roundtrip.txt、09_secrets.txt、10_edge_cases.txt、11_integration.txt
