# V2 Full Agent I/O Logging Design

> 目标: 在不影响 `src` 现有 YAML 工作流、验证 schema、Phase 输出和报告生成逻辑的前提下，确保每一次 OpenCode Agent 调用的完整输入和完整返回都可审计、可回放、可定位。

---

## 1. 背景与现状

`src` 目前存在多条执行路径，Agent 输入输出的持久化能力不一致：

| 执行路径 | 当前保存情况 | 主要问题 |
|---|---|---|
| V1 `StateMachineOrchestrator` | `raw_payload` 保存完整 `prompt`、`response`、`parsed_output` | V1 行为较完整，但不是 V2 YAML 主路径 |
| V2 `PhaseRunner` | raw artifact 注入 `_meta.prompt` 和 `_meta.response` | 只覆盖 `PhaseRunner` 路径，不覆盖所有 `WorkflowExecutor` 子流程 |
| V2 `WorkflowExecutor` | raw/validated artifact 主要保存解析后的 phase output | 不保证保存完整 prompt；若 Agent 返回可解析 JSON，原始 response 也会丢失 |
| V2 `TelemetryObserver` | `telemetry.json` 保存 500 字 command/response preview 和长度 | 适合统计，不适合完整审计 |
| Phase 5 `RepairLoopEngine` | 保存 stdout/stderr、classification raw_response、repair instruction/response | 对修复循环较完整，但不覆盖所有普通 LLM phase |

因此，若只依赖现有 `raw/*.json` 或 `telemetry.json`，无法稳定回答：

- 某个 Agent 在某个 phase 第 N 次到底收到了什么完整 prompt？
- Agent 完整返回了什么？
- 哪个 session、哪个 role、哪个 phase、哪个 retry 触发了这次请求？
- 失败时请求是否发出、返回是否为空、是否被 JSON 修正 prompt 覆盖？

---

## 2. 设计目标

### 2.1 必须满足

1. **完整性**: 保存每次 `send_command()` 的完整输入文本和完整返回文本。
2. **旁路性**: 不改变 `send_command()` 返回值，不改变 phase output，不影响 validator。
3. **覆盖面**: 覆盖主 phase、sub-workflow、repair、review、JSON correction、experience query/refine 等经 `SessionManagerLike.send_command()` 发送的调用。
4. **可关联**: 每条记录必须能关联到 `run_id`、`phase_id`、`session_id`、sequence、时间、耗时和状态。
5. **安全开关**: 默认可关闭或可配置，避免在不需要时产生超大日志或保存敏感内容。
6. **失败可见**: 即使 Agent 调用抛异常，也要记录 prompt、错误、耗时和空 response。
7. **不污染上下文**: 完整 I/O 不能写入 canonical phase output，避免 Phase 6 或后续 phase 读入大段 prompt/response。

### 2.2 非目标

1. 不替代 OpenCode server 自身的 `/session/:id/message` 历史。
2. 不记录 HTTP 原始 header/body、认证信息或底层网络 trace。
3. 不强行解析 Agent 内部 reasoning 或 tool call，除非它已经包含在 OpenCode 返回文本中。
4. 不改变 Agent prompt 内容，不引入额外 Agent 调用。

---

## 3. 推荐方案: TelemetryObserver 旁路 Full I/O Sink

推荐在 `tests/e2e/e2e_observer.py` 的 `TelemetryObserver.send_command()` 中增加一个旁路式 full I/O writer。

原因：

- V2 E2E 中 `WorkflowExecutor` 的 `session_mgr` 是 `TelemetryObserver`，因此普通 LLM phase、sub-workflow LLM phase、review、correction prompt 都会经过该方法。
- 该层已经掌握 `phase_id`、`sequence`、`session_id`、duration、status、command_length、response_length。
- 修改该层不会影响 `WorkflowExecutor`、`PhaseRunner`、`RepairLoopEngine` 的业务返回值。
- 现有 `telemetry.json` 仍保留 preview 统计；新增 full I/O 文件作为审计 sidecar。

---

## 4. 输出目录设计

### 4.1 主输出位置

