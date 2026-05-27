# SEAM 容器执行后端设计文档

> 本文档面向后续开发者，描述 SEAM 迁移框架（`SEAM/src/`）在 Phase 5 验证阶段引入 Docker/Podman 容器执行后端时的架构、兼容性契约和实现规划。

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [核心兼容性契约](#2-核心兼容性契约)
3. [三种执行模式](#3-三种执行模式)
   1. [3.1 local 模式（默认）](#31-local-模式默认)
   2. [3.2 container 模式（显式指定）](#32-container-模式显式指定)
      1. [3.2.1 source: image](#321-source-image从镜像创建新容器)
      2. [3.2.2 source: existing_container](#322-source-existing_container使用已存在的运行中容器)
   3. [3.3 auto 模式（Agent 选择）](#33-auto-模式agent-选择)
4. [配置与 YAML 扩展](#4-配置与-yaml-扩展)
   1. [4.1 execution_backend 顶层键](#41-execution_backend-顶层键新)
   2. [4.2 framework_defaults.yaml 扩展](#42-framework_defaultsyaml-扩展新段不影响现有)
   3. [4.3 Config 类型扩展](#43-config-类型扩展)
   4. [4.4 existing_container 早期检查](#44-existing_container-早期检查early-inspect)
5. [Prompt 策略：新增优于修改](#5-prompt-策略新增优于修改)
6. [容器执行后端架构](#6-容器执行后端架构)
7. [容器生命周期管理](#7-容器生命周期管理)
8. [Bind Mount 与 output_projects 隔离](#8-bind-mount-与-output_projects-隔离)
9. [Phase 5 结果捕获接口兼容性](#9-phase-5-结果捕获接口兼容性)
10. [包安装与验证命令的路由](#10-包安装与验证命令的路由)
11. [与 Agent 的交互边界](#11-与-agent-的交互边界)
12. [PPU/容器硬件考量](#12-ppu容器硬件考量)
13. [新增文件与类设计](#13-新增文件与类设计)
14. [YAML 扩展建议](#14-yaml-扩展建议)
15. [分阶段实现路线图](#15-分阶段实现路线图)
16. [测试清单](#16-测试清单)
17. [迁移计划](#17-迁移计划)
18. [风险与缓解措施](#18-风险与缓解措施)
19. [已知文件索引](#19-已知文件索引)

附录. [docker exec 接口说明](#附录-docker-exec-接口说明)

---

## 1. 背景与目标

SEAM 当前通过 `subprocess.run` 在宿主机本地执行 Phase 5 验证脚本（`repair_loop.py` `_prepare_entry_command`）。随着目标硬件（PPU、NPU 等）在容器环境中的广泛部署，需要将命令执行抽象为可插拔的"执行后端"，支持:

- 本地执行（当前行为，向后兼容）
- 容器执行（Docker/Podman，通过 `docker exec` / `docker run`）
- Agent 自动选择模式（根据环境探测自动决策）

本设计不修改任何已有 Prompt 模板或 Workflow YAML，所有容器相关功能通过新增文件实现。

---

## 2. 核心兼容性契约

### 2.1 默认行为零变更

**契约**: 任何现有 YAML（如 `npu_migration_v2.yaml`）和 `framework_defaults.yaml` 若不包含容器字段，其行为必须与当前完全一致。所有代码通过 `None` / `null` / 默认值走本地执行路径。

具体约束:

| 场景 | 行为 |
|------|------|
| YAML 无 `execution_backend` 字段 | 走本地 `subprocess` 路径 |
| `framework_defaults.yaml` 无容器配置 | 默认 `mode: local` |
| 旧版 YAML 在新框架代码上运行 | 零差异，不需要修改 |
| 不指定容器/镜像 | 不尝试任何容器操作 |

### 2.2 实现方式

在 `core/config.py` 加载 Workflow 时新增可选解析逻辑，仅在 YAML 顶层出现 `execution_backend` 键时才初始化容器后端。`repair_loop.py` 和 `workflow_executor.py` 中所有 `subprocess.run` 调用必须经过一个统一抽象层，该抽象层在默认配置下直接代理到 `subprocess`。

---

## 3. 三种执行模式

### 3.1 `local` 模式（默认）

- 与当前实现完全一致
- 使用 `subprocess.run` 在宿主机执行
- stdout/stderr 直接写入临时日志文件
- 适用于: 本地开发、无容器运行时、不需要隔离的场景

### 3.2 `container` 模式（显式指定）

`container` 模式支持两种子模式，通过 `source` 字段区分:

#### 3.2.1 `source: image`（从镜像创建新容器）

- 用户在 YAML 中指定容器运行时（`docker` 或 `podman`）、镜像名称和挂载配置
- 框架代码在初始化时**确定性创建**新容器，使用唯一名称
- 所有命令通过 `docker exec` 或 `podman exec` 执行
- 适用于: 需要硬件访问权限 (--device)、已知基础镜像、复现性要求

```yaml
execution_backend:
  mode: container
  source: image                # 从镜像创建新容器
  runtime: docker
  image: nvcr.io/nvidia/pytorch:24.05-py3
  container_name_prefix: "seam-migration"
  devices:
    - /dev/davinci_manager
    - /dev/devmm_svm
  volumes:
    - "${PROJECT_DIR}:/workspace"
  env_vars:
    ASCEND_VISIBLE_DEVICES: "0"
    LD_LIBRARY_PATH: "/usr/local/Ascend/driver/lib64"
```

#### 3.2.2 `source: existing_container`（使用已存在的运行中容器）

- 用户在 YAML 中指定**已有容器名称或 ID**，框架不创建新容器
- 框架在启动时执行早期检查（详见第 4.4 节），确认容器存在、正在运行、满足挂载和设备假设
- 所有命令通过 `docker exec` 或 `podman exec` 发送到该已有容器
- 框架**不自动清理**已有容器（cleanup 字段被忽略）
- 适用于: 开发者已手动启动并配置好的长期容器、调试场景、复用已有环境

```yaml
execution_backend:
  mode: container
  source: existing_container   # 使用已有运行中容器
  runtime: docker
  container_name: "my-dev-env-01"
  # image, container_name_prefix, cleanup 等字段在此模式下无意义
  # volumes/env_vars 用于验证假设，不用于创建容器
  volumes:
    - "/data/models:/data/models:ro"
```

### 3.3 `auto` 模式（Agent 选择）

- 框架执行环境探测（检查 Docker/Podman 可用性和硬件驱动）
- Phase 0 环境探测新增容器可用性子检测
- 如果探测到容器运行时和硬件驱动，自动选择 `container`；否则回退 `local`
- Agent 可在 Phase 0 输出中选择覆盖

适用于: 未知目标环境、需要灵活性的场景。

---

## 4. 配置与 YAML 扩展

### 4.1 `execution_backend` 顶层键（新）

所有新增字段均为**可选**。缺失时默认 `mode: local`。`container` 模式下 `source` 字段区分"从镜像创建新容器"与"使用已有运行中容器"两种行为。

#### 4.1.1 `source: image`（从镜像创建新容器）

```yaml
# 示例: workflows/npu_migration_v2_container.yaml
name: npu_migration_container
version: "2.0"
description: "CUDA to Ascend NPU automated migration workflow with container execution"

execution_backend:
  mode: container              # local | container | auto
  source: image                # image | existing_container；未指定时默认 image
  runtime: docker              # docker | podman
  image: "ascendhub:24.03-pytorch"
  container_name_prefix: "seam-migration"
  devices:                     # 硬件设备直通
    - /dev/davinci_manager
    - /dev/devmm_svm
    - /dev/hisi_hdc
  volumes:                     # 额外挂载（PROJECT_DIR 默认挂载）
    - "/data/models:/data/models:ro"
  container_workdir: "/workspace"   # 容器内工作目录，推荐固定路径
  env_vars:                    # 容器内环境变量
    ASCEND_VISIBLE_DEVICES: "0"
    LD_LIBRARY_PATH: "/usr/local/Ascend/driver/lib64"
  network_mode: host           # 网络模式
  runtime_flags:               # docker/podman 原始标志
    - "--cap-add=SYS_PTRACE"
```

#### 4.1.2 `source: existing_container`（使用已存在的运行中容器）

```yaml
# 示例: workflows/npu_migration_existing.yaml
name: npu_migration_existing
version: "2.0"
description: "CUDA to Ascend NPU migration with existing container"

execution_backend:
  mode: container
  source: existing_container     # 使用已有容器
  runtime: docker
  container_name: "my-dev-env-01"  # 必填：已有容器名称或 ID
  # image、container_name_prefix、cleanup、volumes（创建用途）在此模式下忽略
  # env_vars 在 early_inspect 中用于验证假设，不主动设置
  required_env_vars:             # 容器内必须存在的环境变量（early_inspect 校验）
    - ASCEND_VISIBLE_DEVICES
  required_devices:              # 容器内必须存在的安全设备路径（early_inspect 校验）
    - /dev/davinci_manager
```

### 4.2 `framework_defaults.yaml` 扩展（新段，不影响现有）

```yaml
# config/framework_defaults.yaml（新增段）
framework:
  # ... 现有字段不变

  execution_backend:
    mode: local                  # 默认值，与当前行为一致
    source: image                # image | existing_container，默认 image
    runtime: docker
    container_timeout: 7200      # 容器命令超时，秒
    cleanup: true                # 完成后自动销毁容器（仅 source=image 有效）
    log_driver: json-file
    log_max_size: "50m"
```

### 4.3 Config 类型扩展

在 `core/types.py` 或新建 `core/container_config.py` 新增数据类:

```python
@dataclass
class ContainerConfig:
    """Optional container execution configuration from YAML."""
    mode: str = "local"                          # local | container | auto
    source: str = "image"                        # image | existing_container
    runtime: str = "docker"                      # docker | podman
    image: str | None = None                     # 仅 source=image 有效
    container_name: str | None = None            # 仅 source=existing_container 有效
    container_name_prefix: str = "seam-migration"
    devices: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    required_env_vars: list[str] = field(default_factory=list)   # early_inspect 校验
    required_devices: list[str] = field(default_factory=list)    # early_inspect 校验
    container_workdir: str = "/workspace"                        # 容器内工作目录
    network_mode: str | None = None
    runtime_flags: list[str] = field(default_factory=list)
    timeout: int = 7200
    cleanup: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ContainerConfig":
        if raw is None:
            return cls(mode="local")
        mode = str(raw.get("mode", "local"))
        if mode not in ("local", "container", "auto"):
            raise ValueError(f"Invalid execution_backend.mode: {mode!r}")
        if mode == "local":
            return cls(mode="local")
        source = str(raw.get("source", "image"))
        if source not in ("image", "existing_container"):
            raise ValueError(f"Invalid execution_backend.source: {source!r}")
        if source == "existing_container" and not raw.get("container_name"):
            raise ValueError(
                "execution_backend.container_name is required when source=existing_container"
            )
        return cls(
            mode=mode,
            source=source,
            runtime=str(raw.get("runtime", "docker")),
            image=raw.get("image"),
            container_name=raw.get("container_name"),
            container_name_prefix=str(raw.get("container_name_prefix", "seam-migration")),
            devices=list(raw.get("devices", [])),
            volumes=list(raw.get("volumes", [])),
            env_vars={str(k): str(v) for k, v in raw.get("env_vars", {}).items()},
            required_env_vars=list(raw.get("required_env_vars", [])),
            required_devices=list(raw.get("required_devices", [])),
            container_workdir=str(raw.get("container_workdir", "/workspace")),
            network_mode=raw.get("network_mode"),
            runtime_flags=list(raw.get("runtime_flags", [])),
            timeout=int(raw.get("timeout", 7200)),
            cleanup=bool(raw.get("cleanup", True)),
        )
```

### 4.4 `existing_container` 早期检查（Early Inspect）

当 `source: existing_container` 时，框架**必须**在首次执行任何命令前对目标容器执行早期检查。这是 fail-fast 机制，确保后续所有操作在可控环境中进行。

#### 4.4.1 检查流程

1. **容器存在性检查**: 运行 `docker inspect <container_name>`（或 `podman inspect`）
   - 如果容器不存在 → 抛出 `ContainerNotFoundError` 并终止 workflow
2. **运行状态检查**: 检查 `docker inspect` 返回的 `State.Status` 字段
   - 如果 `State.Status != "running"` → 抛出 `ContainerNotRunningError` 并终止 workflow
3. **Bind Mount 验证**: 解析 `docker inspect` 中的 `Mounts` 信息
   - 如果 `PROJECT_DIR` 对应的宿主机目录未在容器挂载列表中 → 记录 warning（如果 YAML 声明了 volumes）或继续（不强制）
4. **设备直通验证**: 如果 YAML 声明了 `required_devices`，检查宿主 `/dev/` 路径是否在容器中可用
   - 检查 `/sys/class` 或 `docker inspect` 的 `HostConfig.Devices` 字段
5. **环境变量验证**: 如果 YAML 声明了 `required_env_vars`，执行 `docker exec <container> env` 并比对
   - 缺失必需变量 → 记录 warning（不强制 fail，仅辅助诊断）

#### 4.4.2 检查结果处理

| 检查结果 | 行为 |
|---------|------|
| 容器不存在 | **fail-fast**：抛出异常，终止 workflow |
| 容器未运行 | **fail-fast**：抛出异常，终止 workflow |
| Bind Mount 缺失 | Warning 日志，继续执行 |
| 设备缺失 | Warning 日志，继续执行 |
| 环境变量缺失 | Warning 日志，继续执行 |

#### 4.4.3 实现要点

```python
def _check_existing_container(self) -> None:
    """Fail-fast inspection for source=existing_container mode."""
    cname = self.config.container_name
    runtime = self._runtime_cmd  # "docker" or "podman"

    # Step 1 & 2: inspect
    result = subprocess.run(
        [runtime, "inspect", "--format", "{{.State.Status}}", cname],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise ContainerNotFoundError(
            f"Container '{cname}' does not exist. "
            f"stderr: {result.stderr.strip()}"
        )
    status = result.stdout.strip()
    if status != "running":
        raise ContainerNotRunningError(
            f"Container '{cname}' exists but status is '{status}', expected 'running'"
        )

    # Step 3: Mounts validation (optional)
    mounts_result = subprocess.run(
        [runtime, "inspect", "--format", "{{json .Mounts}}", cname],
        capture_output=True, text=True, timeout=30
    )
    # parse JSON, verify expected paths

    # Step 4: Required devices (optional)
    if self.config.required_devices:
        for dev in self.config.required_devices:
            check = subprocess.run(
                [runtime, "exec", cname, "test", "-e", dev],
                capture_output=True, timeout=10
            )
            if check.returncode != 0:
                logger.warning("Required device %s not found in container %s", dev, cname)

    self._container_id = cname  # Use the given name directly
    self._container_validated = True
```

---

## 5. Prompt 策略：新增优于修改

### 5.1 契约

**不修改任何已有 Prompt 模板**。所有容器相关的指令通过新增 Prompt 文件注入。

已有 Prompt 模板 (`prompts/phase_0_env_detect.md` 等) **不做任何改动**。

### 5.2 新增 Prompt 文件计划

```
src/prompts/
├── container_env_detect_v2.md          # Phase 0 容器增强探测（不替换原有 phase_0_env_detect.md）
├── container_phase_5_entry.md          # Phase 5 容器模式入口脚本执行提示
├── container_dependency_fix.md         # 容器模式下的依赖修复提示
└── container_troubleshooting.md        # 容器排障指南（runtime skills 可选注入）
```

### 5.3 新增 Workflow YAML 变体

不修改 `npu_migration_v2.yaml`，而是新建容器变体:

```
src/workflows/
├── npu_migration_v2.yaml               # 现有，本地执行，永不修改
├── npu_migration_v2_container.yaml     # NPU 容器变体
├── ppu_migration_v1.yaml               # PPU 本地（未来新增）
├── ppu_container_migration_v1.yaml     # PPU 容器变体（未来新增）
└── npu_migration_v2_auto.yaml          # auto 模式变体
```

容器变体的 `execution_backend` 节指定所需参数，其余 `phases` 定义可以完全复用或通过 YAML anchor/引用机制。

### 5.4 Prompt 加载机制

现有 `PromptLoader` (`core/prompt_loader.py`) 从 `prompts/{phase_id}.md` 加载模板。容器模式通过以下之一注入额外提示:

1. 容器变体 YAML 指向新的 `prompt_template` 值（如 `container_phase_5_entry`），对应新增的 `.md` 文件
2. 框架代码在检测到容器模式时，在已有 prompt 后追加容器上下文段落（追加策略，不修改原文）

推荐方案 1，即容器变体 YAML 独立指定 prompt 模板。

---

## 6. 容器执行后端架构

### 6.1 统一抽象层

所有执行操作（Phase 5 脚本运行、包安装、状态检查）统一通过 `ExecutionBackend` 接口。`ContainerBackend` 内部根据 `config.source` 区分"从镜像创建新容器"与"接入已有容器"的行为:

- `source: image`: 首次 `run()` 调用时 `_ensure_container()` 创建新容器；`cleanup()` 时销毁
- `source: existing_container`: `_check_existing_container()` 在初始化时执行 early inspect；`run()` 直接使用已有容器；`cleanup()` 为空操作

```python
# core/execution_backend.py（新增）

from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass
class ExecResult:
    """统一执行结果，与当前 subprocess 接口兼容。"""
    exit_code: int
    stdout: str
    stderr: str
    duration: float

class ExecutionBackend(Protocol):
    """执行后端抽象接口。"""

    def run(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        ...

    def is_available(self) -> bool:
        """检测后端是否可用（docker/podman 是否存在）。"""
        ...

class LocalBackend:
    """现有 subprocess 实现的直接映射。"""
    def run(self, command, cwd=None, env=None, timeout=None) -> ExecResult:
        # 内部就是 subprocess.run(cmd_argv, cwd=..., shell=..., timeout=...)
        ...
    def is_available(self) -> bool:
        return True

class ContainerBackend:
    """Docker/Podman 容器执行。
    根据 config.source 区分两种行为:
    - source=image: 首次 run() 时确定性创建新容器
    - source=existing_container: 初始化时 early inspect 接入已有容器
    """
    def __init__(self, config: ContainerConfig):
        self.config = config
        self._container_id: str | None = None
        self._runtime_cmd = "docker" if config.runtime == "docker" else "podman"
        self._initialized = False

    def _ensure_container(self) -> str:
        if self._container_id:
            return self._container_id
        if self.config.source == "existing_container":
            self._check_existing_container()
            return self._container_id
        # source=image: 确定性 docker run
        ...

    def _check_existing_container(self) -> None:
        """Early inspect for source=existing_container: 验证容器存在且 running。"""
        ...

    def run(self, command, cwd=None, env=None, timeout=None) -> ExecResult:
        container_id = self._ensure_container()
        # 构建 docker exec 命令
        exec_cmd = [self._runtime_cmd, "exec", "-i"]
        if self.config.container_workdir:
            exec_cmd.extend(["-w", self.config.container_workdir])
        exec_cmd.extend([container_id, "bash", "-c", command])
        # subprocess.run(exec_cmd, capture_output=True, timeout=...)
        ...

    def is_available(self) -> bool:
        # 检查 docker/podman 可执行性
        ...
```

### 6.2 为什么容器创建必须是框架代码确定性行为

**容器创建不应委托给 Agent**，原因如下:

1. **安全性**: Agent 构造的 `docker run` 命令可能包含危险参数（如 `--privileged`、挂载 `/`、破坏性环境变量）。框架代码必须控制所有安全边界。
2. **复现性**: 每次执行必须产生相同配置的容器。Agent 每次可能产生不同命令，导致非确定性结果。
3. **可测试性**: 确定性创建意味着可以 Mock、可以编写集成测试。Agent 动态创建的容器无法被可靠测试。
4. **生命周期管理**: 容器需要在迁移开始前提前创建并在结束后确定性地清理。由 Agent 负责会引入泄漏风险。
5. **Bind Mount 准确性**: 框架代码确切知道 `output_projects/<project>_<timestamp>` 路径，必须自动正确挂载。Agent 不知道这个路径。

实现上，`ContainerBackend._ensure_container()` 在第一次 `run()` 调用时创建容器:

```bash
docker run -d \
  --name seam-migration-<run_id> \
  --device /dev/davinci_manager:/dev/davinci_manager \
  --device /dev/devmm_svm:/dev/devmm_svm \
  --device /dev/hisi_hdc:/dev/hisi_hdc \
  -v /host/output_projects/project_12345:/output_projects/project_12345 \
  -v /data/models:/data/models:ro \
  -e ASCEND_VISIBLE_DEVICES=0 \
  -e LD_LIBRARY_PATH=/usr/local/Ascend/driver/lib64 \
  --network host \
  --cap-add=SYS_PTRACE \
  --log-driver=json-file --log-opt max-size=50m \
  ascendhub:24.03-pytorch \
  tail -f /dev/null
```

容器内运行 `tail -f /dev/null` 保持运行，所有后续命令通过 `docker exec` 发送。

---

## 7. 容器生命周期管理

`container` 模式下根据 `source` 字段的不同，生命周期阶段有所差异:

### 7.1 `source: image` 生命周期

```
[初始化] → [创建容器] → [执行命令 x N] → [结果捕获] → [清理容器]
```

1. **初始化**: `Orchestrator` 加载 YAML 解析 `execution_backend`，实例化 `ContainerBackend(config.source="image")`
2. **创建容器**: `ContainerBackend._ensure_container()` 在首次 `run()` 调用时以阻塞方式创建
3. **执行命令**: 通过 `docker exec <container_id> bash -c "<command>"` 执行，捕获 stdout、stderr、exit code
4. **结果捕获**: 接口统一返回 `ExecResult(exit_code, stdout, stderr, duration)`
5. **清理**: `Orchestrator.run_workflow` 的 `finally` 块调用 `ContainerBackend.cleanup()`

### 7.2 `source: existing_container` 生命周期

```
[初始化] → [Early Inspect 检查] → [接入已有容器] → [执行命令 x N] → [结果捕获] → [跳过清理]
```

1. **初始化**: `Orchestrator` 加载 YAML，实例化 `ContainerBackend(config.source="existing_container")`
2. **Early Inspect 检查**: `_check_existing_container()` 阻塞执行，验证容器存在性、运行状态、设备/环境变量假设
   - 检查失败 → fail-fast 终止 workflow
   - 检查通过 → 记录 `_container_id` 并继续
3. **执行命令**: 通过 `docker exec <existing_container_name> bash -c "<command>"` 执行
4. **结果捕获**: 同 `source: image`
5. **清理**: `cleanup()` 检查 `source != "image"` 后直接返回，**不销毁已有容器**

### 7.3 清理策略（两种 source 统一）

```python
def cleanup(self) -> None:
    if self.config.source == "existing_container":
        # 已有容器不由框架管理生命周期
        return
    if not self.config.cleanup or not self._container_id:
        return
    # docker stop + docker rm
    # 如果 --rm 已传入，rm 可跳过
    ...
```

清理失败不应阻塞主流程，仅记录 warning 日志。

### 7.4 并发与独占

- `source: image`: 每个迁移运行创建独立容器（`container_name_prefix + run_id`）
- `source: existing_container`: 由用户指定已有容器，框架不保证独占；开发者需自行确保多运行不冲突
- `docker exec` 一律是串行调用（与当前 `subprocess.run` 的 sync 行为对齐）

---

## 8. Bind Mount 与 output_projects 隔离

### 8.1 E2E Harness 拷贝路径

E2E harness 将源项目拷贝至 `output_projects/<project_name>_<timestamp>/`。这个拷贝目录就是迁移工作的"项目根目录"。

### 8.2 Bind Mount 策略

推荐做法: 将宿主机的项目目录映射到容器内固定工作目录 `/workspace`:

```
-v <output_projects_dir>/<project>_<timestamp>:/workspace:rw
```

这样做的效果:

- Agent 在容器内修改文件（如修复代码、修改依赖），写入的是 bind mount 中的文件
- 由于是 bind mount (不是 copy)，修改**直接反映在宿主机文件系统**上
- 后续 `docker exec` 调用（以 `/workspace` 为 CWD）能看到最新文件状态
- Phase 6 报告生成和 artifact 保存可以在宿主机侧正常读取结果
- `/workspace` 作为固定容器内路径，不依赖宿主机实际路径长度和字符集

同路径挂载（`<host_path>:<host_path>:rw`）在功能上也能工作，但**可移植性较差**：

- 宿主机路径可能包含特殊字符或超长路径，在容器内未必能正确处理
- Windows/macOS → Linux 容器场景下同路径挂载不可行
- 框架代码需要处理 CWD 路径转换（宿主机路径 → 容器内等价位）
- 因此**不推荐**同路径挂载，仅在兼容历史脚本时作为备选

### 8.3 关键路径映射

| 宿主机路径 | 容器内路径 | 说明 |
|------------|-----------|------|
| `output_projects/<project>_<ts>/` | `/workspace` | **推荐**: 固定工作目录，bind mount:rw |
| `output_projects/<project>_<ts>/` | 同路径 | 备选（不推荐）: 同路径挂载:r/w |
| `workspace_root()` | `/workspace_root` 或省略 | 通常不需要挂载；prompt 由框架注入，非容器内读取 |
| `/data/models/` | `/data/models/:ro` | 可选模型数据挂载 |

### 8.4 CWD 处理

`ContainerBackend.run()` 需要根据 `container_workdir` 自动处理命令的工作目录:

```python
def run(self, command, cwd=None, env=None, timeout=None) -> ExecResult:
    container_id = self._ensure_container()
    # cwd 在容器中需要解析:
    #   如果 cwd 是宿主机 output_projects 路径 → 映射为 /workspace + 相对偏移
    #   如果 container_workdir 已设置（默认 /workspace） → 使用 -w 标志
    exec_cmd = [self._runtime_cmd, "exec", "-i"]
    if self.config.container_workdir:
        exec_cmd.extend(["-w", self.config.container_workdir])
    exec_cmd.extend([container_id, "bash", "-c", command])
    ...
```

---

## 9. Phase 5 结果捕获接口兼容性

### 9.1 当前接口

`repair_loop.py` 的 `run()` 方法通过 `subprocess.run` 捕获:

- `exit_code` → `completed.returncode`
- `stdout` → 从临时 log 文件读取
- `stderr` → 从临时 log 文件读取
- `duration` → `time.monotonic() - run_start`

`workflow_executor.py` 中 `sub_workflows.repair_loop.phases.run_entry_script` 也定义了相同的 capture 映射:

```yaml
capture:
  exit_code: "script_exit_code"
  stdout: "script_stdout"
  stderr: "script_stderr"
  duration: "script_duration"
```

### 9.2 容器后端兼容性

`docker exec` 和 `docker run` 同样提供 exit code、stdout、stderr:

```bash
# docker exec
docker exec -i <container> bash -c "<cmd>"
# exit code: bash 返回值 ($?)
# stdout: 标准输出
# stderr: 标准错误

# docker run (非 daemon 模式)
docker run --rm <image> bash -c "<cmd>"
# 同上
```

因此容器后端只需要:

1. 执行 `docker exec` / `docker run`
2. 捕获 stdout/stderr 为 bytes，decode 为 str
3. 提取 exit code
4. 计算 duration
5. 返回 `ExecResult(exit_code, stdout, stderr, duration)`

Phase 5 的 `RepairLoopEngine` **无需修改接口签名**，只需将内部 `subprocess.run` 替换为对 `ExecutionBackend.run()` 的调用。`script_exit_code`、`script_stdout`、`script_stderr` 等所有下游变量保持不变。

### 9.3 实现要点

`repair_loop.py` 中 `_prepare_entry_command` 和 `self.run()` 内的 `subprocess.run` 调用需要替换:

```python
# 旧代码:
completed = subprocess.run(
    cmd_argv, stdout=..., stderr=..., cwd=script_cwd, shell=True/False, timeout=..., env=run_env,
)

# 新代码:
result = self.exec_backend.run(
    command=" ".join(cmd_argv),   # 或 cmd_argv 列表
    cwd=script_cwd,
    env=run_env,
    timeout=entry_script_timeout,
)
# result.exit_code → completed.returncode
# result.stdout → final_stdout
# result.stderr → final_stderr
```

---

## 10. 包安装与验证命令的路由

### 10.1 问题

Phase 2 (venv_create) 和 Phase 5 的修复循环中可能包含:

- `pip install torch-npu ...`
- `apt-get install ...`
- 脚本验证命令

如果不路由到容器内执行，这些操作会在宿主机生效，容器内的环境仍然缺失依赖，导致容器内脚本执行失败。

### 10.2 解决方案

所有"影响环境"的 shell 命令必须通过 `ExecutionBackend.run()` 执行:

```python
# 在 repair_loop.py 或 workflow_executor.py 中:
# 不再直接调用 subprocess.run
# 统一走:
result = self.exec_backend.run("pip install torch-npu ...")
```

具体改造点:

| 位置 | 当前行为 | 容器模式行为 |
|------|---------|-------------|
| `repair_loop.py` 脚本执行 | `subprocess.run` | `exec_backend.run()` |
| `workflow_executor.py` shell phase | `subprocess.run` | `exec_backend.run()` |
| Phase 2 venv 创建 | 本地创建 venv | 容器内执行 venv 相关命令 |
| 包安装 | `subprocess` | `exec_backend.run()` |

### 10.3 注意事项

- **Local 模式**: `exec_backend.run()` 在 `LocalBackend` 中原样调用 `subprocess.run`，行为不变
- **容器模式**: 命令在容器内执行，安装生效于容器环境
- **混合场景**: 如果某些安装需要在宿主机执行（如 GPU 驱动），需在 YAML 中标注 `backend: host` 并由框架判断

---

## 11. 与 Agent 的交互边界

### 11.1 Agent 不应知道容器细节

Agent 的视角始终是"我在操作一个项目目录"。它不知道也不应该知道:

- 容器是否存在
- 正在使用 `docker exec` 还是 `subprocess`
- 命令在宿主机还是容器内执行

Prompt 模板对容器模式不敏感，除非使用专门的容器 variant prompt（如 `container_phase_5_entry.md`）。

### 11.2 框架负责

- 容器创建、销毁（自动）
- bind mount 路径映射（自动）
- 命令路由（透明）
- 硬件设备直通（由 YAML 声明，非 Agent 决定）

### 11.3 Agent 可感知的场景

仅在以下 Prompt 变体中，Agent 需要知道自己在容器内:

- `container_phase_5_entry.md`: 可能包含"你正在容器内执行，使用 `/path/to/.venv/bin/python`"的提示
- `container_dependency_fix.md`: 可能包含"在容器内安装依赖"的提示

---

## 12. PPU/容器硬件考量

### 12.1 硬件直通

不同设备类型需要不同的 `--device` 参数:

```yaml
# NPU (Ascend 910)
devices:
  - /dev/davinci_manager
  - /dev/devmm_svm
  - /dev/hisi_hdc

# PPU（示例，实际需确认）
devices:
  - /dev/ppu_ctrl
  - /dev/ppu_mem
```

### 12.2 镜像差异

| 硬件 | 基础镜像示例 | 关键环境变量 |
|------|-------------|-------------|
| NPU | `ascendhub:24.03-pytorch` | `ASCEND_VISIBLE_DEVICES`, `LD_LIBRARY_PATH=/usr/local/Ascend/driver/lib64` |
| PPU | 待定 | 待定 |
| GPU | `nvcr.io/nvidia/pytorch:24.05-py3` | `NVIDIA_VISIBLE_DEVICES` |

### 12.3 驱动依赖

容器内需要 CANN/Torch-NPU 等驱动库。这些由基础镜像提供，不需要在容器启动时额外安装。但 bind mount 的宿主目录（如 `/data/models`）可能需要在容器中访问。

---

## 13. 新增文件与类设计

### 13.1 新增文件清单

```
src/
├── core/
│   ├── execution_backend.py          # 新增: ExecutionBackend 抽象接口
│   ├── container_config.py           # 新增: ContainerConfig 数据类与解析
│   └── config_parser.py              # 新增或修改: execution_backend 解析
├── workflows/
│   ├── npu_migration_v2_container.yaml   # 新增: NPU 容器变体
│   └── npu_migration_v2_auto.yaml        # 新增: auto 模式变体
├── prompts/
│   ├── container_env_detect_v2.md        # 新增: 容器增强探测 prompt
│   └── container_phase_5_entry.md        # 新增: Phase 5 容器模式 prompt
├── schemas/
│   └── execution_backend.json            # 新增: execution_backend YAML 校验 schema
├── tests/
│   ├── test_execution_backend.py         # 新增: 单元测试
│   └── test_container_backend.py         # 新增: 容器后端集成测试
└── docs/
    └── container_execution_backend_design.md  # 本文档
```

### 13.2 `ExecutionBackend` 接口（推荐实现）

```python
# core/execution_backend.py
from __future__ import annotations
import subprocess
import shlex
import time
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float

class ExecutionBackend(Protocol):
    def run(self, command: str, cwd: str | None = None,
            env: dict[str, str] | None = None,
            timeout: int | None = None) -> ExecResult: ...
    def is_available(self) -> bool: ...
    def cleanup(self) -> None: ...

class LocalBackend:
    def run(self, command, cwd=None, env=None, timeout=None) -> ExecResult:
        start = time.monotonic()
        # 复用 repair_loop._safe_split_command 逻辑拆分 argv
        # subprocess.run(...)
        elapsed = time.monotonic() - start
        return ExecResult(exit_code=..., stdout=..., stderr=..., duration=elapsed)

    def is_available(self):
        return True

    def cleanup(self):
        pass

class ContainerBackend:
    """Docker/Podman 容器执行后端。
    根据 config.source 区分两种行为:
    - source=image: 首次 run() 时确定性 docker run 创建新容器
    - source=existing_container: 初始化时 early inspect 接入已有容器
    """
    def __init__(self, config: ContainerConfig):
        self.config = config
        self._container_id: str | None = None
        self._initialized = False
        self._runtime_cmd = "docker" if config.runtime == "docker" else "podman"

    def is_available(self) -> bool:
        try:
            subprocess.run([self._runtime_cmd, "--version"],
                          capture_output=True, check=True, timeout=10)
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    def _ensure_container(self) -> str:
        """获取容器 ID。根据 source 创建新容器或验证已有容器。"""
        if self._container_id:
            return self._container_id
        if self.config.source == "existing_container":
            self._check_existing_container()
            return self._container_id
        # source == "image": 确定性 docker run 创建新容器
        run_cmd = [self._runtime_cmd, "run", "-d",
                   "--name", f"{self.config.container_name_prefix}-{run_id}"]
        # 附加 --device, -v, -e, --network, --cap-add, --log-driver 等
        for dev in self.config.devices:
            run_cmd.extend(["--device", dev])
        for vol in self.config.volumes:
            run_cmd.extend(["-v", vol])
        for k, v in self.config.env_vars.items():
            run_cmd.extend(["-e", f"{k}={v}"])
        if self.config.network_mode:
            run_cmd.extend(["--network", self.config.network_mode])
        if self.config.cleanup:
            run_cmd.append("--rm")
        run_cmd.extend(self.config.runtime_flags)
        run_cmd.extend([self.config.image, "tail", "-f", "/dev/null"])
        result = subprocess.run(run_cmd, capture_output=True, text=True, timeout=120)
        self._container_id = result.stdout.strip()
        self._initialized = True
        return self._container_id

    def _check_existing_container(self) -> None:
        """Early inspect for source=existing_container: 验证容器存在且运行中。"""
        cname = self.config.container_name
        result = subprocess.run(
            [self._runtime_cmd, "inspect", "--format", "{{.State.Status}}", cname],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise ContainerNotFoundError(f"Container '{cname}' not found.")
        if result.stdout.strip() != "running":
            raise ContainerNotRunningError(
                f"Container '{cname}' status={result.stdout.strip()}, expected 'running'")
        self._container_id = cname
        self._initialized = True

    def run(self, command, cwd=None, env=None, timeout=None) -> ExecResult:
        cid = self._ensure_container()
        exec_cmd = [self._runtime_cmd, "exec", "-i"]
        if self.config.container_workdir:
            exec_cmd.extend(["-w", self.config.container_workdir])
        # 环境变量通过 docker exec -e 传入
        if env:
            for k, v in env.items():
                exec_cmd.extend(["-e", f"{k}={v}"])
        exec_cmd.extend([cid, "bash", "-c", command])
        start = time.monotonic()
        proc = subprocess.run(exec_cmd, capture_output=True, text=True,
                              timeout=timeout or self.config.timeout)
        elapsed = time.monotonic() - start
        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration=elapsed,
        )

    def cleanup(self) -> None:
        """清理容器。仅 source=image 时停止/移除；existing_container 不操作。"""
        if self.config.source == "existing_container":
            return
        if not self.config.cleanup or not self._container_id:
            return
        try:
            subprocess.run([self._runtime_cmd, "stop", self._container_id],
                          capture_output=True, timeout=30)
            subprocess.run([self._runtime_cmd, "rm", self._container_id],
                          capture_output=True, timeout=30)
        except subprocess.SubprocessError as e:
            logger.warning("Container cleanup failed: %s", e)
        self._container_id = None
```

### 13.3 `ContainerConfig` 解析

```python
# core/container_config.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ContainerConfig:
    mode: str = "local"
    source: str = "image"                        # image | existing_container
    runtime: str = "docker"
    image: str | None = None
    container_name: str | None = None            # 仅 source=existing_container 有效
    container_name_prefix: str = "seam-migration"
    devices: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    required_env_vars: list[str] = field(default_factory=list)   # early_inspect 校验
    required_devices: list[str] = field(default_factory=list)    # early_inspect 校验
    container_workdir: str = "/workspace"
    network_mode: str | None = None
    runtime_flags: list[str] = field(default_factory=list)
    timeout: int = 7200
    cleanup: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ContainerConfig":
        if raw is None:
            return cls(mode="local")
        mode = str(raw.get("mode", "local"))
        if mode not in ("local", "container", "auto"):
            raise ValueError(f"Invalid execution_backend.mode: {mode!r}")
        if mode == "local":
            return cls(mode="local")
        source = str(raw.get("source", "image"))
        if source not in ("image", "existing_container"):
            raise ValueError(f"Invalid execution_backend.source: {source!r}")
        if source == "existing_container" and not raw.get("container_name"):
            raise ValueError(
                "execution_backend.container_name is required when source=existing_container"
            )
        return cls(
            mode=mode,
            source=source,
            runtime=str(raw.get("runtime", "docker")),
            image=raw.get("image"),
            container_name=raw.get("container_name"),
            container_name_prefix=str(raw.get("container_name_prefix", "seam-migration")),
            devices=list(raw.get("devices", [])),
            volumes=list(raw.get("volumes", [])),
            env_vars={str(k): str(v) for k, v in raw.get("env_vars", {}).items()},
            required_env_vars=list(raw.get("required_env_vars", [])),
            required_devices=list(raw.get("required_devices", [])),
            container_workdir=str(raw.get("container_workdir", "/workspace")),
            network_mode=raw.get("network_mode"),
            runtime_flags=list(raw.get("runtime_flags", [])),
            timeout=int(raw.get("timeout", 7200)),
            cleanup=bool(raw.get("cleanup", True)),
        )
```

### 13.4 与 `RepairLoopEngine` 集成

```python
# repair_loop.py 修改点（不改接口签名）

class RepairLoopEngine:
    def __init__(
        self,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator,
        config=None,
        exec_backend=None,  # 新增可选参数
    ):
        # ... 现有初始化不变 ...
        self.exec_backend = exec_backend or LocalBackend()

    def run(self, entry_script, project_dir, ..., exec_backend=None):
        # exec_backend 通过参数注入或从 __init__ 获取
        backend = exec_backend or self.exec_backend

        # 在原有 subprocess.run 处替换:
        result = backend.run(
            command=" ".join(cmd_argv),
            cwd=script_cwd,
            env=run_env,
            timeout=entry_script_timeout,
        )
        final_exit_code = result.exit_code
        final_stdout = result.stdout
        final_stderr = result.stderr
```

### 13.5 与 `Orchestrator` 集成

```python
# orchestrator.py 修改点

from core.container_config import ContainerConfig
from core.execution_backend import LocalBackend, ContainerBackend, auto_select_backend

class Orchestrator:
    def run_workflow(self, ...):
        workflow = load_workflow(self.workflow_path)
        # 新增: 解析 execution_backend
        container_cfg = getattr(workflow, "execution_backend", None)
        cc = ContainerConfig.from_dict(container_cfg)

        if cc.mode == "auto":
            cc = auto_select_backend(cc)

        if cc.mode == "container":
            exec_backend = ContainerBackend(cc)
            # ContainerBackend 内部根据 cc.source 区分:
            #   source=image:        首次 run() 时确定性创建容器
            #   source=existing_container: 初始化后 early inspect
        else:
            exec_backend = LocalBackend()

        # 注入到 RepairLoopEngine
        repair_engine = RepairLoopEngine(
            repair_session_mgr, artifact_store, prompt_loader, validator,
            config=fw_config, exec_backend=exec_backend
        )

        try:
            # ... 现有流程不变 ...
        finally:
            exec_backend.cleanup()  # existing_container 模式下为空操作
            # ... 现有清理不变 ...
```

---

## 14. YAML 扩展建议

### 14.1 NPU 容器变体 YAML（source: image）

```yaml
# workflows/npu_migration_v2_container.yaml
name: npu_migration_container
version: "2.0"
description: "CUDA to Ascend NPU migration with container execution (new container from image)"

execution_backend:
  mode: container
  source: image                # 从镜像创建新容器
  runtime: docker
  image: "ascendhub:24.03-pytorch"
  container_name_prefix: "seam-npu"
  container_workdir: "/workspace"
  devices:
    - /dev/davinci_manager
    - /dev/devmm_svm
    - /dev/hisi_hdc
  volumes:
    - "/data/models:/data/models:ro"
  env_vars:
    ASCEND_VISIBLE_DEVICES: "0"
    LD_LIBRARY_PATH: "/usr/local/Ascend/driver/lib64"
  network_mode: host
  timeout: 7200
  cleanup: true

agents:
  # 与 npu_migration_v2.yaml 完全一致

phases:
  - id: phase_0_env_detect
    type: llm
    agent: main_engineer
    prompt_template: "container_env_detect_v2"    # 新 prompt
    validator: env_detect
    transitions:
      on_success: phase_1_project_analysis

  # phase_1 ~ phase_4 与 npu_migration_v2.yaml 完全一致

  - id: phase_5_validation
    type: loop
    sub_workflow: repair_loop_container        # 新的 sub_workflow 名
    input_mapping:
      entry_script: "${state.phase_3_entry_script.run_command}"
      project_dir: "${context.PROJECT_DIR}"
    transitions:
      on_success: phase_6_report
      on_failure: complete

sub_workflows:
  repair_loop_container:
    id: repair_loop_container
    type: loop
    max_iterations: 5
    # ... stop_conditions 与原版一致 ...
    phases:
      - id: run_entry_script
        type: shell
        command: "${loop_vars.entry_script}"
        cwd: "${loop_vars.project_dir}"
        capture:
          exit_code: "script_exit_code"
          stdout: "script_stdout"
          stderr: "script_stderr"
          duration: "script_duration"
        on_failure: "continue"

      # 其余 phases 与原版一致
```

### 14.2 NPU 已有容器变体 YAML（source: existing_container）

```yaml
# workflows/npu_migration_existing.yaml
name: npu_migration_existing
version: "2.0"
description: "CUDA to Ascend NPU migration with existing running container"

execution_backend:
  mode: container
  source: existing_container   # 使用已有容器
  runtime: docker
  container_name: "my-npu-dev-01"  # 必填: 已有容器名称或 ID
  container_workdir: "/workspace"
  # image, container_name_prefix, cleanup, volumes(创建用途) 在此模式下忽略
  required_env_vars:
    - ASCEND_VISIBLE_DEVICES
  required_devices:
    - /dev/davinci_manager

agents:
  # 与 npu_migration_v2.yaml 完全一致

phases:
  - id: phase_0_env_detect
    type: llm
    agent: main_engineer
    prompt_template: "container_env_detect_v2"
    validator: env_detect
    transitions:
      on_success: phase_1_project_analysis

  # phase_1 ~ phase_6 与 npu_migration_v2.yaml 完全一致
```

### 14.3 Auto 模式 YAML

```yaml
# workflows/npu_migration_v2_auto.yaml
name: npu_migration_auto
version: "2.0"
description: "CUDA to Ascend NPU migration with auto-detected execution backend"

execution_backend:
  mode: auto
  runtime: docker
  image: "ascendhub:24.03-pytorch"
  # mode=auto 时，框架自动探测是否满足容器条件
  # 如果不满足，回退到 local

# ... 其余与 npu_migration_v2.yaml 一致
```

---

## 15. 分阶段实现路线图

| 阶段 | 范围 | 预期产出 | 预计工作量 |
|------|------|---------|-----------|
| Phase 1 | `ExecutionBackend` 接口 + `LocalBackend` 实现 + `ContainerConfig` | `core/execution_backend.py`, `core/container_config.py` | 1-2 天 |
| Phase 2 | `ContainerBackend` 实现（docker exec/run、容器创建、清理） | `ContainerBackend` 完整实现 | 2-3 天 |
| Phase 3 | `RepairLoopEngine` 集成（替换 subprocess 为 exec_backend） | `repair_loop.py` 修改（不改接口签名） | 1-2 天 |
| Phase 4 | `Orchestrator` 注入 + `workflow_executor.py` 适配 | `orchestrator.py` 修改 + sub_workflow shell phase 路由 | 1-2 天 |
| Phase 5 | YAML 变体 + Prompt 新增 | `npu_migration_v2_container.yaml`, `container_env_detect_v2.md` | 1 天 |
| Phase 6 | `framework_defaults.yaml` 扩展 + `auto` 模式探测 | `config_parser.py` + 环境探测逻辑 | 1-2 天 |
| Phase 7 | 测试 + 集成验证 | 单元测试、集成测试文档 | 2-3 天 |

---

## 16. 测试清单

### 16.1 单元测试

- [ ] `ContainerConfig.from_dict(None)` 返回 `mode="local"`
- [ ] `ContainerConfig.from_dict({})` 返回 `mode="local"`
- [ ] `ContainerConfig.from_dict({"mode": "container", "image": "x"})` 正确解析，`source="image"`
- [ ] `ContainerConfig.from_dict({"mode": "container", "source": "existing_container", "container_name": "c1"})` 正确解析
- [ ] `ContainerConfig.from_dict({"mode": "container", "source": "existing_container"})` 抛出 ValueError（缺 container_name）
- [ ] `ContainerConfig.from_dict({"mode": "container", "source": "invalid"})` 抛出 ValueError
- [ ] `LocalBackend.run("echo hello")` 返回 exit_code=0
- [ ] `LocalBackend.run("exit 42")` 返回 exit_code=42
- [ ] `LocalBackend.run()` 超时正确触发
- [ ] `LocalBackend.run()` 捕获 stdout/stderr

### 16.2 向后兼容性

- [ ] 不带 `execution_backend` 的 `npu_migration_v2.yaml` 正常运行
- [ ] `framework_defaults.yaml` 无容器配置时零影响
- [ ] 现有 tests/ 全量通过（不引入回归）

### 16.3 容器集成测试 — `source: image`

- [ ] Docker 可用时 `ContainerBackend.is_available()` 返回 True
- [ ] Docker 不可用时 `ContainerBackend.is_available()` 返回 False
- [ ] 容器创建成功，`_container_id` 非空
- [ ] `docker exec` 命令捕获正确的 exit_code / stdout / stderr
- [ ] bind mount 到 `/workspace` 后文件修改在宿主可见
- [ ] `cleanup()` 正确停止并移除容器
- [ ] `cleanup()` 失败不阻塞主流程
- [ ] 容器 `-w /workspace` 标志正确设置 CWD

### 16.3.5 容器集成测试 — `source: existing_container`

- [ ] 容器存在且 running → `_check_existing_container()` 通过
- [ ] 容器不存在 → `ContainerNotFoundError` 抛出
- [ ] 容器存在但 stopped/exited → `ContainerNotRunningError` 抛出
- [ ] 缺少 required_device → warning 日志，不终止
- [ ] 缺少 required_env_var → warning 日志，不终止
- [ ] `cleanup()` 在 `source=existing_container` 时为空操作
- [ ] `container_name` 未指定时 `from_dict` 抛出 ValueError

### 16.4 Auto 模式

- [ ] Docker 可用 + 有设备 → 选择 container
- [ ] Docker 不可用 → 回退 local
- [ ] Auto 模式选择 container 后 Early Inspect 正确触发
- [ ] auto 模式结果记录到日志

### 16.5 Phase 5 完整性

- [ ] 容器模式下 `script_exit_code == 0` 正确触发 success
- [ ] 容器模式下 `script_exit_code != 0` 正确触发 repair
- [ ] `script_stdout` / `script_stderr` 正确传递给 error_analyzer
- [ ] stagnation 检测逻辑不受影响

### 16.6 Podman 支持

- [ ] `ContainerBackend(runtime="podman")` 使用 `podman` 命令
- [ ] Podman rootless 模式可用

---

## 17. 迁移计划

### 17.1 零停机迁移

由于所有新增功能均为可选，迁移可以渐进式进行:

1. 部署包含新代码的框架版本（默认 `mode: local`，行为不变）
2. 验证现有 workflow YAML 在新代码上全量通过
3. 逐个添加容器变体 YAML（如 `npu_migration_v2_container.yaml`）
4. 在测试环境验证容器后端
5. 生产环境选择性使用容器变体

### 17.2 渐进式适配

- **第一周**: 仅部署 `LocalBackend`，替换 `subprocess` 为 `exec_backend.run()`
- **第二周**: 部署 `ContainerBackend`，在测试环境中使用容器 YAML
- **第三周**: 部署 `auto` 模式，配置环境探测
- **第四周**: 生产验证、性能调优

### 17.3 回滚策略

由于 YAML 变体与原有 YAML 独立存在，容器模式失败时只需切换回原有 `npu_migration_v2.yaml` 即可回滚，无需代码回滚。

---

## 18. 风险与缓解措施

### 18.1 容器运行时不可用

**风险**: 目标机器未安装 Docker/Podman，或版本过低。

**缓解**:
- `is_available()` 前置检测
- `auto` 模式自动回退 `local`
- YAML 中 `mode: container` 时前置验证，不可用则 early-fail 并给出清晰错误

### 18.2 `existing_container` 容器不符合假设

**风险**: 使用 `source: existing_container` 时，目标容器可能缺少必需设备、环境变量或 bind mount。

**缓解**:
- Early Inspect 检查（第 4.4 节）在命令执行前 fail-fast
- `ContainerNotFoundError` 和 `ContainerNotRunningError` 在初始化阶段抛出
- Required device/env 缺失仅 warning 不阻断（允许灵活场景）

### 18.3 硬件设备路径差异

**风险**: 不同机器上 `/dev/davinci_manager` 等路径可能不同。

**缓解**:
- `devices` 列表在 YAML 中可配置
- 框架代码不硬编码设备路径
- Phase 0 环境探测新增设备可用性检查

### 18.4 容器资源泄漏

**风险**: 容器未正确清理导致资源累积。

**缓解**:
- `Orchestrator` 的 `finally` 块强制调用 `cleanup()`
- `ContainerBackend.cleanup()` 捕获异常，记录 warning
- 使用 `docker run --rm` 作为补充手段

### 18.5 Bind Mount 权限问题

**风险**: Docker daemon 无权限访问 `output_projects` 路径。

**缓解**:
- 创建容器前验证路径可访问性
- 错误信息中包含诊断建议（如 `docker run` 完整命令）

### 18.6 Prompt 不一致

**风险**: 新增容器 prompt 与本地 prompt 行为差异导致 Agent 混淆。

**缓解**:
- 容器 prompt 仅包含容器特定的路径/环境变量信息
- 核心工作流指令（修复、分析）保持一致
- 通过 review 流程审核新增 prompt

### 18.7 后台任务限制

**风险**: 用户需求明确指出"避免后台任务"。

**缓解**:
- 容器创建是阻塞调用（`_ensure_container` 同步等待容器运行）
- 所有 `docker exec` 是同步调用（与当前 `subprocess.run` 对齐）
- 不使用 `container.exec_run(detach=True)` 等异步接口
- 如果需要 `docker run` 而非 `exec`，也是阻塞模式，等待命令完成

---

## 19. 已知文件索引

| 文件 | 角色 | 修改策略 |
|------|------|---------|
| `src/workflows/npu_migration_v2.yaml` | 主 workflow | **不修改** |
| `src/config/framework_defaults.yaml` | 默认配置 | **添加新段**，不修改现有 |
| `src/core/types.py` | 类型定义 | **添加** `ContainerConfig`（或移至 container_config.py） |
| `src/core/repair_loop.py` | Phase 5 引擎 | **集成** `ExecutionBackend`（不改 API） |
| `src/core/workflow_executor.py` | 工作流引擎 | **集成** `ExecutionBackend`（shell phase） |
| `src/core/orchestrator.py` | 编排器 | **解析** `execution_backend` 并注入 |
| `src/core/prompt_loader.py` | Prompt 加载 | **不修改** |
| `src/core/config_loader.py` | 配置加载 | **不修改** |
| `src/prompts/` | 提示模板 | **新增** 文件，不修改现有 |
| `src/core/execution_backend.py` | 新功能 | **新增** |
| `src/core/container_config.py` | 新功能 | **新增** |
| `src/schemas/execution_backend.json` | YAML 验证 schema | **新增** |

---

## 附录: `docker exec` 接口说明

`docker exec` 返回:
- **exit code**: 命令退出码（0=成功，非零=失败），等价于 `subprocess.CompletedProcess.returncode`
- **stdout**: 标准输出流，等价于 `subprocess.CompletedProcess.stdout`
- **stderr**: 标准错误流，等价于 `subprocess.CompletedProcess.stderr`

Python 调用示例:
```python
import subprocess

proc = subprocess.run(
    ["docker", "exec", "-i", "container_id", "bash", "-c", "python script.py"],
    capture_output=True,
    text=True,
    timeout=3600,
    cwd=None,  # docker exec cwd 在容器内设置
)
# proc.returncode → exit_code
# proc.stdout → stdout
# proc.stderr → stderr
```

因此 Phase 5 的结果接口（exit_code / stdout / stderr / duration）可以在容器模式下完全保留。
