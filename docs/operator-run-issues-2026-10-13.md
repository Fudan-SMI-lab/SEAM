# 单算子 KernelBench 闭环运行问题报告（NPU 服务器本地）

- 日期：2026-10-13
- 主机：Ascend 910B3（本地执行），CANN 8.3.RC1，torch 2.10.0 + torch-npu 2.10.0
- 仓库：SEAM `MasterSummer/SEAM` 分支 `2.0.0`
- 关联：`docs/HANDOFF_NPU_LOCAL_OPENCODE_DEEPSEEK.zh.md`

## 0. 结论概览

单算子闭环 `run_operator.sh` 已能贯通到 Phase 2，过程中发现并定位了 5 类问题，其中代码缺陷 1 项（已修复），环境/部署问题 4 项。

| # | 问题 | 类型 | 状态 |
| - | --- | --- | --- |
| 1 | 算子 validator 未注册，Phase 1 直接失败 | **代码缺陷** | 已修复（本提交） |
| 2 | 现有 OpenCode server 在他人容器内，agent 看不到工作目录 | 部署 | 用宿主 server 绕过 |
| 3 | 宿主 OpenCode 默认弱模型 `deepseek-flash`，无 skill 工具 | 配置 | 已加全局配置 |
| 4 | Phase 2 单条消息 600s 超时，生成被误判失败 | 配置/超时 | 待调大 |
| 5 | KernelBench 参考实现运行期错误（RE） | 环境/数据 | 大部分已修复 |

## 1. 代码缺陷：算子 validator 未注册（已修复）

**现象**：`phase_1_operator_analysis` FAILED，会话返回

```
Your previous output for phase_1_operator_analysis failed validation.
Error: Validator 'operator_analysis' is not registered.
```

**根因**：`run_operator.sh` → `tests.e2e.e2e_test_v3` → `WorkflowExecutor` 这条链路只注册了迁移类 validator（`env_detect/project_analysis/venv/entry_script/...`），漏注册 4 个算子 validator。算子 validator 只在 `core/phase_runner.py` 的 `_register_default_validators` 中注册过，另一条执行路径没有覆盖。

**修复**：在 `src/tests/e2e/e2e_test_v3.py` 中补注册

```
operator_env_detect / operator_analysis / operator_evaluation / operator_report
```

**验证**：修复后 `phase_1_operator_analysis` 由 FAILED 变为 **PASSED**。

## 2. 部署问题：OpenCode server 位于他人容器

宿主 `ss -ltnp` 可见的 OpenCode server 均在 zhiyuan 的 Docker 容器中：

```
4096 / 18889-91  -> 容器 6bd4e691…（只挂 /home/zhiyuan）
4098             -> 容器 f92251a5……（只挂 /home/zhiyuan，且对所有消息返回 MessageAbortedError）
```

容器启动命令：

```
docker run -v /home/zhiyuan/:/home/zhiyuan/ --name zhiyuan_seam \
  .../ascendhub/cann:8.5.1-910b-ubuntu22.04-py3.11
```

因容器只挂载 `/home/zhiyuan`，其 agent 无法读取 SEAM 在 `/home/yiding` 复制的工作副本，Phase 1 反复报“任务目录不存在”。

**绕过**：在宿主机自起 OpenCode server（`opencode serve --port 4099`），agent 在宿主机执行，可访问 `/home/yiding`。

> SEAM 只是通过 HTTP 调 OpenCode；server 运行在哪个命名空间决定了 agent 能访问哪些文件。算子闭环要求在能看到 SEAM 工作目录的环境中运行 server。

## 3. 配置问题：宿主模型与 skill 工具

宿主 `~/.config/opencode/opencode.jsonc` 原本只有 `$schema`，导致会话默认使用 `deepseek-flash`，且 `skill` 工具不可用（Phase 2 `skill` 调用报错）。

