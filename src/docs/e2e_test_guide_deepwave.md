# migration_utils × DeepWave PPU/V3 端到端迁移测试指南

## 概述

本文档说明如何使用 **migration_utils V3 管线**（非手动操作）对 DeepWave 项目执行完整的自动迁移适配与端到端验证。当前管线面向 **PPU（CUDA-compatible）平台**，运行在容器内（Docker/Podman），不再依赖 NPU/CANN/torch_npu。

**V3 管线支持两条路由**：

| 路由 | 用途 | 对应 Workflow YAML |
|------|------|--------------------|
| 普通入口（normal-entry） | 无需 custom-op 合约的普通迁移验证 | `ppu_migration_normal_entry_057_experiment.yaml` |
| Custom-Op 最终门（custom-op/final-gate） | 含 CUDA 自定义算子的完整证据链 | `ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml` |

两条路由的区别见下文。

---

## 前置条件

### 1. OpenCode 服务

确保 OpenCode 服务运行在 **端口 4098**：

```bash
# 验证
curl -fsS http://127.0.0.1:4098/agent
```

> 推荐的 SEAM 端口是 4098，不要使用 4096。

### 2. 容器运行时

PPU 管线在容器内执行（Docker 或 Podman）。确保容器镜像已拉取到本地：

```bash
# 检查镜像是否存在
docker images | grep inference-xpu-pytorch

# 如果镜像未拉取，请提前拉取（镜像地址见 workflow YAML 中的 images 字段）
```

DeepWave 项目根目录应包含可编译的 `setup.py` 或 `pyproject.toml`（CUDA extension），以及可选的非交互式验证入口脚本（如 `test_data_and_scripts/test_e2e_fwi.py`）。

### 3. Python 环境

```bash
cd /path/to/SEAM
python3.10 -m pytest tests/ -x -q
```

---

## 路由 A：普通入口路由（normal-entry）

### 适用场景

- 你只需要验证 DeepWave 在 PPU 上能否正确构建并跑通普通推理/验证脚本。
- 你不需要 custom-op 的完整证据链（source inventory、manifest、parity、runtime coverage、性能）。
- 你希望跳过 `custom_op_final_gate` 的 machine validation。

### Workflow 关键行为

- `disable_custom_op_contract_injection: true`：框架不会自动注入 `entry_script_kind: custom_op_full_validation`。
- Phase 3 不输出 custom-op 合约字段（不做 reports_dir、required_report_paths、required_checks）。
- Phase 3.5 只做 headless/static 检查，不走 custom-op 合约门。
- Phase 5 的 `custom_op_final_gate` 返回 `{skipped: true, passed: true}`（自动跳过）。
- 仓库中的 native/custom 库仍会正常编译与加载（`deepwave/` 下的 `.so` 文件必须非空），但验证不要求算子级证据。

> 这条路由**不会**错误地输出"该工程零 custom operator"。它只是不走 custom-op 证据链。

### 启动命令

```bash
cd /path/to/SEAM

python3.10 -m tests.e2e.e2e_test_v3 \
  --workflow-path migration_utils/workflows/ppu_migration_normal_entry_057_experiment.yaml \
  --project-dir /path/to/deepwave_project \
  --output-dir ./output_projects \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 5 \
  --keep-temp-dir \
  --verbose
```

**参数说明**：

| 参数 | 值 | 说明 |
|------|---|------|
| `--workflow-path` | `migration_utils/workflows/ppu_migration_normal_entry_057_experiment.yaml` | 普通入口 YAML |
| `--project-dir` | `/path/to/deepwave_project` | DeepWave 源码根目录 |
| `--output-dir` | `./output_projects` | 迁移产物输出目录 |
| `--server-url` | `http://127.0.0.1:4098` | OpenCode 服务地址 |
| `--max-phase5-iter` | `5` | Phase 5 修复循环最大迭代数 |
| `--keep-temp-dir` | flag | 保留迁移后项目副本 |
| `--verbose` | flag | 打印调试日志 |

---

## 路由 B：Custom-Op / Final-Gate 路由

### 适用场景

- DeepWave 包含 CUDA 自定义算子，你需要**完整的 custom-op 证据链**以供审计或投产评估。
- 你需要这些证据：source inventory、manifest、parity、runtime coverage、no-fallback、performance。

### Workflow 关键行为

