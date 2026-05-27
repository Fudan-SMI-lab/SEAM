# E2E 端到端迁移测试 — 使用文档

## 概述

migration_utils 提供了一套 YAML 驱动的 E2E 测试管线（V3），支持将 CUDA/PyTorch 项目迁移到多种加速器平台：PPU、Ascend NPU、MUSA、ROCm、MLU。管线通过状态机编排多个 Phase（环境检测、项目分析、虚拟环境搭建、入口脚本契约、静态校验、规则迁移、验证修复循环、迁移报告、经验提炼），每个 Phase 由持久化的 OpenCode Agent 执行，产出可审计的阶段产物和最终证据。

V3 的核心改变是 **工作流由 YAML 定义**，用户通过 `--workflow` / `--workflow-path` 选择目标平台的工作流文件。如果不指定工作流，默认使用 NPU 迁移工作流。

## 快速开始

### Shell 启动器（推荐）

```bash
cd SEAM
bash src/scripts/run_e2e_v3.sh 项目名 \
  --server-url http://127.0.0.1:4098 \
  --max-iter 8 \
  --verbose
```

### PPU 平台冒烟测试

```bash
bash src/scripts/run_e2e_v3.sh 项目名 \
  --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml \
  --server-url http://127.0.0.1:4098 \
  --max-iter 8 \
  --verbose
```

### 直接使用 Python 模块

```bash
python3.10 -m tests.e2e.e2e_test_v3 \
  --project-dir /path/to/项目目录 \
  --output-dir ./output_projects \
  --workflow-path src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 8 \
  --keep-temp-dir \
  --verbose
```

## 前置条件

### 1. OpenCode 服务器（必须）

确保服务器运行在指定端口（Shell 启动器默认 4098，Python 模块默认 4096）：

```bash
curl -fsS http://127.0.0.1:4098/agent
```

如果服务未运行，先启动它。推荐使用 4098 端口以与 Shell 启动器保持一致：

```bash
opencode serve --port 4098 --hostname 127.0.0.1
```

### 2. 项目目录结构

项目目录可从以下位置自动发现（按优先级搜索）：

- `cuda_projects/`（仓库内）
- `original_projects/`（仓库内）
- `application_migration_cases/`（仓库上级目录）

推荐的项目结构：

```
项目目录/
├── ADAPTATION_REQUIREMENTS.md     ← 用户约束文档（可选）
├── original_src/                  ← 干净的上游源码（可选）
└── test_data_and_scripts/         ← 测试入口（可选）
    └── run_*.py                   ← 非交互式 E2E 入口脚本
```

扁平的项目根目录也可以接受；Phase 3 会自动发现或合成入口命令。

### 3. Python 环境

```bash
python3.10 -c "import yaml, json; print('OK')"
```

## 项目准备

新加入迁移管线的项目需要：

```bash
# 1. 创建项目目录
mkdir -p cuda_projects/项目名/{original_src,test_data_and_scripts}

# 2. 克隆上游源码（干净，无任何修改）
cd cuda_projects/项目名/
git clone --depth 1 <repo-url>.git upstream-temp
cp -r upstream-temp/* original_src/
rm -rf upstream-temp

# 3. 下载模型权重（如有）
# 放入 original_src/ 下项目预期的路径中

# 4. 创建适配约束文档（可选）
cat > ADAPTATION_REQUIREMENTS.md <<EOF
# <项目名称> 平台适配需求

## 项目简介
<项目描述，核心计算，DL 框架>

## 已知失败原因（如有）
<之前的失败日志摘要>

## 适配目标
在目标平台上完整运行 <具体功能>

## 关键依赖（需要平台适配）
| 依赖 | 风险等级 |
|------|---------|
| torch | 高 |
| xformers | 极高 |

## 约束条件
- 零 CPU fallback
- 不修改官方源码逻辑
EOF

# 5. 创建测试入口脚本（非交互式，stdout 输出）
cat > test_data_and_scripts/run_test.py <<'EOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'original_src'))
# ... import 项目，运行非交互式推理 ...
EOF
```

## 使用方式

### Shell 启动器（V3）

基本用法：

```bash
bash src/scripts/run_e2e_v3.sh 项目名 \
  --server-url http://127.0.0.1:4098 \
  --max-iter 8 \
  --verbose
```

选择目标平台工作流（不指定时默认为 NPU）：

```bash
bash src/scripts/run_e2e_v3.sh 项目名 \
  --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml \
  --server-url http://127.0.0.1:4098
```

