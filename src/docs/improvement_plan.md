# migration_utils Pipeline 改进方案 — 完整设计文档

## 文档信息

| 字段 | 值 |
|------|-----|
| 创建日期 | 2026-04-21 |
| 状态 | 待实施 |
| 目标 | 解决 Deepwave E2E 测试中 CPU fallback 未被拦截的根本原因 |

---

## 背景与问题

Deepwave 项目 E2E 测试虽然 exit 0、loss 下降，但实际 100% 的波传播计算走 CPU（`libdeepwave_C.so` 仅有 `_cpu` / `_cuda` 符号，LLM 在 Python 层添加了 `npu→cpu` device 映射和张量 `.to('cpu')` 转换）。根因：

1. **`ADAPTATION_REQUIREMENTS.md` 未被注入任何 LLM prompt** — LLM 不知道该项目的"零 CPU fallback"约束
2. **Phase 4 是纯正则替换** — 不理解 C 共享库依赖，不分析计算后端架构
3. **`operator_fixer` 从未被触发** — 错误分析器将 Python 层 crash 分类为 "migration logic" 而非 "operator"
4. **Phase 5 没有独立审查环节** — exit 0 即判为成功，不验证计算是否真正在 NPU 执行

---

## 改动总览

```
src/
├── scripts/sm_adapt_cli.py                          # 新增 --user-constraints 参数
├── core/orchestrator.py                             # 传递约束 + 注入 review session 到 Phase 5
├── core/phase_runner.py                             # 新增 run_phase_1_5() + run_review_check()
├── core/repair_loop.py                              # 接入 review→analyzer 反馈 + 约束注入
├── prompts/                                         # 新增/修改 prompt 模板
│   ├── phase_1_5_constraint_summary.md              # [新增] Phase 1.5 专用
│   ├── phase_5_review.md                            # [新增] Review Agent 专用
│   ├── phase_1_project_analysis.md                  # [修改] 增加 {user_constraints} 占位符
│   ├── phase_error_recovery.md                      # [修改] 注入约束摘要 + 审查反馈
│   ├── repair_code_adapter.md                       # [修改] 优先 NPU + 角色边界
│   ├── repair_dependency_fixer.md                   # [修改] 依赖修复 prompt + 角色边界
│   └── repair_operator_fixer.md                     # [修改] 三行瘦身 prompt + runtime artifact 引用
└── prompts/ (其他文件保持不变)
```

---

## 改动详细设计

### 改动 1: CLI 入口 — 用户指令输入

**文件**: `scripts/sm_adapt_cli.py`

#### 1.1 新增 `--user-constraints` 参数

```python
parser.add_argument(
    "--user-constraints",
    type=str,
    default=None,
    help=(
        "User-defined constraints for the migration. "
        "Accepts either a direct string or a file path (e.g. ADAPTATION_REQUIREMENTS.md). "
        "If a file path is given, its contents are read and used as the constraint text."
    ),
)
```

#### 1.2 解析逻辑

```python
def _resolve_user_constraints(raw: str | None) -> str:
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return raw.strip()

user_constraints = _resolve_user_constraints(args.user_constraints)
```

#### 1.3 传递到 Orchestrator

```python
result = orchestrator.run_workflow(
    project_dir=args.project_dir,
    user_command=args.user_command,
    user_constraints=user_constraints,
)
```

---

### 改动 2: Orchestrator — 约束传递 + Review Agent 绑定

**文件**: `core/orchestrator.py`

#### 2.1 `run_workflow` 接收 `user_constraints`

```python
def run_workflow(
    self,
    project_dir: str,
    user_command: str | None = None,
    user_constraints: str = "",
) -> dict[str, object]:
    ...
    self.user_constraints = user_constraints
    ...
```

#### 2.2 Phase 0-1 → Phase 1.5 → Phase 2-4 的流水线

```python
# Phase 0-1: 正常执行
phase_0_to_1_outputs = runner.run_phase_0_to_1(
    active_project_dir, runner_session_mgr, artifact_store,
)

# Phase 1.5: 如果用户提供了约束, 生成约束摘要
constraint_summary = ""
if user_constraints:
    constraint_summary = runner.run_phase_1_5(
        main_session_id, runner_session_mgr, artifact_store,
        project_dir=active_project_dir,
        user_constraints=user_constraints,
        phase_1_output=phase_0_to_1_outputs.get("phase_1_project_analysis", {}),
    )

# Phase 2-4: 继续 (Phase 2+ 可读取 constraint_summary)
phase_2_to_4_outputs = runner.run_phase_2_to_4(
    active_project_dir, runner_session_mgr, artifact_store, migrator,
    constraint_summary=constraint_summary,
)

# 合并所有输出
phase_outputs = {**phase_0_to_1_outputs, **phase_2_to_4_outputs}
phase_outputs["constraint_summary"] = constraint_summary
```