建议写入 E2E report 目录：

```text
SEAM/e2e-reports/src/<YYYYMMDD_HHMMSS>/agent_io/
├── agent_io.jsonl
├── payloads/
│   ├── 000001_prompt.txt
│   ├── 000001_response.txt
│   ├── 000002_prompt.txt
│   └── 000002_response.txt
└── index.json
```

### 4.2 迁移项目内副本

可选地复制到迁移项目 artifact 目录：

```text
output_projects/<project>_<timestamp>/.sm-artifacts/<run_id>/agent_io/
```

建议实现上只在 report 目录写主日志；如果需要随 `.sm-artifacts` 一起归档，再在结束阶段复制一份或创建 `agent_io_path` 引用即可。

### 4.3 为什么不直接写入 `raw/phase_*.json`

不推荐把完整 I/O 写入每个 phase raw artifact 的原因：

- `WorkflowExecutor` 的 raw/validated 目前保存 phase output，强行注入 `_meta` 容易污染 schema 和后续上下文。
- Phase 6 会读取前序 artifact，若 `_meta` 进入 canonical 会造成 prompt 上下文膨胀。
- 某些请求不是完整 phase（如 correction prompt、experience query、review retry），没有天然的 phase output 文件名。
- JSON 文件内嵌超长 prompt/response 不利于快速 grep 和增量追加。

---

## 5. 日志格式设计

### 5.1 `agent_io.jsonl`

每次 `send_command()` 追加一行 JSON，保存元数据和 payload 文件引用：

```json
{
  "schema_version": "1.0",
  "run_id": "e2e-v2-facab82c7f63",
  "sequence": 12,
  "phase_id": "phase_5_validation",
  "session_id": "ses_xxx",
  "role": "code_adapter",
  "agent": "Sisyphus",
  "lifecycle": "persistent",
  "started_at": "2026-04-30T09:30:00.123456+00:00",
  "ended_at": "2026-04-30T09:31:02.789000+00:00",
  "duration_seconds": 62.665,
  "timeout_seconds": 3600,
  "status": "passed",
  "error": null,
  "command_length": 18244,
  "response_length": 9120,
  "command_sha256": "...",
  "response_sha256": "...",
  "command_path": "agent_io/payloads/000012_prompt.txt",
  "response_path": "agent_io/payloads/000012_response.txt",
  "command_preview": "first 500 chars...",
  "response_preview": "first 500 chars..."
}
```

### 5.2 Payload 文本文件

完整 prompt/response 分开落盘：

```text
agent_io/payloads/000012_prompt.txt
agent_io/payloads/000012_response.txt
```

优点：

- JSONL 轻量、可快速扫描。
- 大文本不影响 JSONL 可读性。
- 文件 hash 可验证完整性。
- 失败时 response 文件可以为空，但仍保留 prompt。

### 5.3 `index.json`

结束时可生成聚合索引，便于人读：

```json
{
  "schema_version": "1.0",
  "run_id": "e2e-v2-facab82c7f63",
  "generated_at": "...",
  "total_calls": 23,
  "by_phase": {
    "phase_0_env_detect": [1],
    "phase_5_validation": [8, 9, 10, 11, 12]
  },
  "by_session": {
    "ses_xxx": [1, 2, 3]
  }
}
```

`index.json` 不是必要路径；第一版可只实现 `agent_io.jsonl + payloads/`。

---

## 6. 配置开关

### 6.1 环境变量

第一优先级使用环境变量，便于不改 CLI 即可启用：

```bash
SM_ADAPT_FULL_AGENT_IO=1
SM_ADAPT_FULL_AGENT_IO_MAX_BYTES=0
SM_ADAPT_FULL_AGENT_IO_REDACT=1
```

含义：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `SM_ADAPT_FULL_AGENT_IO` | `0` | 是否启用完整 Agent I/O 落盘 |
| `SM_ADAPT_FULL_AGENT_IO_MAX_BYTES` | `0` | 单个 prompt/response 最大保存字节数；`0` 表示不限 |
| `SM_ADAPT_FULL_AGENT_IO_REDACT` | `1` | 是否启用基础敏感信息脱敏 |

