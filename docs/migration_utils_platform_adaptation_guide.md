# migration_utils 平台适配与工作流配置指南

> 面向首次在新加速器平台上引入 migration_utils 的工程师。本文档覆盖框架运行全流程、YAML 工作流配置方法、custom-op 专用路线的工作原理、性能与基线策略，以及逐步适配清单。

---

## 目录

1. [框架运行全流程](#1-框架运行全流程)
2. [YAML 工作流配置详解](#2-yaml-工作流配置详解)
3. [target_platform 与平台策略](#3-target_platform-与平台策略)
4. [性能验证策略](#4-性能验证策略)
5. [Custom-Op 路线深度解析](#5-custom-op-路线深度解析)
6. [普通入口路线 vs Custom-Op 路线](#6-普通入口路线-vs-custom-op-路线)
7. [适配新加速器平台 — 逐步清单](#7-适配新加速器平台-逐步清单)
8. [其他 GPU 平台适配要点](#8-其他-gpu-平台适配要点)
9. [首次端到端运行与产物检查清单](#9-首次端到端运行与产物检查清单)
10. [参考文件索引](#10-参考文件索引)

---

## 1. 框架运行全流程

migration_utils 是一个 YAML 驱动的多阶段自动化迁移框架。整体运行路径从命令行入口或 `Orchestrator` 开始，通过 `WorkflowExecutor` 执行各阶段，最终生成迁移报告。

### 1.1 入口点

框架支持两套执行路径：

| 入口 | 源文件 | 说明 |
|------|--------|------|
| `Orchestrator` | `src/core/orchestrator.py` | 经典编排器，依次执行 Phase 0→1、Phase 1.5、Phase 2→3、Phase 4、Phase 5、Phase 6 |
| `WorkflowExecutor` | `src/core/workflow_executor.py` | YAML 原生引擎，支持 `builtin`、`dispatch`、`loop`、`review`、`orchestration` 等 Phase 类型，含条件求值、钩子、遥测 |

`Orchestrator` 内部创建 `PhaseRunner`（负责 LLM 阶段）和 `RepairLoopEngine`（负责 Phase 5 执行-分析-修复循环），并将两者串联。

`WorkflowExecutor` 则可直接从 YAML 解析全部 Phase 定义并执行，条件跳转（`condition`）、输入映射（`input_mapping`）、变量解析（`VariableResolver`）均支持 `${...}` 和 `$.field` 语法。

### 1.2 阶段概览

```
Phase 0 (env_detect)      — 环境检测：Python 版本、PyTorch 版本、可用加速器
Phase 1 (project_analysis)— 项目分析：依赖、CUDA 模式、入口脚本、custom-op 识别
Phase 1.5 (constraint)    — 约束摘要（仅在用户提供约束时运行）
Phase 2 (venv_create)     — 创建虚拟环境并安装项目依赖
Phase 3 (entry_script)    — 确定/生成 Phase 5 的入口脚本和验证合约
Phase 3.5 (static_validate)— 静态校验 Phase 3 输出（入口脚本路径、合约字段）
Phase 4 (rule_migration)  — 规则化代码迁移（内置 Python 脚本，非 LLM）
Phase 5 (validation)      — 执行-分析-修复循环（repair loop），最多 N 次迭代
Phase 6 (report)          — 生成最终迁移报告
Phase 7 (experience)      — 经验评估与精炼（可选，由 experience 配置控制）
```

### 1.3 Phase 5 Repair Loop 内部结构

Phase 5 是核心验证和修复循环，由 `src/core/repair_loop.py` 中的 `RepairLoopEngine` 实现：

```
1. run_entry_script      — 执行 Phase 3 返回的 run_command
2. custom_op_final_gate  — 如果脚本 exit_code=0，验证 custom-op 最终证据门
3. analyze_error         — 若 exit_code≠0，error_analyzer 分类失败原因
4. repair_dispatch       — 根据分类结果（dependency/code/operator）路由到修复角色
5. fix_dependency / fix_code / fix_operator — 对应修复 agent 执行修复
6. 重复 1–5，直到 exit_code=0 或达到 max_iterations/stagnation
```

自定义算子项目的修复循环中，`operator_fixer` 会收到特殊的 custom-op 合约上下文（源清单、manifest、最终证据门 schema），确保每一行都产出真实的编译产物和运行时覆盖证明。

### 1.4 验证器

通用验证器：
- `validate_env_detect` — Phase 0
- `validate_project_analysis` — Phase 1
- `validate_venv` — Phase 2
- `validate_entry_script` — Phase 3
- `validate_entry_static` — Phase 3.5
- `validate_rule_migration` — Phase 4
- `validate_validation_final` — Phase 5（基本校验：success、iteration_count、errors）
- `validate_custom_op_final_gate` — Phase 5（custom-op 最终证据门，详见第 5 节）

---

## 2. YAML 工作流配置详解

工作流 YAML 文件位于 `src/workflows/`，由 `src/core/config.py` 中的 `load_workflow()` 加载。

### 2.1 顶层结构

```yaml
name: ppu_migration_auto_vllm018_smoke_baseaware_entryfix_keep
version: "2.0"
description: >
  CUDA to PPU automated migration workflow...

target_platform:              # 平台策略（见第 3 节）
  preset: ppu_cuda_compatible
  overrides: { ... }

execution_backend:            # 执行后端（容器/本地）
  mode: auto
  source: image
  runtime: docker
  images: [...]
  devices: [...]
  container_workdir: "/workspace"
  cleanup: false

globals:                       # 全局变量（可在条件/映射中引用）
  max_repair_iterations: 5
  stagnation_threshold: 3
  review_gate_enabled: false
  disable_custom_op_contract_injection: false  # 普通入口路线标志

experience:                    # 经验记忆系统
  enabled: false
  phase7_enabled: false

agents:                        # Agent 角色定义
  main_engineer: { role: "main_engineer", lifecycle: "persistent" }
  error_analyzer: { role: "error_analyzer", lifecycle: "persistent" }
  dependency_fixer: { role: "dependency_fixer", lifecycle: "persistent" }
  code_adapter: { role: "code_adapter", lifecycle: "persistent" }
  operator_fixer: { role: "operator_fixer", lifecycle: "persistent" }

hooks:                         # 生命周期钩子（workflow_start / workflow_end）
  workflow_start: [...]
  workflow_end: [...]

terminals:                     # 终止状态
  complete: "Migration complete"
  failed: "Migration failed"

phases: [...]                  # Phase 列表（按执行顺序）
sub_workflows: {...}           # 子工作流定义（如 repair_loop）
```

### 2.2 `execution_backend` 配置项

| 字段 | 类型 | 说明 |
|------|------|------|
| `mode` | `local` / `container` / `auto` | 执行模式。`auto` 自动检测运行时并 fallback 到 local |
| `source` | `image` / `existing_container` | 容器来源 |
| `runtime` | `docker` / `podman` | 容器运行时 |
| `image` | string | 单个镜像名 |
| `images` | list[string] | 镜像候选列表（`auto` 模式下 agent 从中选择） |
| `container_name` | string | 固定容器名（`existing_container` 模式必填） |
| `container_name_prefix` | string | 自动生成容器名的前缀 |
| `devices` | list[string] | 设备直通路径 |
| `volumes` | list[string] | 额外卷挂载 |
| `env_vars` | dict[string→string] | 容器内环境变量 |
| `container_workdir` | string | 容器工作目录（项目自动挂载于此） |
| `cleanup` | boolean | 工作流结束后是否删除容器 |

### 2.3 `globals` 关键字段

| 字段 | 说明 |
|------|------|
| `max_repair_iterations` | Phase 5 repair loop 最大迭代次数 |
| `stagnation_threshold` | 连续相同错误达到阈值后终止 repair loop |
| `review_gate_enabled` | 是否启用 LLM review gate（exit_code=0 后由 agent 审查） |
| `max_review_iterations` | review gate 单次最大改进迭代数 |
| `max_entry_script_revisions` | 允许 error_analyzer 提议修改 entry_script 的最大次数 |
| `disable_custom_op_contract_injection` | 禁止框架自动注入 `entry_script_kind: custom_op_full_validation`（见第 6 节） |

### 2.4 Phase 定义

每个 Phase 支持以下类型：

| `type` | 说明 |
|--------|------|
| `llm` | 向 agent session 发送 prompt，解析 JSON 输出 |
| `shell` | 执行 shell 命令，捕获 exit_code/stdout/stderr |
| `builtin` | 调用内置操作（`rule_based_migration`、`custom_op_final_gate` 等） |
| `python` | 调用 Python 函数 |
| `review` | LLM review gate |
| `dispatch` | 根据字段值路由到不同 Phase |
| `loop` | 子工作流循环 |
| `orchestration` | 调用编排 handler |

LLM Phase 示例：

```yaml
- id: phase_3_entry_script
  type: llm
  agent: main_engineer
  prompt_template: "phase_3_entry_script_ppu_container_baseaware_entryfix"
  validator: entry_script
  transitions:
    on_success: phase_35_static_validate
```

条件跳转：

```yaml
- id: phase_1_5_constraint_summary
  type: llm
  condition: "${context.USER_CONSTRAINTS} != ''"
  transitions:
    on_success: phase_2_venv_create
    on_skip: phase_2_venv_create      # 条件为 false 时跳过到该 Phase
```

输入映射：

```yaml
- id: phase_1_project_analysis
  input_mapping:
    project_dir: "${context.PROJECT_DIR}"
```

### 2.5 `sub_workflows` 与 `blocks`

子工作流 `repair_loop` 是 Phase 5 的核心：

```yaml
sub_workflows:
  repair_loop:
    id: repair_loop
    type: loop
    max_iterations: 5
    stagnation_threshold: 3
    stop_conditions:
      - condition: "$.script_exit_code == 0 and $.review_gate_enabled == false"
        status: "success"
      - condition: "$.stagnation_count >= 3"
        status: "stagnation"
    phases:
      - id: run_entry_script
        type: shell
        command: "${loop_vars.entry_script}"
        capture:
          exit_code: "script_exit_code"
          stdout: "script_stdout"
          stderr: "script_stderr"
      - id: custom_op_final_gate
        type: builtin
        condition: "$.script_exit_code == 0"
        params:
          operation: "custom_op_final_gate"
      - id: analyze_error
        type: llm
        agent: error_analyzer
        condition: "$.script_exit_code != 0 and $.stagnation_count < 3"
        prompt_template: "phase_error_recovery_container_ppu"
        ...
      - id: repair_dispatch
        type: dispatch
        route_field: "${error_analysis.repair_role}"
        routes:
          dependency_fixer: fix_dependency
          code_adapter: fix_code
          operator_fixer: fix_operator
      ...
```

`blocks` 定义了 review gate 被 reject 后的改进块：

```yaml
blocks:
  improvement_block:
    phases:
      - id: improvement_plan
        type: llm
        agent: error_analyzer
        prompt_template: "phase_review_improvement_container_ppu"
      - id: improvement_dispatch
        type: dispatch
        route_field: "${improvement_plan.repair_role}"
        ...
```

### 2.6 Prompt 模板命名约定

Prompt 模板文件位于 `src/prompts/`，命名约定：

```
phase_0_env_detect.md                            # 通用/NPU 模板
phase_0_env_detect_ppu.md                        # PPU 专用模板

phase_1_project_analysis.md
phase_1_project_analysis_ppu.md
phase_1_project_analysis_ppu_normal_entry_057.md # 普通入口变体

phase_2_venv_create.md
phase_2_venv_create_ppu_container_baseaware.md   # PPU 容器 + base-env 感知

phase_3_entry_script.md
phase_3_entry_script_ppu_container_baseaware_entryfix.md  # PPU 容器 + entryfix + 完整 custom-op 合约
phase_3_entry_script_ppu_normal_entry_057.md              # 普通入口（无 custom-op 合约）

phase_35_static_validate.md
phase_35_static_validate_ppu_baseaware.md
phase_35_static_validate_ppu_normal_entry_057.md

repair_dependency_fixer.md
repair_dependency_fixer_container.md             # 容器感知修复 prompt
repair_dependency_fixer_container_ppu.md         # PPU 专用容器修复 prompt

repair_code_adapter.md               repair_code_adapter_container.md
repair_code_adapter_container_ppu.md

repair_operator_fixer.md             repair_operator_fixer_container.md
repair_operator_fixer_container_ppu.md
```

`_container` 后缀表示该 prompt 包含容器执行环境上下文（container_project_dir 等变量）。

---

## 3. `target_platform` 与平台策略

平台策略由 `src/core/platform_policy.py` 统一管理。这是一个 YAML 驱动的、纯数据类的策略系统，无需外部配置文件。

### 3.1 数据结构

**`PlatformPolicy`** — 顶层策略对象：

```python
@dataclass(frozen=True)
class PlatformPolicy:
    id: str                         # 短标识符，如 "ppu_cuda_compatible"
    display_name: str               # 人类可读标签，如 "PPU (CUDA-Compatible)"
    custom_op_evidence: CustomOpEvidenceConfig  # 证据验证参数
    guidance_prefix: str            # 修复/操作指引中使用的前缀
    guidance_native_label: str      # 原生加速器标签
    guidance_native_framework: str  # 原生框架描述（如 "torch.cuda / PPU-compatible"）
    guidance_python_binary: str     # Python 二进制路径（默认 "python"）
```

**`CustomOpEvidenceConfig`** — 每平台 custom-op 证据验证参数：

```python
@dataclass(frozen=True)
class CustomOpEvidenceConfig:
    target_device_values: list[str]          # 接受的 target_device 字符串值
    positive_boolean_fields: list[str]       # 证明 custom 路径被执行的布尔字段
    artifact_path_tokens: list[str]          # 编译产物路径中必须出现的子串
    native_build_log_tokens: tuple[str, ...] # 构建日志中期望的区分大小写子串
    native_source_tokens: tuple[str, ...]    # 源文件中期望的 token
    native_binary_tokens: tuple[bytes, ...]  # 编译二进制产物中期望的字节 token
    native_artifact_fields: tuple[str, ...]  # 证明原生产物构建/存在的布尔字段
    build_log_error_message: str             # 构建日志证据不足时的错误信息
    binary_source_error_message: str         # 二进制/源码证据不足时的错误信息
    custom_op_evidence_policy: str           # 注入 prompt 的策略标识字符串
    performance_validation: str              # 性能验证模式（见第 4 节）
    performance_baseline_device_values: list[str]   # 接受的基线设备值
    performance_baseline_boolean_fields: list[str]  # 证明基线路径被执行的布尔字段
```

### 3.2 内建预设

当前内建预设列表（`BUILTIN_PRESETS`）：

| preset ID | display_name | 说明 |
|-----------|-------------|------|
| `npu_ascend` | Ascend NPU | 华为昇腾 NPU（传统 NPU/CANN/ACL 证据） |
| `ppu_cuda_compatible` | PPU (CUDA-Compatible) | CUDA 兼容 PPU（torch.cuda 保留） |
| `cuda_nvidia` | NVIDIA CUDA | 原生 NVIDIA CUDA |
| `musa_muxi` | MUXI MUSA | 摩尔线程 MUSA 架构 |
| `rocm_amd` | AMD ROCm | AMD ROCm/HIP |
| `mlu_cambrian` | Cambrian MLU | 寒武纪 MLU |
| `generic_accelerator` | Generic Accelerator | 通用加速器（宽松证据） |

### 3.3 YAML 中的使用方法

```yaml
# 方式一：使用内建预设（推荐）
target_platform:
  preset: ppu_cuda_compatible

# 方式二：使用内建预设 + 覆盖
target_platform:
  preset: ppu_cuda_compatible
  overrides:
    custom_op_evidence:
      performance_validation: presence_only
      performance_baseline_device_values:
        - cuda
        - gpu
        - torch_cuda
        - cpu
        - torch_cpu
      performance_baseline_boolean_fields:
        - cuda_baseline
        - baseline_cuda
        - cpu_baseline
        - baseline_cpu
```

### 3.4 策略解析流程

1. `WorkflowExecutor.__init__` 或 `Orchestrator.run_workflow` 调用 `resolve_policy(target_platform, workflow_name)`
2. 如果 YAML 中存在 `target_platform.preset`：从 `BUILTIN_PRESETS` 查找预设，应用 overrides
3. 如果 YAML 中无 `target_platform`：根据 `workflow_name` 前缀推断（`npu_migration*` → `npu_ascend`，`ppu_migration*` → `ppu_cuda_compatible`，否则 → `generic_accelerator`）
4. overrides 仅允许白名单字段：`id`、`display_name`、`custom_op_evidence.*`、`guidance_*`

### 3.5 策略注入点

策略对象被传递给以下组件：

| 组件 | 文件 | 用途 |
|------|------|------|
| `WorkflowExecutor` | `workflow_executor.py` | 条件求值、custom-op contract 注入判断 |
| `RepairLoopEngine` | `repair_loop.py` | 修复 prompt 中的平台专用指引 |
| `Orchestrator` | `orchestrator.py` | 选择 rule-based migrator（PPU 用 `PPURuleBasedMigrator`，其他用 `RuleBasedMigrator`） |
| `validate_custom_op_final_gate` | `validators/validate_validation_final.py` | 证据门中的平台原生 token 校验 |

---

## 4. 性能验证策略

`CustomOpEvidenceConfig.performance_validation` 支持三种模式。

### 4.1 模式概览

| 模式 | 说明 |
|------|------|
| `full` | 严格默认模式：要求 `baseline_seconds > 0`、`custom_seconds > 0`、`speedup_vs_baseline > 0` |
| `presence_only` | 要求时间和报告存在（`baseline_seconds > 0`、`custom_seconds > 0`），但不强制 speedup 字段为正。**所有其他门禁（无 fallback、source、runtime、原生证据）仍然生效** |
| `disabled` | 跳过性能验证。**所有其余门禁（无 fallback、source、runtime、原生证据、产物路径、构建日志、二进制/源码证据）仍然完整执行** |

### 4.2 CPU 基线策略

CPU 基线是一种性能比较手段，而非迁移目标的回退方案：

- **CPU 基线允许**：当 `performance_baseline_device_values` 包含 `"cpu"`、`"torch_cpu"` 时，性能报告可将 CPU 作为 baseline_device。这意味着你可以比较"目标加速器路线 vs CPU 路线"的性能。
- **CPU 基线不是 CPU fallback**：CPU 基线仅用于性能对比。custom/migrated 路线必须证明目标加速器/原生路线的执行。`no_fallback_no_zero_call_no_builtin_contamination` 证据中的所有 negative flag 必须显式为 `false`。
- 配置示例（YAML overrides）：

```yaml
target_platform:
  preset: ppu_cuda_compatible
  overrides:
    custom_op_evidence:
      performance_baseline_device_values:
        - cuda
        - gpu
        - torch_cuda
        - cpu
        - torch_cpu
      performance_baseline_boolean_fields:
        - cuda_baseline
        - baseline_cuda
        - cpu_baseline
        - baseline_cpu
```

---

## 5. Custom-Op 路线深度解析

Custom-op 路线是 migration_utils 对含有 CUDA/C++ 自定义算子的项目执行的完整验证路线。该路线不依赖入口脚本的 exit_code=0 作为唯一成功标准——它要求结构化的、可机器校验的最终证据门（`custom_op_final_gate.json`）。

### 5.1 触发条件

框架通过以下方式判断项目是否应走 custom-op 路线：

1. Phase 1（project_analysis）输出中包含 custom-op 信号：`custom_op_detected: true`、`custom_op_surface.custom_op_detected: true`、或任何 `CUSTOM_OP_REQUIRED_TERMS` 匹配
2. Phase 3 的 `entry_script_kind` 被设置为 `"custom_op_full_validation"`

**自动注入机制**：
- 在 `PhaseRunner._normalize_output` 中，如果 Phase 1/2 输出中有 custom-op 信号，且 `globals.disable_custom_op_contract_injection` 不为 `true`，框架自动设置 `entry_script_kind: "custom_op_full_validation"`
- Phase 3 prompt 包含完整的 custom-op 合约字段（见 5.2 节）

### 5.2 Phase 3 合约字段

当项目走 custom-op 路线时，Phase 3 prompt 要求 LLM 返回以下额外字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_script_kind` | string | 固定为 `"custom_op_full_validation"` |
| `reports_dir` | string | host-visible 绝对路径，通常 `{project_dir}/migration_reports` |
| `required_report_paths` | list[string] | 阶段 5 需产出的报告文件列表（含 `custom_op_final_gate.json`、`migration_manifest.json`、`performance.json`、`build.log` 等） |
| `required_checks` | list[string] | 必须执行的检查列表 |
| `operator_discovery_sources` | list[string] | 算子发现来源（必须包含 `source`、`bindings`、`wrappers`、`autograd`、`aliases`、`launch`、`setup`、`tests`） |
| `operator_inventory_schema` | object | 算子清单 schema（见下文） |
| `validation_obligations` | object | 验证义务 |
| `phase5_entry_script_revision_allowed` | boolean | 是否允许 operator_fixer 修改入口脚本 |

**`operator_inventory_schema` 结构**：

```json
{
  "semantic_rows": "one row per fine-grained source-discovered operator unit",
  "fine_grained_operator_units": "complete list of source-discovered units",
  "unit_identity": "stable per-unit identity",
  "variant_or_signature": "project-specific variant or signature",
  "native_operator_symbols": "native/exported symbols per row",
  "kernel_functions": "kernel functions per row",
  "kernel_launch_sites": "launch sites per row",
  "public_entry_mapping": "public API entry mapping per row",
  "inventory_granularity": "fine_grained"
}
```

### 5.3 最终证据门 `custom_op_final_gate.json`

`custom_op_final_gate.json` 是入口脚在 Phase 5 中产出的关键报告。验证器 `validate_custom_op_final_gate`（位于 `src/validators/validate_validation_final.py`）对其进行结构化校验。

**顶层字段**：

| 字段 | 要求 |
|------|------|
| `inventory_count` | 整数，必须等于 `manifest_entries` 等于 `closed_pass_entries`，且 > 0 |
| `manifest_entries` | 整数 |
| `closed_pass_entries` | 整数 |
| `remaining_entries` | 必须为 0 |
| `full_migration_status` | 必须为 `"FULL_PASS"` |
| `project_e2e_passed` | 必须为 `true` |
| `report_parity_passed` | 必须为 `true` |
| `rows` | 非空列表，长度必须等于 `manifest_entries` |
| `source_inventory` | 对象，含 `discovery_complete: true`、`discovery_sources_checked` 列表、`entries` |
| `performance_report` | 对象（除非 performance_validation=disabled） |

**每行（row）强制证据字段**：

每行必须包含以下 object/dict 类型的证据（不能是 string 或 scalar）：

1. **`opp_custom_op_artifact_evidence`** — 产物证据
   - `project_local: true` — 产物在项目目录内生成
   - `built: true` / `loaded: true` — 产物已构建/加载
   - `project_relative_path` — 项目相对路径（不能以 `.py` 结尾）
   - `build_provenance: { command, log_path }` — 构建溯源
   - 平台原生 token 校验：产物路径必须包含平台 artifact 子串（如 `ppu`、`cuda`、`ascend`、`musa` 等），且路径下必须存在非空编译二进制
   - 构建日志必须包含原生构建证据（如 `nvcc`、`ppuccl`、`aclrt` 等）
   - 二进制/源码中必须包含原生证据 token

2. **`adapter_evidence`** — 适配器证据
   - `imported: true`、`passed: true`

3. **`parity_evidence`** — 一致性证据
   - `verified: true`、`passed: true`

4. **`integration_e2e_evidence`** — 端到端集成证据
   - `project_api_invoked: true` — 通过项目/公共 API 调用
   - `custom_op_route_executed: true` — custom-op 路线被执行
   - `native_custom_op_route_executed: true` — 原生编译路线被执行

5. **`same_run_runtime_coverage`** — 运行时覆盖
   - `same_run: true` — 同次运行内
   - `custom_call_count > 0` — 自定义调用计数 > 0
   - `project_api_route: true`、`native_custom_op_route_executed: true`

6. **`performance_evidence`** — 性能证据（除非 performance_validation=disabled）
   - `baseline_seconds > 0`、`custom_seconds > 0`
   - `baseline_device`（字符串）、`custom_device`（字符串）
   - `project_api_invoked: true`
   - `speedup_vs_baseline > 0`（仅在 `full` 模式下强制）

7. **`no_fallback_no_zero_call_no_builtin_contamination`** — 无回退/零调用/内建污染
   - 必须是 object，所有以下 flag 显式为 `false`：
     - `fallback_detected: false`
     - `zero_call_detected: false`
     - `builtin_contamination_detected: false`
     - `baseline_only_detected: false`
     - `stub_detected: false`

**源清单（source_inventory）要求**：
- `discovery_complete: true`
- `discovery_sources_checked` 必须包含 `source`、`bindings`、`wrappers`、`autograd`、`aliases`、`launch`、`setup`、`tests`（不能含 `requirements_doc`）
- `entries` 必须与 manifest 行精确匹配（名称集合相等）
- 每个 entry 必须含 `native_operator_symbols`、`kernel_functions`、`source_evidence`
- 每个 entry 必须包含细粒度字段：`unit_identity`、`variant_or_signature`、`kernel_launch_sites`、`public_entry_mapping`、`inventory_granularity`
- `inventory_granularity` 必须为 `fine_grained`（不能是 `coarse`/`family_only`）

**manifest 匹配要求**：
- 验证器读取 `migration_reports/migration_manifest.json`（由入口脚本写入）
- `required_units` 列表必须与 gate rows 的名称集合完全一致
- `inventory_count`、`manifest_entries`、`closed_pass_entries`、`rows.length` 必须等于 `required_units.length`

**性能报告要求**（除非 disabled）：
- `performance_report.complete: true`
- `performance_report` 路径指向 `migration_reports/performance.json`
- `performance_report.unit_count` 等于 `manifest_entries`
- 每个 manifest 行在 performance_report 中有对应 entry，含 baseline_seconds、custom_seconds、device proof

### 5.4 常见失败模式与 operator_fixer 如何修复

| 失败模式 | 根因 | operator_fixer 应采取的行动 |
|----------|------|---------------------------|
| `full_migration_status != FULL_PASS` | 部分行未达标 | 定位剩余行，继续完成（不可降级或标记为 MVP） |
| `remaining_entries != 0` | 未关闭所有行 | 继续完成剩余行的证据 |
| `no_fallback` flag 为 true | 脚本中存在 CPU fallback | 移除所有 fallback 路径，强制走原生加速器路线 |
| `zero_call_detected` | custom-op 未被实际调用 | 修改入口脚本确保真正的 custom-op 调用 |
| `builtin_contamination_detected` | 使用了 torch 内建算子代替 custom-op | 替换为原生加速器 custom-op 实现 |
| 源清单不匹配 manifest | 发现不完全 | 补充缺失的源发现条目 |
| 构建日志无原生证据 | 编译链接未使用原生 SDK | 配置正确的编译器和链接标志 |
| 产物为 Python shim（`.py` 结尾） | 未生成编译产物 | 确保 CMake/setup.py 产出真实 `.so`/`.o` 文件 |
| 产物路径存在但为空/非 ELF | 编译失败 | 检查构建命令和编译环境 |
| `stub_detected` | 使用了桩/stub 占位实现 | 实现真实的算子逻辑 |
| `inventory_granularity` 为 coarse | 源清单粒度不足 | 拆分为细粒度 unit-level 行 |

### 5.5 不可接受的逃避策略

验证器拒绝以下逃避行为：

- **证据仅标记 shim**：文件或库名含 `_evidence_`、`stub`、`dummy`、`fake`、`placeholder`、`mock` 等，即使路径存在也拒绝
- **Python shim 冒充编译产物**：`artifact_type`/`kind`/`description` 含 python_shim、python_binding、delegates_to_python、source_only 等
- **合成/synthetic 标记**：`synthetic_only`、`monkeypatch_only`、`report_only`、`manifest_only`、`benchmark_only`、`mock_only`
- **报告声称完成但实际未达标**：`MVP_ONLY`、`PARTIAL`、`INCOMPLETE`、`FAILED` 等 blocking 状态
- **基线测量值来自元数据/诊断占位**：`diagnostic_only`、`report_only`、`not_measured`、`none`、`unknown`

---

## 6. 普通入口路线 vs Custom-Op 路线

### 6.1 两条路线对比

| 维度 | Custom-Op 路线 | 普通入口路线 |
|------|---------------|-------------|
| Phase 1 | 正常分析，检测到 custom-op 信号 | 正常分析，无 custom-op 信号 |
| Phase 3 | 返回完整 custom-op 合约字段 | 返回基础 `entry_script_path` + `run_command` |
| Phase 3.5 | 校验 custom-op 合约完整性 | 校验普通 headless/static 合规 |
| Phase 4 | 正常规则迁移 | 正常规则迁移 |
| Phase 5 | 入口脚本产出 `custom_op_final_gate.json`，final-gate 校验全部行 | 入口脚本正常执行，`custom_op_final_gate` builtin 自动跳过 |
| Phase 5 修复循环 | operator_fixer 接收完整合约上下文 | operator_fixer 接收普通修复上下文 |

### 6.2 如何配置普通入口路线

```yaml
globals:
  disable_custom_op_contract_injection: true
```

当此标志为 `true` 时：
- Phase 3 的 `_normalize_output` 不会自动注入 `entry_script_kind: custom_op_full_validation`
- Phase 3 prompt 应显式省略所有 custom-op 合约字段
- Phase 5 的 `custom_op_final_gate` builtin 检测到无合约字段时自动返回 `{skipped: true, passed: true}`
- 参考工作流：`ppu_migration_normal_entry_057_experiment.yaml`

### 6.3 禁止伪造"无 custom-op"

普通入口路线适用于**确实不含 CUDA/C++ 自定义算子**的项目。以下行为将导致验证失败：

- 项目含 custom-op 但 Phase 1 输出错误地标记 `custom_op_detected: false`
- Phase 3 使用否定匹配 pattern 绕过（如 `no custom operators found`）
- 手动设置 `disable_custom_op_contract_injection: true` 而项目实际含 custom-op

框架的 `CUSTOM_OP_NEGATIVE_PATTERNS` 会检测到显式的"无 custom-op"声明并将其视为明确的否定信号（即使其他字段有 custom-op 关键词）。

---

## 7. 适配新加速器平台 — 逐步清单

本节提供一份从零开始为新加速器平台接入 migration_utils 的完整清单。所有文件路径相对于 SEAM 仓库根目录。

### 7.1 步骤一：添加平台预设

**修改文件**：`src/core/platform_policy.py`

在 `BUILTIN_PRESETS` 字典中添加新预设。以 MUSA/MUXI 为例（已内建）：

```python
"musa_muxi": PlatformPolicy(
    id="musa_muxi",
    display_name="MUXI MUSA",
    custom_op_evidence=CustomOpEvidenceConfig(
        target_device_values=["musa", "muxi", "musa_gpu"],
        positive_boolean_fields=["musa_custom", "custom_musa", "musa_custom_invoked"],
        artifact_path_tokens=["/musa/", "musa_kernel", "musa_op", "musa_plugin", "muxi", "musart"],
        native_build_log_tokens=("musa", "muxi", "musart", "musacc", "musa_kernel"),
        native_source_tokens=("musa.h", "musart", "musa_runtime", "musa_kernel"),
        native_binary_tokens=(b"musa", b"muxi", b"musart", b"musacc"),
        native_artifact_fields=("musa_custom_op_artifact", "musa_custom_op_built", "musa_kernel_built", "musa_custom_op_loaded"),
        build_log_error_message="must contain MUSA/MUXI build or link evidence, not a CPU-only build",
        binary_source_error_message="must include independent MUSA binary or source evidence",
        custom_op_evidence_policy="require_real_musa_custom_op_artifacts",
    ),
    guidance_prefix="MUXI MUSA",
    guidance_native_label="MUXI GPU (MUSA)",
    guidance_native_framework="torch_musa / MUSA PyTorch primitives",
    guidance_python_binary="python",
),
```

**关键字段填写指南**：

| 字段 | 指南 |
|------|------|
| `target_device_values` | 列出 `.to(device)` 接受的字符串和 PyTorch 设备前缀 |
| `positive_boolean_fields` | 在最终 gate 报告中证明 custom path 被调用的布尔字段名 |
| `artifact_path_tokens` | 编译产物路径中特有的子串（厂商 SDK 目录、驱动前缀等） |
| `native_build_log_tokens` | 编译/链接命令中出现的厂商特有工具或库名（大小写不敏感匹配） |
| `native_source_tokens` | 源文件中引用的厂商头文件、API 函数、kernel 关键字符号 |
| `native_binary_tokens` | 编译二进制中出现的字节 token（ELF 符号表中的厂商标识） |
| `native_artifact_fields` | 最终 gate 报告中证明原生产物存在的布尔字段名 |
| `custom_op_evidence_policy` | 单行简短标识，注入 prompt 作为策略上下文 |

### 7.2 步骤二：创建平台专用工作流 YAML

**创建文件**：`src/workflows/{platform}_migration_v2_container.yaml`

参考 `ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml` 或 `npu_migration_v2_container.yaml`。

**必须修改的配置项**：

```yaml
name: {platform}_migration_container    # 以 {platform}_migration 开头以触发策略名称推断
target_platform:
  preset: {your_preset_id}              # 步骤一中添加的 preset ID

execution_backend:
  runtime: docker                       # 或 podman
  image: "YOUR_PLATFORM_IMAGE_HERE"     # 替换为实际容器镜像
  images:                               # 或 auto 模式下的候选列表
    - "registry.example.com/platform-pytorch:latest"
  container_name_prefix: "seam-{platform}"
  devices:                              # 厂商设备节点
    - /dev/{vendor_device0}
    - /dev/{vendor_device1}
  container_workdir: "/workspace"
  cleanup: true                         # 或 false（保留容器用于 E2E 验证）
```

**提示**：首次调试建议设置 `cleanup: false`，工作流结束后可手动进入容器检查产物。

### 7.3 步骤三：创建/适配 Prompt 模板

**创建目录**：`src/prompts/`（所有模板在此目录下）

**需要创建/复制的 prompt 模板**（以 `{platform}` 替代平台名）：

| Prompt 模板 | 说明 |
|-------------|------|
| `phase_0_env_detect_{platform}.md` | 环境检测：告知 agent 目标平台名称和 API 框架 |
| `phase_1_project_analysis_{platform}.md` | 项目分析：平台特定的依赖/模式检测 |
| `phase_2_venv_create_{platform}_container_baseaware.md` | 虚拟环境：平台 pip index/PyTorch wheel 指引 |
| `phase_3_entry_script_{platform}_container_baseaware_entryfix.md` | 入口脚本：保持 custom-op 合约证据 schema 不变 |
| `phase_35_static_validate_{platform}_baseaware.md` | 静态验证：平台特定语法检查 |
| `phase_5_review_container_{platform}.md` | Review gate：平台特定验收标准 |
| `phase_6_report_{platform}.md` | 最终报告：平台特定摘要 |
| `repair_dependency_fixer_container_{platform}.md` | 依赖修复：平台 pip conda 指引 |
| `repair_code_adapter_container_{platform}.md` | 代码适配：平台 API 替换规则 |
| `repair_operator_fixer_container_{platform}.md` | 算子修复：平台算子映射和构建指引 |
| `phase_error_recovery_container_{platform}.md` | 错误分析：平台特定错误分类 |
| `phase_review_improvement_container_{platform}.md` | 改进计划：平台特定改进策略 |

**Prompt 模板中必须替换的内容**：

- 平台名称（如 `PPU (CUDA-Compatible)` → `MUXI MUSA`）
- Python API 框架（如 `torch.cuda` → `torch_musa`）
- 设备检测命令和预期输出
- 编译器和构建工具链（如 `nvcc` → `musacc`）
- 算子库和头文件引用
- 性能测量和设备枚举方式
- **保持 custom-op 合约 evidence schema 不变**（evidence 字段名和结构是通用的）

**不需要修改的内容**：

- Custom-op 合约证据 schema（`opp_custom_op_artifact_evidence`、`adapter_evidence` 等字段名和结构是跨平台通用的）
- 最终 gate 报告结构（`full_migration_status`、`rows`、`source_inventory` 等）
- 修复循环工作机制
- 容器路径语义（host-visible vs container-visible）
- `_container` 后缀的通用容器执行环境上下文变量

### 7.4 步骤四：更新修复 Prompt 中的平台策略上下文

**修改文件**：`src/core/repair_loop.py`

`_operator_custom_op_guidance` 和 `_operator_generic_guidance` 函数已使用 `platform_policy` 动态生成平台专用指引。**通常无需修改这两个函数**，除非新平台有特殊的指引需求。

平台策略通过以下方式自动注入：
- `guidance_native_label` — 在修复指引中提及原生加速器名称
- `guidance_native_framework` — 原生 API 框架说明
- `performance_validation` — 性能验证模式提示

### 7.5 步骤五：选择 Rule-Based Migrator

**修改文件**：`src/core/orchestrator.py`

`Orchestrator._select_rule_based_migrator` 根据 `platform_policy.id` 选择 migrator：

```python
@staticmethod
def _select_rule_based_migrator(platform_policy: PlatformPolicy) -> RuleBasedMigrator:
    if platform_policy.id == "ppu_cuda_compatible":
        return PPURuleBasedMigrator()      # 保留 torch.cuda 行为
    return RuleBasedMigrator()             # 默认：CUDA → NPU 迁移
```

如果新平台需要特殊的规则迁移策略（如保留 `torch.cuda` 不变，类似于 PPU），在此处添加分支并创建对应的 migrator 类。

### 7.6 步骤六：添加测试

**修改文件**：`src/tests/test_platform_policy.py`

添加以下测试：

1. **预设存在性测试**：验证新 preset 在 `BUILTIN_PRESETS` 中存在且字段齐全
2. **策略推断测试**：验证 `_infer_policy_by_name` 对 `{platform}_migration*` 工作流名称的正确推断
3. **Token helper 测试**：验证 `get_artifact_path_tokens`、`get_native_build_log_tokens` 等函数返回正确的平台 token
4. **override 测试**：验证 YAML overrides 正确合并

**修改文件**：`src/tests/test_workflow_executor.py` 或新建

添加：

5. **工作流加载测试**：验证新 YAML 工作流文件可被正确解析和加载
6. **Custom-op gate 测试**：用平台专用 token 验证 `validate_custom_op_final_gate`

---

## 8. 其他 GPU 平台适配要点

以下要点适用于 MUSA/MUXI、ROCm/AMD、MLU/Cambrian 等平台。所有字段名和路径均使用通用术语。

### 8.1 目标设备 Token

在 `target_device_values` 中列出平台特定的 PyTorch 设备标识符：

```python
target_device_values=["musa", "muxi", "musa_gpu"]   # MUSA
target_device_values=["rocm", "amd", "hip", "gpu"]    # ROCm
target_device_values=["mlu", "cambrian", "cambricon"] # MLU
```

### 8.2 产物路径 Token

`artifact_path_tokens` 用于验证编译产物路径是否属于目标平台：

```python
# MUSA 示例
artifact_path_tokens=["/musa/", "musa_kernel", "musa_op", "muxi", "musart"]

# ROCm 示例
artifact_path_tokens=["/rocm/", "hip_kernel", "hip_op", "rocblas", "miopen"]

# MLU 示例
artifact_path_tokens=["/mlu/", "mlu_kernel", "mlu_op", "cambrian", "cnml", "cnrt"]
```

### 8.3 编译/构建日志 Token

`native_build_log_tokens` 用于匹配编译命令或构建日志输出：

```python
# MUSA — 匹配 musacc 编译器、musart 运行时等
native_build_log_tokens=("musa", "muxi", "musart", "musacc", "musa_kernel")

# ROCm — 匹配 hipcc、rocm、hip_runtime 等
native_build_log_tokens=("hipcc", "rocm", "hip_runtime", "rocblas", "hip", "amdgpu")

# MLU — 匹配 cncc、cnml、cnrt 等
native_build_log_tokens=("cncc", "cnml", "cnrt", "cambrian", "mlu", "bangc")
```

### 8.4 源码 Token

`native_source_tokens` 用于在项目源码中搜索平台原生 API 引用：

```python
# MUSA — 匹配 musa.h、musart、musa_runtime 等
native_source_tokens=("musa.h", "musart", "musa_runtime", "musa_kernel")

# ROCm — 匹配 hip_runtime.h、__global__、hip_kernel 等
native_source_tokens=("hip_runtime.h", "hip/hip_runtime.h", "__global__", "hip_kernel", "rocblas")

# MLU — 匹配 cnml.h、cnrt.h、cambrian、mlu_kernel、bangc 等
native_source_tokens=("cnml.h", "cnrt.h", "cambrian", "mlu_kernel", "bangc")
```

### 8.5 二进制 Token

`native_binary_tokens` 用于在编译产物（`.so`/`.o`/`.a`）的字节内容中验证平台标识：

```python
# MUSA
native_binary_tokens=(b"musa", b"muxi", b"musart", b"musacc")

# ROCm
native_binary_tokens=(b"hipcc", b"rocm", b"hip", b"amdgpu", b"ROCm")

# MLU
native_binary_tokens=(b"cncc", b"cnml", b"cnrt", b"cambrian", b"MLU")
```

### 8.6 Python 后端和 API 保留策略

不同平台对 Python API 的处理方式不同：

| 平台类型 | Python API 策略 | Rule-Based Migrator 行为 |
|----------|----------------|--------------------------|
| CUDA 兼容型（PPU、MUSA/MUXI） | **保留** `torch.cuda` 调用 | 使用 `PPURuleBasedMigrator` 或自定义 migrator，不转换 `torch.cuda` |
| 原生转换型（NPU Ascend、MLU Cambrian） | **转换** `torch.cuda` → `torch_npu` / `torch_mlu` | 使用 `RuleBasedMigrator`，执行 CUDA→NPU 规则转换 |
| HIP 兼容型（ROCm/AMD） | `torch.cuda` 可能通过 HIP 层工作 | 根据实际运行时选择 |

**关键**：在 prompt 模板中明确告知 agent 该平台的 API 策略。PPU prompt 中写"`torch.cuda` calls are expected and correct"，而 NPU prompt 中写"convert `torch.cuda` to `torch_npu`"。

### 8.7 容器设备直通

`execution_backend.devices` 列出需要从宿主机映射到容器的设备节点：

```yaml
# NPU Ascend 示例
devices:
  - /dev/davinci_manager
  - /dev/devmm_svm
  - /dev/hisi_hdc

# PPU 示例
devices:
  - /dev/alixpu
  - /dev/alixpu_ctl
  - /dev/alixpu_ppu0  # 到 ppu7

# MUSA 示例（假设）
devices:
  - /dev/musa
  - /dev/musa_ctl
```

### 8.8 容器镜像候选

为 `execution_backend.images` 提供至少 1 个（`auto` 模式下至少 2 个）可用的镜像候选。镜像必须包含：

- 目标平台的 PyTorch wheel（如 `torch_musa`、`torch_rocm`、`torch_mlu`）
- 平台 SDK（编译器、运行时库、驱动头文件）
- Python 3.8+

### 8.9 性能和基线策略调整

```yaml
target_platform:
  preset: {your_preset_id}
  overrides:
    custom_op_evidence:
      performance_validation: presence_only   # 首次 bring-up 建议使用
      performance_baseline_device_values:
        - cuda
        - gpu
        - torch_cuda
        # 如需 CPU 基线：
        - cpu
        - torch_cpu
```

首次 bring-up 建议使用 `presence_only` 降低性能门槛。后续稳定后可改为 `full`。

---

## 9. 首次端到端运行与产物检查清单

### 9.1 运行前检查

- [ ] 工作流 YAML 文件存在且可通过 `load_workflow()` 加载
- [ ] `target_platform.preset` 指向正确的内建或自定义预设
- [ ] 容器镜像可拉取或已存在于宿主机
- [ ] 设备节点存在且可访问（`ls -la /dev/{vendor_device}`）
- [ ] 测试项目已就绪（建议使用小型 CUDA demo 项目首次验证）
- [ ] `disable_custom_op_contract_injection` 根据项目实际情况正确设置

### 9.2 运行中观察

- [ ] Phase 0 环境检测输出正确识别目标平台和 PyTorch 版本
- [ ] Phase 1 项目分析正确识别依赖和 custom-op
- [ ] Phase 2 虚拟环境创建成功，pip install 使用正确的 index/wheel
- [ ] Phase 3 入口脚本路径存在且可读
- [ ] Phase 3.5 静态验证通过
- [ ] Phase 4 规则迁移完成（日志中的 files_migrated、replacement_counts 合理）
- [ ] Phase 5 repair loop 首次迭代执行正常
- [ ] 如果是 custom-op 路线：`custom_op_final_gate.json` 被写入且通过验证

### 9.3 运行后产物检查

**检查文件**：项目目录下的 `migration_reports/`

- [ ] `migration_reports/migration_manifest.json` — manifest 文件存在，`required_units` 非空且无重复
- [ ] `migration_reports/custom_op_final_gate.json`（custom-op 路线） — JSON 有效，`full_migration_status: FULL_PASS`、`remaining_entries: 0`、所有行含完整 evidence dict
- [ ] `migration_reports/performance.json` — 性能报告存在，entries 数量和名称与 manifest 匹配
- [ ] `migration_reports/build.log` — 或同等构建日志，含原生编译证据
- [ ] 编译产物（`.so`/`.o`/`.a` 文件）存在于项目目录下且为非空二进制
- [ ] 产物路径不含 `_evidence_`、`stub`、`dummy`、`fake`、`placeholder`、`mock` 等逃避标记

**检查 ArtifactStore 产物**：

- [ ] `output_projects/{project}/artifacts/{run_id}/` 目录包含所有阶段产物
- [ ] `artifacts/{run_id}/journal.jsonl` 记录完整，无阶段失败

### 9.4 常见首次运行问题

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| 容器无法启动 | 镜像不存在或设备节点权限不足 | 确认镜像路径，检查 `/dev/` 设备权限 |
| Phase 5 脚本 exit_code≠0 | 缺少平台依赖或 Python 路径错误 | 检查 Phase 2 的 venv 创建日志 |
| Custom-op gate 报告 `builtin_contamination_detected` | 算子实现仍使用 torch 内建算子 | 修改为平台原生实现 |
| 构建日志无原生证据 | 编译器未使用平台 SDK | 设置正确的 `CUDA_HOME`/`CC`/`CXX` 环境变量 |
| `remaining_entries > 0` | 部分 manifest 行未达标 | 检查未完成行的具体缺失 evidence 字段 |
| 工作流名称推断错误 | workflow name 未以 `{platform}_migration` 开头 | 使用显式 `target_platform.preset` 代替名称推断 |

---

## 10. 参考文件索引

### 核心框架

| 文件 | 说明 |
|------|------|
| `src/core/orchestrator.py` | 主编排器，串联 Phase 0→6 |
| `src/core/workflow_executor.py` | YAML 原生工作流执行引擎 |
| `src/core/phase_runner.py` | LLM Phase 执行器（PhaseRunner） |
| `src/core/repair_loop.py` | Phase 5 修复循环引擎（RepairLoopEngine） |
| `src/core/platform_policy.py` | 平台策略定义、预设、解析、token helper |
| `src/core/config.py` | 工作流 YAML 加载器 |
| `src/core/types.py` | 类型定义（PhaseDefinition、WorkflowDefinition 等） |
| `src/core/execution_backend.py` | 容器/local 执行后端 |

### 验证器

| 文件 | 说明 |
|------|------|
| `src/validators/validate_validation_final.py` | Phase 5 验证 + custom-op 最终证据门 |
| `src/validators/validate_entry_script.py` | Phase 3 入口脚本验证 |
| `src/validators/validate_entry_static.py` | Phase 3.5 静态验证 |
| `src/validators/validate_env_detect.py` | Phase 0 环境检测验证 |
| `src/validators/validate_project_analysis.py` | Phase 1 项目分析验证 |
| `src/validators/validate_venv.py` | Phase 2 虚拟环境验证 |
| `src/validators/validate_rule_migration.py` | Phase 4 规则迁移验证 |

### 工作流示例

| 文件 | 说明 |
|------|------|
| `src/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml` | PPU 生产工作流（custom-op 路线，entryfix prompt，含 CPU 基线） |
| `src/workflows/ppu_migration_normal_entry_057_experiment.yaml` | PPU 普通入口实验工作流（custom-op 禁用） |
| `src/workflows/npu_migration_v2_container.yaml` | NPU 容器工作流模板 |
| `src/workflows/npu_migration_v2.yaml` | NPU 本地工作流 |

### Prompt 模板

| 目录 | 说明 |
|------|------|
| `src/prompts/phase_0_env_detect*.md` | Phase 0 环境检测 prompt |
| `src/prompts/phase_1_project_analysis*.md` | Phase 1 项目分析 prompt |
| `src/prompts/phase_2_venv_create*.md` | Phase 2 虚拟环境创建 prompt |
| `src/prompts/phase_3_entry_script*.md` | Phase 3 入口脚本确定 prompt |
| `src/prompts/phase_35_static_validate*.md` | Phase 3.5 静态验证 prompt |
| `src/prompts/repair_*_fixer*.md` | Phase 5 修复 agent prompt |
| `src/prompts/phase_error_recovery*.md` | 错误分析 prompt |
| `src/prompts/phase_5_review*.md` | Review gate prompt |
| `src/prompts/phase_6_report*.md` | Phase 6 最终报告 prompt |

### 测试

| 文件 | 说明 |
|------|------|
| `src/tests/test_platform_policy.py` | 平台策略测试 |
| `src/tests/test_workflow_executor.py` | 工作流执行器测试 |
| `src/tests/test_phase_runner.py` | Phase 运行器测试 |
| `src/tests/test_repair_loop.py` | 修复循环测试 |

---

> 本文档适用于 SEAM migration_utils 框架 v2.x。新平台适配完成后，建议在 `tests/` 中添加对应平台策略测试，并在 `docs/` 中补充平台专用 E2E 指南。
