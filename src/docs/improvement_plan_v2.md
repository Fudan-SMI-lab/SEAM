# Phase 5 Review-Driven Improvement Plan

> Target: Prevent "假通过" when Review Agent detects constraint violations, plus framework hardening.
> Created: 2026-04-22

---

## Problem Recap

From 4 E2E Deepwave runs, the following issues were identified:

| # | Issue | Severity |
|---|-------|:--------:|
| P1 | Repair agent `send_command` 无异常保护 — 网络断开/LLM 超时直接崩溃 Phase 5 | 🔴 致命 |
| P2 | Review verdict=reject 不排除 Phase 5 通过 — "假通过" 破坏零 CPU fallback 约束 | 🟡 高 |
| P3 | 入口脚本选择 LLM 非确定性 | ⏭️ 跳过 |
| P4 | 错误分析器 `send_command` 无异常保护 (同 P1) | 🟡 高 |
| P5 | subprocess 超时错误信息硬编码 "300s" + 框架参数无集中管理 | 🟢 中 |
| P6 | Artifact key 双重前缀混乱 (`phase_*.json` vs `phase_phase_*.json`) | 🟢 低 |

---

## Phase A: Framework Configuration System (P5 + P6)

### Goal
Centralize all configurable parameters in a YAML file, loaded at runtime.

### Current State
- `workflows/npu_migration_v1.yaml` exists with per-phase timeout/retry but is only partially consumed
- Timeouts scattered: `_SESSION_TIMEOUT = 3600`, `timeout=300`, `timeout=1200` hardcoded in `repair_loop.py`
- E2E test doesn't leverage the YAML for any configuration

### Proposed `framework_config.yaml` Structure

```yaml
# Framework-level runtime configuration
framework:
  # LLM session timeouts (seconds)
  session_timeout_repair: 3600        # repair agent send_command
  session_timeout_analyzer: 3600       # error analyzer send_command
  session_timeout_phase: 600           # Phase 0-3 LLM calls
  session_timeout_followup: 300        # retry/follow-up LLM calls

  # Execution timeouts (seconds)
  entry_script_timeout: 1200           # subprocess.run for Phase 5 entry script
  stagnation_threshold: 3              # identical errors before stopping

  # Review configuration
  review:
    enabled: false                     # default: off (backward compatible)
    gate_on_cpu_fallback: true         # if review detects CPU fallback, reject the pass
    max_review_iterations: 3           # max additional iterations when review rejects

  # OpenCode server configuration
  server:
    url: ""                            # empty = auto-detect/start
    auto_start: true                   # auto-start server if url not provided
    port_preference: 0                 # 0 = auto-select, nonzero = use specified
    auth_header: ""

  # Artifact configuration
  artifacts:
    key_prefix: "phase"                # uniform prefix for all artifact keys
    include_raw: true
    include_validated: true
    include_reports: true

# Phase-specific overrides (merge with workflow yaml)
phases:
  phase_5_validation:
    entry_script_timeout: "{{ framework.entry_script_timeout }}"
    session_timeout: "{{ framework.session_timeout_repair }}"
    review_enabled: "{{ framework.review.enabled }}"
    review_gate_on_cpu_fallback: "{{ framework.review.gate_on_cpu_fallback }}"
    max_review_iterations: "{{ framework.review.max_review_iterations }}"
    stagnation_threshold: "{{ framework.stagnation_threshold }}"
```

### Implementation

| # | Task | Files | Dependencies |
|---|------|-------|-------------|
| A1 | Create `core/config_loader.py` — YAML loader with env var interpolation, defaults, and validation | `core/config_loader.py` | pyyaml (already in deps) |
| A2 | Create default `config/framework_defaults.yaml` | `config/framework_defaults.yaml` | — |
| A3 | Replace hardcoded `_SESSION_TIMEOUT` with `config.session_timeout_repair` | `core/repair_loop.py` | A1 |
| A4 | Replace hardcoded `timeout=1200` (entry script) with config value | `core/repair_loop.py` | A1 |
| A5 | Replace hardcoded `timeout=300` (follow-up) with config value | `core/repair_loop.py` | A1 |
| A6 | Fix artifact key prefix inconsistency in orchestrator/e2e_test | `core/orchestrator.py`, `tests/e2e/e2e_test.py` | A1 |