#### 2.3 将 main_session (Phase 0-3) 作为 review agent 暴露给 Phase 5

```python
main_session_id = result.get("main_session_id")

phase_5_output = repair_engine.run(
    entry_script,
    active_project_dir,
    review_session_id=main_session_id,
    phase_0_to_3_outputs=phase_outputs,
    constraint_summary=constraint_summary,
)
```

---

### 改动 3: Phase Runner — 拆分职责 + 新增 Phase 1.5

**文件**: `core/phase_runner.py`

#### 3.1 将 `run_phase_0_to_3` 拆分为 `run_phase_0_to_1` + `run_phase_2_to_3`

使 Phase 1.5 能插入中间：

```python
def run_phase_0_to_1(
    self,
    project_dir: str,
    session_mgr: SessionManagerLike,
    artifact_store: ArtifactStore,
) -> dict[str, JsonObject]:
    """Run Phase 0 and Phase 1. Returns outputs for downstream use."""
    session_id = session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")
    outputs: dict[str, JsonObject] = {}

    for phase_id in ("phase_0", "phase_1"):
        context: JsonObject = {
            "project_dir": project_dir,
            "previous_outputs": outputs,
        }
        result = self._run_single_phase(
            session=session_id, phase_id=phase_id, context=context,
            session_mgr=session_mgr, artifact_store=artifact_store,
        )
        outputs[self._resolve_phase_spec(phase_id).prompt_id] = result

    return outputs


def run_phase_2_to_3(
    self,
    project_dir: str,
    session_mgr: SessionManagerLike,
    artifact_store: ArtifactStore,
    prior_outputs: dict[str, JsonObject],
) -> dict[str, JsonObject]:
    """Run Phase 2 and Phase 3. Reuses the persistent main_engineer session."""
    session_id = session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")
    outputs: dict[str, JsonObject] = dict(prior_outputs)

    for phase_id in ("phase_2", "phase_3"):
        context: JsonObject = {
            "project_dir": project_dir,
            "previous_outputs": prior_outputs,
        }
        result = self._run_single_phase(
            session=session_id, phase_id=phase_id, context=context,
            session_mgr=session_mgr, artifact_store=artifact_store,
        )
        outputs[self._resolve_phase_spec(phase_id).prompt_id] = result

    return outputs
```

#### 3.2 新增 `run_phase_1_5` — 独立约束摘要生成

```python
def run_phase_1_5(
    self,
    main_session_id: str,
    session_mgr: SessionManagerLike,
    artifact_store: ArtifactStore,
    *,
    project_dir: str,
    user_constraints: str,
    phase_1_output: JsonObject | None = None,
) -> str:
    """Run Phase 1.5: Generate migration constraint summary.

    Sends user-provided constraints to the main_engineer session (which already
    has full Phase 0-1 project context) and asks it to produce a concise,
    actionable list of migration rules.  The summary is returned as a string
    and will be injected into all subsequent phases (Phase 2+ and Phase 5).

    Args:
        main_session_id: The persistent main_engineer session.
        project_dir: Project root path.
        user_constraints: Raw user constraint text (string or file content).
        phase_1_output: Phase 1 JSON output for context (optional).

    Returns:
        Constraint summary string (numbered imperative rules).
    """
    prompt = self.prompt_loader.load_prompt("phase_1_5_constraint_summary", {
        "project_dir": project_dir,
        "user_constraints": user_constraints,
        "phase_1_context": self._serialize_context(phase_1_output or {}),
    })

    raw_response = session_mgr.send_command(main_session_id, prompt, timeout=300)

    # Extract summary from response: prefer JSON "constraint_summary" field,
    # otherwise treat the full response as the summary.
    parsed = dict(extract_json_response(raw_response))
    summary = parsed.get("constraint_summary", raw_response.strip())

    # Optional: save to artifact store for audit trail
    _ = artifact_store.save_phase_output("phase_1_5_constraint_summary", {
        "user_constraints_raw": user_constraints,
        "constraint_summary": summary,
        "project_dir": project_dir,
    }, attempt=1)

    return summary
```

#### 3.3 约束摘要注入到 Phase 2+

修改 `_build_prompt_context`：

