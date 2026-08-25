# SEAM Bug 修复报告：#19 evidence_replay 误拒 root 内相对符号链接（附 #15 服务清理兜底）

## 元信息

| 项目 | 内容 |
|---|---|
| Bug 编号 | #19（新发现，e2e 测试中定位；#15 服务退出策略为本报告副修项） |
| Bug 标题 | evidence_replay 复制 `.sm-artifacts` 时误拒 root 内相对 symlink，迁移终态失败 |
| 严重级别 | 高（P1）——证据回放失败导致迁移终态化（finalization）整体失败 |
| 状态 | Fixed（已提交并推送 `origin/v1.2.1-dev-init-fix`） |
| 关联版本 | `v1.2.1-dev-init-fix`（HEAD `f5dd10b` 时发现，修复于 `c42524a`） |
| 症状 | Phase 5 产物回放阶段抛 `SidecarWriteError`：`containment: link or junction is forbidden`，finalization 失败 |
| 发现日期 | 2026-08-25 |
| 修复日期 | 2026-08-25 |
| 结论 | **root 内相对 symlink 被无差别当作逃逸链接拒绝；修复为：相对目标且 lexical 解析仍在 canonical_root 内的链接记录为 `LinkIdentity`，校验、回放、指纹全链路支持；绝对/逃逸目标仍拒绝** |

---

## 一、问题描述

### 1.1 现场证据

`e2e-reports/src/e2e-v3-806febf35fac/finalization_diagnostics.json`：

```json
[
  {
    "stage": "evidence_replay",
    "error_type": "SidecarWriteError",
    "detail": "/home/yiding/output_projects/BlueLM-quant-cuda-to-ascend-e2e_20260824_104002: \
containment: link or junction is forbidden: \
.../quant_npu/build_out_july/_CPack_Packages/Linux/External/\
custom_opp_ubuntu_aarch64.run/packages/vendors/customize/op_impl/ai_core/tbe/\
op_tiling/liboptiling.so"
  }
]
```

### 1.2 链接本体（root 内相对链接，非逃逸）

```
$ ls -la .../op_tiling/
liboptiling.so -> lib/linux/aarch64/libcust_opmaster_rt2.0.so
```

目标为相对路径，且按链接所在目录解析后仍在迁移产物 root 内，属于**合法内部链接**，被 `inspect_real_tree` 的无差别拒绝误伤。

### 1.3 失败链路

`evidence_replay`（sidecar 回放）→ `copy_real_tree`/`artifact_tree_copy` → `inspect_real_tree` 遍历到 reparse 条目 → 直接 `raise _containment_error("link or junction is forbidden")` → `SidecarWriteError` → finalization 失败。

## 二、根因分析

### 2.1 根因

`src/core/run_manifest_paths.py` 的 `inspect_real_tree` 对 **所有** reparse 条目（symlink/junction）无条件拒绝，未区分：

- **合法场景**：相对目标且按链接所在目录 lexical 解析（`normpath(join(entry.parent, link_target))`）仍在 canonical_root 内的链接——例如 NPU/昇腾自定义算子包内 `liboptiling.so -> lib/linux/aarch64/...`；
- **危险场景**：绝对路径目标、或相对目标逃逸出 root 的链接——这类必须拒绝（防回放出根）。

### 2.2 后果面

1. 任何含内部相对链接的迁移产物（常见于算子包、C++ 构建产物、`.so` 软链）在 evidence_replay 阶段必然失败；
2. `inspect_real_tree` 同时被 `digest_inventory`、`_fingerprint`、snapshot 持久化、sidecar 回放多条链路消费，误拒影响面覆盖全链；
3. 产物复制、清单指纹、证据摘要三处对链接的处理缺失（此前根本没有链接概念），即使放开 inspect 也会造成回放复制缺失或指纹/摘要不一致。

## 三、修复方案

**核心决策**：`os.readlink` 结果若为相对路径，且按链接所在目录 lexical 解析后仍在 canonical_root 内 → 记录为 `LinkIdentity`（dangling 允许，预算计 0 字节）；绝对路径或逃逸 root → 维持 containment 拒绝。全链路（记录 / 校验 / 回放 / 摘要 / 指纹）统一支持链接。