### Verification
- `python -c "from core.config_loader import load_config; print(load_config())"` returns valid dict
- All timeout values in `repair_loop.py` sourced from config, no hardcoded magic numbers
- Default config produces identical behavior to current code

---

## Phase B: Review-Driven Flow (P2 — Core Feature)

### Goal
When Review Agent rejects a passing validation, transition to "improvement mode" instead of blindly accepting it as Phase 5 SUCCESS.

### State Machine: Repair Loop

```
[Validation FAIL] → Error Analyzer → Repair Agent → Review (optional)
                        ↕
[Validation PASS] → Review (optional)
                        ├─ verdict=approve → Phase 5 SUCCESS
                        ├─ verdict=reject (not gate) → log + continue normal
                        └─ verdict=reject (gate=true) → Improvement Mode
                                                         ↓
                                                   [Save passing version]
                                                        ↓
                                           "Improvement Analyzer" (new prompt)
                                                        ↓
                                                   Route to repair session
                                                        ↓
                                               [Re-validate entry script]
                                                        ├─ exit 0 → Review again
                                                        │            ↓
                                                        │       (loop until approve or max_iter)
                                                        ├─ exit != 0 → revert to normal repair mode
                                                        └─ max_iter reached → use last passing version
```

### Sub-Phase B1: Save Passing Version

When validation succeeds (exit 0) but review rejects with `gate_on_cpu_fallback=true`:

1. **Snapshot current project state**:
   ```python
   snapshot = {
       "iteration": iteration,
       "exit_code": 0,
       "verdict": review_result["verdict"],
       "reject_reason": review_result.get("reasoning", ""),
       "cpu_fallback_detected": review_result.get("cpu_fallback_detected", False),
       "modified_files": fix_attempt.get("modified_files", []),
       "fix_summary": fix_attempt.get("fix_summary", ""),
       "snapshot_path": snapshot_project_files(project_dir, ".sm-artifacts/passing_version_iter{N}.json"),
   }
   ```

2. Store in `ReviewGateState`:
   ```python
   @dataclass
   class ReviewGateState:
       best_passing_version: dict[str, object] | None = None
       review_reject_reasons: list[str] = field(default_factory=list)
       improvement_iterations: int = 0
   ```

### Sub-Phase B2: New "Improvement Analyzer"

After review reject in gate mode, the error analyzer receives a **different prompt**:

**Normal mode** (existing): "Here's the error output. Classify it and suggest a fix."

**Improvement mode** (new prompt `phase_review_improvement.md`):
```md
# Review-Driven Improvement Analysis

## Current Status
- The entry script currently passes (exit code 0) — no runtime errors.
- However, the Review Agent has identified constraint violations in the current solution.

## Review Feedback
{last_review_json}

## Migration Constraints
{constraint_summary}

## Previous Improvement Attempts
{improvement_history}

## Task
Analyze the review feedback and determine:
1. What specific aspect of the current solution violates the constraints?
2. What improvement direction would bring the project closer to compliance?
3. Which repair role is best suited for this improvement?

## Output
{
  "improvement_area": "e.g., cpu_fallback_elimination, constraint_compliance, performance",
  "suggested_direction": "Specific technical approach to address the violation",
  "repair_role": "code_adapter | dependency_fixer | operator_fixer",
  "priority": "critical | high | medium"
}
```

### Sub-Phase B3: New Repair Session Prompts

Each repair session receives a **new context** in improvement mode:

```md
## Improvement Context
This is NOT a bugfix — the entry script works. The Review Agent has flagged {violation_description}.

## Review Rejection Reason
{review_rejection_reasoning}

## Your Task
Improve the current solution to address the reviewer's concerns.
```

