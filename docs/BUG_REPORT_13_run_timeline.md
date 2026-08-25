# SEAM Bug #13 修复报告：迁移报告时间字段显示 `—` 占位

## 元信息

| 项目 | 内容 |
|---|---|
| Bug 编号 | #13 |
| Bug 标题 | 部分迁移报告测试时间缺失：环境结束、迁移起止与耗时均显示 `—` |
| 严重级别 | 高（依据影响评估：报告时间事实缺失导致无法可靠统计后续测试结果，直接影响 Phase-6 报告可信度） |
| 修复提交 | `88bcb4c`（单一提交，父提交 `7f7a996`） |
| 提交信息 | fix(executor): persist run timeline for #13 |
| 作者 | ZihangZ |
| 日期 | 2026-08-06 12:14:59 +0800 |
| 变更规模 | 8 个文件，+437 |
| 涉及模块 | workflow_executor.py、phase_runner.py、3 个 phase_6_report*.md 提示模板、phase_6_reports.json、validate_reports.py、test_run_timeline.py（新增） |
| 审查状态 | 定向回归（359 passed）+ 全量回归（2934 passed / 14 预存失败）已验证，未执行四路并行审查 |

**概述**：SEAM 的 Phase-6 迁移报告中，任务起始时间存在，但环境结束、迁移起止与耗时字段均显示 `—` 占位。根因是运行时时间事实从未被生产接线：`TelemetryBridge` 只在测试中使用，生产 `workflow_executor` 不调用其 `on_phase_start/on_phase_end`，LLM 在无权威时间源的情况下自拟了 `—` 占位。本次修复建立"统一运行时钟 + 阶段时间事实"，在阶段开始/结束时以 ISO-8601 UTC 时间戳持久化 `run_timeline.json`，报告提示模板只消费持久化事实并显式禁止 `—` 占位。用单一提交 `88bcb4c` 落在 `7f7a996` 之上，共改动 8 个文件、净增 437 行。修复后全量测试 2934 passed / 14 预存失败基线，无新增失败。

---

## 一、问题描述

Phase-6 生成的迁移报告中，时间字段出现两种不一致的状态：

- 任务起始时间（run started）能够出现；
- 但环境结束（environment ended）、迁移起止（migration start/end）以及各阶段耗时（duration）均显示 `—`（em-dash 占位符）。

`—` 不是任何合法时间戳格式，报告消费方无法解析、无法计算耗时，Phase-6 报告作为后续统计依据的价值被破坏。

## 二、根因分析

**根因**：运行时时间事实从未被生产接线。具体拆为两层：

1. **TelemetryBridge 未在生产接线**：`src` 下所有 `TelemetryBridge(` 实例化点均在 `tests/` 目录，生产代码路径 `workflow_executor` 不调用 `on_phase_start` / `on_phase_end`，因此阶段时间从未被采集；
2. **LLM 自拟占位**：`—` 在 `src` 源码中零匹配，说明它不是任何代码生成的合法值，而是 LLM 在提示模板没有给出权威时间源时自行编造的占位符。

两件事叠加，报告出现"起始时间有、其余时间全为 `—`"的畸形状态。

## 三、修复方案（架构设计）

设计思路：把时间事实从"LLM 自拟"变成"执行器持久化、报告只消费"。建立统一运行时钟，阶段开始/结束时记录 ISO-8601 UTC 时间戳并落盘 `run_timeline.json`；提示模板声明 `{run_timeline}` 为唯一权威时间源，且显式禁止 `—` 占位；schema 声明 `run_timeline` 为 optional 字段以保持向后兼容。

### 3.1 五个核心变更点

| 组件 | 变更 | 职责 |
|---|---|---|
| workflow_executor.py | +72 | 统一运行时钟：`_run_started_at`/`_run_ended_at`、阶段 `started_at`/`ended_at`/`duration_seconds`、`_build_run_timeline()`/`_persist_run_timeline()`、守卫式 `telemetry_bridge.on_phase_start/on_phase_end` |
| phase_runner.py | +5 | `run_phase_6` 的 prompt_context 注入空 `run_timeline` 默认结构（修复 `{run_timeline}` 占位符 KeyError 回归） |
| 3 个 phase_6_report*.md | 各 +5 | "## Time Facts" 节：`{run_timeline}` 为权威时间源、ISO-8601 UTC、禁止 `—` |
| phase_6_reports.json | +34 | optional `run_timeline` 属性（不在 required 中） |
| validate_reports.py | +21 | 校验 `run_timeline.phases[].started_at/ended_at` 必须为真实字符串且非 `\u2014` |

各组件职责详述如下。

**1. workflow_executor.py（+72 行）**