### 6.2 YAML 配置

第二阶段可加入 `framework_defaults.yaml`：

```yaml
framework:
  artifacts:
    full_agent_io:
      enabled: false
      max_bytes_per_payload: 0
      redact: true
      write_payload_files: true
```

环境变量优先级高于 YAML，方便临时调试。

---

## 7. 代码改动设计

### 7.1 新增 `AgentIOLogger`

建议新增文件：

```text
src/core/agent_io_logger.py
```

职责：

- 创建 `agent_io/` 和 `payloads/` 目录。
- 原子追加 `agent_io.jsonl`。
- 写完整 prompt/response payload。
- 计算 sha256。
- 进行可选脱敏和可选截断。

接口草案：

```python
class AgentIOLogger:
    def __init__(self, output_dir: str, run_id: str = "", enabled: bool = False, max_bytes: int = 0, redact: bool = True):
        ...

    def record(
        self,
        *,
        sequence: int,
        phase_id: str | None,
        session_id: str,
        role: str | None,
        agent: str | None,
        lifecycle: str | None,
        started_at: str,
        ended_at: str,
        duration_seconds: float,
        timeout_seconds: int,
        status: str,
        command: str,
        response: str,
        error: str | None,
    ) -> dict[str, str]:
        ...
```

返回值可包含 `jsonl_path`、`command_path`、`response_path`，供 telemetry metadata 引用。

### 7.2 修改 `TelemetryObserver`

文件：

```text
src/tests/e2e/e2e_observer.py
```

新增可选构造参数：

```python
def __init__(self, session_mgr, output_dir, agent_io_logger=None):
    self._agent_io_logger = agent_io_logger
```

在 `send_command()` 的 `finally` 中，保留现有 `CommandMetric` 逻辑，同时旁路写完整 I/O：

```python
if self._agent_io_logger is not None:
    record_paths = self._agent_io_logger.record(
        sequence=self._command_sequence,
        phase_id=active_phase,
        session_id=session_id,
        role=metric.role if metric else None,
        agent=getattr(metric, "agent", None),
        lifecycle=metric.lifecycle if metric else None,
        started_at=started_at,
        ended_at=_utc_now(),
        duration_seconds=duration_seconds,
        timeout_seconds=timeout,
        status=status,
        command=command,
        response=response,
        error=error_message,
    )
```

注意：当前 `SessionMetric` 没有 `agent` 字段，如果需要记录 agent，可扩展 dataclass；否则先记录 `role/lifecycle/session_id`。

### 7.3 修改 `e2e_test.py` / `e2e_test_v2.py`

文件：

```text
src/tests/e2e/e2e_test.py
src/tests/e2e/e2e_test_v2.py
```

在创建 `TelemetryObserver` 前初始化 logger：

```python
agent_io_logger = AgentIOLogger.from_env(output_dir=str(output_dir), run_id=run_id)
observer = TelemetryObserver(session_mgr, output_dir, agent_io_logger=agent_io_logger)
```

在 `summary.json` 中加入路径引用：

```json
"agent_io_paths": {
  "jsonl": ".../agent_io/agent_io.jsonl",
  "payload_dir": ".../agent_io/payloads"
}
```

如果不想改 `RunSummary` dataclass，第一版也可以把路径写入 `telemetry.json.metadata.agent_io_path`。

### 7.4 可选修改 `HookManager.copy_artifacts`

如果希望 `.sm-artifacts` 副本也包含 `agent_io/`，有两种做法：

1. 结束时把 report 目录下 `agent_io/` 复制到 `{project_dir}/.sm-artifacts/{run_id}/agent_io/`。
2. 直接将 `AgentIOLogger` 的输出目录设置为 `{project_dir}/.sm-artifacts/{run_id}/agent_io/`，再由现有 `copy_artifacts()` 复制到 report 目录。

推荐第二种，但需要 `TelemetryObserver` 初始化时拿到 `artifact_store.artifact_dir`。如果为了最小改动，第一版可先写 report 目录。