Key: `last_review["alternative_suggestions"]` (from `phase_5_review.md` prompt) becomes the "error" for this iteration.

### Sub-Phase B4: Fallback to Normal Mode

If during improvement mode the entry script starts failing (exit != 0):
- Immediately revert to normal error-analyze → repair flow
- Use the latest saved passing version as fallback
- Log the transition: `[Improvement mode → Normal mode] (script exit {code})`

### Sub-Phase B5: Max Improvement Iterations Reached

If `improvement_iterations >= max_review_iterations` and review still rejects:
- **Phase 5 status**: `passed_with_reviews` (not `passed` or `failed`)
- **Output**: The most recent passing version (saved in B1)
- **Artifacts**: Full improvement history for debugging

```python
result = {
    "status": "passed_with_reviews",
    "passing_iteration": gate_state.best_passing_version["iteration"],
    "review_rejections": len(gate_state.review_reject_reasons),
    "last_passing_version_path": gate_state.best_passing_version["snapshot_path"],
    "final_status": "passed_with_unresolved_constraints",
    ...
}
```

### Implementation

| # | Task | Files | Dependencies |
|---|------|-------|-------------|
| B1 | Add `ReviewGateState` dataclass + snapshot function | `core/repair_loop.py` | — |
| B2 | Add `enable_review_gate`, `max_review_iterations` params to `RepairLoopEngine.run()` | `core/repair_loop.py` | — |
| B3 | Create `prompts/phase_review_improvement.md` — improvement analyzer prompt | `prompts/phase_review_improvement.md` | — |
| B4 | Add improvement mode state machine to `run()` loop (new branch after review reject) | `core/repair_loop.py` | B1, B2 |
| B5 | Add new status `"passed_with_reviews"` to result builder | `core/repair_loop.py` | B4 |
| B6 | Wire `enable_review_gate` + `max_review_iterations` through e2e_test CLI | `tests/e2e/e2e_test.py`, `core/orchestrator.py` | A1 |
| B7 | Wire `config.review.*` values into repair loop invocation | `core/orchestrator.py` | A1 |

### Verification
- With `review.enabled=false`: identical behavior to current (backward compatible)
- With `review.enabled=true`, `gate_on_cpu_fallback=true`, review reject:
  - Passing version is saved
  - Improvement analyzer routes to correct repair role
  - Max 3 improvement iterations
  - Final result reflects `passed_with_reviews` if still rejected
  - If script starts failing during improvement, reverts to normal mode

---

## Phase C: Exception Protection for LLM Calls (P1 + P4)

### Goal
Prevent Phase 5 crash from `send_command` exceptions (timeout, connection loss, server error).

### Analysis

**Current code flow** (repair_loop.py):

```
for iteration in range(1, max_iterations + 1):
    try:
        completed = subprocess.run(entry_script, ...)     # Line 168
    except subprocess.TimeoutExpired:                     # Line 187 — CAUGHT ✅
        ...

    classification = self._analyze_error(...)             # Line 201 — NOT CAUGHT
    #   ↑ send_command at line 373 — _SESSION_TIMEOUT

    repair_response = self.session_mgr.send_command(...)  # Line 256 — NOT CAUGHT
    #   ↑ _SESSION_TIMEOUT
```

**Two unprotected `send_command` call sites**:
1. `_analyze_error()` → line 373 → error analyzer classification
2. Main loop repair call → line 256 → repair agent response

### Implementation

| # | Task | Description | Files |
|---|------|-------------|-------|
| C1 | Wrap `_analyze_error` send_command in try/except | If fails → classify as `category=communication_error`, `repair_role=dependency_fixer` | `core/repair_loop.py` |
| C2 | Wrap main repair send_command in try/except | If fails → `fix_attempt.status="communication_error"`, log, continue to next iteration | `core/repair_loop.py` |
| C3 | Both exceptions use config-derived timeout (not hardcoded) | Use `config.session_timeout_*` | `core/repair_loop.py` |

