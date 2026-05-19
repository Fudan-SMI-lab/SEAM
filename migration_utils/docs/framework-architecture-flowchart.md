# migration_utils 框架整体流程图

> **框架定位**: CUDA → Ascend NPU 自动化迁移编排系统  
> 通过 LLM 驱动的多阶段工作流 (via OpenCode/Sisyphus Agent)，完成从环境检测、依赖安装、代码迁移、验证修复到报告生成的全流程。

---

## 1. Phase 5 修复流程主图 (项目展示主图)

> **Phase 5** 是本框架最核心的引擎：自动执行迁移后的代码，遇到错误时自主分析、分派修复、审查质量，直到成功或达到退出条件。

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                          Phase 5: Validation & Repair Loop                             │
│                          (core/repair_loop.py — RepairLoopEngine)                      │
└──────────────────────────────────┬────────────────────────────────────────────────────┘
                                   │
                   ┌───────────────┴───────────────┐
                   │  运行入口脚本 (subprocess.run)  │
                   │  timeout=config:entry_script   │
                   │  timeout=1200s                 │
                   └───────────────┬───────────────┘
                                   │
                          ┌────────┴───────┐
                          │  exit_code == 0? │
                          └──┬─────────┬───┘
                         YES │         │ NO
                             │         ▼
                             │   ┌─────────────────────┐
                             │   │   Combine stderr +  │
                             │   │   stdout → error_text│
                             │   │ Normalize signature  │
                             │   └──────────┬──────────┘
                             │              │
                             │       ┌──────┴──────┐
                             │       │Same error   │
                             │       │3× in a row? │
                             │       └──┬──────┬───┘
                             │      YES │      │ NO
                             │         │      ▼
                             │         │  ┌────────────────────────┐
                             │         │  │ Error Analyzer (LLM)    │
                             │         │  │ Session: error_analyzer │
                             │         │  │ Prompt: phase_error     │
                             │         │  │ _recovery.md            │
                             │         │  │ Retry: 3× [5s,15s]      │
                             │         │  └──────────┬─────────────┘
                             │         │             │
                             │         │             ▼ Output:
                             │         │  ┌──────────────────────────┐
                             │         │  │  错误分类 (6种)           │
                             │         │  │  • environment           │
                             │         │  │  • dependency            │
                             │         │  │  • pathing               │
                             │         │  │  • migration logic       │
                             │         │  │  • operator              │
                             │         │  │  • unknown               │
                             │         │  │                          │
                             │         │  │  → 映射到 3种修复角色     │
                             │         │  └──────────┬───────────────┘
                             │         │             │
                             │         │      ┌──────┴──────┐
                             │         │      │ Map to      │
                             │         │      │ repair_role │
                             │         │      └──┬─────┬───┘
                             │         │         │     │
                             │         │         ▼     ▼     ▼
                             │         │  ┌──────────┐┌───────────┐┌─────────────┐
                             │         │  │dependency││ code_     ││ operator    │
                             │         │  │_fixer    ││ adapter   ││ _fixer      │
                             │         │  │          ││           ││             │
                             │         │  │Fix: deps ││Fix:      ││Fix: custom  │
                             │         │  │& imports ││CUDA→NPU  ││op & kernel  │
                             │         │  │          ││API       ││port         │
                             │         │  │Prompt:   ││Prompt:   ││Prompt:      │
                             │         │  │repair_dep││repair_   ││repair_op    │
                             │         │  │endency_  ││code_     ││erator_fixer │
                             │         │  │fixer.md  ││adapter.md││.md          │
                             │         │  └────┬─────┘└─────┬─────┘└─────┬──────┘
                             │         │       │            │            │
                             │         │       └──────┬─────┴────────────┘
                             │         │              │
                             │         │              ▼
                             │         │   ┌──────────────────────────┐
                             │         │   │ Repair Agent (LLM 修复)   │
                             │         │   │ Session: lazy-created,   │
                             │         │   │ persistent, per-role     │
                             │         │   │ retry: 3× [5s, 15s]     │
                             │         │   │ → 执行修复命令           │
                             │         │   │ → 修改项目文件           │
                             │         │   │ → 返回 fix_summary JSON  │
                             │         │   └──────┬───────────────────┘
                             │         │          │
                             │         │          ▼
                             │         │   ┌──────────────────────────┐
                             │         │   │ Review Gate (审查门)      │
                             │         │   │ Session: main_engineer   │
                             │         │   │ Prompt: phase_5_review.md│
                             │         │   │ 检查 3 项:               │
                             │         │   │ ① 修复正确性              │
                             │         │   │ ② CPU fallback检测       │
                             │         │   │ ③ 约束合规性              │
                             │         │   └──────┬───────────────────┘
                             │         │          │
                             │         │    ┌─────┴──────┐
                             │         │    │ verdict?   │
                             │         │    └──┬────┬───┘
                             │         │  accept│    │reject
                             │         │       │    │
                             │         │       │    ▼ AND cpu_fallback_detected?
                             │         │       │    ┌──────────────────────┐
                             │         │       │    │ 改进循环:             │
                             │         │       │    │ 1. snapshot .py (SHA)│
                             │         │       │    │ 2. improvement_iters++│
                             │         │       │    │ 3. _run_improvement_ │
                             │         │       │    │    iteration()       │
                             │         │       │    │ 4. if iters >= 3:    │
                             │         │       │    │    → passed_w_reviews│
                             │         │       │    └──────────┬───────────┘
                             │         │       │               │
                             │         │       │         continue loop
                             │         │       │
                             │         │       ▼
                             │         │   Record iteration → ArtifactStore
                             │         │   (history + journal + checkpoint)
                             │         │
                             │         ▼
                             │   Next iteration (up to max=5)
                             ▼
                      ┌──────────────┐
                      │   SUCCESS!    │
                      │   验证通过     │
                      │   break loop  │
                      └──────────────┘
