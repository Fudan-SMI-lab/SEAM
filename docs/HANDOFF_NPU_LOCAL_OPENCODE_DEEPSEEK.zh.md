# NPU 服务器本地执行 Handoff

## 1. 目的

本文用于把 SEAM 的 NPU/Ascend C 单算子试验交给**已经具备 NPU 和本地 `opencode-deepseek` 配置的服务器**执行。

服务器无法通过 SSH 访问时，不影响执行流程：所有命令都在 NPU 服务器本地运行；执行完成后只需要通过已有文件传输渠道带回报告目录和必要日志。

当前首个试验算子为：

```text
npu-kernelbench/data/kernel_generator/level1/GELU
```

目标闭环为：

```text
任务检查 → 算子分析 → Ascend C solution 准备
→ 全 workload KernelBench 评测 → 失败分析/修复 → 结果报告
```

## 2. 服务器侧目录约定

以下路径是示例，执行前请按服务器实际目录修改。建议使用绝对路径，避免 OpenCode、`uv` 和 SEAM 在不同工作目录下产生路径歧义。

```bash
export SEAM_ROOT="$HOME/SEAM-v1.3.1-bugfix"
export KERNELBENCH_ROOT="$HOME/npu-kernelbench"
export OPERATOR_TASK_DIR="$KERNELBENCH_ROOT/data/kernel_generator/level1/GELU"
export OPERATOR_OUTPUT_DIR="$SEAM_ROOT/operator_runs/GELU"
export OPERATOR_SOLUTION_PATH="$OPERATOR_TASK_DIR/solution.json"
export TARGET_SOC="$(python -c 'import os; print(os.environ.get("ASCEND_SOC_VERSION", "Ascend910B1"))')"

mkdir -p "$OPERATOR_OUTPUT_DIR"
cd "$SEAM_ROOT"
```

如果服务器使用的是其他 SoC，请显式设置，例如：

```bash
export TARGET_SOC=Ascend910B4
```

不要把 API key、token 或 OpenCode 私有配置提交回 Git 仓库，也不要把它们写入本 handoff 的日志附件。

## 3. 基础环境检查

```bash
cd "$SEAM_ROOT"

git status --short --branch
python --version
uv --version
npu-smi info

python - <<'PY'
import torch
import torch_npu

print("torch:", torch.__version__)
print("torch_npu:", torch_npu.__version__)
print("npu_available:", torch.npu.is_available())
if not torch.npu.is_available():
    raise SystemExit("NPU is not available to torch_npu")
print("device_count:", torch.npu.device_count())
print("device_name:", torch.npu.get_device_name(0))
PY

test -f "$OPERATOR_TASK_DIR/definition.json"
test -f "$OPERATOR_TASK_DIR/workload.jsonl"
test -f "$SEAM_ROOT/src/workflows/npu_kernelbench_custom_op.yaml"
```

验证 KernelBench 命令入口：

```bash
cd "$KERNELBENCH_ROOT"
uv run npu-kernelbench eval --help
```

如果上述检查失败，先记录完整输出，不要进入 OpenCode 或 repair loop。尤其需要记录：

- `npu-smi info` 的 SoC、驱动和 CANN 信息；
- `torch`、`torch_npu` 版本；
- `uv run npu-kernelbench eval --help` 的退出码和 stderr。

## 4. 启动并检查本地 OpenCode

SEAM 推荐使用本机回环地址 `127.0.0.1:4098`。如果服务尚未运行，在服务器本地启动：

```bash
nohup opencode serve \
  --hostname 127.0.0.1 \
  --port 4098 \
  > "$OPERATOR_OUTPUT_DIR/opencode.server.log" 2>&1 &

echo $! > "$OPERATOR_OUTPUT_DIR/opencode.server.pid"
```

检查服务是否可访问：

```bash
curl -fsS http://127.0.0.1:4098/agent \
  > "$OPERATOR_OUTPUT_DIR/opencode.agent.json"

python -m json.tool \
  "$OPERATOR_OUTPUT_DIR/opencode.agent.json" \
  | sed -n '1,160p'
```

确认 `opencode-deepseek` 对应的 agent/model 已出现在返回内容中。若配置通过 OpenCode 项目配置文件注入，应在服务器本地检查配置是否生效，但不要把密钥内容复制到报告中。

如果服务器已有 OpenCode 进程，不要重复启动；直接确认：

```bash
curl -fsS http://127.0.0.1:4098/agent > "$OPERATOR_OUTPUT_DIR/opencode.agent.json"
```

## 5. 先执行一次纯 KernelBench baseline

这一步不依赖 SEAM 的 LLM workflow，用于确认 NPU、KernelBench、任务数据和当前 `solution.json` 都能独立工作。

```bash
test -f "$OPERATOR_SOLUTION_PATH" || {
  echo "Missing solution: $OPERATOR_SOLUTION_PATH"
  exit 2
}

cd "$KERNELBENCH_ROOT"
set -o pipefail
uv run npu-kernelbench eval "$OPERATOR_TASK_DIR" \
  --solution "$OPERATOR_SOLUTION_PATH" \
  --runner persistent \
  --timeout 600 \
  -o "$OPERATOR_OUTPUT_DIR/traces.baseline.jsonl" \
  --verbose \
  2>&1 | tee "$OPERATOR_OUTPUT_DIR/eval.baseline.log"
```

保留以下文件：

```text
traces.baseline.jsonl
eval.baseline.log
solution.json 或其 SHA256
```

生成校验值：

```bash
sha256sum "$OPERATOR_SOLUTION_PATH" \
  > "$OPERATOR_OUTPUT_DIR/solution.sha256"
```

若 baseline 失败，先不要启动 repair loop。常见分类包括：