### 3.1 各层变更

| 层 | 文件 | 变更 |
|---|---|---|
| 模型 | `core/run_manifest_path_models.py` | 新增 `LinkIdentity` NamedTuple（8 身份字段 + `link_target`）；`RealTree` 增 `_links` slot/property/ctor 参数（默认 `()`） |
| 检视 | `core/run_manifest_paths.py` `inspect_real_tree` | reparse 分支重写：绝对目标 → 拒绝；相对目标 lexical 解析逃逸 root → 拒绝；否则记录为 link，`budget.charge(rel, 0)` |
| 校验 | `core/run_manifest_paths.py` `_require_link` + `_require_tree` | 校验链接的祖先链、路径身份（device/inode/时间戳）、`os.readlink == link_target`（防 TOCTOU） |
| 回放 | `core/run_manifest_paths.py` `_populate_real_tree` | 收集 `(relative, link_target)` 传给 `write_real_tree(..., links=...)` |
| 摘要 | `core/run_manifest_paths.py` `digest_inventory` | 每个链接追加 `EvidenceDigest(relative_path, sha256(link_target), size=len(target_bytes))` |
| 写入 | `core/run_manifest_tree_writer.py` | `write_real_tree` 增 `links` 参数（默认 `()`）；POSIX 走 `_write_link`（`os.symlink(target, name, dir_fd=parent)` + `os.fsync(parent)`，防符号链接攻击）；nt 分支 `os.symlink(target, link_path)` |
| 指纹 | `harness/run/artifact_paths.py` `_fingerprint` | 链接并入排序条目，标记字节 `"L"` + `link_target.encode()`，保证 claim/seal 指纹一致 |

### 3.2 回归防线（新增测试）

`src/tests/test_run_manifest_in_root_links.py` 6 用例：

1. root 内相对文件链接被记录为 `LinkIdentity`（原始相对目标保留）；
2. root 内相对 **dangling** 链接被记录（不跟随）；
3. **绝对目标仍拒绝**（CONTAINMENT）；
4. **逃逸相对目标仍拒绝**（`../outside.txt` → CONTAINMENT）；
5. `copy_real_tree` 复现链接（`is_symlink()` + `readlink == target`，内容一致）；
6. `digest_inventory` 包含链接摘要（sha256 of `"target.txt"`，size 正确）。

### 3.3 副修项：#15 服务退出策略（e2e 清理兜底）

`src/tests/e2e/e2e_test_v3.py` `run_e2e_v3` finally 块：`server_proc is not None` 时懒加载 `harness.server.lifecycle.stop_server` 并调用；`stop_server` 幂等（`proc.poll() is not None` 直接返回）；清理异常仅记录日志，不掩盖原始退出码（KeyboardInterrupt 场景）。`stop_server`（lifecycle.py:444-456）：terminate → wait(5s) → 超时 kill，幂等。

## 四、变更统计

| 文件 | 变更 |
|---|---|
| `src/core/run_manifest_path_models.py` | +20/−1（LinkIdentity、RealTree._links） |
| `src/core/run_manifest_paths.py` | +58/−2（inspect/_require_link/_require_tree/_populate/digest） |
| `src/core/run_manifest_tree_writer.py` | +19/−1（_write_link、links 参数） |
| `src/harness/run/artifact_paths.py` | +7/−1（_fingerprint "L" 分支） |
| `src/tests/e2e/e2e_test_v3.py` | +11（finally 清理兜底） |
| `src/tests/test_run_manifest_in_root_links.py` | 新增 123 行（6 用例） |

**提交**（均含 `Sisyphus` 合作署名）：
- `c42524a` `fix(core): record and replay in-root relative symlinks in manifest copy`
- `8b47080` `fix(e2e): stop auto-started OpenCode server when run is interrupted`

## 五、测试验证

### 5.1 定向回归（全部通过）