- 新增 `from datetime import datetime, timezone` 导入与 `_run_started_at` / `_run_ended_at` 状态字段；
- `_build_run_timeline()`：从 `phase_results` 汇总各阶段 `phase_id/status/started_at/ended_at/duration_seconds`，连同 `run_started_at/run_ended_at` 组装时间线字典；
- `_persist_run_timeline()`：以 `atomic_write_bytes` 将时间线原子写入 `output_dir/run_timeline.json`，写失败仅记 error 日志不阻断主流程；
- `execute()` 入口记录 `run_started_at = datetime.now(timezone.utc).isoformat()`，循环结束后记录 `run_ended_at` 并做最终持久化；
- 每阶段开始记录 `started_at`（ISO UTC）与 `start_mono = time.monotonic()`；dispatch 分支与常规分支结束均计算 `ended_at` 与 `duration_seconds = round(duration_mono, 3)`；
- `telemetry_bridge.on_phase_start(phase.id)` / `on_phase_end(phase.id, status, duration_mono)` 以守卫式调用（try/except + debug 日志），bridge 为 None 时安全跳过；
- `_inject_llm_phase_specific_context` 对 phase_6 通过 `input_ctx.setdefault("run_timeline", self._build_run_timeline())` 注入真实时间线。

**2. phase_runner.py（+5 行）**

`run_phase_6` 的 prompt_context 新增 `run_timeline` 空结构默认值：

```python
"run_timeline": {
    "run_started_at": None,
    "run_ended_at": None,
    "phases": [],
},
```

修复一个实现过程中发现的回归：`phase_6_report.md` 新增 `{run_timeline}` 占位符后，走 PhaseRunner 路径（不经 WorkflowExecutor）渲染模板会因占位符无值而 KeyError；注入默认结构后占位符始终可渲染，真实时间线由 WorkflowExecutor 覆盖注入。

**3. 三个提示模板（各 +5 行）**

`phase_6_report.md`、`phase_6_report_musa.md`、`phase_6_report_ppu.md` 均新增 "## Time Facts" 节，声明：

- 以 `{run_timeline}` 为运行与阶段时间的唯一权威来源；
- `run_timeline.phases` 每项含 ISO-8601 UTC 的 `started_at`/`ended_at`（如 `2026-08-06T00:00:00+00:00`）与 `duration_seconds`，`run_started_at`/`run_ended_at` 标记整个运行；
- 在操作日志与摘要中记录来自 `run_timeline` 的真实时间戳，**禁止**用 `—` 等占位值填充 `started_at`/`ended_at`。

**4. phase_6_reports.json（+34 行）**

新增 optional `run_timeline` 属性：`run_started_at`/`run_ended_at` 为 string，`phases` 为对象数组（每项含 `phase_id`/`status`/`started_at`/`ended_at`/`duration_seconds`）。`run_timeline` **不在** `required` 列表中，旧报告结构不受影响。

**5. validate_reports.py（+21 行）**

`run_timeline` 校验逻辑：

- `run_timeline` 缺失或为 None 时不报错（向后兼容）；
- 存在时必须为 dict，`phases` 必须为 list；
- 每个 phase 条目必须为 dict；
- `started_at`/`ended_at` 必须为非空字符串且不等于 `\u2014`（em-dash），否则报 "must be a real ISO-8601 UTC timestamp"。

## 四、实施明细

### 4.1 变更文件清单

| 文件 | 变更 | 说明 |
|---|---|---|
| src/core/workflow_executor.py | +72 | 统一运行时钟 + 时间线构建/持久化 + 守卫式 telemetry 接线 + phase_6 注入 |
| src/core/phase_runner.py | +5 | run_phase_6 prompt_context 注入空 run_timeline 默认结构 |
| src/prompts/phase_6_report.md | +5 | Time Facts 权威时间源声明 |
| src/prompts/phase_6_report_musa.md | +5 | 同上（MUSA 变体） |
| src/prompts/phase_6_report_ppu.md | +5 | 同上（PPU 变体） |
| src/schemas/phase_6_reports.json | +34 | optional run_timeline 属性 |
| src/validators/validate_reports.py | +21 | run_timeline 时间事实校验 |
| src/tests/test_run_timeline.py | 新增 290 行 | 13 个测试（B2-B4 全部契约） |

### 4.2 测试用例清单（test_run_timeline.py，13 个）

| 测试 | 契约 |
|---|---|
| test_telemetry_bridge_phases_recorded_after_execute | execute 后 bridge 记录了各阶段 start/end |
| test_phase_results_contain_start_and_end_timestamps | phase_results 含 started_at/ended_at/duration_seconds |
| test_run_timeline_json_persisted_after_each_phase | 每阶段结束后 run_timeline.json 落盘且含该阶段 |
| test_run_timeline_records_failed_phase_with_ended_at | 失败阶段同样记录 ended_at；execute 以 `complete` 状态正常返回 |
| test_phase6_context_includes_run_timeline | phase_6 的 input_ctx 含 run_timeline 且含既有阶段事实 |
| test_phase6_prompts_declare_time_contract | 3 个提示模板均含 run_timeline/started_at 契约（参数化 3 次） |
| test_validate_reports_accepts_valid_run_timeline | 合法 run_timeline 通过校验 |
| test_validate_reports_rejects_em_dash_placeholder_time | `—` 占位时间被拒绝 |
| test_validate_reports_accepts_missing_run_timeline | 缺失 run_timeline 不报错 |
| test_schema_declares_optional_run_timeline | schema 中 run_timeline 为 optional |
| test_utc_now_iso_timezone_suffix | ISO-8601 UTC 时间戳带 +00:00 时区后缀 |

