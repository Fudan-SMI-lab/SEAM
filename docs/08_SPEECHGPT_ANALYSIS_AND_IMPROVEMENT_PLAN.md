# 08_SpeechGPT-2.0-preview 适配失败深度分析与改进方案

> 日期: 2026-04-26
> 项目: SEAM/src
> 分析对象: 08_SpeechGPT-2.0-preview E2E 适配全流程

---

## 一、全流程逐环节分析

### 1.1 时间线总览

| 阶段 | 时间 | 内容 | 耗时 |
|------|------|------|------|
| Phase 0-4 (env→venv→entry→migration) | 06:41-06:50 | 环境检测、项目分析、创建venv、确定入口、规则迁移 | 9min |
| Attempt 1 | 06:57 | IndentationError → 修复 | 7min |
| Attempt 2 | 07:01 | ModuleNotFoundError → 修复 | 4min |
| Attempt 3 | 07:14 | aclnnTriu崩溃 + FileNotFoundError → 只识别了pathing | 13min |
| Attempt 4 | 08:04 | 超时(1200s) → 添加非交互模式+monkeypatch | 50min |
| Attempt 5 | 08:17 | 标记 success (无raw执行记录) | 13min |

### 1.2 每个 Attempt 的完整链路

#### Attempt 1 — IndentationError ✅ 正确修复
- **Error**: `import torch_npu` 以零缩进注入在 except 块内
- **Analyzer 分类**: `migration logic` → `code_adapter`
- **Code Adapter**: 移动 import 到顶部 + flash_attention_2→sdpa + 移除 torch.compile + numpy 降级
- **结果**: IndentationError 解决

#### Attempt 2 — ModuleNotFoundError ✅ 正确修复
- **Error**: `from mimo_qwen2_grouped import *` — `mimo_qwen2_grouped.py` 在 `original_src/` 但脚本在 `test_data_and_scripts/`
- **Analyzer 分类**: `pathing` → `code_adapter`
- **Code Adapter**: `sys.path.insert(0, os.path.abspath('../original_src'))`
- **结果**: 模块找到

#### Attempt 3 — aclnnTriu 崩溃 ❌ 误分类
- **Error (完整)**:
  ```
  第 1 行: RuntimeError: aclnnTriu is not supported on this device  ← 真正的根因
  ...
  最后行: FileNotFoundError: [Errno 2] ... sg2_codec_config.yaml  ← 被误认为根因
  ```
- **Analyzer 分类**: `pathing` → `code_adapter`（只看到了最后一行）
- **Code Adapter**: 修复路径
- **问题**: Analyzer 的 prompt 引导它找 "last error"（Python traceback 最后），导致 `RuntimeError: aclnnTriu` 被忽略
- **结果**: `FileNotFoundError` 修好了，但 aclnnTriu 从未被处理

#### Attempt 4 — Timeout ❌ 非交互模式 + monkeypatch
- **Error**: `Execution timed out after 1200s`（脚本是交互式 REPL，`input()` 一直阻塞）
- **Analyzer 分类**: `migration logic` → `code_adapter`
- **Code Agent 的修复**:
  - ✅ 添加 `if not sys.stdin.isatty()` 非交互模式
  - ✅ monkeypatch `torch.triu`, `torch.tril`, `torch.rsqrt`
  - ✅ monkeypatch `F.scaled_dot_product_attention`（用 Python tril 建 mask + is_causal=False）
  - ❌ 但 Code Agent 仍然 escalated: "需要 operator_fixer 处理 aclnnTriu"
- **结果**: 脚本跑到模型初始化，但因代码复杂度超时

#### Attempt 5 — "成功" ⚠️ False Positive
- **实际行为**: 脚本跑通，进入非交互模式 → `self.process_text_input("你好")` 触发 aclnnTriu 崩溃 → `except Exception` 吞掉 → `return 0` → exit code 0
- **Review Gate**: accept（只做了代码静态检查，看不到运行时异常）
- **真相**: 推理从未真正执行过

### 1.3 为什么 Review Gate 不拦截

**Review Prompt 设计** (`phase_5_review.md`):

```markdown
## Repair History
{repair_history}  ← 只有表格摘要

## Review Checklist
1. Correctness
2. NPU Compliance (CPU fallback patterns)
3. Constraint Compliance
4. Root Cause vs Symptom
5. Better Alternatives
```

**Review Agent 收到的输入**:

| 输入 | 内容 |
|------|------|
| `repair_history` | 表格摘要 (exit_code, category, fix_summary 150字截断, modified_files) |
| `project_dir` | 可读取代码文件 |
| **没有** `stdout` | 模型是否正常推理 |
| **没有** `stderr` | 是否有 RuntimeError |
| **没有** duration | 运行时长 |
| **没有** artifacts | 生成的音频文件 |

**Review Agent 只能做静态代码审查**: 扫描 `device='cpu'`、`.cpu()` 调用等。但 false positive 的脚本里没有 CPU fallback —— 它的异常被 `try/except` 吞掉了。

---

## 二、框架机制深度分析

### 2.1 Error Analyzer 上下文传递链路

```
repair_loop.py _analyze_error():
  → prompt_context = {
       "failure_log": error_text,        ← 当前失败的 stderr
       "previous_outputs": self._format_error_analyzer_context(history),
       ...
     }
```

`_format_error_analyzer_context()` 生成的是 **Markdown 表格**:

```markdown
| Iter | Exit | Category | Repair Role | Error Signature | Suggested Fix |
|------|------|----------|-------------|-----------------|---------------|
| Iter 1 | exit=1 | migration logic | code_adapter | exit=1 | Fixed 3 issues... |
| Iter 2 | exit=1 | pathing | code_adapter | pathing | Added sys.path... |
| Iter 3 | exit=1 | pathing | code_adapter | pathing | Made all path... |
```

**问题**: Analyzer 能看到前几次的摘要，但 **没有**看到前几次 Code Agent 的完整回复。特别是：

| 信息 | 是否传递给下一轮 Analyzer |
|------|-------------------------|
| 前轮 exit_code | ✅ (通过 summary_entry) |
| 前轮 fix_summary (1-2句) | ✅ (150字截断) |
| 前轮 code agent 完整回复 | ❌ 完全没有 |
| 前轮 code agent 的 escalated_to | ❌ 完全没有 |
| 前轮 stdout | ❌ 完全没有 |
| 前轮 stderr | ❌ 完全没有 |

### 2.2 `context.history` 的构建过程

```python
# _record_iteration() (line 743-779)
summary_entry = {
    "iteration": iteration,
    "exit_code": record["exit_code"],
    "error_category": str(record["classification"].get("category", "unknown")),
    "repair_role": str(record["fix_attempt"].get("repair_role", "")),
    "modified_files": record["fix_attempt"].get("modified_files", []),
    "fix_summary": str(record["fix_attempt"].get("fix_summary", "")),
}
context.history.append(summary_entry)
```

**完整记录（IterationRecord）被保存到 `raw/` JSON 文件**，但 `summary_entry` 是从中提取的 6 个字段：

```
iteration_record (保存为 raw json):
├── exit_code ✅ → summary_entry
├── stdout ❌ 丢弃
├── stderr ❌ 丢弃
├── error ❌ 丢弃
├── classification ✅ (只提取 category)
├── fix_attempt ✅ (只提取 repair_role, modified_files, fix_summary)
│     ├── response: "完整 code agent 回复..." ❌ 丢弃
│     └── escalated_to: "operator_fixer: ..." ❌ 丢弃
└── error_analyzer_session_id ❌ 丢弃
```

**结论**: 前一轮 Code Agent 的完整回复（包括 `escalated_to` 字段）和运行输出的 stdout/stderr 全部被丢弃，不会传递给下一轮 Error Analyzer。

### 2.3 文件级证据是否可达

Artifacts 保存在项目 `.sm-artifacts/` 目录下。理论上，Error Analyzer Agent 和 Code Adapter Agent 都可以读取项目目录下的文件：

- `raw/phase_5_validation_attempt1.json` → 包含 stdout, stderr, error, classification, fix_attempt.response
- `execution_journal.jsonl` → Phase 执行时间线
- `state.json` → Repair 循环状态

**但 Agent 没有被引导去查看这些文件**。Prompt 中没有任何地方告诉 Analyzer 或 Code Agent 去读取 `.sm-artifacts/raw/phase_5_validation_attempt<N>.json`。

### 2.4 HybridErrorClassifier 与实际使用的对比

存在两套分类机制：

**A. `core/hybrid_error_classifier.py`** — 纯正则规则，16条规则匹配，无LLM

```python
# 实际路由:
env_dependency   → hephaestus
script_code_adapt → hephaestus
operator_incompat → ultrabrain
```

**B. `src/core/repair_loop.py`** — 使用 LLM Error Analyzer

