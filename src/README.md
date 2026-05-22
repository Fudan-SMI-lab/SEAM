# src: CUDA 到 Ascend NPU 自动化迁移框架

`src` 是 SEAM 的核心运行时：它用 YAML state machine、OpenCode persistent agents、deterministic CUDA->NPU rule migration、Phase 5 validation/repair loop 和 experience memory，把 CUDA/PyTorch 项目迁移到 Ascend NPU。

## 核心能力

- YAML 驱动工作流：阶段、agent、validator、transition、sub-workflow 和 runtime skill 都由 `workflows/npu_migration_v2.yaml` 描述。
- 智能修复循环：Phase 5 会运行入口命令、分类错误、路由到 `dependency_fixer` / `code_adapter` / `operator_fixer`，并在有限迭代内重试。
- custom-op final gate：CUDA 自定义算子项目必须闭环 inventory、manifest、parity、runtime coverage、performance 和 no-fallback evidence。
- 经验记忆系统：Phase 7a/7b 从迁移产物中抽取可复用经验，并沉淀为 skill。
- 全链路审计：`.sm-artifacts/`、`e2e-reports/src/` 和 telemetry 记录每个阶段的输入输出和验证结果。

## 快速开始

### 1. 准备待迁移项目

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

### 2. 推荐运行方式

```bash
bash src/scripts/run_seam.sh my_project \
  --server_type opencode \
  --server_url http://127.0.0.1:5000 \
  --max-iter 8 \
  --review
```

SEAM 会根据 `--server_type` 和 `--server_url` 自动复用或启动服务：URL 端口空闲时自动启动，同类型服务已存在时直接复用，被其他服务占用时提示是否由 SEAM 建立新的同类型服务。

### 3. Direct Python entrypoint

```bash
python -m tests.e2e.e2e_test_v2 \
  --server_type opencode \
  --server_url http://127.0.0.1:5000 \
  --project-dir /path/to/your/cuda/project \
  --output_dir ./output_projects \
  --keep-temp-dir \
  --review-gate
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--project-dir` | 待迁移项目根目录。 |
| `--output_dir` | 迁移产物输出根目录，通常是 `./output_projects`。 |
| `--max-phase5-iter` / `--max-iter` | Phase 5 修复循环最大迭代次数，默认 10。 |
| `--review-gate` / `--review` | 开启可选 review gate。 |
| `--server_type` / `--server_url` | 服务器类型和基础 URL；当前 `server_type` 支持 `opencode`。 |

## YAML runtime skills

在 `workflows/npu_migration_v2.yaml` 的 agent、phase 或 sub-workflow phase 上添加：

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
Phase 4   rule-based CUDA -> NPU 迁移
Phase 5   validation + repair loop + custom-op final gate
Phase 6   报告生成
Phase 7a  experience evaluation
Phase 7b  experience refinement
```

Phase 4 成功不代表最终成功；最终状态以 Phase 5 行为验证和 final gate 为准。
