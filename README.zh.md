# <p align="center">SEAM</p>
<p align="center">迁移CUDA代码到中国产GPU，变简单。</p>
<p align="center">SEAM: Self-Evolving Agentic Migration for Chinese GPUs.</p>


<p align="center">
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
    <a href="https://opencode.ai"><img alt="OpenCode Server" src="https://img.shields.io/badge/runtime-OpenCode%20Server-111827" ></a>
</p>

<p align="center">
  <a href="README.en.md">English</a> |
  <a href="README.zh.md">简体中文</a> 
</p>


SEAM是一个自动化迁移AI工具，能把原来只能在NVIDIA显卡上运行的AI项目，自动化迁移到中国国产GPU算力卡上运行并调优。


### 适用场景

如果您初次接触中国国产GPUs，可能会担心遇到如下问题：
*   **运行不起来**：不同的GPU硬件环境需要代码适配、虚拟环境适配、缺失算子重新生成等，迁移技术栈深、碎片化知识难掌握。
    
*   **缺参考案例**：”别人真的跑通了吗？”“是我的问题还是GPU厂商问题？”，决策是否能使用中国国产GPU的最关心的信任问题。
    
*   **自己迁移结果“薛定谔”**：代码改了、环境搭了，一跑：精度不对、算子回退、报诡异的错。比完全不迁移更糟糕，咨询改进又找不到及时的技术指导。
 

<p align="center">
🐧❤️ 别担心，SEAM会陪伴你用好中国国产GPU。❤️🐧
</p>

---

### 快速开始
在您要用的中国产GPU服务器、容器环境里，下载和使用SEAM：
```bash
git clone https://github.com/seam-project/seam.git
cd seam
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode \
  --server_url http://127.0.0.1:5000
```

运行后：
*   是否跑通：终端最后会直接显示 `E2E TEST PASSED` / `E2E PASS` 或失败信息；也可以通过 `./e2e-reports/migration_utils/<时间戳>/summary.json`获取更具体的信息
    
*   迁移的代码库：会默认写入 `./output_projects/<项目名>_<时间戳>/`，或是执行时输入的参数 `--output-dir`。
    
*   迁移报告：会在迁移后的代码库下创建`.migration_reports/`文件夹, 用于查看迁移后项目本身的验收结果、性能、custom-op迁移情况、构建日志等。
    
*   详细运行时log：在迁移后项目的 `.sm-artifacts/` 下；如果运行失败，可以把运行报告和 `.sm-artifacts/` 一起反馈给我们排查。
    
*   .memory .skill 等文件夹会更新，是SEAM的自进化学习的经验记忆和技能素材，非必要勿删。
    
---
### SEAM 能力和技术方案简介

1.  **多硬件×多框架覆盖**
    
    | 硬件 \ 框架 | Torch | vLLM | SGLang |
    | --- | --- | --- | --- |
    | **阿里 平头哥PPU** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |
    | **华为 昇腾Ascend** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |
    | **沐曦 MetaX** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |
    | **其他GPUs** | 🔜 等你提需求 | 🔜 等你提需求 | 🔜 等你提需求 |
    
2.  **自动化端到端迁移**
    
    SEAM当前实现的核心是一个由YAML状态机驱动的多阶段迁移流水线，配合5个持久化智能体协同工作，基于当前GPU真实运行反馈。整个流水线包含8个阶段：
    
    ```text
    GPU环境检测 → 用户项目分析 → 依赖准备 → 规则迁移 → 验证修复循环 → 自定义算子等最终关卡 → 迁移报告生成 → 经验评估与精炼
    ```
    
3.  **自进化：越用越聪明**
    
    SEAM有零先验运行、跨案例知识迁移、边际成本趋近于零等能力。其中，每次迁移完成后，评估成功与失败的案例，把有效的适配方案提炼为可复用技能（skill），存入 `.memory/skills/` 和 `.memory/memory/` 目录，为下一次运行提供参考。
    
4.  **幻觉控制：确保迁移结果真实有效**
    

SEAM采用行为验证、错误分类和精准路由、三振出局、Fail-closed 门控、自定义算子验证证据链等策略，确保迁移结果真实有效。


<p align="center">

“自进化”和“幻觉控制”是SEAM的核心能力，相辅相成，正向循环。

更多SEAM的技术方案介绍，参见[SEAM技术方案讲解](docs/SEAM_Tech_Intro.zh.md)。
</p>

---

### 文档

- [用户手册](docs/User_Guide.md) — 详细的功能介绍、配置方法和使用指南
- [常见问题解答](docs/FAQ.md) — 用户最常遇到的疑问
- [贡献指南](docs/CONTRIBUTING.md) — 如何参与贡献
- [更新日志](docs/CHANGELOG.md) — 版本历史与发布说明


---

### 联系我们

无论是什么想法或疑问，与SEAM和中国国产GPU相关的，都可以联系我们。

我们思考了很多种联系方式，最后决定第一跳联系放 cfff@fudan.edu.cn ，这是复旦CFFF平台邮箱，多工程师值班，确保您的反馈我们都有处理无遗漏！

---

### 开源许可证

SEAM 基于 MIT License 开源。详见 [LICENSE](LICENSE) 文件。

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```

<p align="center">
  <sub>❤️本项目由复旦大学SMI Lab、复旦大学CFFF平台、上海创智学院共同构建❤️</sub>
</p>
