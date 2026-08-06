# Platform Verification Registry — Remaining 8 Handoff Bugs

> **Purpose**: 每个 bug 必须登记真实平台验证状态。本机（/home/yiding/SEAM，无 NPU/MUSA/MACA 硬件）
> 只能完成**本机代码层验证**；真实硬件验证统一标记 **"待硬件环境"**，并记录触发条件。
> 由后续 GREEN 任务（T6/T7/T8/T11/T12/T16/T17/T18）在提交时填写"本机代码层验证(命令/证据)"列。
>
> **Created**: 2026-08-06 · T1 (Step 0) — baseline HEAD `6018b85` (`v1.2.1-dec-test`; T2 #16 token 已提交)
> **相关基线**: 2935 passed / 13 failed (预存环境类) / 84 skipped — 见 `baseline-pre.md`

| Bug | 修复内容 (本计划) | 本机代码层验证 (命令/证据) | 真实平台验证状态 | 待硬件环境触发条件 |
|---|---|---|---|---|
| #15 | dispatch 死锁：falsy next_id 不再设全量 dispatch_route + 按 workflow 路由表校验 repair_role（新建 test_workflow_dispatch_policy.py） | ✅ T6 已填 — `pytest src/tests/test_workflow_dispatch_policy.py` = 6/6 通过（`task-6-green.log`）；定向回归 `pytest src/tests/test_workflow_executor.py -k "dispatch or stagnation"` = 8/8（`task-6-control.log` 仅 2 个预存 #14 RED）；`tests/test_repair_loop.py` 70/70；ppu_sglang 工作流级验证：路由表提取成功、未声明角色 final_gate_report_fixer 显式 ValueError 而非静默、空 route 保持 no-op（`task-6-e2e.log`）；提交 `6018b85^..HEAD` `fix(dispatch): do not set full dispatch_route on falsy next_id; route-map scoped validation for #15` | **待硬件环境** | 无需硬件（纯逻辑死锁）→ 需在**真实多轮 dispatch 生产工作流**上复现原 3 链死锁场景并确认修复后不再 stagnation；musa_muxi_* 对照 workflow 回归 |
| #14 | custom_op_final_gate 不再静默 auto-skip（声明 gate 即 fail-closed）+ 可重放依赖持久化 + setuptools pin 进 PPU wheelhouse manifest | ✅ T7 已填 — `pytest src/tests/test_dependency_persistence.py` = 3/3；`pytest src/tests/test_workflow_executor.py -k "custom_op_final_gate or declared_custom_op or non_custom_project_skips"` = 8/8（178 deselected）；全量套件 `pytest tests -q -m "not opencode and not docker and not e2e"` = 22 failed / 2944 passed / 84 skipped / 45 deselected（`task-7-suite.log`：22 = 13 预存基线 + 3 HEAD 预存 + 6 未提交 test_platform_capability.py；相对上一轮 26 失败**恰减 4 个 #14 回归测试，0 新增失败**）；ppu_vllm 工作流级 e2e：声明 gate + route 禁用即 fail-closed 运行（skipped=False）、缺报告显式失败、失败置 script_exit_code=1、有效报告通过、review-gate 激活时 defer（skipped=True）、依赖 plan 持久化/重放 roundtrip 保包（`task-7-e2e.log` 9/9）；提交 `07a67cc..HEAD` `fix(gate): honor declared custom_op_final_gate; persist replayable dependency plan for #14` | **待硬件环境** | 需在**真实 PPU 私有源/镜像环境**验证 wheelhouse 中 setuptools==77.0.3 pin 生效、依赖 manifest 在环境重建后可重放、gate 报告真实产出 |
| #8 | NPU 能力预检 + unsupported 分类 + CPU fallback 降级标注（分类基础设施，不宣称根因修复） | ✅ T8 已填 — `pytest src/tests/test_platform_capability.py -v` = **7/7 通过**（`task-8-green.log`：6 个 T5 RED 全转 GREEN + 1 个 npu 识别 control）；新增 `accelerator_context.get_platform_capabilities()`（per-family bool dict，npu 键保证存在）与 `accelerator_context.precheck_platform_capability()`（5 键分类器：supported/blocked_reason/usable_backend/degraded_fallback/platform_degraded；gguf CUDA 后端 llama-cpp-python on npu → `blocked_reason="unsupported_backend"`；CPU-only 环境 → `platform_degraded=True` + `degraded_fallback=True` 显式降级）；新增 `platform_policy.satisfies_platform_requirements()`（#17 依赖，target_device 匹配 policy family 规则）；定向回归 `pytest tests/test_workflow_executor.py tests/test_repair_loop.py -q` = **253/253 通过（0 新增失败）**；repair_loop 未接线（无 installed_packages 数据源，预检保持独立可调用，理由见提交体/notepad） | **待硬件环境** | 需真实 **NPU（torch_npu / Ascend）环境**：验证能力预检识别、GGUF-on-NPU 场景走 unsupported_backend 分类、CPU fallback 出现"降级"标注而非静默成功 |
| #9 | MUSA/MACA/Metax 能力识别 + 预检 + unsupported 分类 | ⏳ 由 T11 填写（`pytest src/tests/test_platform_capability.py -k "musa or maca or muxi or metax or gguf"`） | **待硬件环境** | 需真实 **MUSA/MACA/Metax 硬件环境**：验证前缀识别、预检成功路径、GGUF-on-musa 缺后端 → unsupported_backend 分类 |
| #17 | 成功标准布尔契约 `entry_exit_ok AND required_artifacts_valid AND platform_policy_satisfied AND required_gates_passed AND review_policy_satisfied`；max_phase5_iter 仅安全上限 | ⏳ 由 T12 填写（`pytest src/tests/test_success_contract.py` / `test_orchestrator.py -k "phase5 or succeeded"`） | **待硬件环境 + 待需求方最终确认** | 成功判定需在**真实多轮修复工作流**上验证 5 项 AND 契约行为；布尔契约最终确认流程待需求方拍板（重做触发点已文档化） |
| #18 | 报告版本化 schema + validator 消费 JSON schema + fallback/phase_runner run_timeline 一致性 | ⏳ 由 T16 填写（schema 版本化测试 / validate_reports 定向回归） | **待硬件环境** | 需真实运行产生 phase_6_reports.json 的平台环境，验证报告带 schema_version 且 validator 按 schema 消费 |
| #10 | wheel 打包（package-data/scripts/packages）+ seam CLI 入口 + importlib.resources + test_ci_contract 同步 | ⏳ 由 T17 填写（构建 wheel → venv 安装 → `seam --help` → 资源加载断言） | **待硬件环境** | 需在**干净 venv/安装环境**验证 wheel 安装后 seam CLI 可用、资源从包内加载（本机无资源受限条件）；CI PUSH_BRANCH 推送验证待 CI 环境 |
| #12 | ADAPTATION_REQUIREMENTS.md + src/test_data_and_scripts/ + User_Guide/README 契约文档 | ⏳ 由 T18 填写（`pytest src/tests/test_documented_cli_contracts.py` / `test_usage_guide_docs.py`） | **待硬件环境** | 需在真实运行目录验证 cwd 契约（repair_loop `_resolve_script_cwd`）在 `cd /path && python test_data_and_scripts/run_inference.py` 场景下解析正确 |

## 全局说明
- **本机硬件能力**：无 NPU / MUSA / MACA / Metax 硬件，无对应驱动。所有"真实平台验证"一律 **"待硬件环境"**。
- **本机代码层验证**：由各 GREEN 任务（T6/T7/T8/T11/T12/T16/T17/T18）完成并在提交时回填本表（命令 + 证据文件路径）。
- **#8/#9 语义**：本计划仅提供**分类基础设施 + 标注**，不宣称根因已在真实平台修复（Metis Q2 重述）。
- **#17 语义**：实现 + "待需求方最终确认"标注，不阻塞执行（用户决策 ③）。
- **基线保护**：任何任务不得新增失败；预存 13 个失败 node ID 见 `baseline-pre.md`。