**修复**：写入全局配置（项目级 `.opencode/opencode.jsonc` 对 SEAM 无效，因为 SEAM 按复制后的任务目录解析配置）：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "deepseek/deepseek-v4-pro",
  "agent": {
    "build": {
      "model": "deepseek/deepseek-v4-pro",
      "permission": { "external_directory": "allow" }
    }
  }
}
```

验证：新会话模型确认为 `deepseek-v4-pro`。

## 4. 超时问题：Phase 2 单条消息 600s

**现象**：

```
phase_2_custom_op_prepare: FAILED (1260.12s)
  transport_timeout ... timeout_s=600.000 ... reason=request_timeout exhausted=true
  Retrying top-level LLM phase in a fresh session ...
```

两次尝试各 600s 均超时。但 **agent 实际已产出真实 Ascend C 解**：

```
/home/yiding/output_projects/Abs_20261013_064921/solution.json
name=Abs_ascendc_custom  languages=["ascendc"]  build_method=bisheng
sources=["abs_custom.asc","main.py"]
```

**根因**：`DEFAULT_SESSION_WAIT_TIMEOUT = 600.0`（`harness/session/manager.py`），且 workflow 的 phase 未设置 `timeout`，`_llm_timeout_for_phase` 返回 None → 用 600s。而该 agent 在 Phase 2 会做大量 KernelBench 源码 / CANN 头文件侦查，单条消息超过 600s。

**建议**：为 phase 设置更大超时（workflow yaml 加 `timeout: 2400`，或 framework config `session_timeout_phase`），或收敛 Phase 2 的探索范围。

## 5. KernelBench 参考实现运行期错误（RE）

在把数据集 `definition.json` 的 reference 作为基准解运行时，出现 10 个 RUNTIME_ERROR，已大部分修复：

| 算子 | 原因 | 处理 | 结果 |
| --- | --- | --- | --- |
| AdamW / LSTM | CANN TBE/tvm JIT 缺 Python 依赖（`AclSetCompileopt 500001`） | 补装 `decorator/scipy/attrs/psutil` | 100% |
| NMS | 同上 500001 | 补依赖 | RE 消除（余数值差异） |
| IOU | 参考用 `mmcv.ops.bbox_overlaps`（无 NPU 实现 + mode 类型不符） | 重写为纯 torch | 100% |
| ChunkGlaFwdKernelO | 输入生成器 `_get_value` 未读 `ScalarInput`（chunk_size 取默认 64 与 A 的 32 冲突） | 生成器支持 ScalarInput | 100% |
| GqaSparseDecode / GqaSparseFwd | 同上，scalar 输入被置 None | 生成器支持 ScalarInput | 100% |
| DenseSparseAttention | 输入生成器用 CPU generator 生成 NPU 张量 | 改为 CPU 生成后 `.to(device)` | 100% |
| GroupedMatmulSwigluQuantV2 | CANN 8.3.RC1 仅有非 V2 `aclnnGroupedMatmulSwigluQuant` | 环境限制 | 未修复 |
| LightningIndexer | `aclnnLightningIndexer` 在 libopapi.so 中缺失 | 环境限制 | 未修复 |

依赖修复已固化进 `npu-kernelbench/pyproject.toml`（新增 `decorator/scipy/attrs/psutil/mmcv` 及 `[tool.uv.extra-build-dependencies]`）。

## 6. 环境注意事项

- 本机访问 loopback 需 `NO_PROXY=127.0.0.1,localhost`（否则走 Squid 代理报 503）。`run_operator.sh` 已自动处理。
- `git push` 经 Squid 代理返回 `503 after CONNECT`，本机无法直接推送。

## 7. 复现命令

```bash
cd /home/yiding/seam
export PATH="/root/.local/bin:$PATH"
export PYTHON=/home/yiding/bench/npu-kernelbench/.venv/bin/python

bash src/scripts/run_operator.sh \
  /home/yiding/bench/npu-kernelbench/data/kernel_generator/level1/Abs \
  /home/yiding/bench/npu-kernelbench \
  --target-soc Ascend910B3 \
  --operator-output-dir /home/yiding/seam/operator_runs/Abs \
  --server-url http://127.0.0.1:4099 \
  --agent build \
  --max-iter 1
```
