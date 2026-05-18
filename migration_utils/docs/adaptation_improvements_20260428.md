# 适配框架改进记录

> 日期: 2026-04-28  
> 修改范围: `core/phase_runner.py`, `tests/e2e/e2e_test.py`, 多个 prompt 文件

---

## 第一批修改: Review Gate 修复 (01-06)

### 修复 01: Runtime evidence 字段为空

**现象**: reviewer agent 收到的指令中只有文件路径，没有实际的 stdout/stderr 日志内容。

**修复** (`core/repair_loop.py`): 新增 `_load_attempt_log_content()` 静态方法，读取最后一次 validation attempt JSON，提取 stdout/stderr/error 注入 review 上下文。

**修复** (`core/orchestrator.py`, `core/phase_runner.py`): 新增 `attempt_log_content` 参数传递链路。

### 修复 02: "files created during execution" 占位符

**现象**: `generated_artifacts` 始终为 `"(not tracked)"`。

**修复**: 从 `phase_5_review.md`、`phase_runner.py`、`orchestrator.py`、`repair_loop.py` 中全部移除 `generated_artifacts`。移除 reviewer prompt 中关于 expected artifacts 的审查问题。

### 修复 03: accept_with_warning verdict 选项

**现象**: `accept_with_warning` 导致 agent 总是偏向选择这个中间态。

**修复** (`phase_5_review.md`): verdict 从 `accept | accept_with_warning | reject` 改为 `accept | reject`。扩展 `reject` 规则：发现更优 NPU-native 方案时必须 reject 并在 `alternative_suggestions` 中说明。

### 修复 04: CPU fallback pattern 过于具体

**现象**: checklist 给出了具体代码模式（`.to('cpu')` 等），agent 只搜索这几种。

**修复** (`phase_5_review.md`): 将具体代码示例替换为抽象指导：要求 agent 全面检查任何形式的 CPU fallback 行为。

### 修复 05: CPU fallback "is it necessary" 的具体示例

**现象**: 问题中给出了具体例子，限制了 agent 思考范围。

**修复** (`phase_5_review.md`): 替换为深度思考引导，要求 agent 深度评估是否真的没有 NPU-native 替代方案。

### 修复 06: Repair history 截断

**现象**: `agent_diagnostics` 和 `fix_summary` 被截断到 80-150 字符。

**修复** (`core/repair_loop.py`):
| 字段 | 旧上限 | 新上限 |
|------|:---:|:---:|
| `fix_summary` | 150 | **500** |
| `agent_diagnostics` | 80-100 | **300-500** |

---

## 第二批修改: Prompt 冗余去除 (07-09)

### 修复 07: Phase 0-3.5 提示词中的 prior results 冗余

**现象**: Phase 0-3.5 共享同一 LLM session，但 prompt 中仍注入前序阶段的完整 JSON 序列化结果，浪费大量 token。

**修复** (`core/phase_runner.py`): 新增 `_SHARED_SESSION_PHASES` class 常量，`_build_prompt_context` 仅对非共享阶段序列化 `previous_outputs`。

### 修复 08: Phase 3/3.5 prompt 中的 Use prior results 文本

**修复** (`prompts/phase_3_entry_script.md`, `prompts/phase_35_static_validate.md`): 移除 `Use prior results from {previous_outputs}` 行。

### 修复 09: Phase 0 AscendC 编译器检测路径错误

**现象**: `which ascendc` 和 `latest/*/compiler/ascendc/` 路径在 CANN 8.0 环境中不存在，导致 `ascendc_available` 始终为 `false`。

**修复** (`prompts/phase_0_env_detect.md`): 
- 命令名: `which ascendc` → `which ccec`
- 目录路径: `latest/*/compiler/ascendc/` → `latest/compiler/ccec_compiler/`
- 补充 SDK 头文件路径检查

---

## 第三批修改: Code Adapter Agent 职责明确

### 修复 10: Code Adapter 持续修复义务和入口脚本适配边界

**修复** (`prompts/repair_code_adapter.md`):
- **第 10 条**: 仅当错误属于职责范围外时停止并报告
- **第 11 条** *(新增)*: 入口脚本本身也是适配目标，核心测试逻辑不得删减
- **第 12 条**: 职责范围内问题持续修复重试直到脚本成功运行，不设次数上限

---

## 第四批修改: `_meta` 追踪注入

### 修复 11: Phase 0-6 原始指令和响应持久化

**现象**: Phase 0-3.5 只保存 JSON 结果，不保存 Agent 的完整 prompt 和 response，无法追溯 Agent 的推理过程。