### Error Recovery Strategy

| Exception | Recovery |
|-----------|----------|
| `TimeoutError` | Log `[Iter N] LLM call timed out (config: {timeout}s)` → treat as stagnation, move to next iteration |
| `RuntimeError` (server error) | Log → retry once with follow-up → if still fails, mark as communication error |
| `ConnectionRefusedError` | Log → attempt 2 retries with exponential backoff (5s, 15s) → if all fail, abort Phase 5 with `server_unreachable` |

### Verification
- Kill OpenCode server mid-Phase5 → graceful degradation, not crash
- Kill OpenCode server → restart → reconnection succeeds
- All 3 exception types produce meaningful log messages

---

## Phase D: Auto OpenCode Server (P6)

### Goal
Automatically start an OpenCode server in a temp directory if no URL is provided, with configurable port.

### Behavior

```
Config: server.url
  ├─ "" (empty) → auto_start
  │   ├─ server.port_preference == 0 → find available port
  │   ├─ server.port_preference > 0 → use specified port
  │   └─ Start OpenCode server in temp dir
  └─ "http://host:port" → use directly
       └─ Still validate connectivity before proceeding
```

### Implementation

| # | Task | Description | Files |
|---|------|-------------|-------|
| D1 | Create `harness/server/lifecycle.py` — start/stop/health_check functions | Subprocess management of `opencode server` | `harness/server/lifecycle.py` |
| D2 | Port discovery: find available port if `port_preference=0` | `socket` + bind test, fallback range 4096-4099 | `harness/server/lifecycle.py` |
| D3 | Auto-start before e2e_test if `server.url=""` | In `run_e2e()` before `check_server_running()` | `tests/e2e/e2e_test.py` |
| D4 | Cleanup: stop server on E2E exception/completion | `try/finally` in `run_e2e()` | `tests/e2e/e2e_test.py` |
| D5 | CLI args: `--hostname`/`--port`/`--server_type` endpoint args | In `build_parser()` | `tests/e2e/e2e_test.py` |

### Verification
- Default `--hostname 127.0.0.1 --port 4098 --server_type opencode` selects the local OpenCode endpoint
- `--hostname host --port 4098 --server_type opencode --server-no-auto-start` uses a pre-started server
- Server crashed mid-run → detect + report, don't hang

---

## Implementation Order & Dependencies

```
Phase A: Config System (A1 → A2 → {A3-A6 parallel})
    ↓
Phase B: Review Gate (B1-B2 → B3 → B4-B5 → {B6-B7 parallel})
    ↓
Phase C: Exception Protection (C1-C3 parallel)
    ↓
Phase D: Auto Server (D1-D2 → D3-D5 parallel)
```

Total estimated effort: ~10-12 file changes across 4 phases.

---

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|-----------|
| A | Config system changes break existing code | Default config = current hardcoded values, zero behavior change |
| B | Improvement mode loops infinitely | Hard limit: `max_review_iterations` (default 3) |
| B | Review gate breaks backward compatibility | Default: `review.enabled=false`, gate only opt-in |
| C | Exception masking hides real bugs | Log full exception + stack trace at ERROR level before falling back |
| D | Auto server port conflicts | Port discovery tests before binding, fallback range 4096-4099 |

---

## Backward Compatibility Matrix

| Change | Backward Compatible? | Default |
|--------|:---:|---------|
| Config system | ✅ Yes | All values = current hardcoded |
| Review gate | ✅ Yes | `enabled=false` |
| Exception protection | ✅ Yes | Same behavior (just no crash) |
| Auto server | ✅ Yes | Only when `server.url=""` |
| Entry script timeout | ⚠️ Changed (300→1200) | 1200 is the current runtime value |
| Session timeout | ⚠️ Changed (1800→3600) | 3600 is the current runtime value |