### 直接使用 Python 模块

对于不在标准搜索路径下的项目，可以直接指定绝对路径：

```bash
python3.10 -m tests.e2e.e2e_test_v3 \
  --project-dir /absolute/path/to/项目 \
  --output-dir ./output_projects \
  --workflow-path src/workflows/npu_migration_v2.yaml \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 8 \
  --keep-temp-dir \
  --review-gate \
  --verbose
```

### 查看运行计划（不执行）

```bash
bash src/scripts/run_e2e_v3.sh 项目名 \
  --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml \
  --dry-run \
  --server-url http://127.0.0.1:4098
```

### 调整最大修复次数

```bash
bash src/scripts/run_e2e_v3.sh 项目名 --max-iter 12
```

### 自定义服务器地址

```bash
bash src/scripts/run_e2e_v3.sh 项目名 \
  --server-url http://10.0.0.1:8080
```

### 关闭 Review Gate

```bash
bash src/scripts/run_e2e_v3.sh 项目名 --no-review
```

### 不保留输出目录

```bash
bash src/scripts/run_e2e_v3.sh 项目名 --no-keep-temp
```

### 批量运行（顺序）

```bash
for proj in 项目A 项目B 项目C; do
    bash src/scripts/run_e2e_v3.sh "$proj" \
      --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml \
      --server-url http://127.0.0.1:4098 || echo "FAILED: $proj"
done
```

## 输出产物

### E2E 报告目录

```
e2e-reports/src/<YYYYMMDD_HHMMSS>/
├── summary.json                             ← 运行汇总（overall_status，phases，errors）
├── phase_results.json                       ← 每个 Phase 的状态和耗时
├── before_snapshot.json                     ← 迁移前 Python 文件快照
├── after_snapshot.json                      ← 迁移后 Python 文件快照
├── telemetry.json                           ← 完整 telemetry（sessions，commands，events）
├── telemetry_bridge.json                    ← TelemetryBridge 指标
├── agent_io.jsonl                           ← Agent 输入输出日志（需设置 SM_ADAPT_FULL_AGENT_IO）
└── .sm-artifacts/                           ← 阶段产物副本（从 output_projects/ 复制）
    └── e2e-v3-<run_id>/
        ├── execution_journal.jsonl          ← 逐条阶段日志
        ├── state.json                       ← 状态机状态
        ├── validated/                       ← 验证通过的阶段输出
        │   └── phase_5_validation_canonical.json  ← Phase 5 最终验证结果
        └── raw/                             ← Agent 原始响应
```

### 迁移产物目录

```
output_projects/<项目名>_<YYYYMMDD_HHMMSS>/
├── <迁移后的项目代码>                  ← 已修改的 .py 文件
├── .venv/                                ← 虚拟环境
├── test_data_and_scripts/                ← 入口脚本
├── .sm-artifacts/                        ← 管线产物（canonical 位置；e2e-reports 中为副本）
└── migration_reports/                    ← 入口脚本产出的关键报告
    ├── custom_op_final_gate.json         ← CUDA 自定义算子最终关卡（如有）
    ├── migration_manifest.json           ← 迁移清单
    ├── performance.json                  ← 逐算子性能数据
    ├── baseline.json                     ← 基线设备测量数据
    ├── runtime_coverage.json             ← 运行时覆盖率计数
    └── build.log                         ← 构建日志（含平台原生构建标记）
```

## 参数速查

### Shell 启动器 `run_e2e_v3.sh`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `<PROJECT_NAME>` | （必填） | 项目目录名。搜索路径：`cuda_projects/`、`original_projects/`、`application_migration_cases/` |
| `--workflow PATH` | 默认 NPU 工作流 | 目标平台工作流 YAML 文件路径 |
| `--server-url URL` | `http://127.0.0.1:4098` | OpenCode 服务器地址 |
| `--max-iter N` | `8` | Phase 5 最大修复迭代次数 |
| `--review` | 启用 | 启用 Review Gate |
| `--no-review` | — | 关闭 Review Gate |
| `--no-keep-temp` | — | 不保留输出项目目录 |
| `--agent NAME` | 自动检测 | 指定 Agent 名称 |
| `--dry-run` | — | 仅验证环境，打印命令 |
| `--verbose` | — | 启用详细日志 |
| `--extra 'ARGS...'` | — | 透传给 `e2e_test_v3.py` 的额外参数 |