```

### Phase 5 退出条件

| 退出状态 | 触发条件 | 最终结果 | 含义 |
|---------|---------|---------|------|
| **success** | 入口脚本 exit_code == 0 | ✅ 通过 | 代码在 NPU 上正常运行 |
| **stagnation** | 相同错误连续出现 3 次 | ❌ 失败 | 无法找到有效修复方案 |
| **passed_with_reviews** | 审查门拒绝达到 max_review_iterations(3) | ⚠️ 通过 | 带审查摘要通过，记录了所有被拒修复 |
| **max_iterations** | 达到 max_iterations(5) 上限 | ❌ 失败 | 迭代次数用尽 |

### 5 个 Agent Session 角色

| 角色 | 生命周期 | 创建方式 | 用途 | 对应 Prompt |
|---|---|---|---|---|
| `error_analyzer` | persistent | 立即创建 | 错误分类 (6种 → 3角色) | `phase_error_recovery.md` |
| `dependency_fixer` | persistent | 首次按需创建 | 修复依赖/导入/安装 | `repair_dependency_fixer.md` |
| `code_adapter` | persistent | 首次按需创建 | CUDA→NPU 代码适配 | `repair_code_adapter.md` |
| `operator_fixer` | persistent | 首次按需创建 | 算子/内核移植 | `repair_operator_fixer.md` |
| `main_engineer` | persistent | 复用外部创建 | Review 审查 + 改进分析 | `phase_5_review.md` + `phase_review_improvement.md` |

### 错误分类 → 修复角色映射

```
environment ──────────┐
dependency ───────────┤──► dependency_fixer (修复依赖/导入)
pathing ──────────────┤
migration logic ──────┤──► code_adapter (代码级适配)
operator ─────────────┤──► operator_fixer (算子/内核)
unknown ──────────────┘    (默认 dependency_fixer, 3次相同错误后自动触发停滞)
```

### 验证与审查机制

```
验证层 (ValidatorEngine):
  • 每次 LLM 输出都经过 validate()
  • error_analyzer 输出: category, root_cause, suggested_fix, repair_role
  • repair 输出: modified_files[], summary
  • 最终结果: validate_validation_final (success, iteration_count, errors)

审查层 (Review Gate):
  • 在修复成功后触发 (可选, 默认关闭)
  • main_engineer 审查 3 项:
    ① 修复是否真正解决了问题?
    ② 是否引入了 CPU fallback? (device='cpu', .to('cpu') 等)
    ③ 是否违反了迁移约束?
  • verdict 3 种:
    accept → 接受修复, 继续
    accept_with_warning → 接受但有警告
    reject + cpu_fallback → 触发改进循环
  • 改进循环:
    → snapshot 当前所有 .py (SHA256)
    → improvement_iterations++
    → _run_improvement_iteration() → 分析改进方向
    → 如果 iters >= 3: 退出为 passed_with_reviews
  • 关键机制:
    → 保留最佳通过版本的快照 (passing_version_*.json)
    → 支持回滚到被审查拒绝前的最后已知良好状态
