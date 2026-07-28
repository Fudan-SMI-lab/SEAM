# V3 OpenCode raw trace、完整性与关联边界

本文记录当前已经实现的 V3 raw recursive OpenCode trace。它取代旧的 `agent_io.jsonl + payloads` 方案作为 V3 session/tool 原始证据说明，但不删除 legacy full-I/O sidecar 的兼容能力。两者是不同产物，不能互换路径或完整性声明。

## 1. 启用方式和非目标

Raw trace 默认关闭，只有显式 `--save-agent-trace` 才启用。省略 flag 表示用户未请求且未启用；`--no-save-agent-trace` 表示用户显式请求关闭。两个 flag 互斥。

<!-- cli-contract:trace-direct -->
```bash
PYTHONPATH=src python -m tests.e2e.e2e_test_v3 \
  --project-dir /absolute/path/to/cuda-project \
  --workflow-path src/workflows/seam_auto_default.yaml \
  --save-agent-trace \
  --container-retention retain
```

Trace 是 finalization 的 observational side channel：

- 不修改 OpenCode prompt、Agent response、Phase output 或 validator。
- 不选择 continuation parent，不参与 hydration 或 terminal anchor。
- 不读取或恢复 checkpoint/state。
- 不控制 `RunOutcome` 或 review acceptance。普通 optional trace 失败不改退出码；continuation required evidence publication 失败可使 finalization exit 1。
- 不执行 replay，也不为 replay 提供 authority。
- 不尝试导出 provider-hidden reasoning；只有 OpenCode endpoint 实际返回且 SEAM 可访问的数据可持久化。

Trace export、server/session cleanup 或可选 telemetry 失败会形成诊断和 partial/error status，但不会把冻结的 PASS 改成 FAIL。Continuation child 将 trace export/evidence sealing 作为 required finalization 组合的一部分时，required publication 失败可使 finalization exit 1；这仍不改写原 `RunOutcome`。

## 2. Raw、unredacted 和 bounded 的准确含义

对通过 parser、identity 和安全边界的 accessible endpoint data，导出器保存原始 JSON 值，不脱敏、不改写未知字段、不压缩成 preview，也不截断已经接受的 payload。Duplicate JSON key、malformed schema、unknown part/tool state 和 arbitrary metadata 不会被静默正规化。

“完整”只表示在本次 feature/capability 和 bounds 下，所有可访问数据都已成功捕获。它不表示 OpenCode provider 持有的所有内部状态，更不表示能取得模型供应商隐藏的 chain-of-thought。

默认 hard bounds：

| 维度 | 上限 |
| --- | ---: |
| sessions | 10,000 |
| child edges | 100,000 |
| graph depth | 256 |
| session ID | 4,096 characters |
| 单个 local overflow file | 64 MiB |
| 总 trace artifacts | 512 MiB |
| manifest | 64 MiB |
| overflow references | 100,000 |
| retained errors | 100,000 |

超过任一上限时，导出器不会截断后宣称 complete。它拒绝越界数据或停止扩展对应范围，记录明确 reason，并使 manifest partial/error。Destination 在 publish 前必须不存在；全部 session/overflow artifacts 在 private sibling staging tree 中写入，`manifest.json` 完成后才整体原子发布。未发布 staging 会清理，清理失败也进入诊断。

## 3. OpenCode v1.18.5 feature detection

Capability 来自 endpoint 状态和 body schema，不从 version 字符串单独猜测。

| 能力 | 1.18.5 integrated V1 行为 | 不完整或错误边界 |
| --- | --- | --- |
| Health/version | health 200、`healthy=true`、version `1.18.5` | 非 pinned version 为 unsupported；malformed pinned response 为 error |
| Feature doc | `GET /doc` | 404/405 为 unsupported；其他非 200 为 error |
| Message history | `GET /session/{sessionID}/message`，不发送正 `limit` | positive limit、pagination cursor、foreign session 或 malformed history 不能称为完整 |
| Direct children | `GET /session/{sessionID}/children` | 404/405 为 unsupported；401/403/429/5xx 为 error |
| Fallback listing | direct children 不支持时读取无分页 `GET /session` 并按 `parentID` 过滤 | 只补充 accessible child records，不把 direct capability 改写成 supported，也不让整体变 complete |

V1 message history 是 authoritative persisted message projection；V2 durable history 只做 optional enrichment。Direct children 返回 immediate children，recursive graph 由 exporter 按 seed order 和 response order 做 deterministic breadth-first traversal。