---

## 8. 脱敏策略

完整 Agent I/O 可能包含路径、token、API key、私有模型路径或用户输入，因此需要基础脱敏。

### 8.1 默认脱敏规则

保存前对 prompt/response 做以下替换：

| 类型 | 示例 | 替换 |
|---|---|---|
| Bearer token | `Bearer abc...` | `Bearer <REDACTED>` |
| API key | `sk-...` | `<REDACTED_API_KEY>` |
| 环境变量密钥 | `HF_TOKEN=...` | `HF_TOKEN=<REDACTED>` |
| 密码字段 | `password: xxx` | `password: <REDACTED>` |

### 8.2 不建议脱敏的内容

- 普通文件路径：迁移审计需要路径定位。
- stdout/stderr：除非命中明确敏感模式。
- Agent 返回中的诊断文字：除非命中明确敏感模式。

---

## 9. 性能与容量控制

### 9.1 容量风险

大型迁移任务中，单次 prompt 可能超过几十 KB，response 可能超过数百 KB；Phase 5 多轮修复会进一步放大日志。

### 9.2 控制措施

1. 默认关闭 full I/O，仅按需开启。
2. 支持 `max_bytes_per_payload` 截断。
3. JSONL 只保存 metadata 和文件引用，大文本放 payload 文件。
4. 可选后处理压缩：运行结束后将 `agent_io/payloads/` 打包为 `payloads.tar.gz`。
5. 保留 `command_sha256` 和 `response_sha256`，即使截断也能标记 `truncated=true`。

---

## 10. 与现有 artifact 的关系

| 文件 | 定位 | 是否保存完整 I/O | 保留原因 |
|---|---|---|---|
| `telemetry.json` | 统计与可视化 | 否，只保存 preview | 保持轻量 |
| `raw/*.json` | phase 原始输出 | 视执行路径而定 | 保持现有 phase artifact 语义 |
| `validated/*.json` | schema 通过后的 canonical 输出 | 否 | 供后续 phase 消费，必须干净 |
| `execution_journal.jsonl` | phase 时间线 | 否 | 快速定位 phase 状态 |
| `agent_io/agent_io.jsonl` | 完整 Agent 调用审计索引 | 是，引用 payload | 新增主审计源 |
| `agent_io/payloads/*.txt` | 完整 prompt/response | 是 | 可回放、可排查 |

---

## 11. 运行时查询方式

### 11.1 查看某个 phase 的所有 Agent 调用

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path('e2e-reports/src/<timestamp>/agent_io/agent_io.jsonl')
for line in path.read_text().splitlines():
    item = json.loads(line)
    if item.get('phase_id') == 'phase_5_validation':
        print(item['sequence'], item['session_id'], item['status'], item['command_path'], item['response_path'])