```

---

## 2. 系统架构全景图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         E2E 测试入口 (e2e_test_v2.py)                        │
│  CLI: --project-dir / --output-dir / --server-url / --review-gate │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        基础设施层 (Infrastructure)                         │
│                                                                          │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Server Lifecycle     │  │ SessionManager   │  │ ArtifactStore      │  │
│  │ start/stop/health    │  │ HTTP→OpenCode    │  │ raw/validated/     │  │
│  │ :4096-4099 auto-port │  │ CRUD sessions    │  │ journal.jsonl      │  │
│  └─────────────────────┘  │ session_mgr      │  │ state.json         │  │
│                           └──┬───────────────┘  └──┬─────────────────┘  │
│                              │                     │                     │
│  ┌─────────────────────┐    │                     │                     │
│  │ ConfigLoader         │    │                     │                     │
│  │ YAML + {ENV} 插值    │    │                     │                     │
│  └─────────────────────┘    │                     │                     │
│  ┌─────────────────────┐    │                     │                     │
│  │ PromptLoader          │    │                     │                     │
│  │ .md模板 + {placeholder}│    │                     │                     │
│  └─────────────────────┘    │                     │                     │
│  ┌─────────────────────┐    │                     │                     │
│  │ ValidatorEngine       │    │                     │                     │
│  │ Registry + normalize  │    │                     │                     │
│  └─────────────────────┘    │                     │                     │
│  ┌─────────────────────┐    │                     │                     │
│  │ RuleBasedMigrator     │    │                     │                     │
│  │ 7条CUDA→NPU正则       │    │                     │                     │
│  └─────────────────────┘    │                     │                     │
│  ┌─────────────────────┐    │                     │                     │
│  │ TelemetryObserver     │    │                     │                     │
│  │ Phase/Command/Event   │    │                     │                     │
│  │ Timing & Metrics      │    │                     │                     │
│  └─────────────────────┘    │                     │                     │
└──────────────────────────────┼─────────────────────┼─────────────────────┘
                               │                     │
                               ▼                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          核心引擎层 (Core Engine)                          │
│                                                                          │
│                    ┌────────────────────────────────┐                     │
│                    │     core/orchestrator.py        │                     │
│                    │    Orchestrator.run_workflow()  │                     │
│                    │   Top-level coordinator         │                     │
│                    └───────────────┬────────────────┘                     │
│                                    │                                     │
│              ┌─────────────────────┼─────────────────────┐               │
│              ▼                     ▼                     ▼               │
│  ┌───────────────────┐ ┌───────────────────┐ ┌──────────────────────┐    │
│  │ PhaseRunner        │ │ RepairLoopEngine  │ │ StateMachine         │    │
│  │ Phase 0→4, 6       │ │ Phase 5          │ │ phase transitions    │    │
│  │ Sequential phases  │ │ Validate→Repair   │ │ from YAML workflow   │    │
│  └───────────────────┘ └───────────────────┘ └──────────────────────┘    │
│                                                                          │
│         Validators (6): env_detect → project_analysis → venv            │
│                      → entry_script → rule_migration → validation_final  │
│                                                                          │
│         Prompts (14 .md templates in prompts/ directory)                 │
│         Config (config/framework_defaults.yaml)                          │
│         Workflow (workflows/npu_migration_v1.yaml)                       │
│         Schemas (schemas/ * .json)                                       │
└──────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       外部系统边界 (External Systems)                       │
│                                                                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ OpenCode Server  │  │ Subprocess       │  │ File System            │  │
│  │ (HTTP :4098)     │  │ (entry script    │  │ .sm-artifacts/         │  │
│  │ ↔ SessionManager │  │  execution)      │  │   /{run_id}/raw/       │  │
│  │                  │  │                  │  │   /{run_id}/validated/ │  │
│  │ Agent Roles:     │  │ subprocess.run() │  │   execution_journal    │  │
│  │ main_engineer    │  │                  │  │   passing_version_*.json│ │
│  │ error_analyzer   │  │                  │  │   reports/             │  │
│  │ dependency_fixer │  │                  │  │                        │  │
│  │ code_adapter     │  │                  │  │                        │  │
│  │ operator_fixer   │  │                  │  │                        │  │
│  └──────────────────┘  └──────────────────┘  └────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 模块依赖关系图

```
                       CLI Entry Point
                        (sm_adapt_cli.py)
                              │
                              ▼
                    ┌─────────────────────┐
                    │  core/orchestrator.py │
                    │   Orchestrator       │◄── workflow YAML ──┐
                    │   run_workflow()     │                   │
                    └──┬──┬──┬──┬──┬──┬──┬│───────────────────┘
              ┌───────┼──┼──┼──┼──┼──┼──┤
              ▼       ▼  ▼  ▼  ▼  ▼  ▼  ▼
┌──────────┐ ┌──────┐ │  │  │  │  │  │  │  ┌──────────────────┐
│config.py │ │config│ │  │  │  │  │  │  │  │state_machine.py  │
│load_     │ │loader│ │  │  │  │  │  │  └──► StateMachine     │
│workflow()│ │.py   │ │  │  │  │  │     ┌──►record_success()  │
│→Workflow │ │env-var│ │  │  │  │     │   │record_failure()   │
│ Definition││interp │ │  │  │  │     │   │is_terminal()      │
└────┬─────┘ └──┬───┘ │  │  │  │     │   └──────────────────┘
     │          │     │  │  │  │     │
     ▼          ▼     │  │  │  │     │
┌──────────┐ ┌──────┐ │  │  │  │     │
│types.py  │ │config│ │  │  │  │     │
│Dataclasses│ │.yaml │ │  │  │  │     │
└──────────┘ └──────┘ │  │  │  │     │
                      │  │  │  │     │
                      ▼  ▼  ▼  ▼     ▼
              ┌────────────────────────────────┐
              │      core/phase_runner.py       │
              │  PhaseRunner                    │
              │  run_phase_0_to_1()             │
              │  run_phase_1_5()                │
              │  run_phase_2_to_3()             │
              │  run_phase_4()                  │
              │  run_review_check()             │
              │  run_phase_6()                  │
              │  _run_single_phase()            │
              └──┬──────┬──────┬───────┬───────┘
                 │      │      │       │
                 ▼      ▼      ▼       ▼
          ┌─────────┐┌──────┐┌──────┐┌─────────────┐
          │artifact ││prompt││valid ││ migrator/    │
          │_store   ││_loader││ator ││ rule_based.py│
          │.py      ││.py   ││_engine││ RuleBased    │
          │         ││      ││.py  ││ Migrator     │
          └─────────┘└──┬───┘└──┬──┘└─────────────┘
                        │      │
                        ▼      ▼
              ┌─────────────────────────┐
              │     prompts/  (14 .md)  │
              │  validators/ (6 files)  │
              │  schemas/    (7 JSON)   │
              └─────────────────────────┘