```python
def _build_prompt_context(
    self, phase: PhaseSpec, context: JsonObject,
) -> dict[str, str]:
    previous_outputs = context.get("previous_outputs", {})
    return {
        "phase_name": str(context.get("phase_name", phase.prompt_id)),
        "project_dir": str(context.get("project_dir", ".")),
        "previous_outputs": self._serialize_context(previous_outputs),
        "constraint_summary": str(context.get("constraint_summary", "")),
    }
```

#### 3.4 新增 `run_review_check` — Review Agent 方法

```python
def run_review_check(
    self,
    review_session_id: str,
    session_mgr: SessionManagerLike,
    phase_0_to_3_outputs: dict[str, JsonObject],
    project_dir: str,
    repair_context: dict[str, object],
) -> JsonObject:
    """Send a repair modification to the review agent for audit.

    The review agent (main_engineer persistent session from Phase 0-3) has
    complete project context and can assess whether a repair is valid,
    NPU-native, and respects migration constraints.

    Args:
        review_session_id: Session ID of the review agent.
        phase_0_to_3_outputs: All Phase 0-3 outputs as context.
        repair_context: Dict with keys:
            - iteration: repair iteration number
            - error_text: the error that triggered this repair
            - classification: error analyzer's classification dict
            - repair_role: which repair agent was used
            - fix_instruction: the full prompt sent to repair agent
            - fix_response: the repair agent's response text
            - fix_metadata: parsed JSON with modified_files and summary

    Returns:
        Review result dict with verdict, cpu_fallback status, suggestions.
    """
    prompt = self.prompt_loader.load_prompt("phase_5_review", {
        "project_dir": project_dir,
        "previous_outputs": self._serialize_context(phase_0_to_3_outputs),
        "repair_iteration": str(repair_context.get("iteration", "?")),
        "repair_error": str(repair_context.get("error_text", "")),
        "repair_role": str(repair_context.get("repair_role", "")),
        "repair_classification": self._serialize_context(
            repair_context.get("classification", {})
        ),
        "repair_files": self._serialize_context(
            repair_context.get("fix_metadata", {}).get("modified_files", [])
        ),
        "repair_summary": str(
            repair_context.get("fix_metadata", {}).get("summary", "")
        ),
        "repair_instruction": str(repair_context.get("fix_instruction", "")),
        "repair_response": str(repair_context.get("fix_response", "")),
    })

    raw_response = session_mgr.send_command(review_session_id, prompt, timeout=600)
    parsed: JsonObject = dict(extract_json_response(raw_response))

    return {
        "verdict": parsed.get("verdict", "unknown"),
        "cpu_fallback_detected": bool(parsed.get("cpu_fallback_detected", False)),
        "cpu_fallback_necessary": bool(parsed.get("cpu_fallback_necessary", False)),
        "alternative_suggestions": parsed.get("alternative_suggestions", ""),
        "reasoning": parsed.get("reasoning", ""),
    }
```

---

### 改动 4: Repair Loop — review 反馈注入

**文件**: `core/repair_loop.py`

#### 4.1 `run` 方法签名扩展

```python
def run(
    self,
    entry_script: str,
    project_dir: str,
    max_iterations: int = 5,
    logger: Callable[[str], None] | None = None,
    review_session_id: str | None = None,
    phase_0_to_3_outputs: dict[str, object] | None = None,
    constraint_summary: str = "",
) -> dict[str, object]:
```

#### 4.2 每次修复后触发 review

在执行完修复 agent 响应后、记录之前：

```python
fix_metadata = self._extract_fix_summary(repair_session_id, repair_response)

# Review step (NEW)
review_result: JsonObject | None = None
if review_session_id and phase_0_to_3_outputs:
    try:
        review_result = self._session_mgr.send_command(
            review_session_id,  # 通过 PhaseRunner.review_check
            ...
        )
        # 实际通过传入的 phase_runner 调用 run_review_check
    except Exception as e:
        self._log(f"[Iter {iteration}] Review step failed: {e}", logger)
        review_result = None

# 记录 review 结果到 iter record
context.last_review = review_result  # 新增字段
```

> **注意**：由于 RepairLoopEngine 不直接持有 PhaseRunner，有两种传法：
> - 方式 A：Orchestrator 传入一个 `review_callable` 回调
> - 方式 B：Orchestrator 在 `run` 之后、下一次迭代之前手动调用 review
>
> 推荐方式 B — 更解耦，Orchestrator 在每次 iter 后显式调用 review。

#### 4.3 审查结果注入 error analyzer prompt

