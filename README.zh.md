# <p align="center">SEAM</p>
<p align="center">迁移代码到中国GPU，变简单。</p>
<p align="center">SEAM: Self-Evolving Agentic Migration for Chinese GPUs.</p>


<p align="center">
  <img src="https://img.shields.io/github/license/seam-project/seam" alt="许可证">
  <img src="https://img.shields.io/badge/runtime-OpenCode%20Server-111827" alt="pencode-server">
</p>


<p align="center">
  <a href="README.en.md">English</a> |
  <a href="README.zh.md">简体中文</a> 
</p>



SEAM是一个自动化迁移AI工具，能把原来只能在NVIDIA显卡上运行的AI项目，自动化迁移到中国国产GPU算力卡上直接运行。



### 适用场景

如果您初次接触中国国产GPUs，可能会担心遇到如下问题：
- **运行不起来**：不同的GPU硬件环境需要代码适配、虚拟环境适配、缺失算子重新生成等，迁移技术栈深、碎片化知识难掌握。
- **缺参考案例**：”别人真的跑通了吗？”“是我的问题还是GPU厂商问题？”，决策是否能使用中国国产GPU的最关心的信任问题。
- **自己迁移结果“薛定谔”**：代码改了、环境搭了，一跑：精度不对、算子回退、报诡异的错。比完全不迁移更糟糕，咨询改进又找不到及时的技术指导。


<p align="center">
🐧❤️ 别担心，SEAM会陪伴你用好中国国产GPU。❤️🐧
</p>

---

### 快速开始
在您要用的中国产GPU服务器、容器环境里，下载和安装SEAM：
```bash
git clone https://github.com/seam-project/seam.git
cd seam
pip install -e ".[dev]"
```

运行SEAM：
```bash
cd seam
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode \
  --server_url http://127.0.0.1:5000
```

运行后：
- 迁移的代码库：会默认写入 `./output_projects`，如果要设置输出目录，可以加参数`--output_dir`。
- 迁移报告：也会在`--output_dir`定义目录下新建。
- 详细运行时log：存档在xx目录，如果反馈问题debug，可以查看或者反馈此报告给我们。
- .memory .skill 等文件夹会更新，是SEAM的自进化学习的素材，非必要勿删。

---
### SEAM 能力和技术方案简介

1. **多硬件×多框架覆盖**

| 硬件 \ 框架 | Torch | vLLM | SGLang |
|:---:|:---:|:---:|:---:|
| **阿里 平头哥PPU** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |
| **华为 昇腾Ascend** | ✅ 已完成 |✅ 已完成 | ✅ 已完成 |
| **沐曦 MetaX** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成|
| **其他GPUs** | 🔜 等你提需求| 🔜 等你提需求 | 🔜 等你提需求 |

2. **自动化端到端迁移**

SEAM当前实现的核心是一个由YAML状态机驱动的多阶段迁移流水线，配合5个持久化智能体协同工作，基于当前GPU真实运行反馈。整个流水线包含10个阶段：

```text
GPU环境检测 → 用户项目分析 → 依赖准备 → 规则迁移 → 验证修复循环 → 自定义算子等最终关卡 → 迁移报告生成 → 经验评估与精炼
```

3. **自进化：越用越聪明**

SEAM有零先验运行、跨案例知识迁移、边际成本趋近于零等能力。其中，每次迁移完成后，评估成功与失败的案例，把有效的适配方案提炼为可复用技能（skill），存入 `.memory/skills/` 和 `.memory/memory/` 目录，为下一次运行提供参考。

4. **幻觉控制：确保迁移结果真实有效**

SEAM采用行为验证、错误分类和精准路由、三振出局、Fail-closed 门控、自定义算子验证证据链等策略，确保迁移结果真实有效。

<p align="center">

“自进化”和“幻觉控制”是SEAM的核心能力，相辅相成，正向循环。

更多SEAM的技术方案介绍，参见[SEAM技术方案讲解](docs/SEAM_Tech_Intro.zh.md)。

</p>

---

### 文档

- [用户手册](docs/USER_GUIDE.md) — 详细的功能介绍、配置方法和使用指南
- [常见问题解答](docs/FAQ.md) — 用户最常遇到的疑问
- [贡献指南](docs/CONTRIBUTING.md) — 如何参与贡献
- [更新日志](docs/CHANGELOG.md) — 版本历史与发布说明
- [项目治理](GOVERNANCE.md) — 项目治理模式
- [维护者指南](MAINTAINERS.md) — 核心维护者内部工作手册

###开源许可证

SEAM 基于 MIT License 开源。详见 [LICENSE](LICENSE) 文件。

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```

<p align="center">
  <sub>❤️本项目由复旦大学SMI Lab、复旦大学CFFF平台、上海创智学院共同构建❤️</sub>
</p>