### Python 模块 `e2e_test_v3`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--workflow-path PATH` | 默认 NPU 工作流 | 工作流 YAML 文件路径。**这是选择平台的主要方式。** |
| `--project-dir PATH` | 内置模板 | 源 CUDA 项目路径 |
| `--output-dir PATH` | `output_projects/` | 迁移产物输出根目录 |
| `--server-url URL` | `http://127.0.0.1:4096` | OpenCode 服务器地址 |
| `--max-phase5-iter N` | `5` | Phase 5 修复循环最大迭代次数 |
| `--keep-temp-dir` | — | 保留迁移项目副本供检查 |
| `--review-gate` | — | 运行时成功后启用 Review / 改进循环 |
| `--agent NAME` | 自动检测 | 覆盖服务器报告的 Agent 名称 |
| `--user-constraints PATH` | — | 用户约束文件路径或约束文本 |
| `--framework-config PATH` | — | 覆盖框架默认配置 |
| `--server-auto-start` | 启用 | 允许自动启动 OpenCode |
| `--server-no-auto-start` | — | 要求已有运行中的 OpenCode 服务器 |
| `--server-port PORT` | `0`（自动选择） | 自动启动时的端口偏好 |
| `--verbose` | — | 启用调试日志 |

## 运行状态判断

### 成功标志

在测试日志末尾显示：

```
══════════════════════════════════════════════════════════
  E2E TEST PASSED
══════════════════════════════════════════════════════════
```

`summary.json` 中 `overall_status: "PASS"` 且所有 Phase 的 `status: "passed"`。

### Review 结果

日志中会显示 Review Gate verdict：

| Verdict | 含义 | 后续 |
|---------|------|------|
| `accept` | 无 CPU fallback，约束合规 | 进入 Phase 6 |
| `accept_with_warning` | 通过执行但有轻微警告 | 进入 Phase 6 |
| `reject` | 有 CPU fallback 或约束违反 | 触发 improvement iteration |

### 失败诊断

```bash
# 1. 查看总体状态和各 Phase 结果
python3.10 -c "
import json
s = json.load(open('e2e-reports/src/<timestamp>/summary.json'))
print('Status:', s['overall_status'])
for p in s['phases']:
    if p['status'] != 'passed':
        print(f'  Failed: {p[\"phase_id\"]} — {p.get(\"error\", \"no error detail\")}')
"

# 2. 查看 Phase 5 最终验证结果
python3.10 -c "
import json
p5 = json.load(open('output_projects/<项目名>_<timestamp>/.sm-artifacts/e2e-v3-<run_id>/validated/phase_5_validation_canonical.json'))
print('Status:', p5.get('status'))
print('Errors:', json.dumps(p5.get('errors', []), indent=2, ensure_ascii=False))
print('Custom op gate:', json.dumps(p5.get('custom_op_final_gate', {}), indent=2, ensure_ascii=False))
"

# 3. 查看自定义算子最终关卡（仅 CUDA 自定义算子项目）
python3.10 -c "
import json
g = json.load(open('output_projects/<项目名>_<timestamp>/migration_reports/custom_op_final_gate.json'))
print('Status:', g.get('full_migration_status'))
print('Inventory:', g.get('inventory_count'))
print('Passed/Manifest:', g.get('closed_pass_entries'), '/', g.get('manifest_entries'))
print('Parity:', g.get('report_parity_passed'))
for r in g.get('rows', []):
    nf = r.get('no_fallback_no_zero_call_no_builtin_contamination', {})
    print(f'  {r.get(\"name\",\"?\")}: fallback={nf.get(\"fallback_detected\")} zero_call={nf.get(\"zero_call_detected\")}')
"

# 4. 查看迁移前后的代码差异
diff -qr output_projects/<项目名>_<timestamp>/ \
  cuda_projects/<项目名>/ | grep -v __pycache__ | grep '\.py'
```

## 平台策略说明

### 选择目标平台

SEAM 通过工作流 YAML 中的 `target_platform` 字段声明目标平台。内置预设包括：

| 预设值 | 对应平台 |
|--------|---------|
| `ppu_cuda_compatible` | PPU |
| `npu_ascend` | Ascend NPU |
| `cuda_nvidia` | CUDA / NVIDIA |
| `musa_muxi` | MUSA / 沐曦 |
| `rocm_amd` | ROCm / AMD |
| `mlu_cambrian` | MLU / 寒武纪 |
| `generic_accelerator` | 通用加速器 |

