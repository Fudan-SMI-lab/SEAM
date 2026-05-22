# SEAM：让国产算力迁移从「人工工程」走向「系统能力」

<p align="center">
  <img src="https://img.shields.io/github/license/seam-project/seam" alt="许可证">
  <img src="https://img.shields.io/badge/platform-Ascend%20NPU-blue" alt="Ascend NPU">
  <img src="https://img.shields.io/badge/runtime-OpenCode%20Server-111827" alt="pencode-server">
  <img src="https://img.shields.io/badge/platform-PPU-orange" alt="PPU">
  <img src="https://img.shields.io/badge/platform-Muxi-green" alt="Muxi GPU">
  <img src="https://img.shields.io/badge/framework-PyTorch-red" alt="PyTorch">
  <img src="https://img.shields.io/badge/code%20style-google-blue" alt="代码风格：Google">
  <img src="https://img.shields.io/github/contributors/seam-project/seam" alt="贡献者">
</p>

<p align="center">
  <strong>SEAM 是一个自动化迁移工具——帮你把一个原来只能在 NVIDIA 显卡上运行的 AI 项目，自动搬到国产算力卡上，让它能直接跑起来。</strong>
</p>

---

## 📖 项目简介

SEAM (Self-Evolving Agentic Migration) 的起点很具体：**把一个基于 PyTorch 的 CUDA 项目迁移到昇腾 NPU（Ascend NPU），到底难在哪里。**

实际迁移中会遇到三类问题：**代码适配**——CUDA API 需要逐一替换为昇腾对应接口，涉及 `torch.cuda`、设备放置、通信后端等多个层面；**虚拟环境适配**——不同项目的依赖版本、CANN 工具链、驱动版本各不相同，无法用一套模板覆盖；**缺失算子重新生成**——CUDA custom op 没有现成的昇腾等价物，必须从算子语义出发重新实现。

这些问题叠加在一起，使得迁移不是简单的文本替换，而是一个需要理解项目结构、自动适配环境、并在遇到未知算子时自主寻找解决方案的系统工程。**SEAM 由此诞生。**

### 🎯 目标用户与核心痛点

**目标用户**：面向国产显卡进行模型部署、智能体开发、科学智能研究，以及对算力卡有推理或少量训练需求的开发者与研究员。

用户拿到一张国产算力卡，想跑一个原来在 CUDA 上的项目，面对的是**三重障碍**：

| 障碍 | 说明 |
|------|------|
| **🔧 迁移技术栈深、碎片化严重** | 代码适配、虚拟环境适配、缺失算子重新生成——三个层面相互耦合 |
| **❓ 迁移结果是"薛定谔的"——不敢信** | 代码改了、环境搭了，一跑：精度不对、算子回退、报诡异的错。比完全不迁移更糟糕 |
| **📭 缺乏可参考的真实案例** | 「别人真的跑通了吗？」——这是决策国产算力时用户最关心的信任问题 |

---

## 🔧 SEAM 怎么解决？

SEAM 把迁移问题分解成 **6 步** 来解决：

> **📥 输入项目路径 → SEAM 自动完成全流程 → 📤 输出可用代码 + 迁移报告**

| 步骤 | 你做什么 | SEAM 做什么 |
|:---|:---|:---|
| **第 1 步** 🔍 | 输入项目路径 | 自动检测环境、分析项目结构 |
| **第 2 步** 📦 | 等待 | 准备虚拟环境、安装依赖 |
| **第 3 步** ✍️ | 等待 | CUDA → NPU 代码适配（确定性规则文本替换，不经过大模型） |
| **第 4 步** 🔄 | 等待 | 执行迁移后代码 → 分析错误 → 分派修复 → 审查质量 → 直到通过 |
| **第 5 步** 🛡️ | 等待 | Custom-op 最终关卡验证 |
| **第 6 步** ✅ | **拿到可用代码 + 迁移报告** | 同时沉淀经验，下次迁移更快 |

---

![SEAM结构介绍](./doc/imgs/SEAM.png)

## ✨ 核心能力

### 🌐 多硬件 × 多框架覆盖

| 硬件 \ 框架 | Torch | vLLM | SGLang |
|:---:|:---:|:---:|:---:|
| **🔷 Ascend** | ✅ 已完成 | 🔜 进行中 | 🔜 进行中 |
| **🔴 PPU** | 🔜 进行中 | 🔜 进行中 | 🔜 进行中 |
| **🟢 Muxi** | 🔜 进行中 | 🔜 进行中 | 🔜 进行中 |

> ✅ = 已适配 | 🔜 = 规划中

### 📝 端到端自动迁移，覆盖全链路

SEAM 当前实现的核心是一个由 **YAML 状态机驱动的多阶段迁移流水线**，配合 **五个持久化智能体**协同工作。整个流水线包含十个阶段：