- Phase 3 输出 `entry_script_kind: custom_op_full_validation` 及完整的 custom-op 合约字段（`reports_dir`、`required_report_paths`、`required_checks`）。
- Phase 3.5 验证 custom-op 合约覆盖与证据链完整性。
- Phase 5 运行 `custom_op_final_gate` machine validation，输出 `custom_op_final_gate.json`。
- 性能验证设为 `presence_only`：要求 timing 证据存在（`baseline_seconds > 0`、`custom_seconds > 0`），但不强制加速比优势。适合异构 bring-up 阶段。
- CPU baseline 仅作为**性能对比参考**，不是回退目标。所有 no-fallback 证据门仍然强制执行。
- 最终门检查清单：inventory 计数、manifest 闭合、parity 通过、runtime coverage 非零、no-fallback 标志全部为 `false`、native build artifact 存在。

### 启动命令

```bash
cd /path/to/SEAM

python3.10 -m tests.e2e.e2e_test_v3 \
  --workflow-path migration_utils/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml \
  --project-dir /path/to/deepwave_project \
  --output-dir ./output_projects \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 8 \
  --keep-temp-dir \
  --verbose
```

**参数说明**：

| 参数 | 值 | 说明 |
|------|---|------|
| `--workflow-path` | `migration_utils/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml` | Custom-Op YAML |
| `--project-dir` | `/path/to/deepwave_project` | DeepWave 源码根目录 |
| `--output-dir` | `./output_projects` | 迁移产物输出目录 |
| `--server-url` | `http://127.0.0.1:4098` | OpenCode 服务地址 |
| `--max-phase5-iter` | `8` | Phase 5 修复循环最大迭代数（custom-op 路线建议至少 8） |
| `--keep-temp-dir` | flag | 保留迁移后项目副本 |
| `--verbose` | flag | 打印调试日志 |

### 自定义用户约束（可选）

如果你有 DeepWave 专属的约束声明（如 `ADAPTATION_REQUIREMENTS.md`），可以通过 `--user-constraints` 注入：

```bash
python3.10 -m tests.e2e.e2e_test_v3 \
  --workflow-path migration_utils/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml \
  --project-dir /path/to/deepwave_project \
  --user-constraints /path/to/ADAPTATION_REQUIREMENTS.md \
  --output-dir ./output_projects \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 8 \
  --keep-temp-dir \
  --verbose
```

约束中的内容会在 Phase 1.5 被摘要为可执行规则并注入后续 Phase。

---

## 产物位置与分析方法

每条 E2E 运行会在多处产生产物：

| 位置 | 内容 |
|------|------|
| `output_projects/deepwave_project_<timestamp>/` | 迁移后的项目副本（含所有修改） |
| `output_projects/deepwave_project_<timestamp>/.sm-artifacts/e2e-v3-<run_id>/validated/` | Phase 验证的 canonical JSON（含 `phase_5_validation_canonical.json`） |
| `output_projects/deepwave_project_<timestamp>/migration_reports/` | 入口脚本产生的核心报告 |
| `output_projects/deepwave_project_<timestamp>/migration_reports/custom_op_final_gate.json` | 自定义算子最终门结果（仅 custom-op 路由） |
| `output_projects/deepwave_project_<timestamp>/migration_reports/performance.json` | 逐算子 timing 证据 |
| `output_projects/deepwave_project_<timestamp>/migration_reports/baseline.json` | baseline 设备测量记录 |
| `output_projects/deepwave_project_<timestamp>/migration_reports/runtime_coverage.json` | 同次运行覆盖计数 |
| `output_projects/deepwave_project_<timestamp>/migration_reports/build.log` | 构建日志（须含 PPU 原生构建 token，如 `ppuccl`） |
| `e2e-reports/migration_utils/<timestamp>/summary.json` | 顶层运行总结 |
| `e2e-reports/migration_utils/<timestamp>/phase_results.json` | 逐 Phase 详细结果 |
| `e2e-reports/migration_utils/<timestamp>/before_snapshot.json` / `after_snapshot.json` | 迁移前后 Python 文件快照 |

### 查看整体运行状态

```bash
python3.10 -c "
import json, sys
s = json.load(open('e2e-reports/migration_utils/<timestamp>/summary.json'))
print('Status:', s['overall_status'])
for p in s['phases']:
    print(f'  {p[\"phase_id\"]}: {p[\"status\"]} ({p[\"duration_seconds\"]}s)')
if s.get('errors'):
    print('Errors:', json.dumps(s['errors'], indent=2))
"
```