实际执行走的是 B 路径。LLM 收到 `phase_error_recovery.md` 的 prompt，由 LLM 自主判断分类。

关键问题在路径 B 中，LLM Analyzer 只依赖当前 `error_text` 和 `previous_outputs`（摘要表）做判断。

---

## 三、改进方案

### 改进 1：Code Adapter Prompt — 加入 Monkeypatch 策略原则（非代码示例）

**修改文件**: `src/prompts/repair_code_adapter.md`

**新增段落**: 在 "Required Actions" 中插入：

```markdown
## Strategy When C-Level NPU Operators Are Missing

If the execution failure indicates a missing CANN toolkit operator at the kernel
level (e.g., the error references `aclnn` operators, "operator not implemented",
"not supported on this device", or similar C++ dispatch failures), consider
composing the missing functionality from lower-level NPU-supported primitives.

The general approach should:
- Identify the failing function's role within the computation graph (e.g.,
  mask construction, normalization, element-wise math).
- Use primitive NPU-supported PyTorch operations to reconstruct equivalent
  behavior at a higher abstraction level.
- Intercept the failing entry point before it reaches the unsupported C++
  dispatcher, keeping the fix transparent to all callers.
- Maintain NPU device affinity throughout — tensors must stay on NPU.

Attempting Python-level composition is your first step. Only escalate when
you confirm that the missing operator's behavior cannot be composed from
available primitives.
```

**设计原则**: 只给出策略性的思考方向，不提供具体代码模板。Agent 根据项目实际情况自主决定如何组合底层算子。

### 改进 2：给 Error Analyzer 提供 Artifact 路径（非原文填充）

**修改文件**: `src/core/repair_loop.py`, `src/prompts/phase_error_recovery.md`

**在 `_build_repair_prompt()` 的 context 中新增**:

```python
prompt_context = {
    # ... existing fields ...
    "artifact_base_path": self.artifact_store.base_path,  # e.g. ".sm-artifacts/e2e-real-xxx/"
    "raw_attempt_files": [  # list of saved raw attempt JSON paths
        raw_path,  # current attempt
    ] + list(self.artifact_store.list_attempt_outputs(_PHASE_ID)),  # previous attempts
}
```

**在 `phase_error_recovery.md` 中新增**:

```markdown
## Available Execution Artifacts

Raw execution log files from previous validation attempts:

{raw_attempt_files}

Each JSON file contains: `stdout`, `stderr`, `error`, `classification` (with
category/root_cause/suggested_fix), `fix_attempt` (with response text and
modified_files).

When analyzing error patterns:
- Read the relevant JSON files from the paths above to understand the full
  stdout/stderr of previous attempts.
- Pay special attention to the FIRST exception in each traceback (not just
  the last line) — cascading failures often have the root cause at the top.
- Compare the complete output progression across attempts to identify whether
  fixes are actually addressing root causes or just suppressing symptoms.
```

**好处**:
- Prompt 不会因填充大量日志而膨胀
- Analyzer 可以自主决定读取哪些 artifact 文件
- 可以追溯完整的 stdout/stderr 历史
- Agent 可以查看前一轮 Code Agent 的完整 response

### 改进 3：Review Gate 增加运行证据（通过 artifact 路径）

**修改文件**: `src/core/repair_loop.py`, `src/prompts/phase_5_review.md`

**在 Review Gate 调用处新增 context**:

```python
review_context = {
    "repair_history": repair_history_table,
    "project_dir": project_dir,
    "last_artifact_path": last_raw_path,       # the most recent attempt JSON
    "stdout_file": f"{artifact_dir}/stdout.log",  # captured stdout
    "stderr_file": f"{artifact_dir}/stderr.log",  # captured stderr
    "execution_duration": duration_seconds,
    "generated_artifacts": list_new_files(),    # files created during run
    # ... existing fields ...
}
```

**在 `phase_5_review.md` 中新增**:

```markdown
## Available Runtime Evidence

The last validation attempt's execution artifacts are saved at the following
paths:

- Raw attempt log: {last_artifact_path}
  Contains: stdout, stderr, error, classification, fix_attempt.response
- Execution output files: {stdout_file}, {stderr_file}
- Duration: {execution_duration} seconds
- Files created during execution: {generated_artifacts}

When reviewing, cross-reference the code changes against the ACTUAL runtime
output:
1. Did the script produce meaningful inference results? Check stdout for
   model output, generated file paths, or success indicators.
2. Were there any hidden failures? Scan the raw attempt log for exceptions
   that may have been caught and suppressed by the entry script.
3. Did the execution complete within normal time? Unusually short runs
   (e.g., <5s for model loading) may indicate early exits.
4. Were expected artifacts generated? (e.g., audio files, response images)
```

