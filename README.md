# <p align="center">SEAM</p>
<p align="center">迁移CUDA代码到中国产GPU，变简单。</p>
<p align="center">SEAM: Self-Evolving Agentic Migration for Chinese GPUs.</p>


<p align="center">
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
    <a href="https://opencode.ai"><img alt="OpenCode Server" src="https://img.shields.io/badge/runtime-OpenCode%20Server-111827" ></a>
</p>

<p align="center">
  <a href="README.en.md">English</a> |
  <a href="README.md">简体中文</a>
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
git clone https://github.com/Fudan-SMI-lab/SEAM.git
cd SEAM
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode
```

请先确认本机 OpenCode Server 已启动，默认地址为 `http://127.0.0.1:4098`；如果端口不同，可以加 `--server_url` 显式指定。

不传 `--workflow` 时，启动器会使用 `src/workflows/seam_auto_default.yaml` 自动选择流程，通常无需手动指定 workflow。

项目根目录下的 `ADAPTATION_REQUIREMENTS.md` 会自动加载；非标准约束可以通过 `--extra '--user-constraints PATH'` 传入。

#### 已验证的 V3 公共契约

`run_seam.sh` 是日常入口，默认选择 `src/workflows/seam_auto_default.yaml`。高级自动化可直接调用 Python 入口；它必须且只能提供 `--project-dir` 或 `--continue-from` 之一。下面的示例由 `src/tests/test_documented_cli_contracts.py` 使用真实解析器执行验证。

<!-- cli-contract:readme-zh-direct -->
```bash
PYTHONPATH=src python -m tests.e2e.e2e_test_v3 \
  --project-dir /absolute/path/to/cuda-project \
  --workflow-path src/workflows/seam_auto_default.yaml \
  --server-url http://127.0.0.1:4098 \
  --review-gate \
  --container-retention retain
```

- Review Gate 默认关闭；启用后，有效验证加显式 `accept` 才是普通 PASS。严格模式是最终有效默认值，只有 `reject_exhausted` 可由 `--no-review-fail-closed` 放宽为 `passed_with_reviews`；unknown、session error、improvement error 和验证失败仍然 FAIL。
- `--continue-from` 只接受显式的终态父运行 `summary.json`。它会创建全新子会话和独立子证据，父报告保持不可变；不是崩溃恢复，也不恢复进行中的 Agent 或 Phase 状态。当前普通直接运行尚未生成 continuation 所需的 sealed root run manifest，因此不要宣称任意直接运行都可继续。
- 容器默认保留。`delete` 只对 SEAM 明确拥有且现场复核通过的 image 容器生效；外部或用户容器永不删除。已通过的运行若请求清理但清理失败，迁移结果仍是 PASS，但最终进程退出码为 2。
- `--save-agent-trace` 是默认关闭的可选旁路。它递归导出 OpenCode 可访问的原始数据，不脱敏、不截断已接受的数据，但受容量和图边界限制；不可访问、分页、未知或不支持的数据会明确标为 partial。它不能导出提供方隐藏的 reasoning，也不改变 continuation authority 或冻结的 `RunOutcome`。普通可选 trace 失败不改变退出码，但 continuation 的 required evidence publication 失败会使 finalization 退出 1。
- Replay 只显示同一进程中已接受的真实 Phase 5 receipt 所对应的命令。SEAM 不自动执行 replay，也不保证确定性复现。

完整 CLI、continuation 矩阵、产物树、超时语义和可选集成检查见 [`src/docs/E2E_TESTING.md`](src/docs/E2E_TESTING.md)；原始 trace 完整性和 schema-v2 关联边界见 [`src/docs/full_agent_io_logging_design.md`](src/docs/full_agent_io_logging_design.md)。

运行后：
*   是否跑通：终端最后会显示 `E2E TEST PASSED`、`E2E PASS`、`E2E FINALIZATION FAILED` 或失败信息；权威详情位于 `./e2e-reports/src/e2e-v3-<run-id>/summary.json`。迁移 FAIL 退出 1；仅在迁移已 PASS 且请求的授权清理失败时退出 2。
    