### 查看 Phase 5 Canonical 验证结果

```bash
python3.10 -c "
import json
from pathlib import Path
# 找到最新 output_projects 下的 validated 目录
base = Path('output_projects')
proj = sorted(base.glob('deepwave_project_*'))[-1]
artifact_dir = sorted(proj.glob('.sm-artifacts/e2e-v3-*/validated'))[-1]
v = json.load(open(artifact_dir / 'phase_5_validation_canonical.json'))
print('Phase 5 status:', v.get('status'))
print('Errors:', v.get('errors'))
print('Custom-op final gate:', v.get('custom_op_final_gate'))
"
```

### 查看 Custom-Op 最终门（仅路由 B）

```bash
python3.10 -c "
import json
from pathlib import Path
base = Path('output_projects')
proj = sorted(base.glob('deepwave_project_*'))[-1]
g = json.load(open(proj / 'migration_reports/custom_op_final_gate.json'))
print('Overall:', g.get('full_migration_status'))
print('Inventory:', g.get('inventory_count'))
print('Closed/Manifest:', g.get('closed_pass_entries'), '/', g.get('manifest_entries'))
print('Remaining:', g.get('remaining_entries'))
print('Parity:', g.get('report_parity_passed'))
for r in g.get('rows', []):
    nf = r.get('no_fallback_no_zero_call_no_builtin_contamination', {})
    print(f'  {r.get(\"name\",\"?\")}: no_fallback={nf.get(\"fallback_detected\")} zero_call={nf.get(\"zero_call_detected\")}')
"
```

### 查看性能与 baseline

```bash
python3.10 -c "
import json
from pathlib import Path
base = Path('output_projects')
proj = sorted(base.glob('deepwave_project_*'))[-1]
p = json.load(open(proj / 'migration_reports/performance.json'))
b = json.load(open(proj / 'migration_reports/baseline.json'))
c = json.load(open(proj / 'migration_reports/runtime_coverage.json'))
print('Performance entries:', len(p) if isinstance(p, list) else 'N/A')
print('Baseline:', json.dumps(b, indent=2)[:500])
print('Runtime coverage ops:', c.get('covered_ops', 'N/A'))
"
```

### 检查构建日志

```bash
python3.10 -c "
from pathlib import Path
base = Path('output_projects')
proj = sorted(base.glob('deepwave_project_*'))[-1]
build_log = proj / 'migration_reports/build.log'
if build_log.exists():
    text = build_log.read_text()
    # PPU 构建 token
    has_ppu = 'ppuccl' in text or 'PPU_SDK' in text
    print('PPU build evidence:', 'FOUND' if has_ppu else 'MISSING')
else:
    print('build.log not found')
"
```

---

## 测试通过标准

| 条件 | 验证方式 |
|------|---------|
| 最终状态 | summary.json 中 `overall_status: "PASS"` |
| 全部 Phase | summary.json 中每个 phase 的 `status: "passed"` |
| Phase 5 修复 | 入口脚本 exit 0，无 stagnation |
| Phase 5 Canonical | `phase_5_validation_canonical.json` 的 status 为 `success` |
| Custom-Op 最终门（路由 B） | `custom_op_final_gate.json` 的 `full_migration_status` 为 `FULL_PASS`、`remaining_entries` 为 `0` |
| 构建 | `build.log` 含 PPU 原生 token（如 `ppuccl`、`PPU_SDK`） |
| CPU fallback | **禁止**出现 CPU fallback 代码；如出现则运行不合格 |

---

## 测试结束后检查

### 1. 检查迁移后代码是否存在 CPU fallback

```bash
DEST="$(ls -dt output_projects/deepwave_project_*/ | head -1)"

# 搜索 CPU fallback 模式
grep -rn "device.*=.*cpu\|\.to('cpu')\|\.cpu()\|device_str.*=.*'cpu'" \
  "$DEST" --include="*.py"
```

如果 grep 有输出，说明迁移管线让 LLM 写入了 CPU fallback，违反"零 CPU fallback"约束，测试视为**不合格**。

### 2. 查看迁移后的 native build 产物