### 改进 4：通过 Prompt 约定建立 Agent 间诊断信息传递链

**核心问题**: 上一轮 Repair Agent 对自己工作的判断（如"这个问题超出了我的范围，应由 xxx agent 处理"、"我的修复可能只解决了表面症状，根本原因在 xxx"）完全没有传递给下一轮 Error Analyzer。框架只在 `_record_iteration()` 中保存了 exit_code、category、role、fix_summary 四个摘要字段，Repair Agent 的完整 response 被丢弃。

**设计思路**: 不在 Python 代码中做硬编码解析，而是在每个 Repair Agent 的 prompt 中新增一个 `agent_diagnostics` 输出字段，由 Agent 自主判断是否需要向后续 Agent 传递关键信息。Error Analyzer 在下一轮的 Fix History 表格中直接看到这一列。

#### 步骤 1：在三个 Repair Agent Prompt 中新增输出字段

**修改文件**:
- `src/prompts/repair_code_adapter.md`
- `src/prompts/repair_dependency_fixer.md`
- `src/prompts/repair_operator_fixer.md`

每个文件的末尾 JSON 输出部分，新增 `agent_diagnostics` 字段：

**以 `repair_code_adapter.md` 为例**（其他两个类似）：

将原有的 JSON 输出要求：

```markdown
## Output Format
At the end of your response, append a JSON code block with:
  - "modified_files": ...
  - "summary": ...
  - "escalated_to": ...
```

替换为：

```markdown
## Output Format
At the end of your response, append a JSON code block with exactly these keys:

```json
{
  "modified_files": ["path/to/changed_file.py"],
  "summary": "A 1-2 sentence description of what you fixed",
  "agent_diagnostics": ""  // see guidance below
}
```

## When to Fill `agent_diagnostics`

Use this field to communicate with the Error Analyzer that will review the next
failed iteration. Leave it empty ("") if your fix fully resolved the issue and
you have nothing further to note.

Fill it when ANY of the following applies:

- **Out of scope**: The root cause is outside your agent's scope and another
  agent (dependency_fixer / code_adapter / operator_fixer) is more appropriate.
  Example: "This is a C-level operator limitation. code_adapter has exhausted
  Python-level alternatives. Recommend operator_fixer."
- **Partial fix**: Your change resolved one symptom but a deeper root cause
  likely remains. Example: "Fixed the pathing issue, but the aclnnTriu error
  in the LLM attention layer is still present — it was masked by the earlier
  failure."
- **Directional guidance**: You have insight about what the next iteration
  should focus on. Example: "The timeout was caused by interactive input().
  Adding non-interactive mode should be the next step."
- **Repeated pattern**: You noticed the same category of error appearing across
  multiple iterations without being addressed. Example: "This is the 3rd time
  pathing errors appear. The script's directory structure relative to
  original_src/ is fundamentally broken."
```

#### 步骤 2：在 `_record_iteration()` 中提取 `agent_diagnostics`

**修改文件**: `src/core/repair_loop.py`

在 `_record_iteration()` 方法的 `summary_entry` 构建中新增一个字段：

```python
def _record_iteration(self, iteration, context, record):
    fix_attempt = record.get("fix_attempt", {})
    response_text = fix_attempt.get("response", "")

    # Extract agent_diagnostics from the repair agent's JSON response
    agent_diagnostics = ""
    if response_text:
        try:
            parsed = extract_json_response(response_text)
            agent_diagnostics = parsed.get("agent_diagnostics", "")
        except InvalidJSONError:
            pass

    summary_entry = {
        "iteration": iteration,
        "exit_code": record["exit_code"],
        "error_category": str(record["classification"].get("category", "unknown")),
        "repair_role": str(fix_attempt.get("repair_role", "")),
        "modified_files": fix_attempt.get("modified_files", []),
        "fix_summary": str(fix_attempt.get("fix_summary", "")),
        "agent_diagnostics": agent_diagnostics,  # NEW
    }
    context.history.append(summary_entry)
```

#### 步骤 3：在 Fix History 表格中增加一列

**修改文件**: `src/core/repair_loop.py`

在 `_format_history_summary()` 和 `_format_error_analyzer_context()` 的表格中增加 `Agent Diagnostics` 列：

