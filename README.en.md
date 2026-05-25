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


SEAM is an automated AI migration tool. It seamlessly migrates AI projects originally designed for NVIDIA GPUs to run directly on Chinese GPUs.


### Application Scenarios

New users working with Chinese GPUs often encounter these challenges:
- **Failed Execution**: Code adaptation, environment configuration and custom operator regeneration are required across heterogeneous GPU hardware. The migration stack is complex and fragmented knowledge is hard to master.
- **Lack of Reference Cases**: Uncertainty over project operability and difficulty distinguishing hardware or operational issues, raising credibility concerns for Chinese GPU adoption.
- **Unstable Migration Outcomes**: Code revision and environment setup may lead to inaccurate precision, operator fallback and unexpected runtime errors. Troubleshooting support maybe limited.


<p align="center">
🐧❤️ SEAM eases your Chinese GPU usage.❤️🐧
</p>

---

### Quick Start
Download and install SEAM on your Chinese GPU server or container environment:

```bash
git clone https://github.com/seam-project/seam.git
cd seam
pip install -e ".[dev]"
```

Run SEAM:
```bash
cd seam
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode \
  --server_url http://127.0.0.1:5000
```

Execution Results:
- Migrated code are saved to `./output_projects` by default.  Customize the path via `--output_dir`.
- Migration reports are also generated under the assigned output directory.
- Detailed runtime logs will be archived in the `xx` directory.
- Folders including `.memory` and `.skill` store self-evolution learning data. Do not delete unnecessarily.


---
### Core Capabilities & Technical Overview

1. **Multi-Hardware & Multi-Framework Compatibility**

| Hardware \ Framework | Torch | vLLM | SGLang |
|:---:|:---:|:---:|:---:|
| **Alibaba Pingtouge PPU** | ✅ Completed | ✅ Completed | ✅ Completed |
| **Huawei Ascend** | ✅ Completed |✅ Completed | ✅ Completed |
| **MetaX** | ✅ Completed | ✅ Completed | ✅ Completed|
| **Other GPUs** | 🔜 On-demand support| 🔜 On-demand support | 🔜 On-demand support |

2. **End-to-End Automated Migration**

SEAM adopts a YAML state machine driven multi-stage migration pipeline with 5 collaborative intelligent agents, powered by real-time GPU runtime feedback. The pipeline covers 10 sequential phases:

```text
GPU Environment Detection → Project Analysis → Dependency Preparation → Rule-based Migration
→ Iterative Validation & Fix → Custom Operator Processing → Report Generation → Experience Evaluation & Optimization
```

3. **Self-Evolution for Progressive Optimization**

Supports zero-prior startup and cross-case knowledge reuse with nearly zero marginal cost. Valid adaptation solutions are summarized as reusable skills and stored in `.memory/skills/` and `.memory/memory/` to optimize subsequent migration tasks.

4. **Hallucination Control for Reliable Migration**

Adopts behavior verification, error classification, precise routing, fail-closed gating and custom operator validation mechanisms to guarantee valid and trustworthy migration results.

<p align="center">

Self-evolution and hallucination control serve as dual core strengths, forming a mutually reinforcing positive iteration loop.

See [SEAM Technical Introduction](docs/SEAM_Tech_Intro.zh.md) for detailed technical details.

</p>

---

### Documentation

- [User Guide](docs/USER_GUIDE.md) — Function introduction, configuration and operation instructions
- [FAQ](docs/FAQ.md) — Common troubleshooting solutions
- [Contribution Guide](docs/CONTRIBUTING.md) — Guidelines for community contribution
- [Changelog](docs/CHANGELOG.md) — Version updates and release notes
- [Project Governance](GOVERNANCE.md) — Project management specifications
- [Maintainer Manual](MAINTAINERS.md) — Internal guidelines for core maintainers

### Open Source License

SEAM is released under the MIT License. Refer to the [LICENSE](LICENSE) file for full terms.

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```

<p align="center">
  <sub>❤️ Co-developed by Fudan University SMI Lab, Fudan CFFF Platform and Shanghai Chuangzhi Institute ❤️</sub>
</p>



