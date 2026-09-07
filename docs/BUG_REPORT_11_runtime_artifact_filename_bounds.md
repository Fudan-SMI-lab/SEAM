# SEAM Bug #11 修复报告：运行时产物文件名超长（NPU `Errno 36`）

## 元信息

| 项目 | 内容 |
|---|---|
| Bug 编号 | #11 |
| Bug 标题 | NPU 平台报 `OSError: [Errno 36] File name too long`（运行时产物文件名超长） |
| 严重级别 | 高（依据影响评估：可能直接中断 NPU 工作流和产物保存） |
| 修复提交 | `7f7a996`（单一提交，父提交 `ee4f4e6`） |
| 提交信息 | fix(artifacts): bound runtime artifact filenames for #11 |
| 作者 | ZihangZ |
| 日期 | 2026-08-06 11:50:35 +0800 |
| 变更规模 | 3 个文件，+317/-7 |
| 涉及模块 | runtime_artifacts.py、repair_loop.py、test_runtime_artifact_filename_bounds.py（新增） |
| 审查状态 | 定向回归（8 passed）+ 全量回归（2921 passed / 14 预存失败）已验证 |

**概述**：在 NPU 平台上，DAMO-YOLO 等长项目名触发 `OSError: [Errno 36] File name too long`，运行时产物（runtime error 说明、runtime card、operator repair context、final gate validator 脚本）因文件名超过组件长度上限而写入失败，可能直接中断 NPU 工作流与产物保存；现场日志只展示到 OpenCode 环境预检，完整异常需从堡垒机产物读取。本次修复定位产生超长文件名的唯一入口（`runtime_artifacts.py` 的 4 处拼接调用点），引入 `bounded_runtime_filename()`（按 UTF-8 字节安全截断 + 稳定 SHA-256 哈希后缀，保留扩展名）与 `guard_artifact_path()`（完整路径长度保护），并配套 8 个回归测试。用单一提交 `7f7a996` 落在 `ee4f4e6` 之上，共改动 3 个文件、净增 317 行、净删 7 行。

---

## 一、问题描述

在 NPU 平台执行迁移时，OpenCode 环境预检通过，但后续产物写入阶段出现：

```
OSError: [Errno 36] File name too long
```

日志只展示到 OpenCode 环境预检，完整异常需从堡垒机产物读取。DAMO-YOLO 等项目的目录名/项目名较长，运行时产物文件名（如 `runtimeCard_{project_name}.md`、`operatorRepairContext_{project_name}.md`、`finalGateValidator_{project_name}.sh`）由"前缀 + 项目名 + 扩展名"直接拼接，组件长度可超过 POSIX `NAME_MAX`（典型文件系统 255 字节），系统调用直接失败。

## 二、根因分析

**根因**：运行时产物文件名由 `prefix + project_name + extension` 直接拼接，没有任何长度约束。

- `sanitize_project_name()` 只做字符清洗，不限制长度；
- `write_repair_runtime_artifacts()`、`write_operator_repair_context_artifact()`、`repair_loop._write_final_gate_validator_runner()` 三处调用点均直接拼接文件名；
- 文件系统对单个路径组件有 255 字节上限（POSIX `NAME_MAX`），对完整路径有 4096 字节上限（POSIX `PATH_MAX`）；项目名超长时组件超限，OS 抛出 `Errno 36`；
- 错误发生在写入瞬间，难以定位，且现场日志不完整，需要从堡垒机产物反向排查。

**影响**：

- 运行时产物无法落盘，NPU 工作流可能中断；
- 修复循环依赖这些产物文件继续推进，失败后无法自动恢复；
- 异常信息不透明（裸 `Errno 36`），排查成本高。

## 三、修复方案（架构设计）

设计思路：对"文件名超长"这一确定性根因做确定性修复，而不是捕获异常后忽略。两层保护：

1. **单组件保护**：`bounded_runtime_filename()` 把拼接结果钳制在 `NAME_MAX`（默认 255 字节）内——短名时字节级与原拼接完全一致（零行为变化），长名时按 UTF-8 字符边界安全截断项目名，并追加稳定 SHA-256 短哈希后缀，保证不同长项目名不碰撞；
2. **完整路径保护**：`guard_artifact_path()` 在写入前检查解析后的完整路径是否超过 `PATH_MAX`（4096 字节），超限时抛出带字节数与路径的明确诊断错误，而不是让 OS 抛出不透明的 `Errno 36`。

### 3.1 两个核心函数

**1. `bounded_runtime_filename(prefix, project_name, extension, name_max_bytes=255)`**

- 短名路径：拼接结果按 UTF-8 编码不超过 `name_max_bytes` 时，字节级原样返回——既有产物文件名完全不变，零回归风险；
- 长名路径：对 `project_name` 计算 SHA-256，取前 8 位十六进制作为稳定哈希后缀 `_<digest>`；在 `name_max_bytes` 预算内扣除前缀、哈希后缀与扩展名所占字节后，将项目名按 UTF-8 字节前缀截断（`errors="ignore"` 保证多字节字符不劈半），最后拼接 `prefix + truncated + hash_suffix + extension`；
- 防御：若前缀 + 哈希后缀本身已超过限制（`available < 1`），抛出 `RuntimeError` 并给出明确的诊断信息。