```python
lines = [
    "| Iter | Exit | Category | Repair Role | Agent Diagnostics | Fix Summary | Modified Files |",
    "|------|------|----------|-------------|-------------------|-------------|----------------|",
]
for h in history:
    # ... existing extraction for iter_num, exit_code, category, role, summary, files ...
    diagnostics = h.get("agent_diagnostics", "")
    lines.append(
        f"| Iter {iter_num} | exit={exit_code} | {category} | {role} | "
        + f"{diagnostics} | {summary} | {files_str} |"
    )
```

#### 步骤 4：指导 Error Analyzer 阅读 diagnostics

**修改文件**: `src/prompts/phase_error_recovery.md`

在 "Fix History" 段落下方新增：

```markdown
## Agent Diagnostics Column

The `Agent Diagnostics` column in the Fix History table above contains the
previous repair agent's own assessment of the situation. This may include:
- Whether the agent believes the issue is outside their scope
- Which agent type they recommend handling the problem instead
- Warnings that their fix only addresses a symptom, not the root cause
- Observations about recurring patterns across iterations

Treat repair agent diagnostics as strong signal — they have direct access to
the codebase and understand their own scope limitations. If a repair agent
explicitly states that a problem belongs to a different agent type, or that
all Python-level alternatives have been exhausted, classify accordingly and
route to the recommended agent.
```

#### 信息流对比

**修复前**:
```
Code Agent response: {"summary": "...", "escalated_to": "operator_fixer: aclnnTriu..."}
    ↓
_record_iteration(): 保存 summary_entry = {exit_code, category, role, fix_summary}
    → escalated_to 丢弃
    → 下一轮 Analyzer 只看到 "fix_summary" 摘要，看不到 Code Agent 的判断
```

**修复后**:
```
Code Agent response: {"summary": "...", "agent_diagnostics": "C-level operator, recommend operator_fixer"}
    ↓
_record_iteration(): 提取 agent_diagnostics 加入 summary_entry
    → 下一轮 Analyzer 看到的表格:
| Iter | Exit | Category    | Repair Role  | Agent Diagnostics                    |
| Iter 4 | exit=1 | migration logic | code_adapter | C-level operator, recommend operator_fixer |
    → Analyzer 根据此信息将分类改为 "operator", 路由到 operator_fixer
```

---

## 四、改进优先级

| 优先级 | 改进项 | 影响 | 实施难度 |
|--------|--------|------|---------|
| **P0** | Code Adapter 增加 monkeypatch 策略原则 | 直接解决 aclnnTriu 类问题 | 低（改 prompt） |
| **P0** | Error Analyzer 获取 artifact 路径 | 解决信息丢失，正确分类 | 中（改 code + prompt） |
| **P0** | 传递 Code Agent 诊断到下一轮 Analyzer | 实现 agent 间信息链 | 低（改 code + prompt） |
| **P1** | Review Gate 增加运行时证据 | 消除 false positive | 中（改 code + prompt） |
| **P2** | 增加 CANN operator 兼容性预检 | Phase 0 提前发现限制 | 高（新模块） |

---

## 五、改进实施验证指南

### 5.1 如何运行 08_SpeechGPT-2.0-preview 端到端测试

**前提条件**：
- OpenCode server 推荐运行在端口 `4098`（或通过 `--hostname` / `--port` / `--server_type` 指定其他端口）
- 08_SpeechGPT-2.0-preview 项目已准备好在 `original_projects/08_SpeechGPT-2.0-preview/`
- 模型权重已下载到 `uploaded_files/SpeechGPT-2.0-preview-7B/` 和 `uploaded_files/SpeechGPT-2.0-preview-Codec/`

**运行命令**：

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM

python -m tests.e2e.e2e_test_v2 \
  --hostname 127.0.0.1 --port 4098 --server_type opencode \
  --project-dir /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM/original_projects/08_SpeechGPT-2.0-preview/ \
  --output_dir /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM/output_projects/ \
  --user-constraints /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM/original_projects/08_SpeechGPT-2.0-preview/ADAPTATION_REQUIREMENTS.md \
  --keep-temp-dir \
  --review-gate