```python
def _analyze_error(
    self,
    *,
    analyzer_session_id: str,
    entry_script: str,
    project_dir: str,
    iteration: int,
    error_text: str,
    history: list[object],
    constraint_summary: str = "",
    last_review: JsonObject | None = None,
) -> ClassificationDict:
    prompt_context = {
        "phase_name": _PHASE_ID,
        "project_dir": project_dir,
        "failed_phase": _PHASE_ID,
        "entry_script": entry_script,
        "iteration": str(iteration),
        "previous_outputs": self._format_error_analyzer_context(history, error_text),
        "failure_log": error_text,
        "constraint_summary": constraint_summary,
        "last_review": self._serialize(last_review) if last_review else "(No review available)",
    }
    ...
```

#### 4.4 Review 影响分类倾向

当 review 检测到非必要的 CPU fallback 时，analyzer 应倾向于 `"operator"` 分类：

在 `phase_error_recovery.md` 中增加规则（见下方 prompt 改动）。

---

## Prompt 模板改动

### 新增: `prompts/phase_1_5_constraint_summary.md`

```markdown
# Phase 1.5 - Migration Constraint Summary Generation

You have just completed Phase 1 project analysis for a CUDA-to-NPU migration project.

## Project Directory
{project_dir}

## Phase 1 Analysis Results
{phase_1_context}

## User-Provided Migration Constraints
The user has explicitly provided the following constraints for this migration:

{user_constraints}

## Goal
Produce a concise, actionable list of migration rules derived from the user constraints, adapted to the specific project context you analyzed in Phase 1.

## Required Actions
1. Read each user constraint carefully and understand its intent.
2. Cross-reference with your Phase 1 analysis (project structure, dependencies, CUDA patterns, compiled extensions).
3. For each user constraint, derive 1-2 specific, imperative migration rules that apply to THIS project. For example:
   - If user says "zero CPU fallback", and you found `libdeepwave_C.so` during analysis → "Port all C library functions used by Python (e.g. scalar_iso_2d_2_float_forward_*) from CUDA/C to AscendC. Do not redirect NPU device to CPU in Python."
   - If user says "no modification of official source logic" → "Add new backend routing in backend_utils.py instead of modifying existing functions."
4. Keep the total list under 10 items.
5. Make each rule specific, testable, and project-aware — do NOT produce generic rules like "use NPU instead of CUDA".

## Hard Rules
- Do not dilute or remove user constraints. If a constraint is technically challenging, note the challenge but still include it as a rule.
- If a user constraint conflicts with the project's architecture, flag it and explain why, but still include it.
- The rules you generate WILL be injected into ALL subsequent phases (Phase 2, 3, 4, 5 repair agents, error analyzer, and review agent). They are binding.

## Output Format
End with a JSON block:
```json
{
  "constraint_summary": "1. [rule]\n2. [rule]\n3. [rule]\n...",
  "constraint_count": 3,
  "challenges_flagged": ["If any constraint has technical challenges, note them here"]
}
```
```

---

### 修改: `prompts/phase_1_project_analysis.md`

在模板头部增加用户约束展示区（即使此时还未生成摘要，Phase 1 也需要知道存在用户约束）：

```markdown
# Phase 1 - Project Analysis

## User-Provided Constraints (for awareness)
{user_constraints}

*Note: A detailed, project-specific constraint summary will be generated in Phase 1.5.*

## Goal
... (原有内容保持不变)
```

需要确保 prompt_loader 在加载此模板时，`{user_constraints}` 有默认空值：

```python
# 在 _build_prompt_context 中确保默认值
"constraint_summary": str(context.get("constraint_summary", "")),
"user_constraints": str(context.get("user_constraints", "")),
```

---

### 修改: `prompts/phase_2_venv_create.md`

注入约束摘要：

```markdown
## Migration Constraints (from Phase 1.5)
{constraint_summary}

*These constraints are binding. Keep them in mind when setting up the environment.*
```

---

### 修改: `prompts/phase_3_entry_script.md`

注入约束摘要：

```markdown
## Migration Constraints (from Phase 1.5)
{constraint_summary}

*These constraints are binding. Consider them when selecting the entry script.*
```

---

### 新增: `prompts/phase_5_review.md` — Review Agent

