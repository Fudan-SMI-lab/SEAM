# migration_utils: CUDA 到 Ascend NPU 自动化迁移框架

**migration_utils** 是一个基于 YAML 驱动和 AI Agent 的自动化流水线框架，旨在将依赖 CUDA 的 PyTorch 项目无缝迁移至 Ascend 910B NPU 硬件。

本框架的核心优势在于**经验记忆系统 (Experience Memory System)**：它能从成功的迁移任务中自动提炼经验，并在后续任务中通过 Agentic RAG 自动检索和注入，从而不断降低迁移成本，提高首次成功率。

---

## 🌟 核心特性

- 🤖 **YAML 驱动工作流**: 所有阶段 (Phase) 的定义、条件判断和流转逻辑均通过 YAML 配置，无需硬编码，灵活可扩展。
- 🔧 **智能修复循环 (Intelligent Repair Loop)**: 在 Phase 5 中，当脚本执行失败时，框架会自动分析错误、分类根因，并路由到专门的 Agent（如依赖修复、代码适配、算子修复）进行修复，形成闭环。
- 🧠 **经验记忆系统 (Experience Memory)**:
    - **自动学习**: 任务完成后自动提取高价值经验（Phase 7）。
    - **跨任务检索**: 在新任务的关键阶段（如项目分析、错误修复）自动检索历史经验并注入 Prompt。
    - **技能库管理**: 支持 `skills/` (技能) 和 `staging/` (候选经验) 的自动晋升。
- 🚪 **质量守门员 (Quality Review Gate)**: 迁移成功后，可触发 Review Agent 检查是否存在 CPU Fallback 或约束违反，确保迁移质量。
- 📝 **全链路产物追踪**: 每个步骤的输入输出、Agent 回复、验证结果均记录在 `.sm-artifacts/` 中，支持审计与回溯。

---

## 🚀 快速开始

### 1. 前置要求

- **硬件**: Ascend 910B NPU
- **软件栈**: CANN >= 8.0.RC, Python >= 3.10, PyTorch 2.5.1 + torch-npu
- **LLM Server**: 运行中的 OpenCode Server

### 2. 安装与启动 Server

```bash
# 启动 OpenCode Server
./scripts/start_server.sh --port 4096

# 验证 Server 状态
curl -s http://127.0.0.1:4096/agent
```

### 3. 运行端到端测试 (E2E)

使用 `tests/e2e/e2e_test_v2.py` 运行完整的迁移流程：

```bash
python tests/e2e/e2e_test_v2.py \
  --project-dir /path/to/your/cuda/project \
  --server-url http://127.0.0.1:4096 \
  --max-phase5-iter 5
```

**参数说明**:
- `--project-dir`: 待迁移项目的根目录（需包含 `original_src/` 和 `test_data_and_scripts/`）。
- `--max-phase5-iter`: Phase 5 修复循环的最大重试次数。
- `--review-gate`: 开启质量审查模式。

---

## 📖 架构概览

整个迁移流程分为 7 个阶段，由 `workflows/npu_migration_v2.yaml` 驱动：

### 1. 环境准备阶段 (Phase 0 - 3.5)
- **Phase 0 (Env Detect)**: 检测 NPU 环境、CANN 版本。
- **Phase 1 (Project Analysis)**: 🧠 **经验注入点**。分析项目结构、依赖、CUDA 特征。
- **Phase 2 (Venv Create)**: 创建并配置虚拟环境。
- **Phase 3 (Entry Script)**: 确定非交互式入口脚本。
- **Phase 3.5 (Static Validate)**: 静态检查脚本是否包含阻塞性代码（如 GUI、Input）。

### 2. 迁移执行阶段 (Phase 4 - 6)
- **Phase 4 (Rule Migration)**: 机械替换 CUDA API 到 NPU API (如 `.cuda()` -> `.npu()`)。
- **Phase 5 (Repair Loop)**: 🔄 **核心修复循环**。运行脚本 -> 报错分析 -> 路由修复 -> 重试。
- **Phase 6 (Report)**: 生成 5 份详细的迁移总结报告。

### 3. 经验沉淀阶段 (Phase 7)
- **Phase 7a (Evaluate)**: Evaluator Agent 扫描产物，评估是否值得沉淀为经验。
- **Phase 7b (Refine)**: Refiner Agent 将候选经验细化为结构化 Skill (包含 Root Cause, Fix Steps, Antipatterns)。

---

## 🧠 经验记忆系统 (Experience Memory)

### 工作流程
```
迁移完成 (Phase 6) -> 经验评估 (Phase 7a) -> 经验细化 (Phase 7b) -> 存入内存 (Memory/Skills)
下次任务 -> Phase 1/5 经验检索 (Query Agent) -> 选择最相关经验 -> 注入 Prompt -> 辅助修复
```

### 关键目录结构
```text
migration_utils/
├── memory/                 # 经验存储
│   ├── index/              # cases.jsonl 索引文件
│   ├── stages/             # 当前运行产生的候选经验
│   └── promotions/         # 待晋升的经验
├── skills/                 # 已晋升的成熟技能库
│   ├── torch-npu-pyyaml-preinstall/
│   │   ├── SKILL.md        # 结构化的技能文档
│   │   └── ...
├── workflows/
│   └── npu_migration_v2.yaml # 核心工作流配置
└── prompts/                # LLM Prompt 模板
```

### 检索与注入 (Query & Injection)
- **检索策略**: 使用 Agentic RAG，Agent 根据当前错误上下文（Error Category, Stderr）在索引中选择最相关经验。
- **注入格式**: 将经验的标题、类别、文件路径直接注入 Prompt，Agent 可按需读取完整文件。

---

## 🛠️ 配置与扩展

### 添加新修复角色 (Repair Role)
1. 在 `workflows/npu_migration_v2.yaml` 的 `agents` 中添加新角色。
2. 在 `repair_dispatch` 中添加路由规则。
3. 在 `prompts/` 中定义对应的 Prompt 模板。

---

## ❓ 常见问题 (FAQ)

**Q: Phase 5 修复循环一直失败怎么办？**
A: 检查 `.sm-artifacts/` 中的日志。如果连续 3 次出现相同错误分类 (`stagnation`)，循环将终止。这通常意味着该错误超出了当前 Agent 的能力范围，建议人工介入或添加新的修复 Skill。

**Q: 经验是如何被晋升的？**
A: 当 Evaluator 认为某次修复具有通用价值时，会产出 Candidate。Refiner 会将其细化。若同类经验多次出现，Promoter 会自动将其晋升为 `skill/` 下的正式技能。