```

**参数说明**：

| 参数 | 值 | 作用 |
|------|-----|------|
| `--hostname` / `--port` / `--server_type` | `http://127.0.0.1:4098` | OpenCode LLM server 地址 |
| `--max-phase5-iter` | 默认 `10` | Phase 5 最大修复迭代次数；显式传入时覆盖默认值 |
| `--project-dir` | 源项目路径 | 原始 CUDA 项目目录 |
| `--output_dir` | 输出根目录 | 迁移后的项目会创建到 `{output_dir}/{项目名}_{时间戳}/` |
| `--user-constraints` | 约束文件路径 | ADAPTATION_REQUIREMENTS.md，包含 zero CPU fallback 等要求 |
| `--keep-temp-dir` | (flag) | 保留迁移后的项目目录供检查 |
| `--review-gate` | (flag) | 开启 review gate，exit 0 后由 review agent 做代码质量确认 |

**预期输出结构**（以 `08_SpeechGPT-2.0-preview_20260426_XXXXXX/` 为例）：

```
output_projects/08_SpeechGPT-2.0-preview_20260426_XXXXXX/
├── .sm-artifacts/
│   └── e2e-real-{uuid}/
│       ├── execution_journal.jsonl     ← Phase 执行时间线
│       ├── state.json                  ← Repair loop 状态
│       ├── raw/                        ← 每次 attempt 的完整记录
│       │   ├── phase_0_env_detect_attempt1.json
│       │   ├── phase_1_project_analysis_attempt1.json
│       │   ├── phase_1_5_constraint_summary_attempt1.json
│       │   ├── phase_2_venv_create_attempt1.json
│       │   ├── phase_3_entry_script_attempt1.json
│       │   ├── phase_4_rule_migration_attempt1.json
│       │   ├── phase_5_validation_attempt1.json  ← 第1轮修复
│       │   ├── phase_5_validation_attempt2.json  ← 第2轮修复
│       │   ├── ...                                   ← 更多轮次...
│       │   └── phase_6_report_attempt1.json    ← 最终报告
│       ├── validated/                    ← 通过 schema 验证的 canonical 输出
│       │   ├── phase_0_env_detect_canonical.json
│       │   ├── phase_5_validation_canonical.json  ← 最终验证结果
│       │   └── phase_6_report_canonical.json
│       └── reports/                      ← 人工可读的报告
│           ├── SUMMARY_REPORT.md
│           ├── OPENCODE_OPERATIONS_LOG.md
│           └── ...
├── .venv/                              ← Python 虚拟环境
├── original_src/                       ← 迁移后的源代码
├── test_data_and_scripts/
│   └── moss_test_script.py             ← Entry script（被 agent 修改过）
└── uploaded_files/                     ← 模型权重（硬链接/软链接）
```

### 5.2 改进验证清单

每个改进实施后，通过以下方法从输出文件中验证是否已生效：

#### 验证方法总览

所有验证都通过读取 `raw/phase_5_validation_attempt<N>.json` 中的 `fix_attempt.instruction` 字段（即实际发送给 Agent 的完整 prompt）来确认 prompt 内容已更新。

#### 改进 1：C-Level 策略原则

**验证目标**：确认 code_adapter 的 prompt 中包含了 monkeypatch 策略指导。

```python
import json, os

# 找到最新的 attempt 文件
raw_dir = "output_projects/08_SpeechGPT-2.0-preview_XXXXXX/.sm-artifacts/e2e-real-*/raw"
attempt_files = sorted(glob.glob(os.path.join(raw_dir, "phase_5_validation_attempt*.json")))

for fp in attempt_files:
    with open(fp) as f:
        data = json.load(f)
    # 只检查 code_adapter 的 attempt
    if data.get("fix_attempt", {}).get("repair_role") != "code_adapter":
        continue

    instruction = data["fix_attempt"]["instruction"]

    # 验证检查
    has_strategy_guidance = any(phrase in instruction.lower() for phrase in [
        "composing the missing",     # 我们的策略原则关键词
        "c-level npu operators",     # C-Level 算子策略标题
        "intercept the failing",     # 拦截失败入口点
        "npu device affinity",       # NPU 设备亲和性要求
    ])
    print(f"{fp}: C-Level strategy present = {has_strategy_guidance}")
```

**判定标准**：
- ✅ 通过：至少有一个 attempt 的 instruction 中包含策略原则关键词
- ❌ 未通过：所有 attempt 的 instruction 都不包含上述关键词

#### 改进 2：Artifact 路径注入

**验证目标**：确认 Error Analyzer 的 instruction 中包含 artifact 文件路径，引导其读取完整日志。