```markdown
# Phase 5 - Repair Review Agent

You are the migration review agent. You have complete knowledge of this project from Phase 0-3 (project structure, dependencies, CUDA patterns, build system, compiled extensions, entry script, and migration constraints).

## Repair Context

### 1. What is the problem being fixed?
{repair_error}

### 2. Who fixed it?
Repair Role: {repair_role}
- `dependency_fixer`: Handles packages, version conflicts, install commands
- `code_adapter`: Handles Python-level CUDA→NPU API replacements
- `operator_fixer`: Handles missing NPU operators, C kernel ports, custom op implementation

### 3. What files were modified?
{repair_files}

### 4. How was it fixed?
Fix Summary: {repair_summary}

Error Analyzer Classification:
{repair_classification}

### Full Repair Instruction (what the repair agent was told)
{repair_instruction}

### Full Repair Response (what the repair agent did)
{repair_response}

## Original Project Context
{previous_outputs}

## Goal
Review this repair modification and determine:
1. Whether the fix is correct and sufficient for the identified problem.
2. Whether the fix introduces or relies on CPU fallback patterns.
3. If CPU fallback is used, whether it is technically unavoidable or if an NPU-native alternative exists.
4. Whether the fix respects the project's migration constraints.

## Review Checklist
1. **Correctness**: Does this fix actually resolve the original error?
2. **NPU Compliance**: Check for CPU fallback patterns:
   - Device mapping: `if device == 'npu': device = 'cpu'`
   - Explicit conversions: `.to('cpu')`, `.cpu()`, `device='cpu'`
   - Library calls to functions lacking `_npu` symbols
3. **Constraint Compliance**: Does this fix violate any migration constraints from Phase 1.5?
4. **Root Cause vs Symptom**: Did the fix address the root cause, or just suppress the error?
5. **Better Alternatives**: Is there a lower-level, more correct fix that keeps execution on NPU?

## If CPU Fallback Is Detected
Determine:
- **Is it necessary?** E.g., the C library genuinely cannot execute on NPU hardware and must be recompiled with AscendC.
- **Is there an alternative?** E.g.:
  - Can the C/CUDA kernel be ported to AscendC?
  - Can PyTorch native ops (that work on NPU) replace the custom kernel?
  - Can the computation be restructured to avoid the unsupported op?

## Output Format
End with a single JSON:
```json
{
  "verdict": "accept | accept_with_warning | reject",
  "cpu_fallback_detected": true,
  "cpu_fallback_necessary": false,
  "alternative_suggestions": "Port scalar_iso_2d_2_float_forward_cuda from CUDA to AscendC. The kernel performs FDTD wave propagation which maps directly to AscendC's CubeUnit vector operations...",
  "reasoning": "The fix adds device_str='cpu' mapping in backend_utils.py and .to('cpu') on all computation tensors in common.py. This means 100% of wave propagation runs on CPU. The constraint requires zero CPU fallback. Alternative: port the C kernel..."
}
```

## Verdict Rules
- `"accept"`: Fix is correct, NPU-native, respects all constraints.
- `"accept_with_warning"`: Fix resolves the error and is acceptable, but has minor concerns or trade-offs worth noting.
- `"reject"`: Fix introduces unacceptable CPU fallback, violates constraints, or fails to address the root cause.
```

---

### 修改: `prompts/phase_error_recovery.md` — Error Analyzer

