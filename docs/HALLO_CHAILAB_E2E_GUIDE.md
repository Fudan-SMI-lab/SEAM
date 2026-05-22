# E2E 迁移实验操作指南 — 01_Hallo & 02_ChaiLab

> 本文档用于指导后续 agent 对剩余两个项目进行端到端 NPU 迁移实验。

---

## 项目概览

| 项目 | 模型大小 | 预估时间 | 主要风险 | 建议顺序 |
|------|---------|---------|---------|---------|
| **01_Hallo** | 11 GB 权重 | 1-2 小时 | xformers 无条件 import; onnxruntime-gpu; bitsandbytes | 先跑 |
| **02_ChaiLab** | 6.6 GB 权重 | 1.5-3 小时 | TorchScript JIT 兼容性; numba LLVM | 后跑 |

两个项目均为**纯 CUDA 状态**, 未经任何 NPU 预适配, entry script 保持原始 `cuda` 设备声明。

---

## 环境检查清单 (前置条件)

### 必须确认

- [ ] OpenCode 服务器运行中: `curl -s http://127.0.0.1:4098/agent` 返回 agent 信息
- [ ] 内存充裕: `free -g` 查看 available ≥ 15 GB (扣除系统占用后)
- [ ] NPU 空闲: `npu-smi info` — HBM Usage 应 < 10 GB, 无 running process
- [ ] 无 stale 进程: `ps aux | grep -E 'opencode|compile_worker' | grep -v grep` — 应无残留
- [ ] cgroup 内存: `/sys/fs/cgroup/memory/memory.usage_in_bytes` → 应 < 40 GB

### 如有 stale opencode 进程, 清理后再启动:
```bash
ps -eo pid,rss,cmd --sort=-rss | grep opencode | head -20
# 确认无正在运行的 E2E 实验后:
pkill -f 'opencode-ai/bin/.opencode'
```

---

## 如何执行迁移

### E2E 启动脚本

```bash
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM

# Hallo 迁移
bash src/scripts/run_seam.sh 01_Hallo --server_type opencode --server_url http://127.0.0.1:4098

# ChaiLab 迁移
bash src/scripts/run_seam.sh 02_ChaiLab --server_type opencode --server_url http://127.0.0.1:4098

# 如需更详细的 Phase 5 迭代次数 (默认 8)
bash src/scripts/run_seam.sh 01_Hallo --server_type opencode --server_url http://127.0.0.1:4098 --max-iter 12
```

### 参数说明

| 参数 | 默认值 | 用途 | 何时需要调整 |
|------|--------|------|-------------|
| `--max-iter N` | 8 | Phase 5 最大修复迭代次数 | 项目复杂度超出预期时增大 (如Hallo可能需要 10-12) |
| `--no-review` | 关闭 | 禁用 Review Gate (exit 0 后直接通过) | 修复循环陷入 reject/improve 循环时使用 |
| `--no-keep-temp` | 关闭 | 不保留 output_projects 目录 | 磁盘空间不足时使用 |
| `--extra 'ARGS...'` | 无 | 传递额外参数给底层 e2e_test_v2.py | 高级场景 |

### 框架默认配置 (`framework_defaults.yaml`)

| 配置项 | 默认值 | 实际含义 |
|--------|--------|---------|
| `max_iterations` | 10 | Phase 5 默认最大迭代数（launcher 可用 `--max-iter` 覆盖） |
| `stagnation_threshold` | 3 | 连续同类错误达到阈值后停止修复循环 |
| `max_entry_script_revisions` | 2 | Phase 5 可控入口命令修订次数上限 |
| `review.enabled` | false | 默认不启用 review gate；launcher 可用 `--review` / `--no-review` 控制 |
| `review.max_review_iterations` | 3 | Review Gate 拒绝后的最大整改次数 |
| `server.port_preference` | 0 | Python harness 自动启动时选择可用端口；launcher 推荐显式传入 4098 |

---

## 实验流程拆解

每个项目运行时, 框架会依次执行 7 个 Phase:

| Phase | 名称 | 预计耗时 | 输出说明 | 可观察到的现象 |
|-------|------|---------|---------|---------------|
| **0** | 环境检测 + 项目分析 | ~2 min | 检测 NPU/CUDA, 分析项目结构 | shell 终端显示 `[Phase 0/7]` |
| **1.5** | 约束摘要 | ~30 sec | 合并 ADAPTATION_REQUIREMENTS.md 约束 | 生成 constraint_summary |
| **2** | 虚拟环境 + 入口脚本 | ~5-10 min | 创建 .venv, 安装依赖 | Hallo 可能因 xformers 报错, 框架会记录 |
| **3** | 入口脚本确认 | ~2 min | 确定运行命令 | Phase 2 完成后自动进入 |
| **4** | 规则迁移 | < 1 min | 机械替换 .cuda()→.npu() 等 | 快速完成, 输出 replacement counts |
| **5** | 验证 + 修复循环 | **30-90 min** (核心) | 反复: 运行→分类→修复→重跑 | **重点观察阶段** |
| **6** | 报告生成 | ~2 min | 生成 SUMMARY_REPORT.md 等 | Phase 5 完成后自动生成 |
| **7** | 制品归档 | < 1 sec | state.json, journal 等 | 自动完成 |

**最终结果**: 终端显示 `E2E TEST PASSED` 或 `E2E TEST FAILED`。

---

## 如何观察实验状态

### 实时: 看终端输出

run_e2e.sh 会实时打印 Phase 进度:
```
[Phase 0/7] Environment Detection + Project Analysis — STARTING
[Phase 0/7] Environment Detection + Project Analysis — PASSED (156.6s)
[Phase 5/7] Validation Repair Loop — STARTING
[Iter 1/8] Running entry script...
[Iter 1] Validation FAILED (exit 1)
[Iter 1] Analyzer classified -> category=environment, role=dependency_fixer
[Iter 1] Created new repair session ses_XXXXX (role: dependency_fixer)
```

### 实时: 看制品目录

实验创建的输出目录路径格式:
```
/inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM/output_projects/01_Hallo_YYYYMMDD_HHMMSS/
```

其中 `.sm-artifacts/e2e-real-<hash>/` 包含完整运行记录:

```bash
# 查看当前 Phase 状态
cat output_projects/01_Halo_*/.sm-artifacts/e2e-real-*/state.json

# 查看执行日志
cat output_projects/01_Halo_*/.sm-artifacts/e2e-real-*/execution_journal.jsonl

# 查看每次验证的输入输出
ls output_projects/01_Halo_*/.sm-artifacts/e2e-real-*/raw/phase_5_validation_attempt*.json
```

### 关键文件说明

| 文件 | 用途 |
|------|------|
| `raw/phase_5_validation_attempt<N>.json` | 第 N 轮验证的 stdout, stderr, 错误分类, 修复方案 |
| `state.json` | 当前修复状态快照 (iteration_count, last_error, history) |
| `execution_journal.jsonl` | 完整事件时间线 |
| `reports/SUMMARY_REPORT.md` | 最终迁移报告 (Phase 6 生成) |

### 检查 NPU 使用情况

```bash
npu-smi info
# 重点看:
# - HBM-Usage: 推理时应有占用, 证明模型真正跑在 NPU 上
# - AICore(%): 应 > 0, 证明 NPU 核心在工作
```

### 检查内存是否充足

```bash
cat /sys/fs/cgroup/memory/memory.usage_in_bytes
# 应 < 60 GB (留余量避免 cgroup OOM -> exit -9)
```

---

## 可能出现的问题及处理

### 1. Exit -9 (OOM/SIGKILL)

**症状**: entry script 被系统 kill, exit code -9 或 247, stderr 含 `Killed` 或 TBE 报错

**原因**: cgroup 内存上限 64 GB, 模型加载超限时进程被 OOM Killer 终止。

**处理**:
- 清理内存再重跑: `pkill -f 'opencode-ai'`
- 确认不是代码 bug 导致内存泄漏
- 如果确认为模型本身太大, 则此项目在当前硬件下不可行 (如 Hallo3)

**注意**: `exit -9` 被框架错误分类为 `operator` 类型, 会触发无用的 operator_fixer。应改为 `environment` 或 `validation` 类型, 让框架直接跳过。

### 2. 修复循环陷入停滞

**症状**: 同一错误反复出现 ≥ 3 次, frame 自动 break 或 repair agent 返回空修改

**处理**:
```bash
# 使用 --no-review 跳过 Review Gate 重试
bash src/scripts/run_seam.sh 01_Hallo --server_type opencode --server_url http://127.0.0.1:4098 --no-review

# 或增大 max-iter
bash src/scripts/run_seam.sh 01_Hallo --server_type opencode --server_url http://127.0.0.1:4098 --max-iter 12
```