```python
import json

with open(latest_attempt_file) as f:
    data = json.load(f)

instruction = data["fix_attempt"]["instruction"]

# 验证检查
has_artifact_paths = any(phrase in instruction for phrase in [
    "artifact_base_path",              # artifact 基础路径
    "raw_attempt_files",               # 原始 attempt 文件列表
    "Available Execution Artifacts",   # 新增的小节标题
    ".sm-artifacts",                    # artifact 目录引用
])

# 额外验证：检查 analyzer 是否真的被引导去读取文件
has_read_guidance = any(phrase in instruction.lower() for phrase in [
    "read the relevant json files",     # 读取 JSON 文件的引导
    "first exception in each traceback", # 引导看第一个异常
    "compare the complete output",       # 跨轮次比对引导
])

print(f"Artifact paths in prompt: {has_artifact_paths}")
print(f"Read guidance in prompt: {has_read_guidance}")
```

**判定标准**：
- ✅ 通过：prompt 中包含 artifact 路径 + 引导 Agent 读取的指导语
- ❌ 未通过：prompt 中没有 artifact 路径信息

#### 改进 3：Review Gate 运行时证据

**验证目标**：确认 Review Agent 的 prompt 中包含 artifact 目录或运行输出路径。

```python
# 方法 1：通过代码检查（实施后立即验证）
with open("src/prompts/phase_5_review.md") as f:
    review_prompt = f.read()

has_runtime_evidence = any(phrase in review_prompt.lower() for phrase in [
    "runtime evidence",
    "artifact path",
    "last_artifact_path",
    "execution_duration",
    "files created during execution",
])

# 方法 2：通过运行的 attempt 文件验证（需要 phase_6_report 或 review 数据）
# Review Agent 的 instruction 会被保存在 phase_5_review_attempt*.json 中
# 如果 framework 有保存的话
```

**判定标准**：
- ✅ 通过：prompt 中包含运行时证据相关小节
- ❌ 未通过：prompt 只有 `repair_history` 表格

#### 改进 4：agent_diagnostics 传递链

**验证目标**：确认 Repair Agent 的 prompt 要求输出 agent_diagnostics，且该字段出现在后续 attempt 的 Fix History 表格中。

```python
import json

# 验证步骤 1: 检查 Repair Agent prompt 是否要求输出 agent_diagnostics
with open("src/prompts/repair_code_adapter.md") as f:
    code_adapter_prompt = f.read()

has_diag_prompt = "agent_diagnostics" in code_adapter_prompt

# 验证步骤 2: 检查 Error Analyzer prompt 是否引导阅读 diagnostics
with open("src/prompts/phase_error_recovery.md") as f:
    analyzer_prompt = f.read()

has_diag_analyzer = "agent_diagnostics" in analyzer_prompt.lower()

# 验证步骤 3: 检查 repair_loop.py 代码是否提取 agent_diagnostics
with open("src/core/repair_loop.py") as f:
    repair_loop_code = f.read()

has_diag_code = "agent_diagnostics" in repair_loop_code

# 验证步骤 4（运行时验证）: 检查 attempt N 的 Fix History 表格是否显示前一轮 diagnostics
for fp in attempt_files:
    with open(fp) as f:
        data = json.load(f)
    instruction = data["fix_attempt"]["instruction"]

    # 检查 instruction 是否包含 "Agent Diagnostics" 列
    has_diag_column = "Agent Diagnostics" in instruction or "agent_diagnostics" in instruction

    # 如果包含，提取 diagnostics 内容
    if has_diag_column:
        lines = instruction.split("\n")
        diag_lines = [l for l in lines if "agent_diagnostics" in l.lower() or "Agent Diagnostics" in l]
        for dl in diag_lines:
            print(f"  Found in {fp}: {dl.strip()}")

    print(f"{fp}: diagnostics column present = {has_diag_column}")
```

**判定标准**：
- ✅ 通过：所有 4 步都通过（prompt 更新 + 代码提取 + 运行时表格显示）
- ⚠️ 部分通过：只有 prompt 和代码更新，但没有实际 diagnostics 内容（可能是因为 Repair Agent 没有填写非空内容）
- ❌ 未通过：prompt、代码或表格中任何一环缺失

### 5.3 自动化验证脚本

以下脚本可以在 E2E 运行结束后自动检查四个改进是否全部生效：

