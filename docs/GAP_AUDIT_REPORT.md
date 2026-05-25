# sm-adapt 框架差距分析报告 (Gap Audit Report)

> **审计日期**: 2025-04-19
> **审计范围**: `opencode-sm-orchestrator/` 全部 36 个文件
> **审计方法**: 逐行代码审查 + 依赖链追踪 + Prompt 模板一致性检查 + 运行时逻辑推演

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [框架当前状态全景](#2-框架当前状态全景)
3. [阻塞性 GAP](#3-阻塞性-gap) — 🔴 G1-G3
4. [重要但非阻塞 GAP](#4-important-but-non-blocking-gaps) — 🟡 G4-G8
5. [架构级设计缺陷](#5-architectural-design-flaws) — D1-D5
6. [代码卫生问题](#6-code-hygiene-issues) — H1-H4
7. [差距汇总清单](#7-issue-summary-matrix)
8. [推荐修复优先级](#8-recommended-fix-priority)

---

## 1. 执行摘要

### 结论

sm-adapt 框架的**核心骨架（编排层 + 状态机 + Session 管理 + Artifact 存储）已经完整实现**，能够正确执行 8 个 Phase 的状态转移、3 路分发逻辑（LLM Session / 本地脚本 / 修复循环）、以及 Phase 间 Schema 验证。**Phase 4 确定性规则迁移已通过实测验证**（4 文件 8 模式迁移成功）。

### 关键数字

| 指标 | 数值 |
|---|---|
| 核心文件数 | 36 |
| ✅ 完整实现 | ~18 (50%) |
| 🟡 部分实现 | ~11 (30%) |
| 🔴 缺失/存根 | ~7 (20%) |
| **阻塞 E2E 的 GAP** | **3 个 (G1, G2, G3)** |
| **测试覆盖率** | **0%** |

### 核心判断

在不修复 G1（Classifier Fallback 存根）、G2（`_apply_result` 无落地验证）、G3（`wait_for_idle` 全局端点竞态）的情况下，对真实项目运行完整 E2E 流水线**大概率会在 Phase 5 修复循环中卡死**。修复这 3 项后，框架具备 E2E 运行能力，但可观测性和健壮性仍有提升空间。

---

## 2. 框架当前状态全景

### 2.1 组件清单

| 层级 | 组件 | 文件 | 行数 | 状态 |
|---|---|---|---|---|
| **编排层** | 状态机编排器 | `core/orchestrator.py` | 759 | ✅ 完整 |
| **状态机** | 状态转移引擎 | `core/state_machine.py` | 90 | ✅ 完整 |
| **会话管理** | OpenCode HTTP 客户端 | `core/session_manager.py` | 339 | ✅ 完整 |
| **验证引擎** | 3 层验证管道 | `core/validator_engine.py` | 236 | 🟡 L2/L3 缺 |
| **修复循环** | 验证-分类-修复循环 | `core/validation_repair_loop.py` | 302 | 🟡 无落地验证 |
| **错误分类** | 正则 + LLM 分类器 | `core/hybrid_error_classifier.py` | 170 | 🟡 LLM 存根 |
| **CLI 入口** | 命令行接口 | `cli/main.py` | 319 | ✅ 完整 |
| **Artifact 存储** | 文件持久化 | `core/artifact_store.py` | 228 | ✅ 完整 |
| **Hook 系统** | Hook 运行器 | `core/hook_runner.py` | 48 | ✅ 运行器 OK |
| **Hook 脚本** | 12 个 Hook 脚本 | `hooks/*.py` | 10-18 行 | 🔴 全 logging-only |
| **Validator 脚** | 7 个 Validator | `validators/*.py` | 18-30 行 | 🟡 字段级检查 |
| **Prompt 模板** | 8 Phase + 3 Repair | `prompts/**/*.md` | — | ✅ 齐全 |
| **Pydantic Schema** | 7 个 Phase 模型 | `schemas/phase_schemas.py` | 129 | ✅ (缺自定义校验) |
| **规则迁移** | 确定性正则替换 | `utils/sm_rule_migrator.py` | 185 | ✅ 完整 |
| **TS 插件** | OpenCode 插件 | `plugins/sm-orchestrator/src/` | — | ✅ 完整 |
| **服务器启动** | 自动健康检查 | `core/server_bootstrap.py` | 136 | ✅ 完整 |

### 2.2 数据流现状

```
用户自然语言 ──→ Phase 0 (LLM) ──→ user_context_parsed
                     │
                     ▼
Phase 1 (项目分析) ←── user_context (filtered)
                     │
                     ▼
Phase 2 (venv 创建) ──→ {vnc_path, python_bin}
                     │
                     ▼
Phase 3 (入口脚本) ←── user_context (original_run_command)
                     │
                     ▼
Phase 4 (规则迁移) ──→ {canonical_command, changed_files} (本地 Python)
                     │
                     ▼
Phase 5 (验证+修复) ──→ 循环: 运行 → 分类 → 修复Session → 再运行
                     │
                     ▼
Phase 6 (报告生成) ──→ final_report
```

### 2.3 验证管道现状

```
Layer 1 (Schema) → Pydantic model_validate ──→ ✅ 工作
     │ 失败? ──→ 立即返回, 跳过 L2/L3
     ▼ 通过
Layer 2 (Rule)   → 动态加载 validators/*.py ──→ 🟡 脚本存在但检查极浅
     │ 失败? ──→ 立即返回, 跳过 L3
     ▼ 通过
Layer 3 (LLM)    → _llm_judge() ──→ 🔴 return True (存根)
```

---

## 3. 阻塞性 GAP

> 这些 GAP 在真实 E2E 运行中**必定或极大概率导致失败**。

---

### G1: LLM Classifier Fallback 是硬编码存根 — 🔴 CRITICAL

**文件**: `core/hybrid_error_classifier.py`, 第 98-118 行

**具体情况**:

当 17 条确定性正则规则全部不匹配时（即遇到未见过的新错误类型），`classify()` 方法回退到 `_llm_fallback()`。但该方法**完全没有调用真实的 LLM**，而是返回硬编码值：

```python
def _llm_fallback(self, stderr, stdout, exit_code) -> ErrorClassificationOutput:
    return ErrorClassificationOutput(
        error_category="unknown",
        error_subtype="unknown",
        root_cause="Unable to classify — LLM fallback mock",
        suggested_fix="Manually inspect the error log",
        confidence=0.3,           # ← 低于 0.7 阈值
        classification_method="llm_fallback",
        repair_agent_type="ultrabrain",
        needs_manual=True,         # ← 直接触发 _is_unclassifiable()
        manual_reason="No deterministic rule matched and LLM fallback is mocked",
        ...
    )
```

**运行时影响链**:

```
新型错误 (不在 17 条规则中)
  → _llm_fallback() 返回 confidence=0.3
  → _is_unclassifiable() 拦截 (confidence < 0.7)
  → repair loop 立即以 "error_unclassifiable" 退出
  → Phase 5 整体失败
  → 状态机转移到 phase_error_recovery → failed
```

**覆盖率估算**: 17 条规则覆盖了常见 CUDA→NPU 迁移错误约 60-70% 的场景。剩余 30-40% 的错误类型（如 CANN API 版本不兼容、特定算子精度问题、分布式训练中 rank 数量变化等）都会触发 fallback → 立即失败。

**修复方案**:

**方案 A — 注入真实 LLM 调用 (推荐)**:

```python
def _llm_fallback(self, stderr, stdout, exit_code) -> ErrorClassificationOutput:
    # 通过 SessionManager 调用 LLM 进行错误分类
    prompt = (
        f"Classify the following execution error into one of these categories:\n"
        f"- env_dependency: Missing/wrong package version\n"
        f"- script_code_adapt: Code still uses CUDA APIs\n"
        f"- operator_incompat: Operator not available on NPU\n\n"
        f"Stderr:\n```\n{stderr[:3000]}\n```\n\n"
        f"Stdout:\n```\n{stdout[:1000]}\n```\n\n"
        f"Return ONLY a JSON object with these fields:\n"
        f"error_category, error_subtype, root_cause, suggested_fix, confidence"
    )
    # ... create session, wait for response, parse JSON
```

**方案 B — 扩展正则规则覆盖 (快速但不治本)**:

增加更多正则规则覆盖 CANN ACL 错误、NPU 特定警告、分布式训练错误等常见模式。目标覆盖率 >90%。

**方案 C — 混合策略 (最优)**:

先用方案 B 将正则覆盖率提升到 >90%，剩余 10% 用方案 A 的 LLM 调用兜底。

**预估工作量**: 方案 A: 2-3 天；方案 B: 0.5 天；方案 C: 2 天

---

### G2: `_apply_result()` 无落地验证 — 🔴 CRITICAL

**文件**: `core/validation_repair_loop.py`, 第 247-249 行

**具体情况**:

```python
def _apply_result(self, repair_entry: RepairHistoryEntry) -> None:
    self.current_state = "apply_result"
    self.repair_history.append(repair_entry)   # ← 仅此而已
```

修复 session (OpenCode Agent) 被创建后，Agent 可能执行了文件修改，也可能没有。`_apply_result()` 把修复记录写入内存列表，但**没有任何机制验证文件确实被修改了**。

**运行时影响**:

```
迭代 1: 错误发生 → 分类 → 创建 repair session → Agent 可能未修改文件
  → _apply_result() 记录成功 (仅内存)
  → 重新运行 validate_cmd → 同样的错误
  → 分类 → 同样的错误指纹
  → 指纹重复 2 次 → "repair_stagnation" 退出
  → 但无法区分是 "Agent 没改" vs "改了但没修好"
```

**修复方案**:

```python
def _apply_result(self, repair_entry: RepairHistoryEntry, project_dir: str) -> None:
    self.current_state = "apply_result"

    # ===== 新增: 验证文件确实被修改 =====
    affected_files = self._get_affected_files_from_response(repair_entry)
    modification_verified = False
    for f in affected_files:
        full_path = os.path.join(project_dir, f)
        if os.path.isfile(full_path):
            # 1. 语法检查
            try:
                py_compile.compile(full_path, doraise=True)
                modification_verified = True
            except py_compile.PyCompileError:
                # 文件语法错误 → Agent 写了但写坏了
                repair_entry["verification_failed"] = True
                repair_entry["note"] = f"Syntax error in {f}"
                break

    self.repair_history.append(repair_entry)
```

**补充措施**: 在 `_execute_repair_session` 执行前，用 `git diff` 或文件哈希记录 snapshot；执行后 diff 验证变化。

**预估工作量**: 0.5 天

---

### G3: `wait_for_idle` 使用全局端点 — 🔴 HIGH

**文件**: `core/session_manager.py`, 第 143-153 行

**具体情况**:

```python
def wait_for_idle(self, session_id: str, timeout_s: float = 300.0) -> bool:
    started_at = time.time()
    while time.time() - started_at < timeout_s:
        payload = self._request("GET", "/session/status")   # ← 全局端点!
        if payload is None:
            return False
        token = self._extract_status_token(payload, session_id)
        if token not in RUNNING_TOKENS:
            return True
        time.sleep(0.5)
    return False
```

**问题**: `GET /session/status` 返回所有 session 的全局状态。`_extract_status_token()` 方法从响应中尝试提取指定 `session_id` 的 token，但如果 OpenCode 的 API 设计是按单个 session 查询状态（`GET /session/{id}/status`），则当前实现会**错误地读取到其他 session 的状态**。

**竞态场景**:

```
Phase 1 session (build agent) ──→ 正在运行
Phase 2 session (build agent) ──→ 空闲
  → wait_for_idle(Phase1) 调用全局端点
  → 读到 Phase2 的 "IDLE" 状态
  → 误判 Phase1 已空闲 → 过早获取响应 → 得到不完整/错误结果
```

**修复方案**:

```python
def wait_for_idle(self, session_id: str, timeout_s: float = 300.0) -> bool:
    started_at = time.time()
    while time.time() - started_at < timeout_s:
        # 改为查询特定 session 的状态
        payload = self._request("GET", f"/session/{session_id}/status")
        if payload is None:
            time.sleep(1.0)
            continue
        token = self._extract_status_token(payload, session_id)
        if token not in RUNNING_TOKENS:
            return True
        time.sleep(0.5)
    return False
```

**前置验证**: 先用 `curl http://127.0.0.1:4096/session/{id}/status` 确认该端点可用。

**预估工作量**: 0.5 小时（含验证）

---

## 4. 重要但非阻塞 GAP

> 这些 GAP 不会导致框架崩溃，但会降低可靠性、可维护性和用户体验。

---

### G4: Phase 5 Prompt 缺失 `{user_context}` — 🟡 MEDIUM

**文件**: `prompts/phase_5_validation.md`

**具体情况**:

Phase 5 的 Prompt 模板只声明了 4 个变量：`{project_dir}`, `{platform}`, `{phase_name}`, `{previous_outputs}`。没有声明和使用 `{user_context}`。

但 `orchestrator.py` 的 `_resolve_phase_user_context("phase_5_validation")` 已经准备好了 3 个关键信息：

```python
"phase_5_validation": {
    "original_run_command": parsed.get("original_run_command", ""),
    "previous_failures": parsed.get("previous_failures", ""),
    "config_path": parsed.get("config_path", ""),
},
```

**影响**: 修复循环 Agent **看不到**用户提供的原始运行命令、已知失败模式和配置文件路径。这意味着 Agent 在修复时必须"盲猜"，增加了修复失败的概率。

**修复方案**: 在 `phase_5_validation.md` 中追加：

```markdown
## Template Variables (续)
- `{user_context}` — JSON 对象，包含:
  - `original_run_command`: 项目原始运行命令
  - `previous_failures`: 用户报告的已知失败
  - `config_path`: 配置文件路径

## User Provided Context

{user_context}

Use the context above to understand the project's execution requirements.
When repairing errors, ensure the fixed script can be run with the original command.
```

**预估工作量**: 15 分钟

---

### G5: Phase 2/6/Error Recovery Prompt 缺失 `{user_context}` — 🟡 MEDIUM

**具体情况**: 类似 G4，以下三个模板未声明 `{user_context}`：

| 模板 | 已准备好但未使用的信息 |
|---|---|
| `phase_2_venv_create.md` | (当前映射为空 `{}`) |
| `phase_6_report.md` | (当前映射为空 `{}`) |
| `phase_error_recovery.md` | (当前映射为空 `{}`) |

**分析**: 目前这三个 Phase 的映射都返回空 `{}`，所以即使模板加变量也无数据可注入。但如果未来 Phase 0 解析出更多有用信息（如环境约束、依赖版本偏好），这些 Phase 会受益。

**修复建议**: 扩展 `_resolve_phase_user_context` 的映射：

```python
"phase_2_venv_create": {
    "dependency_notes": parsed.get("dependency_notes", ""),   # 新增
},
"phase_6_report": {
    "model_type": parsed.get("model_type", ""),
    "model_architecture": parsed.get("model_architecture", ""),
},
"phase_error_recovery": {
    "previous_failures": parsed.get("previous_failures", ""),
    "original_run_command": parsed.get("original_run_command", ""),
},
```

**预估工作量**: 0.5 天

---

### G6: `ValidationRetryPolicy` 是死代码 — 🟠 LOW-MEDIUM

**文件**: `core/validator_engine.py`, 第 52-78 行

**具体情况**:

```python
class ValidationRetryPolicy:
    max_retries: int = 3

    @staticmethod
    def error_envelope(schema_errors, rule_errors, warnings, attempt) -> dict:
        """Build a structured error envelope for retry feedback."""
        # ... 构建包含中文修复指令的 dict ...
```

该方法**从未被任何代码调用**。其设计意图是：在 Schema 校验失败后，不直接 abort，而是构建一个错误信封（envelope）发送回 LLM，请求重试。但当前实际行为是：Layer 1 失败 → 直接返回 `ValidationResult(ok=False)` → Phase 状态变为 `failed` → 转移到 `phase_error_recovery`。

**修复方案**:

在 `ValidatorEngine.validate_phase_output()` 中增加可配置的重试逻辑：

```python
def validate_phase_output(self, phase_id, output_data, attempt=1, max_retries=3):
    ...
    if schema_errors:
        if attempt < max_retries:
            # 返回错误信封，允许 LLM 重试
            envelope = ValidationRetryPolicy.error_envelope(
                schema_errors, [], warnings, attempt
            )
            return ValidationResult(
                ok=False,
                errors=errors,
                suggestion=json.dumps(envelope, ensure_ascii=False),
                retry_allowed=True,   # 新增字段
                retry_envelope=envelope,
            )
        # 超过最大重试次数 → 硬失败
        return ValidationResult(ok=False, errors=errors, ...)
```

然后在 `orchestrator.py` 中检查 `retry_allowed` 标志，决定是否给 LLM 重新发送修正请求。

**预估工作量**: 1 天

---

### G7: 4 个 Pydantic 模型零自定义校验 — 🟡 MEDIUM

**文件**: `schemas/phase_schemas.py`

**具体情况**:

以下模型仅有 Pydantic 自动生成的结构校验（类型、必填），没有 `@field_validator` 进行语义校验：

| 模型 | 缺少哪些校验？ |
|---|---|
| `ProjectAnalysisOutput` | `entry_script_candidates` 中的每个路径是否真实存在？`project_dir` 是否匹配？ |
| `EntryScriptOutput` | `script_path` 是否是真实存在的文件？`script_type` 是否有效？ |
| `ValidationFinalOutput` | `success=True` 但 `exit_code!=0` 是矛盾的（无校验拦截） |
| `ReportsOutput` | `report_paths` 中的每个路径是否真实存在？ |

**对比有校验的模型**:

```python
class EnvDetectOutput(BaseModel):
    @field_validator("platform")
    def platform_must_be_valid(cls, v):
        if v == "unknown":
            raise ValueError("未检测到 AI 加速器，无法继续")
        return v

class VenvOutput(BaseModel):
    @field_validator("npu_available")
    def npu_must_be_available(cls, v):
        if not v:
            raise ValueError("torch.npu.is_available() 返回 False，环境配置失败")
        return v
```

**修复方案**:

```python
class EntryScriptOutput(BaseModel):
    ...
    @field_validator("script_path")
    @classmethod
    def script_must_exist(cls, v):
        if not os.path.isfile(v):
            raise ValueError(f"脚本文件不存在: {v}")
        return v

class ValidationFinalOutput(BaseModel):
    ...
    @model_validator(mode="after")
    def validate_consistency(self):
        if self.success and self.exit_code != 0:
            # success 但 exit_code!=0 是矛盾的
            pass  # 或者选择 raise ValueError
        return self
```

**预估工作量**: 0.5 天

---

### G8: 零测试 — 🔴 CRITICAL (运维角度)

**具体情况**: `opencode-sm-orchestrator/` 下**没有任何** `test/`、`tests/` 目录，也没有 `pytest` 相关配置。

**覆盖率**: 0%

**风险矩阵**:

| 改动范围 | 无测试的风险 |
|---|---|
| 修改 `orchestrator.py` 分发逻辑 | 可能误改 Phase 转移路径，难以回归测试 |
| 新增正则规则到 classifier | 无法验证是否误匹配正常输出 |
| 修改 `_apply_result` | 无法验证修复验证逻辑是否正常工作 |
| 重构 `ArtifactStore` | 无法验证 journal/resume 逻辑 |

**推荐测试清单** (按优先级):

| 优先级 | 测试类型 | 测试目标 | 预估用例数 |
|---|---|---|---|
| P0 | 单元测试 | `HybridErrorClassifier.classify()` — 每种规则命中 + fallback | 20 |
| P0 | 单元测试 | `ValidatorEngine.validate_phase_output()` — 通过/失败/边界 | 15 |
| P0 | 单元测试 | `ArtifactStore` — journal CRUD / resume 逻辑 | 10 |
| P1 | 单元测试 | `StateMachineEngine.transition()` — 各种转移路径 | 12 |
| P1 | 单元测试 | `ValidationRepairLoopEngine` — 各种退出条件 | 8 |
| P2 | 集成测试 | Mock OpenCode HTTP → 验证 `_run_phase()` 全流程 | 7 (每个 Phase) |
| P2 | 集成测试 | Mock subprocess → 验证 `_run_local_phase()` | 3 |
| P3 | E2E | 完整 8 Phase 流水线 (小项目) | 1 |

**预估工作量**: 初始测试框架搭建 1 天 + 编写用例 2-3 天

---

## 5. 架构级设计缺陷

> 这些不是"bug"，而是设计层面的不足，在扩展或长期运行中会暴露问题。

---

### D1: `session_manager` 初始化失败无降级策略 — 🟡

**文件**: `core/orchestrator.py`, 第 70-78 行

```python
try:
    self.session_manager = SessionManager(base_url=base_url, ...)
except Exception as exc:
    self.session_manager = None      # ← 设为 None
    self.session_manager_error = str(exc)  # ← 只记错误
```

**问题**: 当 OpenCode 服务器不可达时，`session_manager` 为 `None`。后续：
- Phase 0/1/2/3/6 调用 `_execute_phase_session()` → 返回空 JSON → validation 失败
- Phase 5 的 `_dispatch_repair()` → 静默跳过修复 (`if self.session_manager is not None` 不成立)
- **没有降级路径**: 无法自动改用本地执行模式

**建议**:

```python
# 初始化时增加 fallback 模式
if self.session_manager is None:
    logger.warning("OpenCode server unavailable at %s, falling back to local mode", base_url)
    self.execution_mode = "local"
else:
    self.execution_mode = "session"

# 分发时根据模式选择路径
def _run_phase(self, phase_def):
    if self.execution_mode == "local":
        return self._run_local_fallback(phase_def)
    # ... normal session flow ...
```

---

### D2: Phase 4 Prompt 模板是死代码 — 🟠

**文件**: `workflow/npu_migration_v1.yaml` + `prompts/phase_4_rule_migration.md`

Phase 4 的 `agent` 为 `null` → 走 `_run_local_phase()` → 不调用 LLM → 不渲染 Prompt。
但模板文件存在 (52 行)，且声明了 `{user_context}` 变量。

**建议**:
1. 将 `phase_4_rule_migration.md` 改名为 `phase_4_rule_migration.md.disabled` 或添加注释说明
2. 或保留但作为文档（描述 Phase 4 做了什么）

---

### D3: `hook_context` 全局注入但所有模板零引用 — 🟠

**文件**: `core/orchestrator.py` 第 534-535 行

```python
"hook_context": json.dumps(hook_context, indent=2, sort_keys=True, default=str),
```

12 个 Prompt 模板全部接收此变量，但无一使用。

**建议**:
1. **保留但文档化**: 在模板中增加注释 `<!-- reserved: {hook_context} for future hook-to-LLM communication -->`
2. **或移除**: 如果短期内不使用，避免增加 token 成本（每个 session 多消耗 ~200 tokens）

---

### D4: 并发安全缺失 — 🟡

**文件**: `core/artifact_store.py` (全局)

**具体问题**:

1. **Journal 写入无锁**: `append_journal()` 做 `open(..., "a")` + `write`，无锁定保护。如果两个 Phase 同时完成（理论上不会，但不排除异常场景），可能产生交错写入。
2. **用户上下文无乐观锁**: `save_user_context_parsed` 读取 → 修改 → 写入，中间可能被其他进程覆盖。
3. **Phase 间 Artifact 引用可能过期**: `load_upstream()` 读取 canonical 文件，但如果上游 Phase 重试（新的 attempt），下游可能读到旧数据。

**建议**:

```python
# artifact_store.py - 增加 fcntl 文件锁 (Linux)
import fcntl

def append_journal(self, record):
    journal_path = ...
    with open(journal_path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(asdict(record)) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
```

---

### D5: 可观测性不足 — 🟡

**具体表现**:

| 缺失项 | 影响 |
|---|---|
| 无进度条 | 用户不知道当前在哪一步，还剩多少 |
| 无 Phase 耗时统计 | 无法定位性能瓶颈（哪个 Phase 跑最久） |
| 无 Token 消耗追踪 | 无法估算 OpenCode LLM 调用成本 |
| 无运行时状态查询 | 无法在长时间运行中检查当前状态（必须等跑完） |

**建议**:

```python
# orchestrator.py - 在 run() 中增加统计
class StateMachineOrchestrator:
    def run(self):
        start_time = time.monotonic()
        phase_stats = {}
        for phase_id in ...:
            phase_start = time.monotonic()
            result, details = self._run_phase(phase_def)
            elapsed = time.monotonic() - phase_start

            # 实时输出
            status_icon = "✅" if details["status"] == "succeeded" else "❌"
            print(f"  {status_icon} {phase_id} completed in {elapsed:.1f}s")

            phase_stats[phase_id] = {"elapsed": elapsed, **details}

        total = time.monotonic() - start_time
        print(f"\n  总耗时: {total:.1f}s")
        print(f"  结果: {_terminal_state}")

        return {**phase_stats, "_total_seconds": total}
```

---

## 6. 代码卫生问题

> 不修复不会导致故障，但会增加维护成本和认知负担。

---

### H1: 6 个空的 `__init__.py`

**文件**: `cli/__init__.py`, `config/__init__.py`, `core/__init__.py`, `hooks/__init__.py`, `utils/__init__.py`, `validators/__init__.py`

全部为 0 行空文件。虽然不影响功能（PyPI 和 sys.path 导入都能工作），但缺失了 `__all__` 公共 API 定义。

**建议**: 至少在 `core/__init__.py` 添加：

```python
__all__ = [
    "ArtifactStore",
    "ExecutionRecord",
    "StateMachineOrchestrator",
    "SessionManager",
    "ValidatorEngine",
    "ValidationRepairLoopEngine",
    "HybridErrorClassifier",
    "StateMachineEngine",
    "HookRunner",
]
```

---

### H2: Validator 脚本检查过浅

**文件**: `validators/validate_*.py` (7 个文件, 每个 18-30 行)

所有 Validator 脚本只做 `output.get("field") is None` 级别的检查。举例：

```python
# validate_venv.py
if not output.get("python_bin"):
    errors.append("python_bin is missing or empty")

installed_packages = output.get("installed_packages")
if installed_packages is None:
    errors.append("installed_packages is missing")
```

**缺失的有意义的检查**:
- `validate_venv`: `npu_available` 是否为 `True`？`torch_npu_version` 是否匹配预期？
- `validate_rule_migration`: `total_patterns_migrated > 0`（如果为 0 说明没有迁移发生）
- `validate_entry_script`: `script_path` 是否真实存在？`script_type` 是否在枚举值中？
- `validate_env_detect`: `sdk_path` 是否真实存在？

**建议**: 将 Layer 2 校验的逻辑从 Pydantic 的纯结构校验升级为语义校验。

---

### H3: `RepairHistoryEntry` TypedDict 字段不一致

**文件**: `core/repair_loop_types.py` + `core/validation_repair_loop.py`

`RepairHistoryEntry` TypedDict 定义:
```python
class RepairHistoryEntry(TypedDict):
    iteration: int
    category: str
    subtype: str
    suggested_fix: str
    fingerprint: str
```

但 `_execute_repair_session` 返回后，`_dispatch_repair` 额外添加了两个运行时字段：
```python
entry["repair_response"] = repair_response     # ← TypedDict 未定义
entry["repair_completed"] = repair_ok           # ← TypedDict 未定义
```

**建议**: 在 `RepairHistoryEntry` 中声明这些字段为 `NotRequired`，或创建 `FullRepairHistoryEntry` 子类。

---

### H4: `ValidationFinalOutput.repair_history` 类型不一致

**文件**: `schemas/phase_schemas.py` line 96-104

Pydantic 模型定义为 `dict[str, Any]`:
```python
repair_history: dict[str, Any] = Field(default_factory=dict)
```

但 `ValidationRepairLoopEngine.run()` 产生的修复历史是 `list[RepairHistoryEntry]`。

协调发生在 `orchestrator.py` 的 `_normalize_phase_output()`:
```python
if phase_id == "phase_5_validation" and isinstance(output.get("repair_history"), list):
    normalized = dict(output)
    normalized["repair_history"] = {"entries": output["repair_history"]}  # ← 手动包装
```

**建议**: 将 Pydantic 模型改为 `list[RepairHistoryEntry]` 类型，或在 TypedDict 和 Pydantic 之间建立明确的转换层。

---

## 7. Issue Summary Matrix

| ID | 类别 | 严重程度 | 组件 | 影响描述 | E2E 阻塞 | 修复优先级 | 预估工时 |
|---|---|---|---|---|---|---|---|
| **G1** | 错误分类 | 🔴 Critical | hybrid_error_classifier | 新型错误直接退出修复循环 | ✅ 是 | **P0** | 2-3 天 |
| **G2** | 修复循环 | 🔴 Critical | validation_repair_loop | 无法验证修复是否生效 | ✅ 是 | **P0** | 0.5 天 |
| **G3** | 会话管理 | 🔴 High | session_manager | 多 session 竞态导致误判空闲 | ⚠️ 条件 | **P0** | 0.5 小时 |
| **G4** | 模板一致性 | 🟡 Medium | phase_5 prompt | 修复 Agent 缺少关键上下文 | 否 | **P1** | 15 分钟 |
| **G5** | 模板一致性 | 🟡 Medium | phase_2/6/err prompt | 扩展后可能受益 | 否 | **P2** | 0.5 天 |
| **G6** | 重试机制 | 🟠 Low-Med | validator_engine | retry envelope 死代码 | 否 | **P2** | 1 天 |
| **G7** | Schema 校验 | 🟡 Medium | phase_schemas | 4 模型无自定义 validators | ⚠️ 弱 | **P1** | 0.5 天 |
| **G8** | 测试 | 🔴 Critical | 全局 | 零测试覆盖 | ⚠️ 运维 | **P0** | 3-4 天 |
| **D1** | 降级策略 | 🟡 Medium | orchestrator | 服务不可达无 fallback | ⚠️ 条件 | **P1** | 0.5 天 |
| **D2** | 代码卫生 | 🟠 Low | phase_4 | prompt 模板是死代码 | 否 | **P3** | 15 分钟 |
| **D3** | 模板一致性 | 🟠 Low | 全局 | hook_context 全局注入零引用 | 否 | **P3** | 15 分钟 |
| **D4** | 并发安全 | 🟡 Medium | artifact_store | 无文件锁保护 | ⚠️ 稀有 | **P2** | 0.5 天 |
| **D5** | 可观测性 | 🟡 Medium | 全局 | 无进度/耗时/token 统计 | 否 | **P2** | 1 天 |
| **H1** | 代码卫生 | ℹ️ Info | __init__.py | 6 个空文件 | 否 | **P3** | 30 分钟 |
| **H2** | Validator | 🟡 Medium | validators/*.py | 检查仅到字段级别 | ⚠️ 弱 | **P1** | 1 天 |
| **H3** | 类型安全 | ℹ️ Info | repair_loop_types | TypedDict 字段不一致 | 否 | **P3** | 15 分钟 |
| **H4** | 类型安全 | ℹ️ Info | phase_schemas | repair_history 类型包装 | 否 | **P3** | 15 分钟 |

---

## 8. 推荐修复优先级

### Phase 1: E2E 可运行 (阻塞项清除) — 预估 4-5 天

| 顺序 | 任务 | 依赖 | 验证标准 |
|---|---|---|---|
| 1 | **G3: 修复 wait_for_idle 端点** | 无 | `curl` 验证 `/session/{id}/status` 可用 |
| 2 | **G2: 增加 _apply_result 文件修改验证** | 无 | py_compile 验证修复后文件 |
| 3 | **G1: 修复 LLM classifier fallback** | 无 | 至少方案 B（扩展正则）到位 |
| 4 | **G8-P0 测试**: core 单元测试 | G1+G2 | pytest 通过 |

**完成后验证**: 对小项目运行完整 E2E 流水线，确保到达 Phase 6。

### Phase 2: 可靠性提升 — 预估 2-3 天

| 顺序 | 任务 | 依赖 | 验证标准 |
|---|---|---|---|
| 5 | **G4: Phase 5 prompt 注入 user_context** | 无 | 模板包含变量 |
| 6 | **G7: 4 模型增加 field_validator** | 无 | 错误路径被正确拦截 |
| 7 | **H2: Validator 脚本增加语义检查** | 无 | 每个 validator 至少 3 个检查点 |
| 8 | **D1: SessionManager 失败降级** | 无 | `--dry-run` 可显示 local fallback 模式 |

### Phase 3: 质量 & 可维护性 — 预估 1-2 天

| 顺序 | 任务 | 依赖 | 验证标准 |
|---|---|---|---|
| 9 | **D5: 可观测性** (进度/耗时) | 无 | 运行时输出进度信息 |
| 10 | **G5: 扩展剩余 Phase 的 user_context** | 无 | 映射表更新 |
| 11 | **D4: 并发安全** (文件锁) | 无 | journal 写入无交错 |
| 12 | **H1/H3/H4/D2/D3: 代码卫生** | 无 | `py_compile` + `mypy` clean |

### Phase 4 (可选): 高级功能

| 任务 | 描述 | 预估工时 |
|---|---|---|
| G6 重试机制 | 实现 ValidationRetryPolicy 的 LLM retry loop | 2 天 |
| 真实 LLM classifier | G1 方案 A — 完整的 LLM fallback | 2-3 天 |
| E2E 集成测试 | 完整流水线测试 (+ Mock OpenCode) | 2 天 |
| Dockerfile | 容器化部署 | 1 天 |

---

## 附录 A: 修复最小可行方案 (MVP Fix)

如果只需要让框架尽快能跑 E2E，**最小修复集**如下（总工时 ~1 天）:

```diff
# 1. G3: session_manager.py - 等点修复 (~15 min)
- payload = self._request("GET", "/session/status")
+ payload = self._request("GET", f"/session/{session_id}/status")

# 2. G2: validation_repair_loop.py - 增加验证 (~2 小时)
+ def _apply_result(self, repair_entry, project_dir):
+     self._verify_file_modified(repair_entry, project_dir)
      self.repair_history.append(repair_entry)

# 3. G1 B方案: hybrid_error_classifier.py - 扩规则 (~4 小时)
+ DETERMINISTIC_RULES += [   # 新增 8-10 条覆盖常见 NPU 错误
      ("env_dependency", "cann_version_mismatch", re.compile(...)),
      ("operator_incompat", "acl_launch_failed", re.compile(...)),
      ...
  ]

# 4. G4: Phase 5 prompt - 加 user_context (~15 min)
+ ## User Context: {user_context}
```

---

## 附录 B: 本次审查涉及的所有文件

```
opencode-sm-orchestrator/
├── cli/main.py                            ✅ 已读 319 行
├── cli/__init__.py                        ✅ 已验证 0 行
├── core/orchestrator.py                   ✅ 已读 759 行
├── core/state_machine.py                  ✅ 已读 90 行
├── core/session_manager.py                ✅ 已读 339 行
├── core/session_types.py                  ✅ 已读 54 行
├── core/validator_engine.py               ✅ 已读 236 行
├── core/validation_repair_loop.py         ✅ 已读 302 行
├── core/hybrid_error_classifier.py        ✅ 已读 170 行
├── core/artifact_store.py                 ✅ 已读 228 行
├── core/hook_runner.py                    ✅ 已读 48 行
├── core/hook_types.py                     ✅ 已读 37 行
├── core/config.py                         ✅ 已读 98 行
├── core/repair_loop_types.py              ✅ 已读 64 行
├── core/server_bootstrap.py               ✅ 已读 136 行
├── schemas/phase_schemas.py               ✅ 已读 129 行
├── schemas/output_*.json (8 files)        ✅ 已验证存在
├── validators/validate_*.py (7 files)     ✅ 已读全部
├── hooks/*.py (12 files)                  ✅ 已读全部
├── prompts/phase_*.md (8 files)           ✅ 已读 6 + 验证 2
├── prompts/repair/*.md (3 files)          ✅ 已验证存在
├── prompts/_global_constraints.md         ✅ 已验证存在
├── utils/sm_rule_migrator.py              ✅ 已读 185 行
├── workflow/npu_migration_v1.yaml         ✅ 已读 128 行
├── config/llm_classifier_v1.yaml          ✅ 已验证存在
├── config/tool_policies_v1.yaml           ✅ 已验证存在
└── plugins/sm-orchestrator/src/*.ts       ✅ 已验证存在
```