Feature state 使用 supported、unsupported、unknown；组合 contract 使用 compatible、partial、unsupported、error。404/405 的 clean unsupported 不等于空 complete，transport/auth/rate-limit/server/malformed 错误也不 silent fallback。

## 4. Recursive session artifact

每个访问到的 session artifact 保留：

- exact root/current/parent session identity 和 raw `Session.Info`。
- original successful response text 及 endpoint status/header metadata。
- complete no-positive-limit V1 message history，或准确 pagination/transport gap。
- messages、parts、tool states、unknown JSON values 和 raw contract projection。
- direct child capability 与 fallback capture 分开记录。
- safe local overflow copy，或原 `outputPath` reference 加 unavailable reason。
- schema-v2 correlation records，仅在 active V3 提供 typed correlation context 时。

Overflow 文件只有在 absolute、无 traversal、无 symlink/junction、普通文件、位于显式 allowed local root 且未超过上限时才复制。Remote、relative、outside-root、linked、identity changed、unavailable 或 oversized path 保留 reference/reason，不伪装为 captured bytes。Artifact 名来自 hash，不直接使用 hostile session ID 或 source path。

Manifest 记录每个 session 的 completeness、capability、artifact path/size/SHA-256、child edges、overflow inventory、errors、global counts 和 inventory digest。Summary 只保留 bounded trace status/path/correlation，不复制 raw session、message、reasoning、tool 或 parent payload。

## 5. Complete、partial、unsupported 和 error

下面的结构化边界由 documentation contract test 读取：

<!-- trace-contract:boundaries:start -->
| Condition | Required state |
| --- | --- |
| `direct_children_unsupported_with_fallback` | `partial` |
| `provider_hidden_reasoning` | `unavailable` |
| `trace_controls_run_outcome` | `false` |
| `trace_controls_continuation` | `false` |
<!-- trace-contract:boundaries:end -->

以下情况可使 trace truthful partial：

- direct children endpoint unsupported，即使 fallback listing 找到 children。
- message 或 fallback listing paginated。
- unknown message part、tool state 或 unsupported schema extension。
- task lineage 缺失、contradictory parent、cycle、duplicate ID 或 foreign root。
- `outputPath` overflow 不可读取、不安全或超过 bounds。
- session、edge、depth、manifest、artifact、reference 或 error 上限触发。
- correlation orphan、duplicate、cross-run relation 或 required parent trace reference gap。

Pinned contract body malformed、identity splice、auth/rate-limit、HTTP 5xx、transport failure、destination safety failure和 transactional publication failure属于 error/incompatible 证据。它们不会被转成空列表或 complete。

只有所有 required accessible history、session identities、direct child facts、safe overflow 和 correlation 都满足当前 contract 时，manifest 才能 `complete=true`。Provider 不公开的 reasoning 不是可访问数据，但文档和 manifest 不因此声称已捕获隐藏内容。

## 6. Schema versioning

| 调用方式 | raw trace schema | session schema | correlation |
| --- | ---: | ---: | --- |
| standalone legacy exporter，无 typed context | 1 | 1 | 无 |
| active V3 correlated export | 2 | 2 | `seam.trace-correlation` version 1 |

Schema v2 是 additive correlation envelope。它不更改 raw endpoint JSON、Task 21 traversal 或 schema-v1 standalone compatibility。

## 7. Correlation records

`manifest.json.correlation` 使用明确 typed IDs，不从 log prose、filename、mtime 或 display strings 反推：

| Group | 关联边界 |
| --- | --- |
| `run_scope` | run、immediate parent run、lineage root、optional parent trace identity |
| `phase_executions` | run、phase、deterministic phase execution ID |
| `phase5_attempts` | accepted attempt ID 和 attempt number |
| `review_rounds` | Phase 5 iteration、logical review round、framework invocation、session |
| `framework_invocations` | framework invocation、phase execution、session |
| `transport_attempts` | logical invocation、physical attempt/number、event phase、framework invocation、session |
| `sessions` | role/scope、root/current/immediate parent session |
| `tool_calls` | root/current session、message、part、OpenCode `callID`、tool name、optional task child session |

每条 record 绑定一个 run ID。Session/tool correlation 在既有 BFS 访问时投影，不二次 fetch 或重复序列化 session。Timeout observability 带 run、phase、framework、transport、session identity；它不改变 timeout 或 retry policy。