### 3. Phase 5 单次 entry_script_timeout (20 min) 不够

**症状**: entry script 运行中在某个阶段卡住 (如模型加载中) → timed out

**处理**: 需修改 `config/framework_defaults.yaml` 中的 `entry_script_timeout`:
```yaml
framework:
  entry_script_timeout: 3600  # 60 min for large models
```

**注意**: Hallo 模型加载 + 推理可能需要 10-20 min, 默认 20 min 可能刚好卡在边缘。

### 4. Hallo 的特殊问题: xformers

**预期**: xformers 在 `motion_module.py` 中有无条件 `import xformers`, venv 安装阶段就会出错 (该包仅支持 CUDA)。

**框架预期行为**:
- Phase 2 (venv 创建) 尝试安装 requirements.txt → xformers 失败
- Phase 5 修复循环 → dependency_fixer 负责移除/替换 xformers
- 需要修复: 将 motion_module.py 中对 xformers 的使用替换为 diffusers 原生 attention

### 5. ChaiLab 的特殊问题: TorchScript JIT

**预期**: ChaiLab 使用 `torch.jit.load` 加载 6 个预导出 `.pt` 模型, 这些是 TorchScript 格式 (非普通 checkpoint)。

**可能的 NPU 兼容问题**:
- TorchScript 模型内部可能包含 CUDA kernel trace
- `load_exported()` 中 `device != torch.device("cuda:0")` 走特殊分支
- NPU 上 `torch.jit.load` + `.to(device)` 可能产生未定义行为

---

## 结果判断

### 通过 (PASSED)

终端输出 `E2E TEST PASSED`, 且:

```json
// state.json 最后一条 history 记录
{
  "exit_code": 0,
  "review_verdict": "accept" | "accept_with_warning" | "passed_with_reviews"
}
```

### 通过但有注意事项 (accept_with_warning)

exit 0 成功, 但 Review Gate 提出了关注项 (如少量 CPU fallback)。仍可算通过。

### 通过但受限 (passed_with_reviews)

Review Gate 拒绝后整改, 达到 max_review_iterations (3) 上限后触顶通过。说明迁移成功但 Reviewer 认为不够理想。

### 失败 (FAILED)

- exit code != 0, 达到 max_iterations (8) 上限仍未解决
- 出现 exit -9 (OOM), exit -247 (异常退出) 等硬件限制
- stagnation: 同一错误连续 3 次无法修复

### 失败后的分析步骤

1. 打开 `output_projects/<项目名称>/.../raw/phase_5_validation_attempt*.json`
2. 查看每一轮的 `exit_code`, `error`, `classification`
3. 确认修复 agent 返回的 `modified_files` 是否真的有修改
4. 对照 stderr 中的错误信息, 判断是代码问题还是环境问题
5. 如果所有修复尝试都是无意义的改动 (如注释、空格), 说明问题超出框架修复能力

---

## 实验顺序建议

**推荐先跑 01_Hallo 再跑 02_ChaiLab。**

理由:
- Hallo 依赖链更明确 (diffusers + ONNX 路径清晰), 框架修复模式更可预测
- ChaiLab 使用 TorchScript JIT, 如果 NPU 不兼容 TorchScript, 则整个推理链不可用, 修复难度大
- 两者都消耗大量内存,Hallo 预计 11 GB 模型加载峰值约 25-30 GB, ChaiLab 约 8-12 GB
- 如果 Hallo 失败, 可以立即诊断原因再决定 ChaiLab 策略

---

## 快速命令参考

```bash
# 启动 Hallo 迁移
cd /inspire/sj-ssd/project/daijinquan/zhangjiaquan-253108540222/SEAM
bash src/scripts/run_seam.sh 01_Hallo --server_type opencode --server_url http://127.0.0.1:4098

# 启动 ChaiLab 迁移
bash src/scripts/run_seam.sh 02_ChaiLab --server_type opencode --server_url http://127.0.0.1:4098

# 查看最新实验输出
ls -d output_projects/01_Hallo_*/ | tail -1

# 查看当前阶段和迭代
cat output_projects/01_Hallo_*/.sm-artifacts/e2e-real-*/state.json | python3 -m json.tool

# 查看最新一轮的 error
cat output_projects/01_Hallo_*/.sm-artifacts/e2e-real-*/raw/phase_5_validation_attempt*.json | tail -100
```