```bash
DEST="$(ls -dt output_projects/deepwave_project_*/ | head -1)"

# deepwave 包下的 .so 文件应非空
python3.10 -c "
import json, sys
from pathlib import Path
import glob
dest = '$DEST'
so_files = glob.glob(dest + '/**/*.so', recursive=True)
if not so_files:
    print('ERROR: No .so files found')
    sys.exit(1)
all_ok = True
for so in so_files:
    size = Path(so).stat().st_size
    if size == 0:
        print(f'ERROR: {so} is empty')
        all_ok = False
if all_ok:
    print(f'OK: {len(so_files)} shared object(s) with non-zero size')
    for so in sorted(so_files):
        print(f'  {so} ({Path(so).stat().st_size} bytes)')
"
```

### 3. 与原始项目对比差异

```bash
DEST="$(ls -dt output_projects/deepwave_project_*/ | head -1)"
SRC="/path/to/deepwave_project"

diff -qr "$DEST" "$SRC" \
  | grep -v ".sm-artifacts" \
  | grep -v "__pycache__"
```

---

## Dry-Run 模式（快速验证启动配置）

V3 launcher 提供 dry-run，用于检查项目路径、workflow YAML 和工作目录：

```bash
bash migration_utils/scripts/run_e2e_v3.sh /path/to/deepwave_project \
  --workflow migration_utils/workflows/ppu_migration_normal_entry_057_experiment.yaml \
  --dry-run \
  --server-url http://127.0.0.1:4098
```

此模式适合：
- 快速检查项目是否能被 launcher 正确解析
- 确认 `--workflow-path`、`--project-dir`、`--output-dir`、`--max-phase5-iter` 等参数
- 在真实 Phase 5 修复循环前发现路径或 server 配置问题

---

## 常见问题

### E2E 测试卡住不动

```bash
# 检查 OpenCode 服务响应
curl http://127.0.0.1:4098/agent

# 确认会话是否活跃
curl http://127.0.0.1:4098/session
```

### Phase 5 修复循环始终 exit 1

检查 `e2e-reports/migration_utils/<timestamp>/phase_results.json`：

```bash
python3.10 -c "
import json
r = json.load(open('e2e-reports/migration_utils/<timestamp>/phase_results.json'))
for p in r:
    if p['phase_id'] == 'phase_5_validation':
        print(json.dumps(p, indent=2))
"
```

关注 `error` 字段，在 DeepWave 场景常见的问题包括：
- 缺少 PPU SDK 编译链（`CUDA_TOOLKIT_ROOT_DIR` / `CMAKE_CUDA_COMPILER` 缺失或错误）
- `setup.py` 中的 `nvcc` 编译参数与 PPU 工具链不兼容
- `test_e2e_fwi.py` 中使用了 PPU 不支持的 PyTorch 算子

### 迁移后产物找不到

```bash
# 已迁移的项目副本
ls -lt output_projects/ | head -5

# E2E 报告
ls -lt e2e-reports/migration_utils/ | head -5
```

### 创建 Session 失败

```bash
# 确认 server URL 正确
curl http://127.0.0.1:4098/agent

# 如果返回空，说明 OpenCode 未在 4098 端口启动
# 改用正确端口
opencode serve --hostname 127.0.0.1 --port 4098
```

---

## 附录：历史命令（已弃用）

以下命令来自 V2/NPU 时代的旧管线，**在 PPU/V3 路由中不再使用**。保留于此仅供历史参考：

<details>
<summary>V2 E2E 测试命令（已弃用）</summary>

```bash
# V2 NPU 入口 — 已弃用，请改用 V3 + PPU workflow
python -m tests.e2e.e2e_test_v2 \
  --server-url http://127.0.0.1:4098 \
  --project-dir original_projects/04_Deepwave \
  --output-dir output_projects \
  --max-phase5-iter 5 \
  --keep-temp-dir
```

```bash
# V2 Shell launcher — 已弃用
bash migration_utils/scripts/run_e2e_v2.sh 04_Deepwave \
  --dry-run \
  --server-url http://127.0.0.1:4098
```

```bash
# V2 Dry-run — 已弃用
bash migration_utils/scripts/run_e2e_v2.sh 04_Deepwave --dry-run --server-url http://127.0.0.1:4098
```

> **注意**：以上不再是现行迁移方式。当前 V3 路由使用容器内 PPU 平台，不依赖 NPU/CANN/torch_npu 环境。

</details>