每个预设驱动平台特定的验证标记、迁移规则和证据要求。

### 默认工作流

如果 `--workflow` / `--workflow-path` 未指定，V3 运行器回退到默认的 **NPU 迁移工作流**（`src/workflows/npu_migration_v2.yaml`）。要使用其他平台，必须显式指定对应的工作流文件。

### 性能验证模式

| 模式 | 行为 |
|------|------|
| `full`（默认） | 要求 `baseline_seconds > 0`、`custom_seconds > 0` 且 `speedup_vs_baseline > 0`。 |
| `presence_only` | 要求存在性能证据（`baseline_seconds > 0`、`custom_seconds > 0`），但不强制加速比。**仅放宽性能比较，其他所有关卡（no-fallback、源码证据、运行时证据、原生构建证据）仍然适用。** 适用于异构平台 brings-up 阶段。 |
| `disabled` | 完全跳过性能验证。其他关卡仍然适用。 |

工作流中可通过 `target_platform.overrides` 配置性能验证模式和基线设备列表：

```yaml
target_platform:
  preset: ppu_cuda_compatible
  overrides:
    custom_op_evidence:
      performance_validation: presence_only
      performance_baseline_device_values:
        - cuda
        - gpu
        - torch_cuda
        - cpu
        - torch_cpu
```

### CPU 基线策略

CPU 可以作为性能比较的基线设备出现（当 `performance_baseline_device_values` 包含 `cpu` / `torch_cpu` 时），但这**仅是比较基线，不是回退目标**：

- 迁移 / 自定义算子路径仍必须证明原生设备执行，并通过 no-fallback 证据关卡。
- `no_fallback_no_zero_call_no_builtin_contamination` 所有标记必须显式为 `false`。
- CPU 基线不放松任何其他证据要求。

## 基准配置说明（历史参考）

以下配置来自 DeepWave 在 20260423 的成功 E2E 运行，属于 **V1 时代的 NPU 单平台配置**。当前 V3 的多平台工作流由 YAML 文件定义，默认参数已有变化。以下内容仅作为历史参考保留：

| 项目 | 值 | 来源 |
|------|---|------|
| server-url | `4098` | 该运行使用的端口 |
| max-iter | `8` | DeepWave 需要超过 5 轮修复 |
| review-gate | **启用** | 确保验证原生设备执行 |
| user-constraints | `ADAPTATION_REQUIREMENTS.md` | 每个项目的定制约束文件 |
| keep-temp | **保留** | 迁移产物需要事后检查 |

> **注意**: 上述配置基于 V1 的 `./scripts/run_e2e.sh` 启动器和 `e2e-real-*` 产物路径，当前 V3 已迁移到 `run_e2e_v3.sh` 和 `e2e-v3-*` 产物路径，不应再使用旧的启动脚本。

## 超时配置

如果 Phase 5 中 entry script 运行时间超过 20 分钟（1200s），需要修改：

```yaml
# config/framework_defaults.yaml
framework:
  entry_script_timeout: 3600    # 1 hour
  session_timeout_repair: 3600  # repair agent session timeout
```

对于大型模型，建议适当调大。

## 常见问题

### 1. "Server not reachable"

```bash
# 检查服务状态
curl http://127.0.0.1:4098/agent

# 检查端口占用
ss -tlnp | grep 4098
```

### 2. Phase 5 一直 exit 1 不进步

说明修复循环遇到了无法自动解决的问题（如需要手动编译平台特定 kernel）。检查最后一个 attempt 的 error_category：

```bash
python3.10 -c "
import json
p5 = json.load(open('output_projects/<项目名>_<timestamp>/.sm-artifacts/e2e-v3-<run_id>/validated/phase_5_validation_canonical.json'))
print('Status:', p5.get('status'))
for err in p5.get('errors', []):
    print('  Category:', err.get('category', 'unknown'), '—', err.get('message', '')[:200])
"
```

### 3. 修复循环停滞（stagnation）

如果同一错误连续出现 3 次，循环会自动终止。这种情况下通常需要人工介入，查看日志中 error_analyzer 的分类。

### 4. 清理旧测试产物

```bash
# 清理报告（保留最近 5 个）
ls -td e2e-reports/src/*/ | tail -n +6 | xargs rm -rf

# 清理输出项目（保留最近 3 个）
ls -td output_projects/项目名_*/ | tail -n +4 | xargs rm -rf
```
