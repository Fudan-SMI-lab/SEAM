# <p align="center">SEAM</p>
<p align="center">🐧❤️ Make CUDA code migration to Chinese GPUs simple.  ❤️🐧</p>
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


SEAM is an automated AI migration tool. It seamlessly migrates and optimizes AI projects originally designed for NVIDIA GPUs to run directly on Chinese GPUs.


---

### Quick Start
Run the commands below on your domestic GPU server or container environment to try SEAM:

```bash
git clone https://github.com/Fudan-SMI-lab/SEAM.git
cd SEAM
bash src/scripts/init_seam.sh
bash src/scripts/run_seam.sh /path/to/project --server_url http://127.0.0.1:4098
```

The optional live dashboard uses Textual (preferred) or Rich (fallback). Install
its dependencies first:

```bash
python -m pip install -e "./src[dashboard]"
```

Select `--dashboard-mode auto|on|off` and
`--dashboard-backend auto|textual|rich`. Pressing `q` closes only the dashboard;
the migration continues in the background. While active, dashboard events are
written to `ui_events.jsonl` in the run report directory.

---
### Core Capabilities & Features of SEAM


#### 1. **SEAM Supports**

| Hardware \ Framework | Torch | vLLM | SGLang |Other Framework |
| --- | --- | --- | --- | --- |
| **[Alibaba Pingtouge PPU](docs/gpu_docs/阿里平头哥PPU.md)** | ✅ Done | ✅ Done | ✅ Done |🔜 Request welcome |
| **[Huawei Ascend](docs/gpu_docs/华为AscendNPU.md)** | ✅ Done | ✅ Done | ✅ Done |🔜 Request welcome |
| **[MetaX](docs/gpu_docs/沐曦MetaX.md)** | ✅ Done | ✅ Done | ✅ Done |🔜 Request welcome |
| **Other GPUs** | 🔜 Request welcome | 🔜 Request welcome | 🔜 Request welcome | 🔜 Request welcome |

#### 2. **SEAM Advantages** 
- **End-to-End Automated Migration**: Completed within 30 minutes, with a token cost of ¥3–10 per task.
- **Hallucination Control with Real Hardware Evidence Chain**: Multi-strategy evidence chain ensures migration results are authentic and valid.
- **Self-Evolving, Smarter Over Time**: Zero-prior startup, cross-case experience transfer, and shared migration insights across hundreds of models.

---

### Documentation

- [User Guide](docs/User_Guide.md), Detailed feature descriptions and API documentation

- [SEAM Website](https://alidocs.dingtalk.com/i/nodes/P0MALyR8klYkMnpXHYRNKrzwW3bzYmDO): Over 120 model adaptation experiences and 340+ adaptation reports have been published. The website also features partner-contributed free GPU resources — you can apply for access following the on-site instructions.

- [SEAM documentation library](https://alidocs.dingtalk.com/i/nodes/QBnd5ExVEvrpMaQZUgEvvBXZJyeZqMmz)



### Contact

For ideas or questions about SEAM and Chinese GPUs, please send email to **cfff@fudan.edu.cn**, the official mailbox of Fudan University CFFF Platform. Our engineering team will respond to all feedback in a timely manner.


---

### Open Source License

SEAM is released under the MIT License. Refer to the [LICENSE](LICENSE) file for full terms.

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```


---

This project is jointly developed by:
- Statistical Machine Intelligence Lab (SMI-lab), Artificial Intelligence Innovation and Incubation Institute, Fudan University
- Shanghai Innovation Institute
- CFFF platform of Fudan University