┌────────────────────────────────────────┐
│         core/repair_loop.py            │
│   RepairLoopEngine                     │
│   Loop:                                │
│   1. subprocess.run(entry_script)     │
│   2. _analyze_error() → LLM classify  │
│   3. Repair dispatch (3 roles)        │
│   4. Review gate + improvement        │
│   5. Stagnation detection             │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│          harness/ (LLM Layer)          │
│                                        │
│  harness/session/manager.py            │
│  • HTTP client to OpenCode (:4098)     │
│  • get_or_create(role, lifecycle)      │
│  • send_command(session_id, cmd)       │
│  • extract_json_response(text)         │
│                                        │
│  harness/server/lifecycle.py           │
│  • start_server() → Popen              │
│  • stop_server()  → SIGTERM→SIGKILL   │
│  • health_check() → HTTP GET /agent    │
│  • find_available_port() → TCP bind    │
└────────────────────────────────────────┘
```

---

## 4. Phase 0→7 执行流程图

```
┌──────────────────────────────────────────────────────────────────┐
│                    orchestrator.run_workflow()                    │
│                                                                   │
│  1. Load workflow YAML → WorkflowDefinition                      │
│  2. Load framework config (config_loader + env interpolation)    │
│  3. Create all components:                                       │
│     ArtifactStore, PromptLoader, ValidatorEngine, PhaseRunner,   │
│     RepairLoopEngine, RuleBasedMigrator, StateMachine            │
│  4. Create main_engineer session (persistent)                    │
│                                                                   │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌─────────────┐
│ Phase 0     │ ENVIRONMENT DETECTION
│ (env_detect)│ • 检测平台 (NPU vs CUDA)
│             │ • 检查 nvidia-smi / npu-smi / torch_npu
│             │ • 输出: {platform, npu_detected, python_version}
│             │ • 验证: validate_env_detect
│             │ • Session: main_engineer (persistent, shared with Phase 1)
└──────┬──────┘
       │ ✅ validated
       ▼
┌─────────────┐
│ Phase 1     │ PROJECT ANALYSIS
│ (project_   │ • 分析项目结构、依赖文件、入口脚本
│  analysis)  │ • 检测 CUDA 模式 (torch.cuda, .cuda(), nccl)
│             │ • 输出: {project_dir, dependencies, cuda_detected, entry_script}
│             │ • 验证: validate_project_analysis
│             │ • Session: same main_engineer (persistent)
└──────┬──────┘
       │ ✅ validated
       ▼
┌─────────────┐
│ Phase 1.5   │ CONSTRAINT SUMMARY (条件执行)
│ (constraint │ • 仅当提供 user_constraints 时执行
│  _summary)  │ • 交叉引用用户约束与Phase 1分析结果
│             │ • 输出: constraint_summary (string, 换行分隔的规则)
│             │ • Session: same main_engineer (persistent)
│             │ • 直接调用 send_command (不走 _run_single_phase 重试)
└──────┬──────┘
       │
       ▼ (与Phase 0+1输出合并)
       │
┌─────────────┐
│ Phase 2     │ VIRTUAL ENVIRONMENT CREATION
│ (venv_create)│ • 创建/复用 .venv
│             │ • 安装项目依赖 + torch_npu
│             │ • 支持国内镜像 (阿里云/清华)
│             │ • 输出: {venv_path, python_path, installed_packages}
│             │ • 验证: validate_venv
│             │ • Session: NEW main_engineer (persistent, 新建)
└──────┬──────┘
       │ ✅ validated
       ▼
┌─────────────┐
│ Phase 3     │ ENTRY SCRIPT CONFIRMATION
│ (entry_     │ • 优先级: (1) README运行命令 → (2) 已有启动脚本 → (3) 创建smoke_test.py
│  script)    │ • 输出: {entry_script_path, run_command}
│             │ • 验证: validate_entry_script
│             │ • Session: same main_engineer (persistent)
└──────┬──────┘
       │ ✅ validated
       ▼
