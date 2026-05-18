# migration_utils × 04_Deepwave 端到端迁移测试指南

## 概述

本文档说明如何使用 **migration_utils 管线**（而非手动操作）对 `04_Deepwave` 项目执行完整的自动迁移适配 + 端到端验证。

管线流程：

```
Phase 0: 环境检测     → 确认 NPU / Python / CANN 就绪
Phase 1: 项目分析     → 梳理 Deepwave 源码结构、CUDA 模式
Phase 1.5: 约束摘要   → 将 ADAPTATION_REQUIREMENTS.md 转为可执行规则
Phase 2: venv 创建    → 安装 torch_npu 等依赖
Phase 3: 入口确认     → 识别 test_e2e_fwi.py 为验证入口
Phase 4: 规则迁移     → 基于正则的 CUDA→NPU API 替换
Phase 5: 验证修复循环 → 执行脚本 → 分析错误 → 修复 → 审查 → 循环(5次)
Phase 6: 报告生成     → 输出迁移总结
```

---

## 前置条件

### 1. OpenCode 服务

确保 OpenCode 服务运行在 **端口 4098**：

```bash
# 验证
curl -fsS http://127.0.0.1:4098/agent
```

> 注意：不要使用 4096 端口。

### 2. NPU 环境

```bash
# CANN 环境
source /usr/local/Ascend/ascend-toolkit/set_env.sh

# 验证 NPU 可用
python -c "import torch_npu; assert torch_npu.npu.is_available()"
```

### 3. Python 依赖

```bash
# 在 migration_utils 目录下确认可运行单元测试
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM
python -m pytest tests/ -x -q
```

---

## 启动方式

### 方式 A：E2E 测试脚本（推荐 — 完整管线 + 遥测采集）

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM

python -m tests.e2e.e2e_test \
  --server-url http://127.0.0.1:4098 \
  --project-dir original_projects/04_Deepwave \
  --max-phase5-iter 5 \
  --keep-temp-dir
```

**参数说明**：

| 参数 | 值 | 说明 |
|------|---|------|
| `--server-url` | `http://127.0.0.1:4098` | OpenCode 服务地址 |
| `--project-dir` | `original_projects/04_Deepwave` | Deepwave 源码路径 |
| `--max-phase5-iter` | `5` | 修复循环最多 5 轮 |
| `--keep-temp-dir` | *(flag)* | 保留迁移后的项目副本，方便检查 |
| `--review-gate` | *(可选)* | 启用审查门改进模式
| `--output-project-dir` | *(可选)* | 指定迁移产物输出目录 |

**运行中控制台输出示例**：

```
[2026-04-21 10:30:00] Copying project .../04_Deepwave to .../04_Deepwave_20260421_103000...
[2026-04-21 10:30:05] Copied 25 files, symlinked 2 large files to /tmp/sm-adapt-xxx
[2026-04-21 10:30:05] SessionManager created: detected_agent=Sisyphus
[2026-04-21 10:30:10] Main session created: sess-abc123
[2026-04-21 10:30:10] [Phase 0/8] Environment Detection — STARTING
[2026-04-21 10:30:45] [Phase 0/8] Environment Detection — PASSED (35.0s)
[2026-04-21 10:30:45] [Phase 1/8] Project Analysis — STARTING
[2026-04-21 10:32:00] [Phase 1/8] Project Analysis — PASSED (75.0s)
[2026-04-21 10:32:00] [Phase 2/8] Virtual Environment Creation — STARTING
...
[2026-04-21 10:XX:XX] [Phase 5/8] Validation Repair Loop — STARTING
[2026-04-21 10:XX:XX] [Iter 1/5] Running entry script...
[2026-04-21 10:XX:XX] [Iter 1/5] Validation FAILED (exit 1) - ImportError: ...
[2026-04-21 10:XX:XX] [Iter 1/5] Analyzer classified -> category=migration logic, role=code_adapter
...
[2026-04-21 11:XX:XX] E2E PASS
```

**测试通过标准**：

| 条件 | 验证方式 |
|------|---------|
| 最终状态 | `E2E PASS` 打印 + `summary.json` 中 `overall_status: "PASS"` |
| 全部 8 个 Phase | `phase_results.json` 中所有 8 个 phase 的 `status: "passed"` |
| Phase 5 修复 | `phase_results.json` 中 status 为 `passed`（脚本 exit 0） |