| 批次 | 文件 | 结果 |
|---|---|---|
| 新回归 | `test_run_manifest_in_root_links.py` + budget_suffixes + path_identity_cases | **12 passed** |
| 终态链 | manifest_sealing + f2_directory_ownership + f2_functional_transactions + second_oracle_cases + continuation_evidence + cleanup_bookkeeping | **62 passed** |
| 回放链 | f2_atomic_sidecar_writes + f2_atomic_recovery + f2_bounded_identity_reads + f2_filesystem_hardening + artifact_receipt_cases + evidence_limits + manifest_read_bounds | **44 passed, 1 skipped** |
| 快照/指纹 | continuation_environment* + experience_memory_registry + experience_solidification + execution_backend + f2_cleanup_replacement | **238 passed** |
| 安全用例 | phase5_receipt_security + continuation_evidence_security + terminal_authority + run_manifest_oracle_cases + resource_manifest_* | **40 passed, 1 skipped** |
| 兼容 | `test_f2_verified_read_compatibility.py` | **3 passed** |

### 5.2 全量非 e2e 套件

**3696 passed, 89 skipped**；67 个失败全部位于 `test_seam_init_*`、`test_sqlite_provider`、`test_usage_guide_docs`——均不依赖本次改动模块，且已用 `git stash` 回退基线复跑同一批失败文件得到**完全一致结果**（24 failed/255 passed 与改动后相同），确认为既有环境性失败（缺 `uv` CLI、`-S` 隔离模式缺 `typing_extensions` 等），非本次回归。

### 5.3 静态检查

- `py_compile` 全部改动文件通过；
- e2e 模块（`e2e_test_v3`、`test_e2e_v3_root_entrypoint`）import 正常；
- Python 3.8 语法门 `test_active_py38_imports` 仅因本机缺 `uv` 无法执行（环境限制，非代码问题）。

## 六、遗留事项 / 后续建议

1. **#15 完整验收**：本次仅覆盖 `run_e2e_v3` 中断清理路径（auto-start 服务由本次运行启动的场景）。清单 #15 要求的"预先已有服务 / 正常结束 / 异常退出"等五条路径（buglist.md L154）建议按 `v1.2.1-dev-init-fix-buglist.md` 验收标准补齐测试；
2. **真实 e2e 复跑**：建议在真实环境以 `BlueLM-quant-cuda-to-ascend` 全量跑一次 e2e，确认 `op_tiling/liboptiling.so` 场景的 evidence_replay 通过；
3. **链接回放安全说明**：`_write_link` 在 POSIX 用 `dir_fd` 相对 `open(O_NOFOLLOW)` 的 root 描述符，与既有 `_write_file` 同级防符号链接攻击；nt 分支为最小兼容实现（Windows 上 symlink 需开发者模式，超出本项目目标平台范围）。

## 七、证据位置

| 位置 | 说明 |
|---|---|
| `/home/yiding/SEAM/e2e-reports/src/e2e-v3-806febf35fac/finalization_diagnostics.json` | 失败现场（`stage=evidence_replay`，`SidecarWriteError`） |
| `/home/yiding/output_projects/BlueLM-quant-cuda-to-ascend-e2e_20260824_104002/quant_npu/build_out_july/_CPack_Packages/.../op_tiling/` | `liboptiling.so` 相对链接本体 |
| `src/core/run_manifest_path_models.py` | `LinkIdentity`、`RealTree.links` |
| `src/core/run_manifest_paths.py` | `inspect_real_tree` reparse 分支、`_require_link`、`_populate_real_tree`、`digest_inventory` |
| `src/core/run_manifest_tree_writer.py` | `_write_link`、`write_real_tree(links=)` |
| `src/harness/run/artifact_paths.py` | `_fingerprint` "L" 分支（:136-138） |
| `src/harness/server/lifecycle.py` | `stop_server`（:444-456，幂等） |
| `src/tests/e2e/e2e_test_v3.py` | finally 清理兜底（:1584-1594） |
| `src/tests/test_run_manifest_in_root_links.py` | 新增 6 个回归用例 |
| 提交 | `c42524a`、`8b47080`（已推送 `origin/v1.2.1-dev-init-fix`） |