```markdown
# Error Recovery

You are the error analyzer for `{phase_name}` in `{project_dir}`.
The failed phase is `{failed_phase}`.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. When diagnosing failures and suggesting fixes, always prefer solutions that keep computation on NPU. CPU fallback should be a LAST RESORT.

## Current Failure
```
{failure_log}
```

## Fix History
{previous_outputs}

## Previous Review Assessment
{last_review}

The above is the review assessment of the previous iteration's repair attempt.
- If the review detected CPU fallback and suggested an alternative (e.g. porting a C kernel), give STRONG weight to that suggestion.
- If the review rejected the fix with specific alternatives, do NOT recommend repeating the same approach.

## Goal
- Diagnose why the phase keeps failing.
- Identify the smallest credible fix that resolves the root cause.
- Classify the failure and assign it to the right repair role.
- Decide whether the phase is ready to retry or should stop.

## Required Actions
1. Identify the exact failed step, command, or file operation from the current failure below.
2. Compare the current failure against the fix history above — does the same category keep recurring?
3. Trace the failure to one bucket:
   - **environment**: missing env vars, wrong Python version, device not detected
   - **dependency**: missing/mismatched packages, import errors, version conflicts
   - **pathing**: wrong file paths, missing files, directory issues
   - **migration logic**: incomplete CUDA-to-NPU code migration (Python-level API replacements)
   - **operator**: missing/unsupported NPU operators, unsupported math operations, C/CUDA kernel lacking NPU equivalent, shared library with `_cuda` symbols but no `_npu` symbols
   - **validation**: validation script issues, incorrect pass/fail logic
   - **unknown**: cannot determine root cause
4. **NPU-First Diagnosis Rule**: When the error involves a compiled shared library (.so) or custom op:
   a. First check: is the C library compiled for x86_64 or aarch64? NPU memory (HBM) is not accessible by CPU code.
   b. If the C library has `_cuda` symbols but NO `_npu` symbols → this is an **operator** issue, not migration logic. The kernel needs to be ported to AscendC.
   c. Do NOT classify as "migration logic" when the real gap is at the C/kernel level.
5. **Review Feedback Integration**: If the previous review assessment detected CPU fallback and suggested alternatives, consider classifying this as `"operator"` with `"repair_role": "operator_fixer"` to force operator-level fixes.
6. Propose the minimum corrective action that lets the workflow continue, prioritizing NPU-native solutions.
7. If the failure is package or installation related, recommend domestic mirrors first (阿里云镜像 or 清华镜像).

## Hard Rules
- Do not restate the full failure log — quote only short fragments when necessary as evidence.
- Do not claim a root cause without supporting evidence from the current failure or fix history.
- The fix history table shows what was tried before. Do NOT recommend repeating the same fix for the same category.
- **NPU-First**: Always suggest NPU-native fixes first. CPU fallback is the last resort.
- Prefer deterministic fixes over broad speculative refactors.
- Keep the response concise, operational, and directly usable by the next retry attempt.

## Output Format
First, provide your reasoning and diagnosis in free text. Then, at the end of your response, append a JSON code block with exactly these keys:

```json
{
  "category": "<bucket from Required Actions #3>",
  "root_cause": "<specific explanation>",
  "suggested_fix": "<concrete corrective action>",
  "repair_role": "<dependency_fixer | code_adapter | operator_fixer>"
}
```

## Repair Role Descriptions
- `dependency_fixer`: Fix missing/mismatched packages, install commands, version conflicts, mirror configuration.
- `code_adapter`: Fix CUDA-to-NPU code migration at the Python level — device placement, API replacements, tensor operations. Must prioritize NPU-native solutions. If the root cause is a C library limitation (not a Python API issue), STOP and report it — do not implement CPU fallback.
- `operator_fixer`: Fix missing/unsupported NPU operators — implement custom operators, compose alternatives from NPU-supported primitives, or port CUDA kernels to AscendC. ALL fixes must be NPU-native.

## Retry Decision Rule
- Pick a role only when a concrete fix path exists for that role.
- If no concrete fix exists for any role, set `"category": "unknown"` and pick the most plausible role anyway — the system will stop after 3 repeated identical failures.

## Role Boundary Enforcement
If you determine the root cause falls outside the current repair agent's scope (e.g. the error analyzer classified it as "migration logic" but the real issue is a missing C operator), classify it correctly as `"operator"` and assign `"repair_role": "operator_fixer"`. Do NOT pass kernel-level problems to `code_adapter`.
```

---

### 修改: `prompts/repair_code_adapter.md`

```markdown
# Repair: Code Adapter

You are a code adaptation specialist for a CUDA-to-NPU migration project.

## Execution Failure
```
{error_text}
```

## Error Classification
- Category: {category}
- Root Cause: {root_cause}
- Suggested Fix: {suggested_fix}

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. CPU fallback is explicitly restricted — prioritize NPU-native solutions in all fixes.

## Previous Repair Attempts
{history_summary}

## Previous Review Assessment
{last_review}

If the previous review detected CPU fallback in an earlier fix, do NOT repeat that pattern. Instead, look for NPU-native alternatives or escalate to operator_fixer if the issue requires C-level changes.

## Goal
Modify project source code to fix execution failures caused by CUDA-NPU incompatibilities.

## Required Actions
1. Analyze the execution failure to identify which code location needs modification.
2. **Scope Check**: Before making any changes, verify the root cause is actually at the Python level (API calls, device strings, tensor placement).
   - If the real issue is a compiled shared library (.so) lacking NPU support → STOP. Do NOT implement CPU fallback. Report that the issue requires `operator_fixer` to port the C kernel.
   - If the issue is purely Python (e.g. `torch.cuda.current_stream` instead of `torch.npu.current_stream`, wrong device string) → proceed with the fix.
3. Apply the code change — replace CUDA APIs with NPU equivalents, fix device placement, adjust tensor operations.
4. All device placement must use `npu` device type. Verify `torch.npu` APIs replace `torch.cuda` APIs.
5. **NPU-First**: If your fix would map NPU device to CPU (e.g. `if device == 'npu': device = 'cpu'`), STOP. This is CPU fallback. Instead, explore alternatives or report the limitation.
6. Apply the fix directly — do not ask questions or request confirmation.
7. After applying the fix, you MUST try running the project entry script yourself. Use the project's `.venv/bin/python` interpreter and the entry command provided below.
8. When running the entry script, you MUST wrap the execution with a timeout so the process does not hang indefinitely.
9. If the script runs successfully (exit code 0), report the success and the output.
10. If the script fails with an error outside your scope (dependency, environment, C kernel limitation), stop and report the new error.
11. If the script fails with a CUDA-NPU issue still within your scope, apply another fix and retry (up to 3 times).

## Entry Script Information
- Project directory: `{project_dir}`
- Virtual environment: `{project_dir}/.venv/bin/python`
- Entry command: `{entry_script}`

## Hard Rules
- Assigned role: {repair_role}
- **Your scope**: Python-level CUDA→NPU API replacements, device string fixes, tensor operation adjustments.
- **NOT your scope**: C/CUDA shared library modifications, AscendC kernel development, CPU fallback workarounds.
- If the root cause requires C-level changes, STOP and report: describe what C function needs porting, which library it's in, and that `operator_fixer` should handle it.
- Apply the fix directly. Do not ask questions, propose options, or request confirmation.
- Only modify files that are directly related to the identified failure.
- Do not modify unrelated logic, formatting, comments, or documentation.
- Ensure all changes are NPU-native — do not introduce CPU-fallback patterns.
- At the end of your response, append a JSON code block with:
  - `"modified_files"`: list of file paths you changed (relative to project dir)
  - `"summary"`: a 1-2 sentence description of what you fixed
  - `" escalated_to"`: if you stopped because the issue is outside your scope, describe what needs to be done and who should do it
```

