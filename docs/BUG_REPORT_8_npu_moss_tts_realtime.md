# SEAM Bug #8 调查报告：NPU 平台适配 MOSS-TTS-Realtime 失败

## 元信息

| 项目 | 内容 |
|---|---|
| Bug 编号 | #8 |
| Bug 标题 | NPU 平台适配 MOSS-TTS-Realtime 失败 |
| 严重级别 | 中（P2） |
| 状态 | New（本机无法复现，需真实 NPU 日志定位） |
| 目标项目 | ModelScope `openmoss/MOSS-TTS-Realtime` |
| 症状 | Phase 5 执行 8 轮后失败，退出码 1，展开日志被截断 |
| 调查日期 | 2026-08-06 |
| 结论 | **本机无任何 MOSS-TTS-Realtime 复现产物**；handoff 明确要求"必须依赖真实 NPU 日志定位"，本地代码层仅可确认一个缺失点（Phase 5 无平台能力不支持分类），已记录供修复参考 |

---

## 一、问题描述

原始展开记录（handoff 第 2 节 #8 行）：

> ModelScope `openmoss/MOSS-TTS-Realtime`；Phase 5 执行 8 轮后失败，退出码 1，展开日志被截断

症状三要素：

- **Phase 5 修复循环跑满 8 轮**：与 `src/scripts/run_e2e_v3.sh` 默认 `MAX_ITER=8`（第 27 行）一致；NPU 通用工作流 YAML（`npu_ascend_general.yaml`）自身 repair_loop `max_iterations: 5`，但 CLI `--max-phase5-iter` 会覆盖 YAML（workflow_executor.py L4110-4120 解析链：CLI globals > YAML 定义 > framework 默认值）；
- **退出码 1**：repair_loop 循环耗尽后状态为 `max_iterations`（repair_loop.py L1900/L1921），属于"尝试次数用尽"而非"平台不支持"的明确分类；
- **展开日志被截断**：根因诊断信息未完整保留，无法从展开记录区分 依赖 / 模型资产 / 音频流式入口 / NPU 算子 四类根因。

## 二、本机证据排查结果（全部为空）

在堡垒机本地按 handoff 第 4.2 节要求逐一检查，**未发现任何 MOSS-TTS-Realtime 复现产物**：

| # | 检查位置 | 结果 |
|---|---|---|
| 1 | 全仓 grep `MOSS\|TTS\|SpeechGPT\|IndexTTS`（src、workflows、prompts、scripts、docs、memory） | 仅命中 `run_e2e_v2.sh`/`run_e2e_v3.sh` 中的 `07_IndexTTS`、`08_SpeechGPT-2.0-preview` 示例用例名，以及 `experience_refiner.md` 中无关的文本提及；**无 MOSS-TTS-Realtime 代码引用** |
| 2 | `/home/yiding/e2e_results/` | 仅 BlueLM 量化 NPU 运行（`bluelm_quant_ascend_20260726_162840`），无 MOSS |
| 3 | `/home/yiding/output_projects/` | 21 个产物目录全部为 `BlueLM-*` 与 `test_project_*`；grep `MOSS\|TTS\|Speech` 无命中；无任何 `run_manifest*`/`phase_5*`/`loop_history*` 文件 |
| 4 | `/home/yiding/seam_tui_demo_output/` | 仅 `test_project_template_*` 5 个目录，无 MOSS |
| 5 | `application_migration_cases`、`SEAM/original_projects`、`SEAM/cuda_projects`、`$REPO_ROOT/../original_projects` | 本机均不存在或为空（run_e2e_v3.sh L17-22 的 PROJECT_SEARCH_DIRS 全部落空） |
| 6 | `/home/yiding/*.tar.gz`（6 个压缩包，含 handoff 包） | 无 MOSS/TTS 内容 |
| 7 | `.memory/memory/index/cases.jsonl`（4 份） | grep `MOSS\|TTS` 计数为 0 |
| 8 | `e2e-reports/src/*/summary.json`、`ui_events.jsonl` | summary.json 均为空 `{}`；ui_events 无 MOSS/TTS、无"展开/expand"事件 |
| 9 | `.sm-artifacts/` | 仅 `bug16-20260805-170012` 一个目录 |

**结论**：本机不具备 handoff 要求的 Phase 5 stdout/stderr/meta 与 `loop_history` 现场证据，无法稳定复现 #8。

## 三、代码层机制梳理（供修复参考）

按 handoff 第 6 节 #8/#9 平台适配指引排查，确认以下机制现状：

### 3.1 Phase 5 循环结局状态（repair_loop.py）

