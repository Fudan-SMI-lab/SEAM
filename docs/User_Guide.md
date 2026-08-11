# SEAM 用户指南

SEAM（Self-Evolving Agentic Migration）是一个自动化迁移工具，能把原本只在 NVIDIA 显卡上运行的 AI 项目，自动迁移到中国国产 GPU 算力卡上运行并调优。它由 YAML 状态机驱动的多阶段流水线和多个持久化智能体协同完成，基于当前 GPU 真实运行反馈不断修正迁移结果。

本指南面向已经决定使用 SEAM 的开发者，覆盖环境准备、启动参数、实时仪表盘、续做、Review Gate、运行产出和日志排查等全部日常使用场景。所有命令、路径和默认值均依据当前源码核对，与早期版本的过时文档不同。

---

## 目录

1. [环境要求与安装](#1-环境要求与安装)
2. [常规启动与常用参数](#2-常规启动与常用参数)
3. [实时仪表盘（TUI）](#3-实时仪表盘tui)
4. [续做（Continuation）](#4-续做continuation)
5. [Review Gate 参数](#5-review-gate-参数)
6. [运行后产出](#6-运行后产出)
7. [如何查看日志和结果](#7-如何查看日志和结果)
8. [常见问题（FAQ）](#8-常见问题faq)

---

## 1. 环境要求与安装

### 1.1 操作系统与 Python

SEAM 的生产运行目标为 **Linux**，要求 **Python 3.10 及以上**。强制 CI 在无硬件 Linux runner 上验证支持下限，真实 NPU/GPU 集成验证保持可选且不作为发布门禁。

包名为 `sm-adapt`（见 `pyproject.toml`），当前未配置 console_scripts，因此无法直接执行 `sm-adapt` 命令。所有运行都通过 shell 启动脚本或 `python -m` 调用完成。

### 1.2 安装命令

在仓库根目录执行以下命令：

```bash
# 克隆仓库（注意正确的 URL）
git clone https://github.com/Fudan-SMI-lab/SEAM.git
cd SEAM

# 基础安装 + 测试工具链
python -m pip install -e "./src[dev]"

# 可选：实时仪表盘渲染器（Rich + Textual）
python -m pip install -e "./src[dashboard]"

# 可选：SQLite 二进制回退
python -m pip install -e "./src[sqlite]"
```

各 extras 的作用：

| extras | 提供内容 | 不安装的后果 |
|---|---|---|
| `[dev]` | pytest、PyYAML、pydantic、tomli 测试工具链 | 无法运行测试套件 |
| `[dashboard]` | `rich>=13.0`、`textual>=0.50` 渲染器 | 实时仪表盘不可用 |
| `[sqlite]` | `pysqlite3-binary` 作为 stdlib sqlite3 的二进制回退 | 仅禁用 session manager 的 SQLite completion evidence，不影响常规运行 |

CI 永远只安装 base + `[dev]`，不把 `[sqlite]` 设为必需。

### 1.3 启动 OpenCode Server

SEAM 依赖一个运行中的 OpenCode Server 后端。启动方式：

```bash
opencode serve --port 4098 --hostname 127.0.0.1 &
```

默认服务地址为 `http://127.0.0.1:4098`。如果端口不同，需要在启动 SEAM 时用 `--server_url` 显式指定。

### 1.4 代理变量注意

如果环境设置了 `HTTP_PROXY` 或 `HTTPS_PROXY`，必须把本机地址加入 `NO_PROXY` / `no_proxy`，否则 SEAM 调用本地 OpenCode 时会走代理而失败：

```bash
export NO_PROXY="127.0.0.1,localhost,::1"
export no_proxy="127.0.0.1,localhost,::1"
```

### 1.5 交互式初始化器（init_seam.sh）

初始化器是上述 1.2 到 1.4 手动步骤的引导式替代：一次完成 Python 环境选择、SEAM 依赖安装、OpenCode 与 OMO 的安装配置，以及最终验证。首次使用推荐先运行：

```bash
bash src/scripts/init_seam.sh
```

Python 环境三选一：

- **base 解释器**：直接使用当前 Python 3.10+，装进当前环境；
- **已有 venv**：复用你指定的现有虚拟环境；
- **新建 venv**：为 SEAM 创建全新的 `.venv`，隔离性最好。

终态与退出码（与 `seam_init.models` 的生产常量一致）：

| 状态 | 退出码 | 含义 |
|---|---|---|
| READY | 0 | 设置完成，可以运行迁移 |
| PENDING_AUTH | 60 | 结构检查已通过，认证/付费验证被推迟 |
| FAILED | 61-69 | 某一阶段失败，按终端指引修复后重跑 |

PENDING_AUTH **不可直接运行**迁移：需要提供 API key、同意一次付费验证调用，然后重跑 `bash src/scripts/init_seam.sh`，直到 READY（退出码 0）。

READY 时终端会打印迁移命令：

```bash
bash src/scripts/run_seam.sh /path/to/project --server_url http://127.0.0.1:4098
```

将 `/path/to/project` 替换为你的 CUDA 项目目录。可选 flag：`--dashboard`（强制开启实时仪表盘）、`--review`（启用 Review Gate）、`--seal-manifest`（直接运行后封存根 run-manifest，获得续做资格）。

安全提示：API key 可以跳过，之后重跑时再补录；如果提供，key 可能以明文存储在本机配置中。

---

## 2. 常规启动与常用参数

### 2.1 三层入口

SEAM 提供三层入口，从推荐到底层依次为：

1. **推荐入口**：`bash src/scripts/run_seam.sh <项目路径> [选项]`
   自动转译参数后调用 `run_e2e_v3.sh`，日常使用首选。
2. **底层 shell 入口**：`bash src/scripts/run_e2e_v3.sh <项目路径> [选项]`
   跳过参数转译，直接传递给 Python。
3. **Python 直接调用（高级）**：`python -m tests.e2e.e2e_test_v3 --project-dir <路径> [选项]`
   用于自动化脚本和集成测试。

### 2.2 最小启动命令

```bash
bash src/scripts/run_seam.sh /path/to/cuda_project \
  --server_url http://127.0.0.1:4098
```

不传 `--workflow` 时，启动器自动选择 `src/workflows/seam_auto_default.yaml`，通常无需手动指定。

### 2.3 常用命令示例

```bash
# 启用 review + verbose 详细日志
bash src/scripts/run_seam.sh /path/to/cuda_project \
  --server_url http://127.0.0.1:4098 --review --verbose

# Dry-run 模式：只打印转译后的命令，不实际执行
bash src/scripts/run_seam.sh /path/to/cuda_project --dry-run

# 指定自定义 workflow
bash src/scripts/run_seam.sh /path/to/cuda_project \
  --workflow src/workflows/seam_auto_default.yaml
```

### 2.4 常用参数表

下表参数以 `run_seam.sh` 为准：

| 参数 | 取值 | 默认值 | 说明 |
|---|---|---|---|
| `<项目路径>` | 绝对路径 | 必填（与 `--continue-from` 二选一） | 待迁移的项目目录 |
| `--server_url` | URL | `http://127.0.0.1:4098` | OpenCode 服务地址 |
| `--workflow` | 文件路径 | `src/workflows/seam_auto_default.yaml` | 迁移工作流定义 |
| `--max-iter N` | 正整数 | `8` | Phase 5 修复迭代上限 |
| `--review` / `--no-review` | flag | 关闭 | 启用或关闭 Review Gate |
| `--max-review-iter N` | 正整数 | `3` | Review Gate 最大轮次 |
| `--review-fail-closed` / `--no-review-fail-closed` | flag（互斥） | 严格（fail-closed） | Review 耗尽时的结果策略 |
| `--container-retention {retain\|delete}` | `retain` / `delete` | `retain` | 容器保留策略 |
| `--save-agent-trace` / `--no-save-agent-trace` | flag（互斥） | 关闭 | 是否导出原始 Agent trace |
| `--dashboard` / `--no-dashboard` / `--dashboard-mode {auto\|on\|off}` | `auto` / `on` / `off` | `auto` | 仪表盘模式 |
| `--dashboard-backend {auto\|textual\|rich}` | `auto` / `textual` / `rich` | `auto` | 仪表盘渲染后端 |
| `--seal-manifest` | flag | 关闭 | 封存运行证据，续做的前置条件 |
| `--continue-from <summary.json>` | 绝对路径 | 无 | 续做指定的父运行 |
| `--output-dir` | 目录路径 | `../output_projects` | 输出项目根目录 |
| `--dry-run` | flag | 关闭 | 打印命令但不执行 |
| `--verbose` | flag | 关闭 | 启用 DEBUG 详细日志 |
| `--agent NAME` | 字符串 | 自动检测 | 指定使用的 Agent |
| `--opencode-readiness {off\|basic\|message}` | `off` / `basic` / `message` | `message` | OpenCode 就绪检查级别 |
| `--keep-temp` / `--no-keep-temp` | flag | 保留 | 是否保留临时目录 |

### 2.5 重要陷阱提醒

> ⚠️ **陷阱 1：端口不一致**
> Shell 启动器默认端口是 **4098**，但如果直接调用 Python（`python -m tests.e2e.e2e_test_v3`）且不传 `--server-url`，Python 内部会回退到 **4096**。建议无论哪种入口都显式传 `--server-url http://127.0.0.1:4098`。

> ⚠️ **陷阱 2：迭代上限不一致**
> Shell 的 `--max-iter` 默认为 **8**，但 Python 的 `--max-phase5-iter` parser 默认为 **5**。经 shell 调用时实际生效值为 8。直接调 Python 时若不传参，将使用 5。

> ⚠️ **陷阱 3：`sm_adapt_cli.py` 不是仪表盘入口**
> 它是 v2 占位 CLI，仅打印 "Orchestrator integration pending"，不支持任何仪表盘或 review 相关的 flag。请勿用它启动迁移或仪表盘。

---

## 3. 实时仪表盘（TUI）

SEAM 提供两种 TUI 后端：**Textual** 和 **Rich**。两者都需要先安装可选 extra：

```bash
python -m pip install -e "./src[dashboard]"
```

### 3.1 启动 flag

| Flag | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--dashboard` | 无值 | `False` | 强制开启，等价 `--dashboard-mode on` |
| `--no-dashboard` | 无值 | `False` | 强制关闭，等价 `--dashboard-mode off` |
| `--dashboard-mode` | `auto` / `on` / `off` | `auto` | 仪表盘模式选择 |
| `--dashboard-backend` | `auto` / `textual` / `rich` | `auto` | 渲染后端选择 |

### 3.2 三种模式行为

**`auto` 模式（默认）**
仅在**交互式 TTY 且非 CI 环境**时自动启用仪表盘。检测条件为 `sys.stdout.isatty() == True` 且环境变量 `CI` 未设置或非真值。CI、后台任务、管道重定向场景下都会自动关闭，运行行为与无仪表盘版本完全一致。

**`on` 模式**
强制启用仪表盘。若未安装 `[dashboard]` extra，会在产生任何副作用之前抛出 `DashboardBackendUnavailableError`，并提示运行安装命令。

**`off` 模式**
完全关闭仪表盘。不创建 `ui_events.jsonl`，不设置环境变量，不启动渲染线程。

### 3.3 后端选择

`--dashboard-backend auto` 时，通过 `importlib.util.find_spec` 探测已安装的渲染器：

1. 优先使用 **Textual**。
2. 缺失则回退到 **Rich**。
3. 两者都没有则视为 NONE，触发上述报错。

### 3.4 两种后端对比

| 特性 | Rich 后端 | Textual 后端 |
|---|---|---|
| 布局 | 9 面板 Live 渲染，alternate screen | Header + 7 面板 + Footer，CSS 布局，可滚动 |
| 刷新率 | 4 Hz | 0.25 秒轮询 |
| 键盘交互 | `q` 退出（需 POSIX tty） | `q` 退出、`l` / `s` 聚焦活动面板、`?` 帮助 |
| 终端要求 | POSIX tty 才有键盘响应 | 标准 Textual 终端即可 |

### 3.5 使用示例

```bash
# 默认 auto 模式：本机终端自动启用
bash src/scripts/run_seam.sh /path/to/project --server_url http://127.0.0.1:4098

# 强制使用 Textual 后端
bash src/scripts/run_seam.sh /path/to/project \
  --dashboard --dashboard-backend textual --server_url http://127.0.0.1:4098

# 强制使用 Rich 后端
bash src/scripts/run_seam.sh /path/to/project \
  --dashboard-mode on --dashboard-backend rich --server_url http://127.0.0.1:4098

# CI 或后台任务：彻底关闭
bash src/scripts/run_seam.sh /path/to/project --no-dashboard --server_url http://127.0.0.1:4098
```

### 3.6 退出仪表盘

仪表盘激活时按 `q` 键**仅关闭仪表盘视图**，迁移进程和日志继续运行，不会被中断。

---

## 4. 续做（Continuation）

续做允许在一个已经到达终态（PASS 或 FAIL）的父运行基础上继续工作，避免从零重跑整个流水线。

### 4.1 续做命令

```bash
bash src/scripts/run_seam.sh \
  --continue-from /abs/path/to/e2e-reports/src/e2e-v3-xxxxxxxxxxxx/summary.json \
  --server_url http://127.0.0.1:4098
```

`--continue-from` 必须是绝对路径，不接受 latest、时间戳或目录扫描等模糊匹配。

### 4.2 父运行资格要求

一次运行能被续做，必须同时满足：

1. 父运行的 `overall_status` 必须是 `PASS` 或 `FAIL`，不能是 `UNKNOWN`。
2. 父运行必须是直接运行时通过 `--seal-manifest` 成功封存的，即在报告目录下产生 `run-manifest.v1.json` 和 `sealed-artifacts/`。
3. `summary.json` 路径必须是绝对路径、文件名必须是 `summary.json`、文件大小不超过 2 MiB。
4. 工作流摘要必须与父运行一致。续做模式下禁止传 `--workflow`。

### 4.3 续做执行流程

1. 排他锁定父运行项目（`e2e-reports/locks/<sha256>.lock`）。
2. 解析父运行终态锚点，决定子运行的起始阶段。
3. 验证保留的环境（容器或 venv）与父运行完全一致。环境绑定 authority 要求精确的 `environment_id` 加上匹配的 `namespace`；namespace 单独不是 authority，不接受 list-order、fact-count 或 silent fallback，缺失或歧义时 fail closed，不会重建。
4. 创建子运行，分配新的 run ID、新的报告目录、新的 OpenCode 会话。
5. 子运行共享父运行的输出项目，但拥有独立的证据命名空间。

### 4.4 锚点矩阵

父运行状态决定子运行的起始阶段：

| 父运行状态 | 子运行起始阶段 |
|---|---|
| PASS | Phase 5（重新审查） |
| FAIL（Phase 5 之前失败） | 失败的阶段 |
| FAIL（Phase 5 处失败） | Phase 5 |
| FAIL（Phase 5 之后失败） | 后续失败阶段，继承 Phase 5 的接受结果 |

### 4.5 什么不能续做

- **进行中的 Agent 会话**：续做总是创建全新的 OpenCode 会话，不复制或恢复 session。
- **`.sm-artifacts/state.json` 或检查点**：这些只是观测用途，不是续做 authority。
- **父运行的 workflow selector**：续做会跳过 selector 阶段。
- **非终态（UNKNOWN）或未封存的父运行**：直接拒绝。

### 4.6 `--seal-manifest` 与 `--continue-from` 的关系

这两个参数**互斥**，不能同时使用：

- 直接运行时用 `--seal-manifest` 封存运行证据，使该运行获得续做资格。
- 续做时自动消费父运行已封存的证据，不需要也不能再传 `--seal-manifest`。

封存是 outcome-neutral 的旁路：封存失败不改变迁移的 PASS/FAIL、`RunOutcome` 或退出码，只通过 `manifest-sealing.v1.json` sidecar 和 `summary.json` 的 `manifest_sealing` 投影单独发布结果。

---

## 5. Review Gate 参数

Review Gate 是 Phase 5 修复循环内的逻辑审查状态机。它**仅在入口脚本退出码为 0 且 review 已启用时**才会运行。

### 5.1 Review Gate 与修复循环的区别

这是最容易混淆的概念，务必分清：

- **修复循环**（`--max-iter`，默认 8）：重试入口脚本，配合错误分析和修复 Agent。脚本失败时消耗。
- **Review Gate**（`--max-review-iter`，默认 3）：脚本通过后，由审查 Agent 发出 `accept` 或 `reject` 裁决，最多 N 轮。

**关键点**：被 reject 的一轮 review **不消耗修复迭代**。两者是完全独立的预算维度。

### 5.2 参数表

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--review` | flag | 关闭 | 启用 Review Gate |
| `--no-review` | flag | — | 关闭 Review Gate（V2 兼容） |
| `--max-review-iter N` | 正整数 | `3` | Review 最大轮次 |
| `--review-fail-closed` | flag | `True`（严格） | reject 耗尽时判为 FAIL |
| `--no-review-fail-closed` | flag | — | reject 耗尽时判为 `passed_with_reviews`（兼容模式） |

### 5.3 严格模式与兼容模式

| Review 结果 | 严格模式（默认） | 兼容模式 |
|---|---|---|
| `accept` | PASS | PASS |
| `reject_exhausted`（reject 耗尽） | **FAIL** | **passed_with_reviews** |
| `unknown` / `session_error` / `improvement_error` | FAIL | FAIL |

兼容模式只放宽 `reject_exhausted` 一种情况，绝不放宽 `unknown`、session error、improvement error 或验证失败。

### 5.4 配置优先级

最终 review policy 按以下优先级解析（高到低）：

1. CLI 显式参数
2. 物化的 workflow YAML globals
3. 框架配置（`framework_defaults.yaml`）
4. 硬编码默认值（max=3，fail_closed=True）

### 5.5 注意事项

- 审查 Agent（`main_engineer`）和提示模板由 workflow YAML 固定，无法通过 CLI 选择。
- 裁决域固定为 `accept` 或 `reject`，不接受其他值。
- `reject` 是有剩余预算时的中间状态，不是可发布的终态 PASS。

### 5.6 使用示例

```bash
# 启用 review，默认 3 轮严格模式
bash src/scripts/run_seam.sh /path/to/project \
  --review --server_url http://127.0.0.1:4098

# 5 轮 review + 兼容模式
bash src/scripts/run_seam.sh /path/to/project \
  --review --max-review-iter 5 --no-review-fail-closed \
  --server_url http://127.0.0.1:4098

# review 配合更大的修复预算（两个独立维度）
bash src/scripts/run_seam.sh /path/to/project \
  --review --max-iter 12 --max-review-iter 4 \
  --server_url http://127.0.0.1:4098
```

---

## 6. 运行后产出

一次运行会产生**两棵独立的产出树**：报告树和迁移项目树。

### 6.1 报告树（SEAM 运行报告、日志、遥测）

位置：`<仓库根>/e2e-reports/src/e2e-v3-<12位hex>/`

> ⚠️ **重要修正**：早期 User_Guide 写的 `./e2e-reports/migration_utils/` 已过时，正确路径为 `./e2e-reports/src/`。

报告目录结构：

```text
e2e-reports/src/e2e-v3-<run-id>/
├── summary.json                 # 权威运行总结：overall_status、phases、runtime、continuation
├── phase_results.json           # 各阶段状态、时长、错误
├── diagnostics.json             # 收尾诊断
├── before_snapshot.json         # 迁移前文件快照
├── after_snapshot.json          # 迁移后文件快照
├── traceback.txt                # 仅失败时存在，已脱敏堆栈
├── manifest-sealing.v1.json     # 总会写出：封存状态 sidecar
├── run-manifest.v1.json         # 仅 --seal-manifest 成功时存在
├── sealed-artifacts/            # 仅 --seal-manifest 成功时存在
├── .sm-artifacts/               # 从项目拷贝的运行时产物副本
├── ui_events.jsonl              # 仅仪表盘激活时存在
├── trace/                       # 仅 --save-agent-trace 时存在
└── agent_io/                    # 仅 SM_ADAPT_FULL_AGENT_IO=1 时存在
```

### 6.2 迁移项目树（迁移后的代码 + 项目验收报告）

位置：`../output_projects/<项目名>_<时间戳>/`

也可以通过环境变量 `MIGRATION_OUTPUT_PROJECTS_ROOT` 修改默认根目录，或用 `--output-dir` 显式指定本次输出目录。

> ⚠️ **重要修正**：早期 User_Guide 写的 `./output_projects/` 已过时。默认实际位置为 SEAM 仓库**同级**目录 `../output_projects/`。

迁移项目目录结构：

```text
output_projects/<项目名>_<时间戳>/
├── <迁移后的源码树>             # 拷贝 + 大文件软链
├── .venv/                       # Phase 2 创建的虚拟环境（若存在）
├── .sm-artifacts/<run_id>/      # 活动运行时产物存储
│   ├── raw/                     #   原始阶段输出
│   ├── validated/               #   校验后输出
│   ├── execution_journal.jsonl  #   执行日志
│   └── state.json               #   检查点（仅观测用，不是续做 authority）
└── migration_reports/           # 项目验收报告
    ├── USAGE.md                 #   如何运行迁移后项目
    ├── SUMMARY_REPORT.md        #   验收总结
    ├── operator_inventory.json  #   算子清单
    ├── migration_manifest.json  #   闭包清单
    └── custom_op_final_gate.json#   自定义算子关卡
```

> ⚠️ **重要修正**：早期 User_Guide 写的 `.migration_reports/` 已过时。正确目录名为 `migration_reports/`，**没有前导点**。

### 6.3 退出码

| 退出码 | 含义 |
|---|---|
| `0` | PASS，迁移通过 |
| `1` | FAIL，迁移失败 |
| `2` | PASS，但授权容器清理失败 |

注意：仅当迁移已经 PASS、用户明确请求授权容器清理且清理失败时才会返回 2。Required finalization 或 required summary publication 失败仍为 1。

### 6.4 终端标题

运行结束时终端会显示：

- `E2E TEST PASSED`：迁移通过
- `E2E TEST FAILED`：迁移失败
- `E2E FINALIZATION FAILED`：finalization 失败

---

## 7. 如何查看日志和结果

### 7.1 常用查看命令

```bash
# 找到最新运行的报告目录
RUN=$(ls -1dt e2e-reports/src/e2e-v3-* | head -1)

# 一眼看是否跑通 + 详细阶段信息
jq '.overall_status' "$RUN/summary.json"
jq '.phases[] | {phase_id, status, duration_seconds, error}' "$RUN/summary.json"

# 看终态、续做资格、封存状态
jq '.continuation, .runtime' "$RUN/summary.json"
cat "$RUN/manifest-sealing.v1.json"

# 失败时优先查看
cat "$RUN/traceback.txt"
jq '.[] | select(.error_type)' "$RUN/diagnostics.json"

# 查看迁移后项目
OUT=$(ls -1dt ../output_projects/*_* | head -1)
cat "$OUT/migration_reports/USAGE.md"
cat "$OUT/migration_reports/SUMMARY_REPORT.md"
jq '.full_migration_status' "$OUT/migration_reports/custom_op_final_gate.json"

# 查看原始 Agent trace（仅 --save-agent-trace 时存在）
ls -R "$RUN/trace"

# 查看仪表盘事件流（仅仪表盘激活时存在）
jq -c . "$RUN/ui_events.jsonl" | tail
```

### 7.2 故障排查建议顺序

遇到运行失败时，按以下顺序逐步定位：

1. **看终端标题**：确认显示 `E2E TEST FAILED`。
2. **报告树读基础信息**：`traceback.txt` → `diagnostics.json` → `summary.json` 的 `phases[].error`。
3. **确定失败阶段**：查看 `phase_results.json`，定位是哪个 Phase 出错。
4. **Phase 5 修复问题**：查 `.sm-artifacts/<run_id>/` 下的 attempt receipts，看每次重试的 stdout/stderr/meta。
5. **自定义算子问题**：查 `migration_reports/custom_op_final_gate.json`，要求状态为 `FULL_PASS`。
6. **反馈给团队**：打包**报告目录 + 迁移项目的 `.sm-artifacts/`** 一起提交，便于排查。
7. **需要更细的 Agent 行为**：重新运行并加上 `--save-agent-trace`，查看 `trace/` 下的完整 session 数据。

---

## 8. 常见问题（FAQ）

| 问题 | 原因与解决 |
|---|---|
| **连不上 OpenCode Server** | 检查端口是否为 4098（默认）。如果设置了 `HTTP_PROXY` / `HTTPS_PROXY`，必须把 `127.0.0.1,localhost,::1` 加入 `NO_PROXY` / `no_proxy`。 |
| **仪表盘没有显示** | `auto` 模式在 CI 环境或非交互式 TTY 下会自动关闭。需要强制显示时用 `--dashboard-mode on`，并确保已安装 `[dashboard]` extra。 |
| **续做报错 AUTHORITY_INVALID** | 父运行需要先用 `--seal-manifest` 成功封存，才能被续做。检查报告目录下是否存在 `run-manifest.v1.json` 和 `sealed-artifacts/`。 |
| **Review 和修复有什么区别** | 修复是重试入口脚本（`--max-iter`，默认 8），脚本失败时消耗。Review 是脚本通过后的逻辑审查（`--max-review-iter`，默认 3），被 reject 时不消耗修复迭代。两者是独立预算。 |
| **退出码 2 是不是迁移失败** | 不是。退出码 2 表示迁移已经 PASS，但请求的授权容器清理失败。迁移结果本身仍是 PASS。 |
| **直接调 Python 时端口对不上** | Python 入口 `--server-url` 默认回退到 4096，而 shell 启动器默认 4098。建议始终显式传 `--server-url http://127.0.0.1:4098`。 |
| **`.sm-artifacts/state.json` 能用来续做吗** | 不能。它只是观测用途的检查点，不是续做 authority。续做只认父运行的 `summary.json` 加封存证据。 |

---

## 进一步阅读

- [E2E 测试契约](../src/docs/E2E_TESTING.md) — 完整 CLI、continuation 矩阵、产物树、超时语义
- [原始 trace 设计](../src/docs/full_agent_io_logging_design.md) — schema-v2 关联边界与完整性规则
- [使用案例](Use_Cases.md) — 实际迁移场景示例
- [FAQ](FAQ.md) — 更多常见问题