---

### 修改: `prompts/repair_dependency_fixer.md`

`dependency_fixer` 保持依赖修复专用 prompt，直接接收 `Execution Failure`、`Error Classification`、`Migration Constraints`、`Required Actions`、`Hard Rules` 和 `agent_diagnostics` 说明，用于安装、更新或配置 NPU 兼容依赖。

---

### 修改: `prompts/repair_operator_fixer.md`

```markdown
1.先读取 {runtime_error_artifact_path} 和 {runtime_card_artifact_path}。
2.做 Ascend NPU 原生修复，不要加 CPU fallback；若是 custom-op 项目，严格遵守下方 operator_custom_op_guidance。
3.{operator_custom_op_guidance}
4.保持 bounded parallel 子任务和最终 JSON 输出要求。
```

`operator_fixer` 保持 bounded runtime artifacts + `operator_custom_op_guidance` 的提示结构。运行时由 `core/runtime_artifacts.py` 生成上下文文件，再由 `core/repair_loop.py` / `core/workflow_executor.py` 注入 guidance 文本，而不是在 prompt 模板中直接暴露旧的 operator context path 字段：

- `runtime_error_<project>.md`: `Operator Fixer`、`Execution Failure`、`Error Classification`、项目目录和入口命令。
- `runtimeCard_<project>.md`: analyzer-selected `Experience Card 1..N`，无卡片时写入显式空说明。
- `operatorRepairContext_<project>.md`: operator 总数、清单、进度、最终 gate 目标和入口脚本规则；custom-op 项目通过 `operator_custom_op_guidance` 引导 agent 读取该 bounded context。

---

## 数据流全貌

```
                          用户输入
                            │
                            ▼
              ┌─────────────────────────┐
              │   CLI (--user-          │
              │    constraints)         │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
    ┌────────▶│   Orchestrator          │◀──────────────┐
    │         │   - 解析 user_          │               │
    │         │     constraints         │               │
    │         │   - 传递至各 Phase      │               │
    │         └──────┬──────┬───────────┘               │
    │                │      │                           │
    │                │      │ main_session_id           │
    │                ▼      │ (review agent)            │
    │         ┌─────────┐   │                           │
    │         │Phase 0-1│   │                           │
    │         │(main    │   │                           │
    │         │engineer)│   │                           │
    │         └────┬────┘   │                           │
    │              │        │                           │
    │              ▼        │                           │
    │         ┌─────────────┴──────┐                    │
    │         │ Phase 1.5:         │                    │
    │         │ Constraint Summary │                    │
    │         │ Generation         │                    │
    │         └────┬───────────────┘                    │
    │              │                                    │
    │              ▼                                    │
    │         ┌────────────┐                            │
    │         │Phase 2-4   │ ← constraint_summary 注入  │
    │         │(LLM+规则)  │                            │
    │         └─────┬──────┘                            │
    │               │                                   │
    │               ▼                                   │
    │    ┌──────────────────────┐                       │
    │    │ Phase 5: Repair Loop │                       │
    │    │                      │◀──────────────────────┤
    │    │ ┌──────────────┐     │   review反馈注入       │
    │    │ │ Iter 1       │     │                       │
    │    │ │ 修复→审查    │─────┤───────────────────────┘
    │    │ └──────┬───────┘     │
    │    │ ┌──────▼───────┐     │  error_analyzer:
    │    │ │ Iter 2       │     │  - 收到 constraint_summary
    │    │ │ 分析→修复→   │     │  - 收到 last_review
    │    │ │ 审查→反馈    │─────┤
    │    │ └──────┬───────┘     │  repair_agents:
    │    │ ┌──────▼───────┐     │  - 收到 constraint_summary
    │    │ │ ...          │     │  - 收到 last_review
    │    │ │ 成功/停滞    │     │  - 角色边界 enforced
    │    │ └──────────────┘     │
    │    └──────────────────────┘
    │               │
    │               ▼
    │         ┌─────────┐
    │         │Phase 6  │
    │         │(Report) │
    │         └─────────┘
    │
    ▼
  返回结果
```