- `solution.json` 格式或路径错误；
- CANN/`torch_npu`/KernelBench 版本不兼容；
- 编译失败；
- NPU OOM 或设备占用；
- 数值结果错误；
- OpenCode 之前的环境错误。

## 6. 启动 SEAM 算子生成与测试

使用专用 launcher：

```bash
cd "$SEAM_ROOT"
bash src/scripts/run_operator.sh \
  "$OPERATOR_TASK_DIR" \
  "$KERNELBENCH_ROOT" \
  --target-soc "$TARGET_SOC" \
  --operator-output-dir "$SEAM_ROOT/operator_runs/GELU/$(date '+%Y%m%d_%H%M%S')" \
  --operator-constraints "$SEAM_ROOT/docs/operator_constraints.example.json" \
  --server-url http://127.0.0.1:4098 \
  --agent opencode-deepseek
```

launcher 会验证任务文件和 KernelBench 根目录，并让 V3 把算子任务复制到
保留的独立工作副本。Phase 2 在副本中生成 `solution.json` 和 Ascend C 源码，
不会修改原始 benchmark task。完整流程由
`src/workflows/npu_kernelbench_custom_op.yaml` 驱动。

输出目录默认位于 `../operator_runs/<operator>/<timestamp>`；可通过
`--operator-output-dir` 指定。生成工作副本位置会在 V3 日志中打印。

## 7. 算子 API policy 和评测前置检查

算子 workflow 现在支持通过 `--operator-constraints PATH` 注入 JSON API
策略。示例文件位于 `docs/operator_constraints.example.json`。该策略会传给
算子分析、Ascend C 生成、失败分析和 repair prompt；KernelBench 调用前还会
由 SEAM 对 `solution.json` 声明的候选 Python source 执行 API allowlist preflight。

API preflight 是 SEAM 侧第一道检查，不替代 KernelBench 的 `ast_scan` 和
`kernel_signature`。策略文件应只包含 JSON API policy，不要把 token 或其他
敏感信息写入其中。preflight 只扫描候选 solution，不扫描 reference 的 `main.py`。

查看 launcher 参数：

```bash
bash src/scripts/run_operator.sh --help
```

## 8. OpenCode 本地运行时建议

完整 workflow 启动时，显式复用服务器已有的 OpenCode：

```bash
--server-url http://127.0.0.1:4098 \
--server-no-auto-start \
--opencode-readiness message \
--opencode-message-timeout 120
```

这样 SEAM 不会尝试启动第二个服务，也不会在结束时关闭服务器已有的 OpenCode 进程。

如果模型 agent 名称需要显式指定，再增加：

```bash
--agent opencode-deepseek
```

实际 agent 名称以 `/agent` 返回值为准；如果返回名称不同，不要凭名称猜测，使用服务器实际配置中的名称。

## 9. 结果目录和回传清单

建议每次运行使用独立目录，例如：

```text
operator_runs/GELU/2026-10-12T120000+0800/
```

至少回传：

```text
opencode.agent.json
opencode.server.log                 # 若本次由 handoff 启动
eval.baseline.log
traces.baseline.jsonl
solution.sha256
SEAM 运行报告目录中的 summary.json
SEAM 运行报告目录中的错误日志和 phase 状态
```

可选回传：

```text
OpenCode raw trace                 # 仅在明确启用 --save-agent-trace 时
编译日志
NPU/CANN 诊断输出
```

打包前先检查是否包含敏感信息：

```bash
rg -n -i "api[_-]?key|token|secret|password|authorization|bearer" \
  "$OPERATOR_OUTPUT_DIR" || true
```

确认没有密钥后再打包：

```bash
tar -czf "$SEAM_ROOT/GELU-handoff-$(date +%Y%m%d-%H%M%S).tar.gz" \
  -C "$SEAM_ROOT" operator_runs/GELU
```

## 10. 现场失败处理

### OpenCode 不可访问

```bash
ps -ef | rg '[o]pencode'
ss -ltnp | rg ':4098'
curl -v http://127.0.0.1:4098/agent
tail -200 "$OPERATOR_OUTPUT_DIR/opencode.server.log"
```

### KernelBench 评测失败

不要只回传最后一行错误。保留：

```bash
tail -200 "$OPERATOR_OUTPUT_DIR/eval.baseline.log"
tail -50 "$OPERATOR_OUTPUT_DIR/traces.baseline.jsonl"
npu-smi info
```

### 设备被其他任务占用

记录 `npu-smi info`，不要自行杀掉不属于本次 handoff 的进程。由设备管理员确认后再重试。

### OpenCode 模型响应异常

先确认 `/agent` 返回内容和服务器日志，再确认当前 OpenCode 项目配置选择的是 DeepSeek agent。不要通过修改 workflow prompt 来掩盖服务或模型配置错误。

## 11. 验收标准

当前阶段的最低验收标准：

- NPU 可被 `torch_npu` 发现；
- `definition.json` 和 `workload.jsonl` 可读取；
- baseline 评测完整跑完 GELU 的全部 workload；
- 原始 JSONL、日志和 `solution` 校验值已保存；
- OpenCode `/agent` 接口可访问，并能确认 `opencode-deepseek` agent；
- 若完整 workflow 尚未接入 context，报告中明确标记为“未执行”，而不是声称 repair loop 已完成。

完整 workflow 的最终验收标准：

- Phase 0 至 Phase 4 均有结构化状态；
- 每轮 repair 都有对应的 traces 和 summary；
- `required_matched_ratio` 保持为 `0.99`；
- 最终报告能区分 workload pass rate、operator pass/fail、编译错误和环境错误；
- 运行结束不关闭服务器预先启动的 OpenCode。