Correlation 的 `complete=false` 可由 orphan、duplicate、cross-run、contradictory phase/review/attempt/parent、malformed tool link、cycle 或 parent reference gap 引起。Manifest 会同时保持 raw source/export errors 和独立 correlation diagnostics。

`authority.correlation=false` 是 schema 明示边界。删除、篡改或缺失 correlation 不得选择 parent、hydrate canonical、修改 checkpoint/anchor、重写 `RunOutcome` 或改变 normal exit mapping。

精确字段定义见 [`trace_correlation_schema.md`](trace_correlation_schema.md)。

## 8. Continuation parent trace reference

Child 可以包含 `run_scope.parent_trace`，但只保存 Task 14 已验证 parent report inventory 中的 identity：

```json
{
  "run_id": "parent-run-001",
  "manifest_path": "/absolute/path/to/e2e-reports/parent-run-001/trace/manifest.json",
  "sha256": "<immutable-parent-manifest-digest>",
  "size_bytes": 1234
}
```

Child exporter 不打开、复制、重写、嵌入或赋权 parent trace/session payload。Parent raw payload 不进入 prompt、canonical output、checkpoint、summary 或 child trace。Reference 也不是 continuation authority；continuation 仍由 terminal summary、sealed run/resource manifests、workflow/workspace、anchor 和 accepted receipt 决定。

## 9. 与其他 artifacts 的关系

| Artifact | 内容 | 完整性/authority |
| --- | --- | --- |
| `telemetry.json` | sessions、commands、events、preview、duration | 轻量 observability，不是 full raw payload |
| `phase_observability.json` | phase/framework/transport timeout 和 correlation facts | optional observational sidecar |
| `.sm-artifacts/.../raw/` | workflow phase raw output | 不是 OpenCode recursive history，不是 continuation checkpoint |
| `.sm-artifacts/.../validated/` | canonical phase output | 后续 workflow/evidence 使用，不注入 trace payload |
| legacy `agent_io/` | 某些 observer 路径的 command/response sidecar | optional、路径覆盖有限，不等同 raw recursive trace |
| `trace/manifest.json` | graph、inventory、completeness、schema-v2 correlation | observational，authority flags 为 false |
| `trace/sessions/<session-artifact>.json` | raw accessible session/message/part/tool data | unredacted within bounds；partial 状态必须保留 |
| `trace/overflows/<overflow-artifact>` | safe accessible local overflow bytes | 不可访问或越界 reference 保持 partial |
| `summary.json.trace` | bounded status/path/correlation projection | 不包含 raw parent/child payload，不控制 outcome |

旧文档中的 `SM_ADAPT_FULL_AGENT_IO_MAX_BYTES=0`、默认 redaction、`agent_io.jsonl` 主路径、自动 replay 或“完整隐藏 reasoning”都不是当前 raw trace contract。若 legacy sidecar 存在，必须按 legacy optional artifact 标注，不能作为 V3 trace 的规范路径。

## 10. 安全和操作要求

- Raw trace 不脱敏，可能包含用户输入、路径、tool arguments 和 OpenCode 返回的敏感内容。只在受控 report root 显式启用，按敏感证据管理。
- 文档示例不包含 credentials、API keys、ownership labels 或 deletion capability。
- Trace capture 是 read-only OpenCode integration，不执行 tool payload、overflow text、replay command 或 parent content。
- Capture bounds 是拒绝和 truthful partial 边界，不是 silent truncation。
- Retention/deletion policy 与 trace 独立。Trace 既不能授权删除，也不能阻止真实 ownership checker 拒绝删除。
- Mandatory tests 使用 scripted OpenCode client 和 hardware-free fixtures；不要求 live service、network、Docker、credentials 或 accelerator。

## 11. 验证入口

Parser、default-off tri-state、example shape、conflict 和 optional-skip contract：

```bash
PYTHONPATH=src python -m pytest src/tests/test_documented_cli_contracts.py -q
```

Recursive exporter、OpenCode feature detection、limits、transactionality 和 schema-v2 correlation 的确定性 suites 位于：

```text
src/tests/test_opencode_contract.py
src/tests/test_opencode_trace_client.py
src/tests/test_trace_exporter.py
src/tests/test_trace_lifecycle.py
src/tests/test_trace_correlation.py
src/tests/e2e/test_e2e_v3_runtime_features.py
```

Real OpenCode Phase 0-3 和 generic CPU Docker 只作为 [`E2E_TESTING.md`](E2E_TESTING.md) 中的显式 opt-in、non-gating checks；环境不可用时 clean skip。