```text
环境检测 → 项目分析 → 依赖准备 → 规则迁移 → 验证修复循环
    → Custom-op 最终关卡 → 迁移报告生成 → 经验评估与精炼
```

其中 **Phase 4 的规则迁移是确定性文本替换，不经过大语言模型**；**Phase 5 是核心验证闭环**，执行迁移后代码、分析错误、分派修复、审查质量，直到成功或达到退出条件。对于 CUDA custom op 项目，SEAM 激活更严格的证据链：算子清单、构建产物、性能对比、无回退证据，全部通过 `custom_op_final_gate` 机器验证。

### 🧠 自演化机制 — 越用越聪明

自演化是 SEAM 区别于传统迁移工具的关键：

| 能力 | 说明 |
|------|------|
| **🔍 零先验运行** | 系统无需预先了解目标项目的代码结构或依赖关系，从环境检测开始自主完成全流程 |
| **🔄 跨案例知识迁移** | 每次迁移完成后，评估成功与失败的案例，把有效的适配方案提炼为可复用技能（skill），存入 `.memory/skills/` 和 `.memory/memory/` 目录 |
| **📈 边际成本趋近于零** | 第 1 次迁移 ResNet50 学一套适配方案，第 10 次迁移 YOLOv8 直接复用经验只处理差异部分 |

> 🏆 **当前已沉淀的核心技能：**
> - `cuda-custom-op-to-npu-custom-op` — 自定义算子从 CUDA 到昇腾的完整移植方案
> - `cuda-custom-extension-removal` — Apex CUDA 扩展清理策略
> - `torch-npu-venv-setup-cpu-base` — 虚拟环境搭建标准化流程
> - `fail-closed-ascend-npu-validation` — 迁移后验证的门控策略

### 🛡️ 幻觉控制 — 不让「看起来行了」蒙混过关

> 用户最怕什么？**「看起来迁移了，但实际上不能跑。」** 用 LLM 做代码迁移时，LLM 可能生成「看起来合理但实际不能运行」的代码。在国产算力推广的早期阶段，**一次失败的迁移体验就足以让用户放弃。**

| 🛡️ 策略 | 说明 |
|:---|:---|
| **行为验证 > 静态分析** | 通过实际运行入口脚本来检验迁移结果，而非依赖静态分析或代码生成置信度 |
| **错误分类 + 精准路由** | 按错误类型（依赖/导入/算子/性能）路由到最擅长的修复角色 |
| **三振出局** | 连续三次相同错误自动停止迭代，避免 LLM 在幻觉中循环 |
| **Fail-closed 门控** | 验证不通过就停止，不产出「可能行了」的半成品 |
| **Custom-op 证据链** | 算子清单、manifest、parity 精度对比、运行时覆盖率、性能对比、无回退证据——全部通过机器验证才标记 FULL_PASS |

> 💫 **自进化与幻觉控制的关系**：两者互为因果的正向循环——
> **幻觉控制得好** → 迁移成功率高 → 积累的有效经验多 → 自进化基础扎实 → 迁移更少出错 → 幻觉控制压力减小

---

## 🏗️ 技术架构

### YAML 状态机

所有阶段、智能体、验证器、状态转移和子工作流定义在 `src/workflows/npu_migration_v2.yaml` 中。支持运行时技能动态注入、智能体协作策略可配置、验证门控和最大迭代次数可配置。

### 五个持久化智能体

| 智能体 | 职责 |
|:---|:---|
| `main_engineer` | 环境检测、项目分析、报告生成等主干阶段 |
| `error_analyzer` | 在每轮验证中分类错误并推荐修复角色 |
| `dependency_fixer` | 修复依赖和导入问题 |
| `code_adapter` | 处理 CUDA 到 NPU 的 API 适配 |
| `operator_fixer` | 负责自定义算子和内核的移植 |

### 验证闭环

一套不依赖人工判断的机器验证体系，覆盖全链路每个阶段——**每一道关卡都能独立运行、独立审计**：

```text
validate_env_detect → validate_project_analysis → validate_venv
    → validate_rule_migration → validate_entry_script
    → validate_validation_final → validate_reports
```

Phase 4 的规则迁移成功不等于最终通过，最终状态以 Phase 5 行为验证和最终关卡为准。

### Custom-op 最终关卡

CUDA custom op 项目必须产出昇腾 C/CANN OPP 制品，通过以下全部机器验证才能标记为 `FULL_PASS`：算子清单 · manifest · parity 精度对比 · 运行时覆盖率 · 性能对比 · 无回退证据。

### 经验记忆系统

Phase 7a 评估迁移结果，Phase 7b 将有效经验精炼为技能，存储于 `.memory/skills/` 和 `.memory/memory/` 目录，供后续迁移检索。技能可通过 YAML 配置注入到任意阶段或智能体。**成功经验和失败教训都沉淀。**

---

## 🔮 完整愿景

SEAM 的长期定位是成为 **🏛️ 国产算力生态基础设施**。

### 覆盖范围