PY
```

### 11.2 打开某次 prompt/response

```bash
less e2e-reports/src/<timestamp>/agent_io/payloads/000012_prompt.txt
less e2e-reports/src/<timestamp>/agent_io/payloads/000012_response.txt
```

### 11.3 与 phase artifact 对齐

1. 从 `summary.json` 找 `run_id` 和 `temp_dir`。
2. 从 `phase_results.json` 找失败 phase。
3. 从 `agent_io.jsonl` 按 `phase_id` 找 sequence。
4. 从 `execution_journal.jsonl` 和 `raw/*.json` 查 phase output / stdout / stderr。

---

## 12. 验证计划

### 12.1 单元测试

新增测试文件：

```text
src/tests/test_agent_io_logger.py
```

测试点：

- disabled 时不创建文件。
- enabled 时写入 `agent_io.jsonl` 和 payload 文件。
- prompt/response hash 正确。
- 异常场景保存 prompt、error，response 为空。
- max bytes 截断时标记 `command_truncated` / `response_truncated`。
- 脱敏规则生效。

### 12.2 集成测试

扩展 `e2e_smoke_test.py` 或新增轻量 fake session：

- 设置 `SM_ADAPT_FULL_AGENT_IO=1`。
- 运行最小 workflow。
- 断言 `agent_io/agent_io.jsonl` 存在。
- 断言调用次数等于 `observer.command_count`。
- 断言第一条记录可打开 prompt/response 文件。
- 断言 `validated/*.json` 不包含完整 prompt/response。

### 12.3 手工验收

```bash
SM_ADAPT_FULL_AGENT_IO=1 bash scripts/run_e2e.sh 05_InsectID --dry-run
```

如果 dry-run 不触发 Agent，则使用最小 test project：

```bash
SM_ADAPT_FULL_AGENT_IO=1 python -m tests.e2e.e2e_test_v2 \
  --server_type opencode --server_url http://127.0.0.1:4098 \
  --project-dir src/test_project_template \
  --keep-temp-dir
```

验收标准：

- `summary.json` 仍能生成。
- `telemetry.json` 仍保持 preview 格式。
- `agent_io/agent_io.jsonl` 包含完整调用索引。
- `payloads/*_prompt.txt` 和 `payloads/*_response.txt` 存在。
- `validated/*.json` 不包含 `_meta` 或完整 prompt/response。

---

## 13. 分阶段实施计划

### Phase A: 最小可用实现

1. 新增 `core/agent_io_logger.py`。
2. 扩展 `TelemetryObserver` 构造函数和 `send_command()`。
3. 在 `e2e_test.py` / `e2e_test_v2.py` 通过 env 创建 logger。
4. 写入 `agent_io.jsonl + payloads/`。
5. 添加单元测试。

风险低，覆盖面高，不改 workflow 行为。

### Phase B: 配置化与归档

1. 支持 `framework_defaults.yaml` 配置。
2. 在 `summary.json` 或 `telemetry.json.metadata` 写入 `agent_io_path`。
3. 将 `agent_io/` 复制到 `.sm-artifacts/<run_id>/agent_io/` 或在 report 目录保留主副本。

### Phase C: 高级审计能力

1. 生成 `index.json`。
2. 提供 `scripts/show_agent_io.py` 查询工具。
3. 支持压缩归档。
4. 可选对接 OpenCode server `/session/:id/message` 做补充抓取。

---

## 14. 推荐最终目录结构

```text
e2e-reports/src/<timestamp>/
├── summary.json
├── phase_results.json
├── telemetry.json
├── before_snapshot.json
├── after_snapshot.json
├── agent_io/
│   ├── agent_io.jsonl
│   ├── index.json                  # optional
│   └── payloads/
│       ├── 000001_prompt.txt
│       ├── 000001_response.txt
│       ├── 000002_prompt.txt
│       └── 000002_response.txt
└── .sm-artifacts/
    └── <run_id>/
        ├── execution_journal.jsonl
        ├── raw/
        ├── validated/
        └── reports/
```

---

## 15. 关键决策

1. **不把完整 I/O 注入 canonical**: 避免后续 phase 上下文膨胀和 schema 污染。
2. **不依赖 OpenCode server session history**: server 历史可作为外部补充，但本项目必须自持审计日志。
3. **Telemetry 保持轻量，full I/O 单独 sidecar**: 保留现有 telemetry 消费方式。
4. **默认关闭、按需开启**: 避免磁盘膨胀和敏感信息意外落盘。
5. **优先拦截 `send_command()`**: 覆盖面最大，业务侵入最小。

---

## 16. 结论

要让 V2 稳定具备类似 V1 的完整 Agent 输入输出追踪能力，最佳方案是在 `TelemetryObserver.send_command()` 层新增可配置的 `AgentIOLogger` 旁路日志。

该方案满足：

- 对 V2 工作流零语义影响。
- 对 schema/validator 零影响。
- 对 Phase 6 上下文零污染。
- 覆盖所有经 observer 转发的 Agent 请求。
- 与现有 `summary.json`、`telemetry.json`、`.sm-artifacts` 形成互补。

第一版建议只实现 `SM_ADAPT_FULL_AGENT_IO=1` 环境变量开关和 `agent_io.jsonl + payloads/`，确认稳定后再接入 YAML 配置、索引文件和压缩归档。
