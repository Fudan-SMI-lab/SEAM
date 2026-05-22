# E2E 端到端迁移测试 — 使用文档

## 概述

src 提供了一套完整的 E2E 测试管线, 自动将 CUDA 项目适配到 Ascend NPU。本工具基于 Deepwave 成功运行 (`20260423_053647`) 的配置封装, 可直接用于后续的 Hallo、ChaiLab、Hallo3、InsectID、ChatPLUG、IndexTTS 等项目迁移。

## 快速开始

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM
./scripts/run_e2e.sh 01_Hallo
```

这就是全部 — 脚本会自动:
1. 验证项目结构完整性
2. 检查 OpenCode 服务器可达性
3. 以基准配置启动 E2E 测试
4. 输出测试报告路径

## 前置条件

### 1. OpenCode 服务器 (必须)

确保服务器运行在 **端口 4098** (基准端口):

```bash
curl -fsS http://127.0.0.1:4098/agent
# 应返回 agent 配置信息, 不应连接拒绝
```

如果服务未运行, 先启动它 (具体启动方式根据你的部署环境)。

> **注意**: 默认端口是 4098, 不是 4096。如果需要自定义, 使用 `--hostname` / `--port` / `--server_type`。

### 2. 项目目录结构 (必须)

`original_projects/<N>_<Name>/` 下必须存在以下结构:

```
original_projects/01_Hallo/
├── ADAPTATION_REQUIREMENTS.md     ← 项目约束文档 (自动检查, 缺失时创建模板)
├── original_src/                  ← 原始上游源码 (克隆自 GitHub)
└── test_data_and_scripts/
    └── run_*.py                   ← 非交互式 E2E 入口脚本
```

所有 6 个项目已经准备好, 运行:

```bash
ls original_projects/{01_Hallo,02_ChaiLab,03_Hallo3,05_InsectID,06_ChatPLUG,07_IndexTTS}/
```

### 3. Python 环境

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM
python -c "import yaml, json; print('OK')"
```

## 项目准备 (如果新项目)

新加入迁移管线的项目需要:

```bash
# 1. 创建项目目录
mkdir -p original_projects/NN_Name/{original_src,test_data_and_scripts}

# 2. 克隆上游源码 (干净, 无任何修改)
cd original_projects/NN_Name/
git clone --depth 1 <repo-url>.git upstream-temp
cp -r upstream-temp/* original_src/
rm -rf upstream-temp

# 3. 下载模型权重 (如有)
# 放入 original_src/ 下项目预期的路径中

# 4. 创建适配约束文档
cat > ADAPTATION_REQUIREMENTS.md <<EOF
# <项目名称> NPU 适配需求

## 项目简介
<项目描述, 核心计算, DL 框架>

## 已知失败原因 (如有)
<之前的失败日志摘要>

## 适配目标
在 NPU 上完整运行 <具体功能>

## 关键依赖 (需要 NPU 适配)
| 依赖 | 风险等级 |
|------|---------|
| torch | 高 |
| xformers | 极高 |

## 约束条件
- 零 CPU fallback
- 不修改官方源码逻辑
EOF

# 5. 创建测试入口脚本 (非交互式, stdout 输出)
cat > test_data_and_scripts/run_test.py <<'EOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'original_src'))
# ... import 项目, 运行非交互式推理 ...
EOF
```

## 使用方式

### 标准运行

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM
./scripts/run_e2e.sh 01_Hallo
```

### 查看运行计划 (不执行)

```bash
./scripts/run_e2e.sh 03_Hallo3 --dry-run
```

### 调整最大修复次数

```bash
./scripts/run_e2e.sh 02_ChaiLab --max-iter 12
```

### 自定义服务器地址

```bash
./scripts/run_e2e.sh 07_IndexTTS --hostname 10.0.0.1 --port 8080 --server_type opencode
```

### 关闭 Review Gate

```bash
./scripts/run_e2e.sh 05_InsectID --no-review
```

> Review Gate 在 exit 0 后检查是否有 CPU fallback。对于 InsectID 这种纯 ONNX 项目, 可以关闭。

### 不保留输出目录

```bash
./scripts/run_e2e.sh 06_ChatPLUG --no-keep-temp
```

### 批量运行 (顺序)

```bash
for proj in 01_Hallo 05_InsectID; do
    ./scripts/run_e2e.sh "$proj" || echo "FAILED: $proj"