**修复** (`core/phase_runner.py`): 在 `_run_single_phase` 中，`normalized_output` 被注入 `_meta` 字段，包含 `prompt`（发送给 Agent 的完整指令）和 `response`（Agent 的完整原始响应）。

---

## 第五批修改: E2E 测试驱动问题修复

### 修复 12: Phase 6 prompt 被 `_meta` 污染

**现象**: Phase 6 从 canonical 文件读取 prior artifacts，由于 `_meta` 也写入了 canonical，导致 Phase 6 的 context 中 76% 是 `_meta`（29.3 KB / 38.5 KB），尤其是 Phase 3 单独贡献了 10 KB 的 prompt 原文。

**修复** (`core/phase_runner.py`): 在 `_run_single_phase` 的验证成功分支中，`mark_validated` 之前从复制的 `validated_output` 中剥离 `_meta`。`save_phase_output` 的 raw 文件仍保留 `_meta`（用于追溯），但 canonical 文件是纯净的 Phase 输出。

```python
validated_output = dict(normalized_output)
_ = validated_output.pop("_meta", None)
canonical_path = artifact_store.mark_validated(phase.artifact_id, validated_output)
```

### 修复 13: Reviewer JSON 解析缺少重试机制

**现象**: `run_review_check` 只尝试一次解析 reviewer 响应，如果 Agent 未输出有效 JSON 或 verdict 不在 `accept/reject` 中，直接返回 `{"verdict": "unknown"}`，导致 orchestrator 误判为通过。

**修复** (`core/phase_runner.py`): 为 `run_review_check` 添加 `max_retry=2` 重试循环：
- 仅当 `verdict in ("accept", "reject")` 时才接受结果
- 重试时发送 correction prompt，明确指出 verdict 格式要求
- 最大重试次数后返回最后一次解析结果

### 修复 14: E2E 入口的 _review_fn 缺少运行时参数

**现象**: `tests/e2e/e2e_test.py` 中的 `_review_fn` 闭包只传递了 `repair_history`，没有传递 `last_artifact_path`、`attempt_log_content`、`execution_duration`，导致 reviewer 的 runtime evidence 全部为空。

**修复** (`tests/e2e/e2e_test.py`): 更新 `_review_fn` 传递完整参数：
```python
runner.run_review_check(
    ...,
    repair_history=repair_history,
    last_artifact_path=str(repair_ctx.get("last_artifact_path", "(no artifact available)")),
    attempt_log_content=str(repair_ctx.get("attempt_log_content", "(attempt log unavailable)")),
    execution_duration=str(repair_ctx.get("execution_duration", "(not available)")),
)
```

---

## 修改影响范围

| 文件 | 修改批次 | 修改内容 |
|:---|:---:|------|
| `prompts/phase_5_review.md` | #1 | 新增 `attempt_log_content`，移除 `generated_artifacts`，去掉 `accept_with_warning`，抽象 CPU fallback 规则，强化思考引导 |
| `prompts/phase_3_entry_script.md` | #2 | 移除 `Use prior results from {previous_outputs}` |
| `prompts/phase_35_static_validate.md` | #2 | 移除 `Use prior results from {previous_outputs}` |
| `prompts/phase_0_env_detect.md` | #2 | AscendC 编译器检测：`acendc→ccec`，路径修正 |
| `prompts/repair_code_adapter.md` | #3 | 新增第 10-12 条：持续修复义务和入口脚本边界 |
| `core/repair_loop.py` | #1 | 新增 `_load_attempt_log_content()`，截断上限增大，移除 `generated_artifacts` |
| `core/phase_runner.py` | #1,#2,#4,#5 | `run_review_check` 签名变更和重试逻辑，新增 `_SHARED_SESSION_PHASES`，`_build_prompt_context` 条件化序列化，新增 `_meta` 注入，`mark_validated` 前剥离 `_meta` |
| `core/orchestrator.py` | #1 | `_review_fn` 传递 `attempt_log_content`，移除 `generated_artifacts` |
| `tests/e2e/e2e_test.py` | #5 | `_review_fn` 补全 `last_artifact_path`，`attempt_log_content`，`execution_duration` |

---

## 验证状态

- **修复 01-09**: 通过 `04_Deepwave_20260428_125333` E2E 测试结果验证 ✅
- **修复 10**: instruction 注入已在 Phase 5 attempt2.json 中确认 ✅
- **修复 11**: 所有 raw JSON 含 `_meta` 字段 ✅
- **修复 12-14**: 代码已修改，待下一轮 E2E 测试验证

**LSP 诊断**: `core/phase_runner.py` 和 `tests/e2e/e2e_test.py` 无错误