┌─────────────┐
│ Phase 4     │ RULE-BASED MIGRATION
│ (rule_      │ • 无LLM参与! 确定性文本替换
│  migration) │ • 7条正则规则:
│             │   torch.cuda.amp → torch.npu.amp
│             │   torch.cuda     → torch.npu
│             │   .cuda(         → .npu(
│             │   "cuda"/'cuda'  → "npu"/'npu'
│             │   "nccl"/'nccl'  → "hccl"/'hccl'
│             │   + 注入 import torch_npu (如发现CUDA模式)
│             │ • 输出: {files_migrated, files_skipped, replacement_counts, total_replacements}
│             │ • 验证: validate_rule_migration
│             │ • Session: 无 (local_script)
└──────┬──────┘
       │ ✅ validated
       ▼
┌─────────────┐
│ Phase 5     │ VALIDATION REPAIR LOOP (最复杂阶段)
│ (validation)│ • 详见下方 Phase 5 专门流程图
│             │ • Engine: RepairLoopEngine (非PhaseRunner)
│             │ • 执行入口脚本 → 错误分析 → 角色分派修复 → 审查门
│             │ • 最大迭代: 5 (可配置)
│             │ • 停滞检测: 3次相同错误 → 停止
└──────┬──────┘
       │ ✅ validated (success/stagnation/passed_with_reviews)
       ▼
┌─────────────┐
│ Phase 6     │ FINAL REPORT GENERATION
│ (report)    │ • 加载所有Phase 0-5的artifact输出
│             │ • 生成5个Markdown报告:
│             │   API_KEY_REPORT.md
│             │   OPENCODE_OPERATIONS_LOG.md
│             │   TOOLS_EXECUTION_REPORT.md
│             │   SUMMARY_REPORT.md
│             │   LOCAL_TOOL_OPTIMIZATION_REPORT.md
│             │ • 输出: {report_paths, migration_summary, project_dir}
│             │ • Session: main_engineer (persistent, get_or_create)
│             │ • 超时: 900秒
└─────────────┘
       │
       ▼
┌─────────────┐
│  StateMachine │
│  → complete   │
│  cleanup_all()│ 清理所有ephemeral/reusable会话
└─────────────┘
```

---

## 5. Phase 数据流向图

```
Phase 0 Output
{platform, npu_detected, python_version}
       │
       ├──────┐
       │      ▼
Phase 1 Output         Phase 1.5 (conditional)
{project_dir, deps,    {constraint_summary ──────────┐
 cuda_detected,         (string of rules)            │
 entry_script}         ─────────────────────────────┘
       │                                            │
       ▼                                            │
Phase 2 Output                                     │
{venv_path, python_path,                           │
 installed_packages}                                │
       │                                            │
       ▼                                            │
Phase 3 Output                                     │
{entry_script_path,                                │
 run_command} ─────────────────────────────────────┘
       │
       │                                    All previous phases
       ▼                                    fed via {previous_outputs}
Phase 4 Output                              in every phase prompt
{files_migrated, files_skipped,
 replacement_counts, total_replacements}
       │
       ▼
Phase 5 Input (from Phase 3 run_command + Phase 4 migration)
       │
       │  entry_script = Phase 3.run_command
       │  After Rule-Based Migration applied
       │  Constraint summary from Phase 1.5
       │
       ▼
Phase 5 Output
{success, status, iteration_count,
 error_history, review_gate_summary}
       │
       ▼
Phase 6 Input (ALL phases loaded from ArtifactStore)
Phase 6 Output
{report_paths, migration_summary}
       │
       ▼
   Complete
```

---

## 6. Phase 5 详细状态机流程图

### 5A. 主循环与修复调度

```
┌──────────────────────────────────────────────────────────────────┐
│              run() 入口 (RepairLoopEngine.run())                  │
│                                                                   │
│  analyzer_session = get_or_create("error_analyzer")              │
│  repair_session_ids = {} (按角色延迟创建)                         │
│  gate_state = ReviewGateState()                                  │
│  repeated_error_count = 0                                        │
│  status = "max_iterations"                                       │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
              ┌──────────────────┐
              │ For iter 1..max │──────────────────────────────┐
              │ iterations      │                              │
              └───────┬─────────┘                              │
                      │                                        │
                      ▼                                        │
              ┌──────────────────┐                            │
              │ subprocess.run() │                            │
              │ entry_script     │                            │
              │ timeout=1200s    │                            │
              └───────┬──────────┘                            │
                      │                                        │
                ┌─────┴─────┐                                  │
                │exit_code  │                                  │
                │  == 0?    │──YES──► status="success" ───┐   │
                └─────┬─────┘                             │   │
                      │NO                                 │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ Normalize error  │                        │   │
              │ signature        │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                ┌─────┴─────┐                            │   │
                │repeated_  │                            │   │
                │count >=3? │──YES──► status="stagnation"│   │
                └─────┬─────┘                            │   │
                      │NO                                 │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ _analyze_error() │                        │   │
              │ LLM classify (3  │                        │   │
              │ retries, [5,15]s)│                        │   │
              │ Output:          │                        │   │
              │ {category,       │                        │   │
              │  repair_role}    │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ Get/create repair│                        │   │
              │ session by role:  │                        │   │
              │ dependency_fixer │                        │   │
              │ code_adapter      │                        │   │
              │ operator_fixer    │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ _build_repair_   │                        │   │
              │ prompt()          │                        │   │
              │ → load .md        │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ LLM repair call  │                        │   │
              │ 3 retries        │                        │   │
              │ [5,15]s delays   │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                ┌─────┴─────┐                            │   │
                │repair     │                            │   │
                │failed?    │──YES──► fix_attempt=      │   │
                └─────┬─────┘         comm_error        │   │
                      │NO                                 │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ extract JSON     │                        │   │
              │ fix summary      │                        │   │
              │ (3 retries)      │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                      ▼                                   │   │
              ┌──────────────────┐                        │   │
              │ Record iter to   │                        │   │
              │ artifact_store   │                        │   │
              │ journal + check  │                        │   │
              └───────┬──────────┘                        │   │
                      │                                   │   │
                ┌─────┴─────┐                            │   │
                │stagnation │                            │   │
                │or passed? │──YES──► break ────────────┘   │
                └─────┬─────┘                               │
                      │NO                                   │
                      ▼                                     │
            status="max_iterations"                         │
                      │                                     │
                      └─────────────────────────────────────┘
                      ▼
               ┌─────────────────┐
               │ _build_result() │
               │ _save_final()   │
               └─────────────────┘
```

### 5B. 审查门 (Review Gate) 流程

```
修复成功? ──NO──► 继续下一轮迭代
    │
    YES
    ▼
review_callable 存在? ──NO──► 继续下一轮迭代
    │
    YES
    ▼
审查 Agent 调用 (run_review_check)
→ PhaseRunner.run_review_check()
→ Prompt: phase_5_review.md
→ 输出: {verdict, cpu_fallback_detected,
         alternative_suggestions, reasoning}
    │
    ▼
verdict == "reject"
AND cpu_fallback_detected?
    │
    ├── YES ──► 1. _snapshot_project_files()
    │             (遍历项目, SHA256 所有.py)
    │             ↓
    │           2. gate_state.best_passing_version = snapshot
    │             ↓
    │           3. gate_state.improvement_iterations += 1
    │             ↓
    │           4. _run_improvement_iteration()
    │             → Prompt: phase_review_improvement.md
    │             → LLM 分析改进方向
    │             → 输出: {repair_role, improvement_area,
    │                      suggested_direction, priority}
    │             ↓
    │           5. improvement_iterations >=
    │              max_review_iterations (默认3)?
    │             ├── YES → status="passed_with_reviews", break
    │             └── NO  → 继续下一轮迭代
    │
    └── NO ──► 继续下一轮迭代
```

### 5C. Phase 5 退出状态表

| 退出状态 | 触发条件 | 最终 success 值 | 说明 |
|---------|---------|----------------|------|
| `"success"` | 入口脚本 exit_code == 0 | `true` | 验证通过 |
| `"stagnation"` | 相同错误签名连续 3 次 | `false` | 停滞，不再尝试 |
| `"passed_with_reviews"` | 审查门拒绝达 max_review_iterations 次 | `true` (强制) | 带审查摘要通过 |
| `"max_iterations"` | 达到 max_iterations 限制 (默认 5) | `false` | 耗尽迭代次数 |

---

## 7. 会话生命周期与角色矩阵

### 6A. Session 角色

| 角色 | 生命周期 | 创建来源 | 用于哪些阶段 | 用途 |
|---|---|---|---|---|
| `main_engineer` | persistent | Orchestrator.run_workflow() | Phase 0-3, 6, 错误恢复 | 主要分析Agent: 环境检测、项目分析、环境搭建、报告生成 |
| `error_analyzer` | persistent | RepairLoopEngine.run() | Phase 5 (每轮) | 错误分类: 将执行错误分类为6种错误类别 + 推荐修复角色 |
| `dependency_fixer` | persistent | RepairLoopEngine (首次按需创建) | Phase 5 repair dispatch | 修复依赖/导入/安装错误 |
| `code_adapter` | persistent | RepairLoopEngine (首次按需创建) | Phase 5 repair dispatch | 代码级CUDA→NPU适配 |
| `operator_fixer` | persistent | RepairLoopEngine (首次按需创建) | Phase 5 repair dispatch | NPU算子/设备放置修复 |

### 6B. 会话交互模式

```
┌─────────────────────────────────────────────────────────┐
│                    Session Manager                       │
│                                                          │
│  ┌─────────────┐  create_session(role) ┌──────────────┐ │
│  │ main_       │◄─────────────────────►│ OpenCode     │ │
│  │ engineer    │  (persistent)         │ Server API   │ │
│  │             │◄─────────────────────►│ :4098        │ │
│  │ used in:    │  send_command(sid)    │              │ │
│  │ Phase 0-3,6 │◄────────────────────►│  Agent:      │ │
│  │ Review,     │  get_last_response()  │  Sisyphus     │ │
│  │ Recovery    │◄─────────────────────►│              │ │
│  └─────────────┘  wait_for_idle()      └──────────────┘ │
│                                                          │
│  ┌─────────────┐                                         │
│  │ error_      │ ── persistent ──► same session each iter │
│  │ analyzer    │                                         │
│  │             │ ◄── LLM classifies error each iteration │
│  └─────────────┘                                         │
│                                                          │
│  ┌─────────────┐ ┌──────────────┐ ┌─────────────────┐   │
│  │ dependency_ │ │ code_        │ │ operator_       │   │
│  │ fixer       │ │ adapter      │ │ fixer           │   │
│  │ (lazy create)│ │(lazy create) │ │(lazy create)    │   │
│  └─────────────┘ └──────────────┘ └─────────────────┘   │
│       ▲               ▲               ▲                 │
│       └───────┬───────┴───────┬───────┘                 │
│               ▼               ▼                         │
│         repair dispatch (per iteration)                 │
└─────────────────────────────────────────────────────────┘
```

---

## 8. E2E 测试执行流程

```
┌──────────────────────────────────────────────────────────┐
│                    e2e_test.py (CLI)                      │
│                                                           │
│  run_e2e(                                                 │
│    base_url, max_phase5_iter,                     │
│    keep_temp_dir, agent_name, project_dir,                │
│    output_project_dir, user_constraints,                  │
│    server_auto_start, server_port,                        │
│    review_gate, framework_config_path                     │
│  )                                                        │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                    预阶段引导 (Bootstrap)                  │
│                                                           │
│  1. Server auto-start (如果无base_url或默认地址)          │
│     → find_available_port(4096-4099)                      │
│     → start_server() → Popen("opencode server --port X")  │
│     → wait_for_server() → health_check(/agent)           │
│                                                           │
│  2. 项目复制                                              │
│     → copy_project_light() (排除大文件, <50MB)            │
│     → symlink_large_files() (软链接.bin/.pt/.pth等)      │
│     → snapshot_python_files() (SHA256 all .py files)     │
│                                                           │
│  3. 组件初始化                                            │
│     → SessionManager(base_url) → 自动检测Agent           │
│     → TelemetryObserver(session_mgr, output_dir)         │
│     → ArtifactStore(project_dir, run_id)                  │
│     → PromptLoader()                                     │
│     → ValidatorEngine()                                  │
│     → RepairLoopEngine(config)                           │
│     → RuleBasedMigrator()                                │
│                                                           │
│  4. 创建主会话                                            │
│     → get_or_create(role="main_engineer", "persistent")  │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                    Phase 执阶段                          │
│                                                           │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │
│ │Phase 0+1     │ │Phase 1.5     │ │Phase 2+3         │  │
│ │run_phase_0_1│ │(if constraints│ │run_phase_2_3()    │  │
│ │()            │ │ provided)     │ │(with constraint_│  │
│ │(with user_   │ │run_phase_1_5 │ │ summary)          │  │
│ │ constraints) │ │()             │ │                  │  │
│ └──────┬───────┘ └──────┬───────┘ └───────┬──────────┘  │
│        │                │                │              │
│        ▼                ▼                ▼              │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Phase 4: run_phase_4() (RuleBasedMigrator, no LLM)  │ │
│ └────────────────────────┬─────────────────────────────┘ │
│                          │                               │
│                          ▼                               │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Phase 5: RepairLoopEngine.run()                      │ │
│ │  → entry_script from Phase 3 run_command             │ │
│ │  → max_iter (默认 5)                            │ │
│ │  → review_callable = PhaseRunner.run_review_check    │ │
│ │  → enable_review_gate (if --review-gate flag)        │ │
│ └────────────────────────┬─────────────────────────────┘ │
│                          │                               │
│                          ▼                               │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Phase 6: run_phase_6() (Final report)               │ │
│ └────────────────────────┬─────────────────────────────┘ │
│                          │                               │
│                          ▼                               │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Phase 7: Artifact Finalization                       │ │
│ │  → after_snapshot.json (SHA256 .py files)            │ │
│ │  → copy .sm-artifacts/ to output_dir                 │ │
│ └──────────────────────────────────────────────────────┘ │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                    清理与报告 (Cleanup)                    │
│                                                           │
│  1. observer.cleanup_all() (清理所有ephemeral/reusable)  │
│  2. observer.save_metrics() → telemetry.json             │
│  3. write phase_results.json                             │
│  4. write summary.json                                   │
│  5. stop_server() (如果是auto-start的)                   │
│  6. remove temp dir (unless --keep-temp-dir)             │
│                                                           │
│  输出:                                                    │
│  - e2e-reports/migration_utils/<timestamp>/                  │
│    ├── before_snapshot.json                              │
│    ├── after_snapshot.json                               │
│    ├── summary.json                                      │
│    ├── phase_results.json                                │
│    ├── telemetry.json                                    │
│    └── .sm-artifacts/ (copied from project)              │
│                                                           │
│  打印: E2E PASS/FAIL + 各Phase状态与耗时                  │
└──────────────────────────────────────────────────────────┘
```

---

## 9. 错误恢复流程

```
任何 Phase 抛出异常
       │
       ▼
┌──────────────────────┐
│ Orchestrator.        │
│ _handle_phase_failure│
│ • 从错误文本推断失败Phase│
│ • StateMachine.record│
│   _failure()          │
└───────┬──────────────┘
        │
  ┌─────┴─────┐
  │失败次数    │
  │< max_retry│──YES──► 返回 resume 状态
  │(默认3)?    │
  └─────┬─────┘
        │NO
        ▼
──────────── 进入 error_recovery 阶段 ────────────
│                                               │
│  StateMachine.force("error_recovery")         │
│  → Load prompts/phase_error_recovery.md       │
│  → Send to main_engineer session              │
│  → LLM 生成诊断报告 (recovery_memo)            │
│  → Save to ArtifactStore                      │
│  → StateMachine.record_success("error_recovery")│
│                                               │
│  返回结果:                                      │
│  {terminal_state: "resume" | "failed",          │
│   failed_phase: str,                           │
│   error: str,                                  │
│   recovery_memo: str}                          │
└───────────────────────────────────────────────────
```

---

## 10. 配置文件系统

### 9.1 Config 层级

```
┌─────────────────────────────────────────┐
│  config/framework_defaults.yaml         │
│                                         │
│  framework:                             │
│    entry_script_timeout: 1200           │
│    session_timeout_repair: 3600         │
│    session_timeout_followup: 300        │
│    session_timeout_analyzer: 3600       │
│    session_timeout_phase: 600           │
│    review:                              │
│      enabled: false                     │
│      max_review_iterations: 3           │
│    server:                              │
│      auto_start: true                   │
│      port: 4098                         │
│    artifacts:                           │
│      key_prefix: "phase_"               │
│                                         │
│  环境变量插值: 所有 {VAR_NAME}          │
│  替换为 os.environ.get('VAR_NAME', '') │
└─────────────────────────────────────────┘
```

### 9.2 超时配置映射表

| Config Key | 使用位置 | 默认值 |
|---|---|---|
| `framework.entry_script_timeout` | Phase 5 subprocess.run() 执行入口脚本 | 1200s |
| `framework.session_timeout_repair` | _analyze_error() + repair send_command() | 3600s |
| `framework.session_timeout_followup` | JSON 重解析 follow-up 请求 | 300s |
| `framework.session_timeout_analyzer` | _run_improvement_iteration() | 3600s |
| `framework.session_timeout_phase` | Phase 0-3 的Phase级超时 | 600s |

---

## 11. Artifact 文件系统结构

```
{project_dir}/.sm-artifacts/{run_id}/
├── raw/                                    # 每次尝试的原始输出
│   ├── phase_0_env_detect_attempt1.json
│   ├── phase_1_project_analysis_attempt1.json
│   ├── phase_1_5_constraint_summary_attempt1.json
│   ├── phase_2_venv_create_attempt1.json
│   ├── phase_3_entry_script_attempt1.json
│   ├── phase_4_rule_migration_attempt1.json
│   ├── phase_5_validation_attempt1.json
│   ├── phase_5_validation_attempt2.json   (如果Phase 5重试)
│   └── phase_6_report_attempt1.json
│
├── validated/                              # 验证后规范输出 (每个Phase一个)
│   ├── phase_0_env_detect_canonical.json
│   ├── phase_1_project_analysis_canonical.json
│   ├── phase_1_5_constraint_summary_canonical.json
│   ├── phase_2_venv_create_canonical.json
│   ├── phase_3_entry_script_canonical.json
│   ├── phase_4_rule_migration_canonical.json
│   ├── phase_5_validation_canonical.json
│   └── phase_6_report_canonical.json
│
├── runtime/                                # operator_fixer 瘦身 prompt 引用的运行产物
│   ├── runtime_error_<project>.md          # Operator Fixer + Execution Failure + Error Classification
│   └── runtimeCard_<project>.md            # Analyzer-selected Experience Card 1..N
│
├── execution_journal.jsonl                 # 追加式事件日志 (每步一条)
├── state.json                              # RepairContext 检查点 (Phase 5)
├── passing_version_iter1.json              # 审查门快照 (SHA256 of all .py)
├── passing_version_iter2.json              # ...
└── reports/                                # Phase 6 生成的报告
    ├── API_KEY_REPORT.md
    ├── OPENCODE_OPERATIONS_LOG.md
    ├── TOOLS_EXECUTION_REPORT.md
    ├── SUMMARY_REPORT.md
    └── LOCAL_TOOL_OPTIMIZATION_REPORT.md
```

---

## 12. Prompt 模板清单

| 模板文件 | 使用方 | 用途 | 关键 Placeholder |
|---|---|---|---|
| `phase_0_env_detect.md` | PhaseRunner Phase 0 | 检测平台、NPU、Python版本 | `{phase_name}`, `{project_dir}`, `{constraint_summary}`, `{previous_outputs}` |
| `phase_1_project_analysis.md` | PhaseRunner Phase 1 | 分析项目结构、CUDA依赖 | 同上 + `{constraint_summary}` |
| `phase_1_5_constraint_summary.md` | PhaseRunner Phase 1.5 | 约束摘要生成 | `{user_constraints}`, `{phase_1_analysis}`, `{challenge_flags}` |
| `phase_2_venv_create.md` | PhaseRunner Phase 2 | 虚拟环境创建与依赖安装 | `{previous_outputs}`, `{constraint_summary}` |
| `phase_3_entry_script.md` | PhaseRunner Phase 3 | 入口脚本确认与构建 | `{previous_outputs}`, `{constraint_summary}` |
| `phase_4_rule_migration.md` | Workflow 引用 (实际无LLM) | 规则迁移参考 | 无 |
| `phase_5_review.md` | PhaseRunner `run_review_check()` | 审查修复迭代的NPU合规性 | `{repair_context}`, `{iteration}`, `{cpu_fallback_analysis}` |
| `phase_5_validation.md` | Workflow 引用 | 验证阶段参考 | 无 |
| `phase_6_report.md` | PhaseRunner Phase 6 | 生成最终迁移报告 | 全部Phase 0-5的输出 |
| `phase_error_recovery.md` | RepairLoop + Orchestrator 错误恢复 | 错误分析、分类、角色推荐 | `{failure_log}`, `{entry_script}`, `{previous_outputs}`, `{constraint_summary}` |
| `phase_review_improvement.md` | RepairLoop `_run_improvement_iteration()` | 审查拒绝后的改进方向分析 | `{last_review_json}`, `{constraint_summary}`, `{improvement_history}` |
| `repair_dependency_fixer.md` | RepairLoop (dependency_fixer角色) | 修复依赖/导入/安装错误 | `{error_text}`, `{project_dir}`, `{constraint_summary}` |
| `repair_code_adapter.md` | RepairLoop (code_adapter角色) | 代码级CUDA→NPU适配 | `{error_text}`, `{project_dir}`, `{constraint_summary}` |
| `repair_operator_fixer.md` | RepairLoop (operator_fixer角色) | NPU算子/设备放置修复；详细上下文写入 `runtime/` Markdown 产物 | `{runtime_error_artifact_path}`, `{runtime_card_artifact_path}` |

---

## 13. Validator 清单

| 验证器文件 | 对应Phase | 验证内容 | 失败处理 |
|---|---|---|---|
| `validate_env_detect.py` | Phase 0 | `platform ∈ {npu,cuda}`, `npu_detected: bool`, `python_version: str` | 重试 (最大3次), 自动补正Prompt |
| `validate_project_analysis.py` | Phase 1 | `project_dir: str`, `dependencies: list[str]`, `cuda_detected: bool`, `entry_script: str` | 重试 (最大3次) |
| `validate_venv.py` | Phase 2 | `venv_path: str`, `python_path: str`, `installed_packages: list[str]` | 重试 (最大3次) |
| `validate_entry_script.py` | Phase 3 | `entry_script_path: str`, `run_command: str` | 重试 (最大3次) |
| `validate_rule_migration.py` | Phase 4 | `files_migrated: int≥0`, `files_skipped: int≥0`, `replacement_counts: dict` | 重试 (最大3次) |
| `validate_validation_final.py` | Phase 5 结果 | `success: bool`, `iteration_count: int≥0`, `errors: list` | 直接失败 (无重试) |

---

## 14. 关键设计模式总结

| 模式 | 说明 | 实现位置 |
|---|---|---|
| **Protocol 松散耦合** | `SessionManagerLike`, `InlineSessionLike` Protocol 允许替换会话后端 | `phase_runner.py`, `repair_loop.py` |
| **Registry 验证器** | `ValidatorEngine.register_validator(name, fn)` 将验证器与Phase逻辑解耦 | `validator_engine.py` |
| **Phase 别名映射** | `_PHASE_GROUPS` 元组映射别名 (e.g. `phase_0` ↔ `phase_0_env_detect`) | `phase_runner.py` |
| **自适应 Prompt 修正** | `_build_correction_prompt()` 从验证错误自动提取缺失字段 | `_run_single_phase()` |
| **停滞检测** | 3次相同错误签名 → 停止修复循环 | `repeated_error_count >= _STAGNATION_THRESHOLD` |
| **审查门** | 可选的修复后评估,可触发改进迭代 | ReviewGateState + review_callable |
| **双重 Artifact 存储** | Raw (每次尝试) + Validated (规范版) + JSONL 日志 | `artifact_store.py` |
| **空间路径安全处理** | `_safe_split_command()` 处理路径含空格的情况 | `repair_loop.py` |
| **配置超时解耦** | `_get_timeout()` 从 YAML 获取超时,而非硬编码 | `repair_loop.py` |