**2. `guard_artifact_path(path)`**

- 对路径做 `resolve()` 后按 UTF-8 编码计算字节数；
- 超过 `_PATH_MAX_BYTES = 4096` 时抛出 `RuntimeError`，携带实际字节数与解析后的路径；
- 单组件截断解决了常见场景（组件超长）；当整个目录树已经过深时任何截断都无济于事，此时以明确诊断替代 OS 的裸 `Errno 36`。

### 3.2 覆盖的调用点（超长文件名的唯一入口）

| 调用点 | 产物文件名模式 |
|---|---|
| `write_repair_runtime_artifacts()` | `runtime_error_{project_name}.md`、`runtimeCard_{project_name}.md` |
| `write_operator_repair_context_artifact()` | `operatorRepairContext_{project_name}.md` |
| `repair_loop._write_final_gate_validator_runner()` | `finalGateValidator_{project_name}.sh` |

三个函数均同时应用 `guard_artifact_path`（目录）与 `bounded_runtime_filename`（文件名）。

## 四、实施明细

### 4.1 变更文件清单

| 文件 | 变更 | 说明 |
|---|---|---|
| src/core/runtime_artifacts.py | +74/-1 | `bounded_runtime_filename()`、`guard_artifact_path()`、`_DEFAULT_NAME_MAX_BYTES=255`、`_PATH_MAX_BYTES=4096`、`import hashlib`；3 处调用点接入 |
| src/core/repair_loop.py | +8/-2 | `_write_final_gate_validator_runner` 接入两个保护函数并更新导入 |
| src/tests/test_runtime_artifact_filename_bounds.py | 新增 242 行 | 8 个回归测试 |

### 4.2 测试用例清单（test_runtime_artifact_filename_bounds.py，8 个）

| 测试 | 契约 |
|---|---|
| test_short_name_outputs_are_byte_identical | 短名时输出与原拼接字节级一致 |
| test_long_ascii_project_name_writes_bounded_filename | 长 ASCII 项目名写出有界文件名 |
| test_bounded_runtime_filename_multibyte_truncation_is_valid_utf8 | 多字节截断结果仍为合法 UTF-8 |
| test_truncation_is_deterministic | 同输入截断结果确定可复现 |
| test_distinct_long_names_get_distinct_hashes | 不同长项目名哈希后缀不同，不碰撞 |
| test_final_gate_validator_runner_uses_bounded_name | finalGateValidator 脚本使用有界文件名 |
| test_operator_context_runner_uses_bounded_name | operatorRepairContext 产物使用有界文件名 |
| test_excessive_directory_length_raises_explicit_error | 完整路径超限时抛明确 RuntimeError |

## 五、验证与测试

### 5.1 定向测试统计

| 测试项 | 结果 |
|---|---|
| test_runtime_artifact_filename_bounds.py | 8 passed（0.63s） |

### 5.2 全量回归

结果：**2921 passed / 14 failed / 84 skipped / 45 deselected**（2913 基线 + 8 个新测试）。14 个失败全部为环境性预存基线，与 #16 修复报告记录一致：

| 失败分组 | 数量 |
|---|---|
| py38 imports | 5 |
| resource_manifest | 4 |
| review_observability | 3 |
| sqlite_provider | 1 |
| v3_environment_output | 1 |

### 5.3 静态检查

| 检查项 | 结果 |
|---|---|
| python -m compileall -q src | exit 0 |

## 六、审查

本修复经以下验证路径确认：

- 定向回归：8 个新测试全绿；
- 全量回归：2921 passed / 14 预存失败，与基线一致无新增失败；
- 编译检查：compileall exit 0；
- 行为兼容：短名路径字节级与原实现一致，既有产物文件名零变化。

## 七、已知遗留问题

无阻塞项。`bounded_runtime_filename` 的防御分支（前缀 + 哈希超限即抛错）在当前所有调用点均不可达，属纯防御性代码。

## 八、回滚说明

在 /home/yiding/SEAM 下执行：

```bash
git revert 7f7a996
```

注意事项：

- 执行前先确认工作树状态。当前存在未跟踪的 docs/BUG_REPORT_16_context_management.md 与 4 个 .bak 文件，revert 不受其影响；
- `git revert` 生成新的反向提交，不修改既有历史。若确需丢弃历史可执行 `git reset --hard ee4f4e6`，但会连带移除其后的 #11/#13 提交，请谨慎使用；
- revert 后运行时产物文件名退回无界拼接，超长项目名会再次触发 `Errno 36`。回归前对照 2921 passed / 14 预存失败基线确认无新增失败。

## 附录：证据位置

| 位置 | 说明 |
|---|---|
| /home/yiding/SEAM/src/tests/test_runtime_artifact_filename_bounds.py | 8 个回归测试 |
| /home/yiding/SEAM/src/core/runtime_artifacts.py | `bounded_runtime_filename`、`guard_artifact_path` 及 3 处调用点 |
| /home/yiding/SEAM/src/core/repair_loop.py | `_write_final_gate_validator_runner` 调用点 |