- 状态全集：`success` / `stagnation` / `passed_with_reviews` / `max_iterations` / `review_failed` / `context_exhausted`；
- 循环耗尽（未提前 break）时状态落为 `max_iterations`（L1900），日志为 `Phase 5 completed: MAX_ITERATIONS`（L1921）；
- **不存在"平台能力不支持"（unsupported）分类**：`_REPAIR_ROLES`（L99-104）只有 `dependency_fixer` / `code_adapter` / `operator_fixer` / `final_gate_report_fixer`，没有任何角色处理"目标 GPU 平台不支持该项目所需能力"的情形；
- 停滞检测 `_check_stagnation` 只比较错误签名重复（阈值默认 3），不识别平台能力阻断。

### 3.2 平台能力预检链路（accelerator_context / platform_policy / selector）

- `accelerator_context.py`：仅做**包名提取**（torch_npu、ppukernel、vllm、triton、cuda 等前缀匹配），返回 `accelerator_packages`/`accelerator_package_versions`/`torch_npu_version`，**无能力判定**；
- `platform_policy.py`：`resolve_policy()` 依据 YAML `target_platform.preset`（npu_ascend / ppu_cuda_compatible / generic_accelerator）+ overrides 解析策略，含 `CustomOpEvidenceConfig` 门控，但**无"项目所需能力 × 平台能力"的适配性预检**；
- `workflow_selector.py`：基于 agent 从候选工作流中选择 + fallback，project_context 仅含语言/框架/文件数等轻量信息，**不参与平台能力判定**；
- `validate_env_detect.py`：仅校验 platform 非 cpu、npu/ppu/cuda 的 detected 布尔字段，不校验能力匹配。

### 3.3 已有的 unsupported 先例（可复用模式）

`workflow_executor.py` L5414-5418 存在 `blocked_reason: "unsupported_backend"` 先例（环境重置动作在非 ContainerBackend 下被阻断并给出结构化分类）。该"稳定、可报告的失败分类"模式可直接沿用到 Phase 5 平台能力不支持场景。

## 四、环境差异与复现阻塞点

| 项 | 本机现状 | handoff 要求 |
|---|---|---|
| NPU 设备 / davinci | 无（无 `/dev/davinci*`） | 需要 NPU 环境 |
| 真实 Phase 5 stdout/stderr/meta | 无 | 需要 `.sm-artifacts` 现场 |
| `loop_history` | 无 | 需要 |
| MOSS-TTS-Realtime 项目源码 | 无（PROJECT_SEARCH_DIRS 全空） | 需要 `original_projects/` 或等价目录 |
| OpenCode 长迭代 E2E | 本机未执行过 | 需要 |

## 五、建议的下一步

1. **首选**：从真实 NPU 环境（堡垒机/远端）取得 #8 展开记录的完整 Phase 5 日志与 `loop_history`，按 handoff 区分 依赖 / 模型资产 / 音频流式入口 / NPU 算子 / 误判 五类根因；
2. **代码层候选修复**（可在本机实现+测试，不需 NPU 硬件）：为 Phase 5 增加"平台能力不支持"的稳定分类——在 `platform_policy.py`/`accelerator_context.py` 链路增加项目能力×平台能力适配性预检，命中不支持时产出结构化 `unsupported` 分类（沿用 workflow_executor L5414 的 `blocked_reason` 模式），使 8 轮耗尽不再是唯一结局；同时确保该分类满足 #17 成功布尔契约（`platform_policy_satisfied = false` → 明确 failure/incomplete）。
3. 在获取真实日志前，按 handoff 第 4.2 节"若无法复现，停止修改并报告环境差异及已检查证据"执行，不做猜测性修改。

## 六、证据位置

| 位置 | 说明 |
|---|---|
| /home/yiding/v1.2.1-dev-bugfix-handoff-deepseek.md | #8 原始描述（L23）、第二批处理顺序（L45）、#8/#9 平台适配指引（L310-312） |
| /home/yiding/SEAM/src/core/repair_loop.py | 循环结局状态（L1900-1921）、_REPAIR_ROLES（L99-116） |
| /home/yiding/SEAM/src/core/workflow_executor.py | max_iterations 解析链（L4110-4120）、unsupported_backend 先例（L5414-5418） |
| /home/yiding/SEAM/src/core/accelerator_context.py | 加速器包提取（无能力判定） |
| /home/yiding/SEAM/src/core/platform_policy.py | resolve_policy / preset / CustomOpEvidenceConfig |
| /home/yiding/SEAM/src/core/workflow_selector.py | agent 选择 + fallback |
| /home/yiding/SEAM/src/workflows/npu_ascend_general.yaml | target_platform preset、repair_loop max_iterations=5 |
| /home/yiding/SEAM/src/scripts/run_e2e_v3.sh | MAX_ITER=8 默认（L27）、PROJECT_SEARCH_DIRS（L17-22） |
| /home/yiding/e2e_results/、/home/yiding/output_projects/、/home/yiding/seam_tui_demo_output/、/home/yiding/SEAM/.sm-artifacts/ | 本机全部运行产物（无 MOSS-TTS 内容） |
