# <p align="center">SEAM</p>
<p align="center">🐧❤️ 迁移CUDA代码到中国产GPU，变简单。 ❤️🐧</p>
<p align="center">SEAM: Self-Evolving Agentic Migration for Chinese GPUs.</p>


<p align="center">
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
    <a href="https://opencode.ai"><img alt="OpenCode Server" src="https://img.shields.io/badge/runtime-OpenCode%20Server-111827" ></a>
    <a href="https://alidocs.dingtalk.com/i/nodes/P0MALyR8klYkMnpXHYRNKrzwW3bzYmDO"><img alt="SEAM Website" src="https://img.shields.io/badge/website-SEAM-111827" ></a>
</p>

<p align="center">
  <a href="README.en.md">English</a> |
  <a href="README.md">简体中文</a>
</p>


SEAM是一个自动化迁移AI工具，能把原来只能在NVIDIA显卡上运行的AI项目，自动化迁移到中国国产GPU算力卡上运行并调优。

---

### 快速开始
在您要用的中国产GPU服务器、容器环境里，下载和使用SEAM：
```bash
git clone https://github.com/Fudan-SMI-lab/SEAM.git
cd SEAM
bash src/scripts/init_seam.sh
bash src/scripts/run_seam.sh /path/to/project --server_url http://127.0.0.1:4098
```

---
### SEAM能力和优势

1. **当前支持的硬件和框架**
    | 硬件 \ 框架 | Torch | vLLM | SGLang |其他框架 |
    | --- | --- | --- | --- |--- |
    | **[阿里平头哥PPU](docs/gpu_docs/阿里平头哥PPU.md)** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |🔜 等你提需求 |
    | **[华为昇腾Ascend NPU](docs/gpu_docs/华为AscendNPU.md)** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |🔜 等你提需求 |
    | **[沐曦MetaX](docs/gpu_docs/沐曦MetaX.md)** | ✅ 已完成 | ✅ 已完成 | ✅ 已完成 |🔜 等你提需求 |
    | **其他GPUs** | 🔜 等你提需求 | 🔜 等你提需求 | 🔜 等你提需求 |🔜 等你提需求 |


2. **优势**
    - **端到端自动迁移**：30分钟内完成，每任务3-10元Token消耗。

    - **幻觉控制，真实硬件证据链**：多策略证据链，确保迁移结果真实有效。

    - **自进化，越用越聪明**：零先验启动、跨案例经验迁移、百模迁移经验共享。


---

### 更多文档

- [用户手册](docs/User_Guide.md)：详细的功能介绍和API说明
- [SEAM网站](https://alidocs.dingtalk.com/i/nodes/P0MALyR8klYkMnpXHYRNKrzwW3bzYmDO)于2026年08月上线：已经公开了120+模型适配经验340+份适配报告，并有网站有合作方提供免费GPU资源贡献，可以根据网站指引申请使用。
- [SEAM网站文档中心](https://alidocs.dingtalk.com/i/nodes/QBnd5ExVEvrpMaQZUgEvvBXZJyeZqMmz):更丰富的文档库

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
