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


SEAM is an automated AI migration tool. It seamlessly migrates and optimizes AI projects originally designed for NVIDIA GPUs to run directly on Chinese GPUs.

### Application Scenarios

New to domestic GPUs? You may face these common hurdles:
* **Deployment failures**: Code adaptation, environment setup and missing operator redevelopment require extensive expertise across fragmented tech stacks.
* **Lack of references**: Doubts like "Has anyone successfully run the code?" or "Is the issue on my side or with the GPU vendor?" are major concerns when evaluating Chinese GPU solutions.
* **Unstable migration results**: Modified code often suffers accuracy loss, operator fallback or obscure runtime errors, with limited timely support for troubleshooting.


<p align="center">
🐧❤️ SEAM eases your Chinese GPU usage.❤️🐧
</p>

---

### Quick Start
Run the commands below on your domestic GPU server or container environment to try SEAM:

```bash
git clone https://github.com/seam-project/seam.git
cd seam
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode \
  --server_url http://127.0.0.1:5000
```

Execution Results:
*   **Run status**: The terminal will display `E2E TEST PASSED` / `E2E PASS` or error messages upon completion. For full details, check `./e2e-reports/migration_utils/<timestamp>/summary.json`.

*   **Migrated project**: Outputs are saved to `./output_projects/<project_name>_<timestamp>/` by default, or the custom path specified via the `--output-dir` parameter.

*   **Migration report**: A folder named `.migration_reports/` will be generated inside the migrated project, containing acceptance results, performance data, custom operator migration logs and build records.

*   **Runtime logs**: Detailed logs are stored under `.sm-artifacts/` in the migrated project. Please share the migration report and `.sm-artifacts` folder with us for troubleshooting if errors occur.

*   **Self-evolution directories**: Folders such as `.memory` and `.skill` store accumulated experience and reusable assets for SEAM's self-evolution mechanism. **Do not delete them unnecessarily**.


---
### Core Capabilities & Technical Overview


#### 1. Multi-hardware & Multi-framework Support

| Hardware \ Framework | Torch | vLLM | SGLang |Other Framework |
| --- | --- | --- | --- | --- |
| **[Alibaba Pingtouge PPU](docs/gpu_docs/阿里平头哥PPU.md)** | ✅ Done | ✅ Done | ✅ Done |🔜 Request welcome |
| **[Huawei Ascend](docs/gpu_docs/华为AscendNPU.md)** | ✅ Done | ✅ Done | ✅ Done |🔜 Request welcome |
| **[MetaX](docs/gpu_docs/沐曦MetaX.md)** | ✅ Done | ✅ Done | ✅ Done |🔜 Request welcome |
| **Other GPUs** | 🔜 Request welcome | 🔜 Request welcome | 🔜 Request welcome | 🔜 Request welcome |

#### 2. End-to-End Automated Migration

SEAM adopts a YAML state machine driven multi-stage migration pipeline, collaborated by five persistent intelligent agents, with decisions made based on real runtime feedback from target GPUs.

The full pipeline consists of 8 key phases:
```text
GPU Environment Detection → Project Analysis → Dependency Preparation → Rule-based Migration → Iterative Validation & Fix → Custom Operator Resolution → Report Generation → Experience Evaluation & Refinement
```

#### 3. Self-Evolution: Getting Smarter with Usage

SEAM supports zero-prior execution and cross-case knowledge reuse with near-zero marginal cost for repeated tasks.

After each migration, successful and failed cases are reviewed. Valid adaptation solutions are extracted as reusable skills and saved to `.memory/skills/` and `.memory/memory/` to guide subsequent migrations.

#### 4. Hallucination Control: Guarantee Reliable Migration Results

Multiple strategies are applied to ensure valid and dependable outputs, including behavior verification, error classification & precise routing, three-strike rule, fail-closed gating and full validation chains for custom operators.


<p align="center">

Self-evolution and hallucination control serve as dual core strengths, forming a mutually reinforcing positive iteration loop.

See [SEAM Technical Introduction](docs/SEAM_Tech_Intro.zh.md) for detailed technical details.

</p>

---

### Documentation

- [User Guide](docs/User_Guide.md) — Usage, configuration and feature docs
- [FAQ](docs/FAQ.md) — Common issues and solutions
- [Contributing](docs/CONTRIBUTING.md) — How to join development
- [Changelog](docs/CHANGELOG.md) — Version updates and release notes

### Contact

Feel free to reach out for any ideas or questions regarding SEAM and Chinese GPUs.

For primary contact, please send emails to **cfff@fudan.edu.cn**  — the official mailbox of Fudan University CFFF Platform. Our engineering team will respond to all feedback in a timely manner.


---

### Open Source License

SEAM is released under the MIT License. Refer to the [LICENSE](LICENSE) file for full terms.

```text
MIT License
Copyright (c) 2026 Fudan-SMI-lab
```

<p align="center">
  <sub>❤️ Co-developed by Fudan University and Shanghai Innovation Institute ❤️</sub>
</p>