失败阶段测试中的关键断言（test_run_timeline.py:179-181）：

```python
# execute() terminates the loop via plan_next_phase on non-success/skipped
# status; the bug contract is the time facts in phase_results/timeline.
assert result["status"] == "complete"
assert executor.phase_results["phase_fail"]["status"] == "failure"
```

该断言说明：execute 循环以 `plan_next_phase` 终止（非 success/skipped 即视为完整运行），Bug #13 的契约核心是 phase_results/timeline 中的时间事实，而非运行状态码。

## 五、验证与测试

### 5.1 定向测试统计

| 测试项 | 结果 |
|---|---|
| test_run_timeline.py | 13 passed |
| test_run_timeline + test_telemetry_bridge + test_validator_engine + test_workflow_executor | 359 passed（4.54s） |
| test_phase_runner.py | 48 passed（0.71s） |

### 5.2 全量回归

执行命令：`cd src && python -m pytest -q -m "not opencode and not docker and not e2e"`

结果：**2934 passed / 14 failed / 84 skipped / 45 deselected**。14 个失败全部为环境性预存基线（与 #16 修复报告记录一致）：

| 失败分组 | 数量 |
|---|---|
| py38 imports | 5 |
| resource_manifest | 4 |
| review_observability | 3 |
| sqlite_provider | 1 |
| v3_environment_output | 1 |

对比基线：#16 修复时为 2913 passed / 14 failed，本次在测试基线之上净增 13 个用例（#11 增 8 个 + #13 增 13 个 = 2913 + 21 = 2934），无新增失败。

### 5.3 静态检查

| 检查项 | 结果 |
|---|---|
| python -m compileall -q src | exit 0 |

## 六、审查

本修复经以下验证路径确认：

- 定向回归：时间线相关 4 文件 359 passed，phase_runner 48 passed；
- 全量回归：2934 passed / 14 预存失败，与基线一致无新增失败；
- 编译检查：compileall exit 0。

未执行 #16 那样的四路并行审查（F1-F4）。已完成的验证覆盖：执行器时间线构建与持久化、telemetry 守卫接线、提示模板契约、schema 兼容性、validator 拒绝占位、失败阶段时间记录。

## 七、已知遗留问题

以下 1 项非阻塞：

1. **中断场景时间语义**：若运行中途被硬中断（进程级 kill），最终 `_run_ended_at` 持久化不会执行，`run_timeline.json` 停留在最近一次阶段持久化的状态。提示模板已要求"中断场景明确标注未完成、禁止伪造耗时"，执行器侧未增加信号处理器。属增强项而非正确性缺陷。

## 八、回滚说明

在 /home/yiding/SEAM 下执行：

```bash
git revert 88bcb4c
```

注意事项：

- 执行前先确认工作树状态。当前存在未跟踪的 docs/BUG_REPORT_16_context_management.md 与 4 个 .bak 文件，revert 不受其影响；
- `git revert` 生成新的反向提交，不修改既有历史。若确需丢弃历史可执行 `git reset --hard 7f7a996`，但会连带移除该提交之后的内容，请谨慎使用；
- revert 后 run_timeline.json 不再落盘、阶段时间不再采集，Phase-6 报告时间字段将退回 LLM 自拟。回归前对照 2934 passed / 14 预存失败基线确认无新增失败。

## 附录：证据位置

| 位置 | 说明 |
|---|---|
| /home/yiding/SEAM/src/tests/test_run_timeline.py | 13 个测试（B2-B4 契约） |
| /home/yiding/SEAM/src/core/workflow_executor.py | `_build_run_timeline`（L623-652）、`_persist_run_timeline`（L654-665）、execute 起始（L672-673）、阶段时间（L785 起）、dispatch 分支（L837 起）、run 结束（L983-984）、phase_6 注入（L1983） |
| /home/yiding/SEAM/src/core/phase_runner.py | run_phase_6 的 run_timeline 默认结构（L702-707） |
| /home/yiding/SEAM/src/prompts/phase_6_report*.md | 3 个模板的 Time Facts 节 |
| /home/yiding/SEAM/src/schemas/phase_6_reports.json | optional run_timeline |
| /home/yiding/SEAM/src/validators/validate_reports.py | run_timeline 时间事实校验 |