**产物位置**：

```
# 默认输出目录 (若未指定 --output-project-dir)
SEAM/output_projects/04_Deepwave_YYYYMMDD_HHMMSS/

# 报告目录
e2e-reports/migration_utils/YYYYMMDD_HHMMSS/
├── summary.json           ← 最终运行总结 (overall_status, phases, exit codes)
├── phase_results.json     ← 每个 Phase 的状态和耗时
├── before_snapshot.json   ← 迁移前 Python 文件快照
├── after_snapshot.json    ← 迁移后 Python 文件快照
├── .sm-artifacts/         ← 管线各 Phase 的 JSON 产物
├── telemetry_sessions.json
├── commands.jsonl
└── ...
```

---

### 方式 B：sm_adapt_cli（简洁版 — 无遥测）

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM

python -m scripts.sm_adapt_cli \
  --project-dir original_projects/04_Deepwave \
  --opencode-url http://127.0.0.1:4098 \
  --verbose
```

> 注意：`sm_adapt_cli` 目前是独立的 CLI 入口，内部仅打印进度，还未完全接入 Orchestrator。如果需要完整的 8 Phase 管线，请使用 **方式 A**。

---

## 测试结束后检查

### 1. 查看迁移后的代码差异

```bash
# 迁移产物目录 (由 --keep-temp-dir 保留)
DEST="${HOME}/output_projects/04_Deepwave_YYYYMMDD_*/"

# 与原始对比
diff -qr "$DEST" original_projects/04_Deepwave/ \
  | grep -v ".sm-artifacts" \
  | grep -v "__pycache__"
```

### 2. 检查迁移后的代码是否存在 CPU fallback

```bash
DEST="${HOME}/output_projects/04_Deepwave_YYYYMMDD_*/"

# 搜索 CPU fallback 模式
grep -rn "device.*=.*cpu\|\.to('cpu')\|\.cpu()\|device_str.*=.*'cpu'" \
  "$DEST" --include="*.py"
```

如果 grep 有输出，说明迁移管线在 Phase 5 中让 LLM 写入了 CPU fallback——这违反了"零 CPU fallback"约束，测试视为**不合格**。

### 3. 手动运行迁移后的 E2E 脚本

```bash
# 用迁移后的项目直接跑 test_e2e_fwi.py
pushd "$DEST"
python test_data_and_scripts/test_e2e_fwi.py
popd
```

**成功标志**：
- `NPU available: True`
- `[4/5] Forward passed`
- `Backward passed!`
- `PASS: Non-zero gradient computed!`
- exit code = 0

---

## Skip Validation 模式（快速验证管线逻辑）

仅验证 Phase 0-3 的 LLM 分析是否正确，跳过 Phase 5 的真实 subprocess 执行：

```bash
python -m tests.e2e.e2e_test \
  --server-url http://127.0.0.1:4098 \
  --project-dir original_projects/04_Deepwave \
  --keep-temp-dir
```

此模式适合：
- 快速检查管线是否能正确识别 Deepwave 的入口脚本
- 查看 Phase 0-3 的分析输出（`.sm-artifacts/phase_X.json`）
- 节省等待 Phase 5 修复循环的时间

---

## 常见问题

### E2E 测试卡住不动

```bash
# 检查 OpenCode 服务响应
curl http://127.0.0.1:4098/agent
# 应返回 agent 配置信息

# 检查 NPU
npu-smi info
```

### Phase 5 修复循环始终 exit 1

检查 `e2e-reports/migration_utils/YYYYMMDD_HHMMSS/phase_results.json`：

```bash
cat e2e-reports/migration_utils/YYYYMMDD_HHMMSS/phase_results.json \
  | python -m json.tool
```

关注 `phase_id: "phase_5_validation"` 的 `error` 字段，这就是 Deepwave 迁移失败的真实错误信息。

### 迁移后产物找不到

```bash
# 方式 A 的默认输出
ls -lt SEAM/output_projects/ | head -5

# E2E 报告
ls -lt e2e-reports/migration_utils/ | head -5
```
