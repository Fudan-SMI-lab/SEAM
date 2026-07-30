# <p align="center">SEAM</p>
<p align="center">Make CUDA code migration to Chinese GPUs simple.</p>
<p align="center">SEAM: Self-Evolving Agentic Migration for Chinese GPUs.</p>


<p align="center">
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
    <a href="https://opencode.ai"><img alt="OpenCode Server" src="https://img.shields.io/badge/runtime-OpenCode%20Server-111827" ></a>
</p>

<p align="center">
  <a href="README.en.md">English</a> |
  <a href="README.md">简体中文</a>
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

Production support for this release is Linux only and requires Python 3.10+. Mandatory CI uses hardware-free Linux runners; real NPU/GPU integration checks remain optional and non-gating.

```bash
git clone https://github.com/Fudan-SMI-lab/SEAM.git
cd SEAM
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode
```

Make sure the local OpenCode Server is running first. The default address is `http://127.0.0.1:4098`; if your server uses another port, pass `--server_url` explicitly.

When `--workflow` is not passed, the launcher uses `src/workflows/seam_auto_default.yaml` as the default workflow.

The project-root `ADAPTATION_REQUIREMENTS.md` file is loaded automatically. For custom constraints in another file, pass `--extra '--user-constraints PATH'`.

#### Verified V3 public contract

Use `run_seam.sh` for normal runs; it defaults to `src/workflows/seam_auto_default.yaml`. Advanced automation may call the Python entrypoint directly, which requires exactly one of `--project-dir` and `--continue-from`. The real parser executes the following example in `src/tests/test_documented_cli_contracts.py`.

<!-- cli-contract:readme-en-direct -->
```bash
PYTHONPATH=src python -m tests.e2e.e2e_test_v3 \
  --project-dir /absolute/path/to/cuda-project \
  --workflow-path src/workflows/seam_auto_default.yaml \
  --server-url http://127.0.0.1:4098 \
  --review-gate \
  --container-retention retain
```

- Review Gate is disabled by default. When enabled, valid execution plus an explicit `accept` is a normal PASS. Strict mode is the final effective default. Only `reject_exhausted` can become `passed_with_reviews` under `--no-review-fail-closed`; unknown, session error, improvement error, and validation failure remain FAIL.
- `--continue-from` accepts only an explicit terminal parent `summary.json`. It creates fresh child sessions and separate child evidence while keeping the parent report immutable. It is not crash recovery and never restores an in-flight Agent or phase. Ordinary direct runs currently do not create the sealed root run manifest required by continuation, so not every direct report is eligible.
- Containers are retained by default. `delete` applies only to a positively owned SEAM image container after live revalidation; external and user containers are never deleted. If an otherwise passing run requests authorized cleanup and cleanup fails, the migration remains PASS but final process exit is 2.
- `--save-agent-trace` is an opt-in, default-off side channel. It recursively exports raw OpenCode data that SEAM can access, without redaction or truncation of accepted data, within explicit graph and byte bounds. Inaccessible, paginated, unknown, or unsupported data is marked partial. Provider-hidden reasoning is unavailable, and trace never changes continuation authority or the frozen `RunOutcome`. Ordinary optional trace failure does not change process exit, but failed required evidence publication in continuation finalization exits 1.
- Replay is display-only guidance from the accepted actual Phase 5 receipt in the same process. SEAM never auto-executes it and does not promise deterministic reproduction.

See [`src/docs/E2E_TESTING.md`](src/docs/E2E_TESTING.md) for the complete CLI, continuation matrix, artifact tree, timeout semantics, and optional integration checks. See [`src/docs/full_agent_io_logging_design.md`](src/docs/full_agent_io_logging_design.md) for raw-trace completeness and schema-v2 correlation boundaries.

Execution Results:
*   **Run status**: The terminal displays `E2E TEST PASSED`, `E2E PASS`, `E2E FINALIZATION FAILED`, or a failure. Authoritative details are in `./e2e-reports/src/e2e-v3-<run-id>/summary.json`. Migration failure exits 1; exit 2 is reserved for requested authorized-cleanup failure after a migration PASS.

*   **Migrated project**: Outputs are saved by default under the sibling directory `../output_projects/<project_name>_<timestamp>/`. You can override the default root with `MIGRATION_OUTPUT_PROJECTS_ROOT`, or pass `--output-dir` for this run.

*   **Migration report**: A folder named `migration_reports/` will be generated inside the migrated project, containing acceptance results, performance data, custom operator migration logs and build records.

*   **Runtime logs**: Working evidence is stored under `.sm-artifacts/` in the migrated project. The final report also contains telemetry, the resource manifest, optional raw trace, and finalization diagnostics. Share the matching `summary.json` when troubleshooting; `.sm-artifacts` is not a continuation checkpoint.

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
GPU Environment Detection -> Project Analysis -> Dependency Preparation -> Rule-based Migration -> Iterative Validation & Fix -> Custom Operator Resolution -> Report Generation -> Experience Evaluation & Refinement
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

- [User Guide](docs/User_Guide.md), usage, configuration and feature docs
- [FAQ](docs/FAQ.md), common issues and solutions
- [Contributing](docs/CONTRIBUTING.md), how to join development
- [Changelog](docs/CHANGELOG.md), version updates and release notes

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