当前版本聚焦昇腾 NPU 迁移。完整愿景中，SEAM 应覆盖更多国产加速卡平台，包括阿里巴巴平头哥 PPU (Alibaba T-Head PPU)、沐曦 GPU (Muxi GPU) 等。框架层面，除 PyTorch 外，还应支持 vLLM、SGLang 等推理框架的适配迁移。

### 生态价值

每次迁移中沉淀的稳定适配算子和软件包可以反哺社区，形成可复用的国产算力适配知识库。

### 更广泛的技术影响

| 🌍 影响 | 说明 |
|:---|:---|
| **💎 "经验即资产"的范式** | 让迁移工具自己记录和归纳经验，可推广到跨框架迁移、编译优化等场景 |
| **🤝 跨厂商合作信任体系** | 通过与硬件厂商的合作，持续丰富适配案例，展现不断增长的信任证据 |
| **📏 事实参考标准** | 形成一套事实上的适配参考标准：算子映射关系、性能基线、精度验证方法 |
| **🌱 社区驱动的知识沉淀** | 「每次迁移沉淀技能 → 反哺社区 → 形成国产算力适配知识库」的社区协作范式 |

---

## 🔭 未来方向

### 🧠 自进化增强
- **跨设备迁移经验积累**：从「昇腾→昇腾」到「昇腾→PPU→Muxi」的跨平台迁移经验复用
- **硬件感知技能库**：针对不同硬件特性（FP4/FP8 低精度、算子优化路径差异）的自动适配能力

### 📐 低精度推理适配
- 低精度训练的大模型推理：FP4/FP8 量化模型的推理适配
- 大规模大模型/智能体部署：低精度推理降低显存与算力开销
- 具身与边端应用场景：低精度推理适配边缘设备

### ⚖️ 算子优化
针对不同硬件架构的算子优化路径存在显著差异。SEAM 需要在迁移过程中识别目标设备特征，选择对应的算子优化路径，并将设备特定的优化经验纳入经验记忆系统。

### 🛡️ 幻觉控制持续增强
- 更精细的错误分类体系：从粗粒度分类到细粒度错误图谱
- 行为覆盖率量化：从「能不能跑」升级到「跑得多深、覆盖多少用例」
- 跨案例幻觉模式挖掘：从历史迁移中自动识别高风险组合，提前规避
- 硬件特性感知的幻觉抑制：在生成阶段就考虑硬件约束，从源头减少偏差

---

## 🚀 安装指南

### 环境要求

- Python 3.9 及以上
- 昇腾 NPU 驱动及 CANN 工具链（昇腾平台）
- pip 21.0 及以上

### 从源码安装

```bash
git clone https://github.com/seam-project/seam.git
cd seam
pip install -e ".[dev]"
```

---

## ⚡ 快速开始

先启动用户预先管理的 OpenCode 服务：

```bash
opencode serve --port 4098 --hostname 127.0.0.1
```

然后从 SEAM 仓库根目录运行迁移入口：

```bash
python -m tests.e2e.e2e_test_v2 \
  --hostname 127.0.0.1 \
  --port 4098 \
  --server_type opencode \
  --project-dir /path/to/cuda/project \
  --output_dir ./output_projects
```

`--hostname`、`--port` 和 `--server_type` 共同描述外部服务端点；当前 `server_type` 支持 `opencode`，后续可扩展到其他迁移服务。`--project-dir` 指向待迁移 CUDA/PyTorch 项目，`--output_dir` 指向迁移产物输出根目录。

用户全程只需要：**输入项目路径，输出可用代码 + 迁移报告。** 中间涉及的 CUDA API 适配、缺失算子重新实现、环境变量配置，全部由五个持久化智能体协同完成。

---

## 📚 文档

- [用户手册](doc/USER_GUIDE.md) — 详细的功能介绍、配置方法和使用指南
- [常见问题解答](doc/FAQ.md) — 用户最常遇到的疑问
- [贡献指南](doc/CONTRIBUTING.md) — 如何参与贡献
- [更新日志](CHANGELOG.md) — 版本历史与发布说明
- [项目治理](GOVERNANCE.md) — 项目治理模式
- [维护者指南](MAINTAINERS.md) — 核心维护者内部工作手册

---

## 🤝 参与贡献

我们欢迎任何形式的贡献！请阅读 [贡献指南](doc/CONTRIBUTING.md) 了解：

- 行为准则
- 开发环境搭建
- 测试与代码风格规范
- Pull Request 提交流程

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/) 行为准则。

---

## 📄 版本更新

详见 [CHANGELOG.md](CHANGELOG.md)。我们遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范（MAJOR.MINOR.PATCH）。

---

## 📜 开源许可证

SEAM 基于 MIT License 开源。详见 [LICENSE](LICENSE) 文件。

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```

---

<p align="center">
  <sub>❤️由复旦大学SMI Lab和复旦大学CFFF平台共同构建❤️</sub>
</p>