done
```

## 输出产物

### 报告目录

```
src/e2e-reports/src/<YYYYMMDD_HHMMSS>/
├── summary.json              ← 运行汇总 (overall_status, phases, errors)
├── phase_results.json        ← 每个 Phase 的状态和耗时
├── telemetry.json            ← 完整 telemetry (sessions, commands, events)
├── before_snapshot.json      ← 迁移前 Python 文件快照
├── after_snapshot.json       ← 迁移后 Python 文件快照
└── commands.jsonl            ← 所有命令的详情 (如果保留)
```

### 迁移产物目录

```
output_projects/<N>_<Name>_YYYYMMDD_HHMMSS/
├── deepwave/              ← 迁移后的项目代码 (已修改 .py 文件)
├── .venv/                 ← 虚拟环境 (含 torch_npu)
├── test_data_and_scripts/ ← 入口脚本
└── .sm-artifacts/         ← 管线产物 (journal, validated, raw outputs)
    └── e2e-real-<uuid>/
        ├── execution_journal.jsonl   ← 逐条阶段日志
        ├── state.json               ← 状态机状态
        ├── validated/               ← 验证通过的阶段输出
        └── raw/                     ← Agent 原始响应
```

## 参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--hostname` / `--port` / `--server_type` | `http://127.0.0.1:4098` | OpenCode 服务器地址 |
| `--max-iter` | `8` | Phase 5 最大修复迭代次数 |
| `--review-gate` | 启用 | exit 0 后审查 CPU fallback |
| `--no-keep-temp` | 保留输出 | 不清除临时输出目录 |
| `--agent NAME` | 自动检测 | 指定 Agent 名称 |
| `--dry-run` | false | 仅验证环境, 不运行 |
| `--extra '...` | (无) | 透传给 e2e_test.py 的额外参数 |

## 运行状态判断

### 成功标志

在测试日志末尾显示:

```
═══════════════════════════════════════════
  E2E TEST PASSED
═══════════════════════════════════════════
```

`summary.json` 中 `overall_status: "PASS"` 且所有 Phase 的 `status: "passed"`。

### Review 结果

日志中会显示 Review Gate verdict:

| Verdict | 含义 | 后续 |
|---------|------|------|
| `accept` | 无 CPU fallback, 约束合规 | 进入 Phase 6 |
| `accept_with_warning` | 通过执行但有轻微警告 | 进入 Phase 6 |
| `reject` | 有 CPU fallback 或约束违反 | 触发 improvement iteration |

### 失败诊断

如果 E2E 失败, 检查:

```bash
# 1. 总体状态
python -c "
import json
r = json.load(open('src/e2e-reports/src/<latest>/summary.json'))
print('Status:', r['overall_status'])
print('Errors:', r['errors'])
for p in r['phases']:
    if p['status'] != 'passed':
        print(f'Failed: {p[\"phase_id\"]} — {p[\"error\"]}')
"

# 2. 查看 Phase 5 错误
ls -lt output_projects/01_Hallo_<date>/.sm-artifacts/e2e-real-*/raw/phase_5_validation_attempt*.json | head -1

# 3. 查看迁移后的差异代码
diff -qr output_projects/01_Hallo_<date>/original_src/ \
  original_projects/01_Hallo/original_src/ | grep -v __pycache__ | grep .py
```

## 基准配置说明 (Deepwave 20260423_053647)

当前默认配置来自 Deepwave 唯一一次完全成功的 E2E 运行:

| 项目 | 值 | 来源 |
|------|---|------|
| hostname/port/server_type | `4098` | 该运行使用的端口 |
| max-iter | `8` (不是默认 5) | Deepwave 需要超过 5 轮修复 |
| review-gate | **启用** | 确保验证 NPU-native 执行 |
| user-constraints | `ADAPTATION_REQUIREMENTS.md` | 每个项目的定制约束文件 |
| keep-temp | **保留** | 迁移产物需要事后检查 |
| timeout | `framework_defaults.yaml` | `session_timeout_repair: 3600`, `entry_script_timeout: 1200` |

## 超时配置

如果 Phase 5 中 entry script 运行时间超过 20 分钟 (1200s), 需要修改:

```yaml
# config/framework_defaults.yaml
framework:
  entry_script_timeout: 3600    # 1 hour
  session_timeout_repair: 3600  # repair agent session timeout
```

对于大型模型 (如 Hallo3 5B 参数), 建议适当调大。

## 常见问题

### 1. "Server not reachable"

```bash
# 检查服务状态
curl http://127.0.0.1:4098/agent

# 检查端口占用
ss -tlnp | grep 4098
```

### 2. Phase 5 一直 exit 1 不进步

说明修复循环遇到了无法自动解决的问题 (如需要手动编译 AscendC kernel)。检查最后一个 attempt 的 `error_category`:

```bash
cat output_projects/<project>_<date>/.sm-artifacts/e2e-real-*/validated/phase_5_validation_canonical.json | python -m json.tool
```

### 3. 修复循环停滞 (stagnation)

如果同一错误连续出现 3 次, 循环会自动终止。这种情况下通常需要人工介入, 查看日志中 error_analyzer 的分类。

### 4. 清理旧测试产物

```bash
# 清理报告 (保留最近 5 个)
ls -td src/e2e-reports/src/*/ | tail -n +6 | xargs rm -rf

# 清理输出项目 (保留最近 3 个)
ls -td output_projects/01_Hallo_*/ | tail -n +4 | xargs rm -rf
```