---

## 关键设计决策

### 决策 1: Phase 1.5 作为独立指令而非 Phase 1 的一部分

**理由**:
- Phase 1 的职责是项目分析（结构、依赖、入口），输出是结构化的 JSON。
- 约束摘要生成需要 Phase 1 的分析结果作为上下文 — LLM 需要先理解项目才能将用户约束转化为项目特定的规则。
- 如果合并，一个 prompt 里既要分析项目又要生成约束摘要，容易导致 JSON 格式混乱或摘要质量下降。
- 独立指令保证 Phase 1 输出不变、Phase 1.5 产出专门化的摘要、下游所有 Phase 都可直接读取。

### 决策 2: Review Agent 复用 main_engineer session

**理由**:
- main_engineer (Phase 0-3) 已经建立了完整的项目上下文（结构、依赖、编译扩展、入口脚本、迁移清单）。
- 复用 session 避免额外创建独立 review agent（节省 token、避免信息丢失）。
- main_engineer 的 persistent lifecycle 使其在整个 pipeline 期间保持上下文记忆。

### 决策 3: Review 发生在修复后、下次分析前

**理由**:
- 修复完成后立即审查，捕获最新修改。
- 审查结果注入下一次 error analysis，使分析器在分类时能参考 review 的反馈。
- 如果 review 拒绝了某个 fix，analyzer 不会重复错误分类。

### 决策 4: 角色边界 enforce 在 prompt 层面

**理由**:
- `code_adapter` 的 prompt 明确标注其作用域（Python 层）和非作用域（C 库）。
- 当 code_adapter 发现根因在 C 层时，要求它停止并报告，而不是自行实现 CPU fallback。
- 这是最轻量的方式——不需要改 pipeline 架构，只改 prompt。

### 决策 5: constraint_summary 注入所有 Phase

**理由**:
- Phase 2 (venv) 需要确保安装的包支持 NPU。
- Phase 3 (entry script) 需要确保选择的项目流程符合约束要求。
- Phase 4 (rule migration) 虽为非 LLM 执行，但可通过代码层面约束（后续改进项）。
- Phase 5 所有 repair agent 和 error analyzer 都需要约束作为决策基准。

---

## 实施优先级

| 优先级 | 改动项 | 影响文件数 | 风险 |
|--------|--------|-----------|------|
| **P0** | 改动 1: CLI --user-constraints | 1 | 低 |
| **P0** | 新增 `phase_1_5_constraint_summary.md` | 1 | 低 |
| **P0** | 改动 3.2: `run_phase_1_5` | 1 | 低 |
| **P0** | 改动 2: Orchestrator 约束传递 | 1 | 低 |
| **P0** | 修改 `phase_error_recovery.md` (analyzer) | 1 | 低 |
| **P1** | 新增 `phase_5_review.md` | 1 | 低 |
| **P1** | 改动 3.4: `run_review_check` | 1 | 低 |
| **P1** | 改动 4: Repair Loop review 注入 | 1 | 中 |
| **P1** | 修改 3 个 repair prompt 模板 | 3 | 低 |
| **P1** | 修改 phase 2/3 prompt 注入约束 | 2 | 低 |
| **P2** | 改动 3.1: 拆分 run_phase_0_to_3 | 1 | 中 |

---

## 向后兼容性

- 如果 `--user-constraints` 不传，`constraint_summary` 为空字符串，所有 prompt 中的 `{constraint_summary}` 和 `{last_review}` 占位符会被替换为空值或 "(No review available)" — Pipeline 行为退化到当前版本。
- Phase 1.5 是条件执行的 — 只在有用户约束时运行。
- Review step 是可选的 — 如果 `review_session_id` 为 None，Phase 5 正常运行不审查。
- 所有现有 prompt 模板中新增的占位符都有安全默认值。