*   迁移的代码库：默认写入 SEAM 仓库同级目录 `../output_projects/<项目名>_<时间戳>/`；也可以用环境变量 `MIGRATION_OUTPUT_PROJECTS_ROOT` 改默认根目录，或用 `--output-dir` 显式指定本次输出项目根目录。
    
*   迁移报告：会在迁移后的代码库下创建`migration_reports/`文件夹, 用于查看迁移后项目本身的验收结果、性能、custom-op迁移情况、构建日志等。
    
*   详细运行时 log：工作证据位于迁移项目的 `.sm-artifacts/`，最终报告目录还包含 telemetry、resource manifest、可选 raw trace 和 finalization diagnostics。请一并提供对应的 `summary.json`；不要把 `.sm-artifacts` 当作 continuation checkpoint。
    
*   .memory .skill 等文件夹会更新，是SEAM的自进化学习的经验记忆和技能素材，非必要勿删。
    
---
### SEAM 能力和技术方案简介

1.  **多硬件×多框架覆盖**
    
    | 硬件 \ 框架 | Torch | vLLM | SGLang |其他框架 |
    | --- | --- | --- | --- |--- |
    | **[阿里平头哥PPU](docs/gpu_docs/阿里平头哥PPU.md)** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |🔜 等你提需求 |
    | **[华为昇腾Ascend NPU](docs/gpu_docs/华为AscendNPU.md)** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |🔜 等你提需求 |
    | **[沐曦MetaX](docs/gpu_docs/沐曦MetaX.md)** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |🔜 等你提需求 |
    | **其他GPUs** | 🔜 等你提需求 | 🔜 等你提需求 | 🔜 等你提需求 |🔜 等你提需求 |
    
2.  **自动化端到端迁移**
    
    SEAM当前实现的核心是一个由YAML状态机驱动的多阶段迁移流水线，配合5个持久化智能体协同工作，基于当前GPU真实运行反馈。整个流水线包含8个阶段：
    
    ```text
    GPU环境检测 → 用户项目分析 → 依赖准备 → 规则迁移 → 验证修复循环 → 自定义算子等最终关卡 → 迁移报告生成 → 经验评估与精炼
    ```
    
3.  **自进化：越用越聪明**
    
    SEAM有零先验运行、跨案例知识迁移、边际成本趋近于零等能力。其中，每次迁移完成后，评估成功与失败的案例，把有效的适配方案提炼为可复用技能（skill），存入 `.memory/skills/` 和 `.memory/memory/` 目录，为下一次运行提供参考。
    
4.  **幻觉控制：确保迁移结果真实有效**

    SEAM采用行为验证、错误分类和精准路由、三振出局、Fail-closed 门控、自定义算子验证证据链等策略，确保迁移结果真实有效。


**“自进化”和“幻觉控制”是SEAM的核心能力，相辅相成，正向循环。**

> 更多SEAM的技术方案介绍，参见[SEAM技术方案讲解](docs/SEAM_Tech_Intro.zh.md)。


---

### 文档

- [用户手册](docs/User_Guide.md) — 详细的功能介绍、配置方法和使用指南
- [常见问题](docs/FAQ.md) — 用户最常遇到的疑问FAQ
- [贡献指南](docs/CONTRIBUTING.md) — 如何参与贡献
- [更新日志](docs/CHANGELOG.md) — 版本历史与发布说明


---

### 联系我们

有关 SEAM 与国产 GPU 的咨询、建议，敬请发送邮件至复旦 CFFF 平台邮箱：cfff@fudan.edu.cn ，多人值班，确保每条反馈及时响应。

---

### 开源许可证

SEAM 基于 MIT License 开源。详见 [LICENSE](LICENSE) 文件。

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```


本项目由复旦大学人工智能创新与产业研究院-统计机器智能实验室(SMI-lab)、上海创智学院、复旦大学CFFF智能计算平台共同构建。