```bash
#!/bin/bash
# verify_improvements.sh
# Usage: verify_improvements.sh <output_project_dir> <repo_root>
# Example: ./verify_improvements.sh \
#   output_projects/08_SpeechGPT-2.0-preview_20260426_XXXXXX/ \
#   src/

OUTPUT_DIR="$1"
REPO_ROOT="${2:-.}"

RUN_ID=$(ls "$OUTPUT_DIR/.sm-artifacts/" 2>/dev/null | head -1)
RAW_DIR="$OUTPUT_DIR/.sm-artifacts/$RUN_ID/raw"

if [ ! -d "$RAW_DIR" ]; then
    echo "ERROR: No raw artifacts found in $OUTPUT_DIR"
    exit 1
fi

export RAW_DIR  # Export for Python heredoc

python3 - "$REPO_ROOT" << 'PYEOF'
import json, glob, os, sys

raw_dir = os.environ["RAW_DIR"]
repo_root = sys.argv[1] if len(sys.argv) > 1 else "."

attempt_files = sorted(glob.glob(os.path.join(raw_dir, "phase_5_validation_attempt*.json")))
if not attempt_files:
    print("ERROR: No attempt files found")
    sys.exit(1)

results = {
    "Improvement 1: C-Level Strategy": False,
    "Improvement 2: Artifact Paths": False,
    "Improvement 3: Review Runtime Evidence": False,
    "Improvement 4: Agent Diagnostics Chain": False,
}

# Check code_adapter prompt directly
prompt_path = f"{repo_root}/src/prompts/repair_code_adapter.md"
if os.path.exists(prompt_path):
    with open(prompt_path) as f:
        ca_prompt = f.read()
    results["Improvement 1: C-Level Strategy"] = "composing the missing" in ca_prompt.lower() or "c-level npu operators" in ca_prompt.lower()

# Check error_recovery prompt
err_prompt_path = f"{repo_root}/src/prompts/phase_error_recovery.md"
if os.path.exists(err_prompt_path):
    with open(err_prompt_path) as f:
        er_prompt = f.read()
    results["Improvement 2: Artifact Paths"] = "artifact_base_path" in er_prompt or "Available Execution Artifacts" in er_prompt

# Check review prompt
review_prompt_path = f"{repo_root}/src/prompts/phase_5_review.md"
if os.path.exists(review_prompt_path):
    with open(review_prompt_path) as f:
        rev_prompt = f.read()
    results["Improvement 3: Review Runtime Evidence"] = "runtime evidence" in rev_prompt.lower() or "last_artifact_path" in rev_prompt

# Check diagnostics
results["Improvement 4: Agent Diagnostics Chain"] = (
    "agent_diagnostics" in ca_prompt and
    "agent_diagnostics" in er_prompt.lower()
)

print("=== IMPROVEMENT VERIFICATION RESULTS ===")
all_pass = True
for name, passed in results.items():
    status = "✅ PASS" if passed else "❌ NOT APPLIED"
    if not passed:
        all_pass = False
    print(f"  {status}:  {name}")

if all_pass:
    print("\n✅ All 4 improvements verified successfully!")
    sys.exit(0)
else:
    print("\n⚠️ Some improvements not yet applied. Check output above.")
    sys.exit(1)
PYEOF
```

### 5.4 验证结果解读

| 验证结果 | 含义 | 下一步 |
|---------|------|--------|
| 全部通过 | 所有 prompt 和代码改动已生效，且 E2E 运行正常应用 | 观察 Attempt N 的实际行为是否符合预期 |
| 改进 1 通过 | Code Adapter 已收到 monkeypatch 策略指导 | 检查 Agent 是否真的尝试了 Python 替代方案 |
| 改进 2 通过 | Analyzer 被引导去读取 artifact 文件 | 检查 Analyzer 是否识别到之前被忽略的错误（如 aclnnTriu） |
| 改进 3 通过 | Review Agent 有运行时证据 | 检查 Review 是否拦截了 false positive |
| 改进 4 通过 | Agent 间信息链打通 | 检查 `Agent Diagnostics` 列是否有内容（非空字符串），以及 Analyzer 是否据此改变了分类 |

**关键行为指标**（在改进生效后 E2E 中应观察到）：

| 指标 | 改进前行为 | 改进后期望行为 |
|------|-----------|---------------|
| Attempt 3 的分类 | `pathing`（只看最后一行错误） | `operator` 或识别到 `aclnnTriu` 根因 |
| Code Agent 的 escalated_to | 被丢弃 | 出现在下轮的 diagnostics 列中 |
| Review Verdict | accept（看不到运行时输出） | 可能 reject（发现无实际推理） |
| Attempt 总数 | 5 轮（含 false positive） | 可能减少（正确分类 + 有效修复链） |
