# src: CUDA 多平台自动化迁移框架

`src` 是 SEAM 的核心运行时：它用 YAML state machine、OpenCode persistent agents、deterministic rule migration、Phase 5 validation/repair loop 和 experience memory，把 CUDA/PyTorch 项目迁移到 PPU、Ascend NPU、MUSA、ROCm、MLU 等加速器平台。

## 核心能力

- YAML 驱动工作流：阶段、agent、validator、transition、sub-workflow 和 runtime skill 都由 `workflows/` 下的 YAML 文件描述。V3 入口支持 `--workflow-path` 选择任意平台工作流。
- 平台策略系统：`target_platform` preset（PPU、NPU、MUSA、ROCm、MLU 等）自动驱动平台专用验证 token、迁移规则和证据要求。
- 智能修复循环：Phase 5 会运行入口命令、分类错误、路由到 `dependency_fixer` / `code_adapter` / `operator_fixer`，并在有限迭代内重试。
- custom-op final gate：CUDA 自定义算子项目必须闭环 inventory、manifest、parity、runtime coverage、performance 和 no-fallback evidence。
- 经验记忆系统：Phase 7a/7b 从迁移产物中抽取可复用经验，并沉淀为 skill。
- 全链路审计：`.sm-artifacts/`、`e2e-reports/src/` 和 telemetry 记录每个阶段的输入输出和验证结果。

## 快速开始

### 1. 准备 OpenCode Server

从 SEAM 仓库根目录启动推荐端口：

```bash
opencode serve --port 4098 --hostname 127.0.0.1
curl -fsS http://127.0.0.1:4098/agent
```

### 2. 准备待迁移项目

```bash
cd /path/to/SEAM
mkdir -p cuda_projects output_projects
cp -r /path/to/your_cuda_project cuda_projects/my_project
```

项目可以是 flat source tree，也可以包含：

```text
cuda_projects/my_project/
├── ADAPTATION_REQUIREMENTS.md
├── original_src/
└── test_data_and_scripts/
    └── run_e2e.py
```

### 3. 推荐运行方式（V3 Shell Launcher）

```bash
bash src/scripts/run_e2e_v3.sh my_project \
  --server-url http://127.0.0.1:4098 \
  --max-iter 8 \
  --review \
  --verbose
```

指定平台工作流：

```bash
bash src/scripts/run_e2e_v3.sh my_project \
  --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml \
  --server-url http://127.0.0.1:4098 \
  --max-iter 8 \
  --verbose
```

### 4. Direct Python entrypoint（V3）

```bash
python3.10 -m tests.e2e.e2e_test_v3 \
  --project-dir /path/to/your/cuda/project \
  --output-dir ./output_projects \
  --workflow-path src/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 8 \
  --keep-temp-dir
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--project-dir` | 待迁移项目根目录。 |
| `--output-dir` | 迁移产物输出根目录，通常是 `./output_projects`。 |
| `--workflow-path` / `--workflow` | 平台工作流 YAML 文件路径。V3 入口的核心参数，用于选择目标平台。 |
| `--max-phase5-iter` / `--max-iter` | Phase 5 修复循环最大迭代次数。 |
| `--review-gate` / `--review` | 开启可选 review gate。 |
| `--server-url` | OpenCode server 地址；推荐使用 `http://127.0.0.1:4098`。 |

## YAML runtime skills

在 `workflows/` 下的 YAML 文件的 agent、phase 或 sub-workflow phase 上添加：

```yaml
runtime_skills:
  include:
    - cuda-custom-op-to-npu-custom-op
  inject_full: false
  missing: error
```

也支持 list 简写：

```yaml
runtime_skills:
  - cuda-custom-op-to-npu-custom-op
```

- `include`: 需要注入或引用的 skill 名称。
- `inject_full`: `false` 只注入 compact reference/path；`true` 注入完整 skill 内容。
- `missing`: `error` / `warn` / `ignore`，控制缺失 skill 的处理方式。

## Phase 总览

```text
Phase 0   环境检测
Phase 1   项目分析与经验检索
Phase 1.5 用户约束摘要
Phase 2   venv 和依赖准备
Phase 3   entry script / run command contract
Phase 3.5 静态入口验证
Phase 4   rule-based platform migration
Phase 5   validation + repair loop + custom-op final gate
Phase 6   报告生成
Phase 7a  experience evaluation
Phase 7b  experience refinement
```

Phase 4 成功不代表最终成功；最终状态以 Phase 5 行为验证和 final gate 为准。
