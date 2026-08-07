# V3 E2E CLI、终态语义与产物契约

本文描述当前可执行代码和确定性 fixture 已验证的 V3 行为。它不是 crash resume 设计，也不把 raw trace、replay 或工作目录 state 当作运行结果 authority。

生产支持契约是 Linux 和 Python 3.10+。Mandatory CI 只在无硬件 Linux runner 上验证支持下限和当前解释器，不声称验证真实 NPU/GPU。Real accelerator checks remain optional and non-gating.

包依赖边界：base 安装包含 `typing_extensions>=4.12,<5`（`core.compat` 在 Python < 3.10 时回退使用，但生产 floor 已是 3.10+）。`[sqlite]` 是可选 extra，提供 `pysqlite3-binary>=0.5,<1` 作为 stdlib sqlite3 的二进制回退；不安装 `[sqlite]` 只会禁用 session manager 的 SQLite completion evidence，不会让任何常规 runtime import 崩溃。`[dev]` 提供 pytest/PyYAML/pydantic/tomli 测试工具链；`[dashboard]` 提供 rich/textual 渲染器。CI 永远只安装 base + `[dev]`，不把 `[sqlite]` 设为必需。

## 1. 公共入口

日常入口是 `src/scripts/run_seam.sh`。它把参数交给 `run_e2e_v3.sh`，默认服务器为 `http://127.0.0.1:4098`，默认 workflow 为 `src/workflows/seam_auto_default.yaml`，Phase 5 上限为 8，Review Gate 关闭，输出项目保留，容器保留，raw trace 关闭。

`run_e2e_v3.sh` 也可直接使用。它的 shell 参数名称包括 `--workflow`、`--max-iter`、`--max-review-iter`、`--review`、`--no-review`、`--no-keep-temp` 和 `--dry-run`，并将有效值转换到 Python 参数。两个 shell 启动器都要求项目路径或 `--continue-from` 二选一，并拒绝 continuation 加 workflow override。

高级自动化可直接调用 Python 入口。以下示例由真实 `build_parser()` 执行。

<!-- cli-contract:e2e-direct -->
```bash
PYTHONPATH=src python -m tests.e2e.e2e_test_v3 \
  --project-dir /absolute/path/to/cuda-project \
  --workflow-path src/workflows/seam_auto_default.yaml \
  --server-url http://127.0.0.1:4098 \
  --max-phase5-iter 5 \
  --review-gate \
  --max-review-iter 3 \
  --review-fail-closed \
  --keep-temp-dir \
  --container-retention retain \
  --opencode-readiness message \
  --opencode-message-timeout 120
```

Terminal continuation 必须使用显式父 summary 路径，不进行 latest、时间戳或目录扫描。

<!-- cli-contract:e2e-continuation -->
```bash
PYTHONPATH=src python -m tests.e2e.e2e_test_v3 \
  --continue-from /absolute/path/to/e2e-reports/parent-run-001/summary.json \
  --server-url http://127.0.0.1:4098 \
  --container-retention retain \
  --no-save-agent-trace
```

## 2. Python 参数和精确默认值

下面标记区间由 `src/tests/test_documented_cli_contracts.py` 与真实 parser 的全部长参数比较。

<!-- cli-contract:python-flags:start -->
| 参数 | parser 默认值 | 当前含义 |
| --- | --- | --- |
| `--server-url` | `None` | 显式 OpenCode URL；shell 启动器默认传入 4098。 |
| `--max-phase5-iter` | `5` | 正整数，Phase 5 执行上限。 |
| `--max-review-iter` | 未设置 | 只能出现一次；最终优先级为 CLI、workflow、framework、内置 3。 |
| `--review-fail-closed` | 未设置 | CLI strict override。 |
| `--no-review-fail-closed` | 未设置 | 只放宽 `reject_exhausted`。 |
| `--keep-temp-dir` | `False` | 直接运行是否保留复制项目；continuation 当前强制保留子项目。 |
| `--container-retention` | `retain` | `retain` 或 `delete`，只能出现一次。 |
| `--save-agent-trace` | 未设置 | 显式启用 raw recursive trace。 |
| `--no-save-agent-trace` | 未设置 | 显式关闭 trace；省略也不启用。 |
| `--project-dir` | `None` | 与 `--continue-from` 必须且只能提供一个。 |
| `--continue-from` | `None` | 显式终态父 `summary.json`。 |
| `--agent` | `None` | 覆盖自动选择的 Agent 名称。 |
| `--output-dir` | `None` | 直接运行的输出项目根；continuation 当前接受但不使用。 |
| `--user-constraints` | `None` | 文件路径或边界层解析的约束输入。 |
| `--review-gate` | `False` | 启用 review/improvement loop。 |
| `--framework-config` | `None` | framework 配置 override。 |
| `--server-auto-start` | `True` | 兼容参数；当前实际决策只读取 `--server-no-auto-start`。 |
| `--server-no-auto-start` | `False` | 禁止 framework 自动启动 OpenCode。 |
| `--server-port` | `0` | 自动启动时的端口偏好，0 表示自动选择。 |
| `--opencode-readiness` | `message` | `off`、`basic` 或 `message`。 |
| `--opencode-message-timeout` | `120` | 仅限 message readiness probe 的正整数秒数。 |
| `--dashboard-mode` | `auto` | `auto`、`on` 或 `off`；`auto` 仅在交互式 TTY 且非 CI 时启用实时仪表盘。 |
| `--dashboard` | `False` | 强制启用实时终端仪表盘（等价于 `--dashboard-mode on`）。 |
| `--no-dashboard` | `False` | 强制关闭实时终端仪表盘（等价于 `--dashboard-mode off`）；关闭时运行行为与无仪表盘版本完全一致。 |
| `--dashboard-backend` | `auto` | `auto`、`textual` 或 `rich`；强制指定渲染后端，`auto` 时优先 textual 再 rich。 |
| `--seal-manifest` | `False` | 直接运行结束后创建并封存根 `run-manifest.v1.json`，使该运行有资格被 `--continue-from` 续做。 |
| `--verbose` | `False` | 启用 DEBUG 日志。 |
| `--workflow-path` | `None` | 直接运行的 workflow；与 continuation 冲突。 |
<!-- cli-contract:python-flags:end -->

已验证的冲突：两个 run mode 同时出现、两个 trace flag 同时出现、两个 review strictness flag 同时出现、重复 retention、重复 max review，以及 continuation 加 workflow。Python parser 本身接受 continuation 加 `--output-dir`，但当前 coordinator 不转发该值；文档不把它宣传为功能。

## 3. Review 默认值和结果 authority

最终 review policy 按 CLI、workflow、framework、内置默认值解析。内置默认是 3 个 logical review rounds 和 strict `fail_closed=true`。`--review-gate` 仍默认关闭。

验证成功只是必要条件。普通 PASS 还需要 review disabled，或 review enabled 且最终保留一个精确解析为 `accept` 的有效 round。Agent prose、phase 展示文字和 summary 错误文本都不能自行成为 acceptance authority。Phase 5 的 accepted attempt 还必须有同一进程中完成并通过完整性校验的实际 receipt。

| validation | 最终 review disposition | strict | authority 结果 |
| --- | --- | --- | --- |
| 成功 | `disabled` | 任意 | `passed` |
| 成功 | `accepted`，精确 `accept` | 任意 | `passed` |
| 成功 | `reject_exhausted` | `true` | `failed` |
| 成功 | `reject_exhausted` | `false` | `passed_with_reviews` |
| 成功 | `unknown` | 任意 | `failed` |
| 成功 | `session_error` | 任意 | `failed` |
| 成功 | `improvement_error` | 任意 | `failed` |
| 失败 | 任意 | 任意 | `failed` |

`reject` 是有剩余预算时的中间状态，不是可发布的 terminal PASS。Compatibility 只放宽 exhaustion，绝不放宽 unknown、session、improvement 或 validation failure。

`RunOutcome` 冻结后控制 `summary.overall_status` 和常规退出映射。展示 phase、trace 诊断和普通 cleanup 诊断不改写它。退出码为：PASS 或 `passed_with_reviews` 为 0，FAIL 为 1；仅当迁移已经 PASS、用户明确请求授权容器清理且清理失败时为 2。Required finalization 或 required summary publication 失败仍为 1，并且不得打印 PASS headline。

## 4. Session timeout 可观测性

`--opencode-message-timeout=120` 只限制 readiness message probe；它不会修改工作流 Agent timeout、重试次数或 retry policy。诊断 subprocess 上限为该值加 30 秒。

当前 session manager 的可观测默认值如下：

- HTTP 默认 30 秒。
- 调用方省略 command timeout 时，有效 session wait 是 30,000 秒。
- public `wait_for_idle` 默认 300 秒。
- hard HTTP error 后的等待上限为 300 秒。
- message POST transport timeout 是有效 session wait 加 30 秒。
- 非有限 timeout 在 POST 前拒绝。
- POST 一旦被服务器接受，后续 timeout 处理只轮询 status、message history 和 TODO 收敛，不重新 POST 同一命令。

这些 timeout 会进入 telemetry 和 schema-v2 correlation 的 transport/framework observability。文档不承诺或暗示 retry policy 已变化。

## 5. Terminal continuation 边界

Continuation 只消费 terminal PASS 或 FAIL。输入必须是绝对、无 traversal、无 symlink/junction 组件、大小不超过 2 MiB 的普通 `summary.json`。Summary、sealed run manifest、workflow digest、workspace identity、terminal anchor、sealed resource lifecycle 和 accepted receipt 必须一致。

| 父状态 | 记录 anchor 相对 Phase 5 | 子起点 | inherited canonical | accepted Phase 5 receipt |
| --- | --- | --- | --- | --- |
| PASS | before / at / after | 唯一 Phase 5 | Phase 5 前成功 predecessor | 不继承，Phase 5 重新执行 |
| FAIL | before | 失败 anchor | anchor 前成功 predecessor | 不继承 |
| FAIL | at | Phase 5 | Phase 5 前成功 predecessor | 不继承 |
| FAIL | after | 失败 anchor | anchor 前成功 predecessor，包含 Phase 5 | 必须继承并验证准确 accepted receipt |

每次 continuation 都创建新 child run ID、全新 OpenCode root/role sessions 和独立报告命名空间。它不复制 session，不恢复 in-flight Agent，不从 `.sm-artifacts/state.json` 或 checkpoint 接续，也不重用 parent selector。Parent summary、report、sealed artifacts 和 trace payload 保持 byte-immutable，child 只保留有 digest/size 的 parent evidence 或 parent trace reference。

Retained environment verification 在创建 session、backend 或 child side effect 前执行。绑定 authority 是显式 `continuation_target` 引用，它携带精确的 `environment_id` 和匹配的 `namespace`，并验证两者与已记录环境一致：namespace 单独不是 authority，不接受 list-order、fact-count tiebreaker 或 namespace 缺失时的猜测；引用的环境缺失、重复、或 namespace 不匹配时直接 fail closed。Local backend 必须匹配 interpreter/package fingerprint；container backend 必须匹配 immutable ID、runtime、image、mount、workdir、devices、状态和 ownership facts。缺失、变化、停止或外部环境不会 silent fallback、重建或自动切换；请求会 fail closed。

### 5.1 直接运行的 manifest 封存（opt-in，outcome-neutral）

`--seal-manifest` 是直接运行的可选、独立、outcome-neutral 旁路。只有显式请求且封存成功时，该运行才具备 continuation 资格；不请求、请求但输入不可用、或封存失败的运行都不具备资格。

封存结果通过两个独立 observability 通道发布：

- `manifest-sealing.v1.json` sidecar：`status` 为 `not_requested|succeeded|failed`，附带 `continuation_eligible`、`run_id`，以及成功时的 `manifest_path`/`evidence_dir_path` 或失败时的 redacted `error`。
- `summary.json` 的可选 `manifest_sealing` 投影：`{status, sidecar_path, continuation_eligible}`，由 finalizer 冻结 `overall_status` 之后写入，绝不改写 `overall_status`。

关键不变量：

- 成功封存才会发布根 `run-manifest.v1.json` 和 sealed-artifacts evidence 目录，并设置 `continuation_eligible=true`。封存失败的运行没有根 manifest。
- 封存失败不改变迁移 PASS/FAIL、`RunOutcome`、E2E headline 或封存前的退出码。它只通过 sidecar 和 summary 投影单独提供结果。
- continuation child 的 evidence 封存仍是 required/fatal：子运行必须消费父运行已封存的 evidence，不能用 `--seal-manifest` 重新封存自己的根 manifest（`--seal-manifest` 与 `--continue-from` 冲突，会被 parser 和两个 shell 启动器拒绝）。
- summary 投影是 observability，不是 outcome authority：投影为 `failed` 不会让一个已 PASS 的迁移失去资格，除非父运行自身的封存结果确实未成功。

## 6. Runtime、resource 和 cleanup

`resource-manifest.v1.json` 记录 requested/effective workflow 和 backend、launcher/runtime 观测、execution environment、OpenCode/environment facts、provenance、retention policy、ownership 和 terminal lifecycle。Provenance 明确区分 configured、framework-observed、Agent-reported 和 derived；unknown/error 不会被升级为 observed。当前 resource manifest 的 OpenCode version 仍可能是 unknown，即使 raw trace feature detection 已识别 1.18.5。

Retention 默认 `retain`。删除 authority 不是 manifest 中的 token 字符串，而是同一进程、不可复制、绑定 backend/object/immutable ID/context 的 capability：

- 只允许删除 framework 创建的 image container。
- continuation 删除还要求验证 lineage、retention facts 和仍存活的 exclusive parent-owner lock。
- 删除前重新 inspect immutable ID、running state、`seam.owner` 和 framework ownership label。
- 先 stop 再 remove，成功 receipt 必须绑定删除前 ID。
- 用户或外部 `existing_container` 永不删除。只有 continuation 已验证为同 lineage、framework-owned image container，且 active owner lock 和 live identity/labels 全部匹配时，保留容器 attachment 才可获得删除 authority。
- Evidence seal 成功前不授权删除；失败不会用 broad rollback 清理外部资源。

## 7. Replay

Replay 来自同一进程中被 acceptance authority 选中的实际 Phase 5 receipt，而不是 phase prose、最新文件、mtime 或 trace。它要求 run/attempt identity、reservation authority、stdout/stderr/metadata/custom-op gate 完整性和当前 hash 全部匹配。

Local replay 仅显示已验证 argv/cwd/environment guidance。Container replay 还要求 authenticated runtime、immutable container ID、post-cleanup running 状态和同一个 retained container。停止、删除、替换或外部容器不会生成 replay command。

Replay projection 明确包含 `auto_execute=false` 和 nondeterminism notice。SEAM 永不自动执行 replay，也不保证 dependency、model、service、clock、hardware 或外部数据保持不变。`passed_with_reviews` 不等同于普通 accepted PASS，当前 replay 只对准确 `passed` 开放。

## 8. 生成产物树

目录只列当前 writer 可能生成的项目。`optional` 项只在对应 hook 或错误发生时存在。

```text
e2e-reports/src/e2e-v3-<run-id>/
├── summary.json
│   ├── overall_status / phases / errors
│   ├── runtime              # sanitized environments, retention, access, replay
│   └── trace                # bounded status and correlation only, no raw payload
├── resource-manifest.v1.json
├── run-manifest.v1.json     # continuation child, or direct run when --seal-manifest succeeds
├── before_snapshot.json
├── after_snapshot.json
├── phase_results.json
├── telemetry.json
├── telemetry_bridge.json
├── phase_observability.json # optional
├── agent_io/                # optional legacy full-I/O sidecar, separate from raw trace
├── trace/                   # optional when --save-agent-trace
│   ├── manifest.json        # raw-trace schema v2 in active correlated V3
│   ├── sessions/
│   │   └── <session-artifact>.json
│   └── overflows/
│       └── <overflow-artifact>  # only safe accessible local overflow data
├── artifacts/               # continuation-only child evidence
│   └── pre-continuation/
│       ├── project-baseline.json
│       ├── migration-reports/
│       └── migration-reports.manifest.json
├── .sm-artifacts/           # exclusive final report copy of working evidence
│   └── e2e-v3-<run-id>/
│       ├── execution_journal.jsonl
│       ├── state.json       # observability only, never continuation authority
│       ├── raw/
│       ├── validated/
│       └── shell_attempts/
│           ├── .phase_5_validation-attempt-N.reserved
│           ├── run_entry_script_attemptNNNN.stdout.log
│           ├── run_entry_script_attemptNNNN.stderr.log
│           ├── run_entry_script_attemptNNNN.meta.json
│           └── run_entry_script_attemptNNNN.receipt.json
├── finalization_diagnostics.json # optional callback/cleanup/publication diagnostics
└── traceback.txt                  # optional unhandled failure record

output_projects/<project>_<timestamp>/
├── <migrated project>
├── .venv/                         # workflow-dependent
├── migration_reports/             # custom-op gate and project reports when produced
└── .sm-artifacts/e2e-v3-<run-id>/ # mutable working evidence namespace

e2e-reports/src/parent-run-001/     # immutable terminal parent namespace
└── ... sealed parent evidence ...
e2e-reports/src/child-run-001/      # separate fresh continuation namespace
└── ... child evidence and parent digest references ...
```

Writer-backed canonical paths are `artifacts/pre-continuation/migration-reports/`, `artifacts/pre-continuation/migration-reports.manifest.json`, `trace/sessions/`, and `trace/overflows/`.

Finalizer 只接受位于 report root 内、由当前 hook 新建或实质修改、经过 fingerprint 冻结的文件或目录。Stale destination、symlink escape 和 unchanged pre-existing claim 都拒绝。Raw trace directory 使用 private staging 并在 manifest 完成后整体发布。

## 9. Raw trace 和 OpenCode capability detection

`--save-agent-trace` 是显式 opt-in；省略和 `--no-save-agent-trace` 都不导出。Active V3 导出 schema v2 correlation，standalone legacy exporter 在没有 typed correlation context 时保留 schema v1。

OpenCode capability detection 以真实 endpoint status 和 response body shape 为权威。任何健康的非空 product version（包括非参考版本）作为 metadata 投影到 `manifest.server.versions`；`PINNED_VERSION`（当前为 `1.18.5`）是 verified reference/deployment baseline，publicly exported 但不是 runtime equality gate。Version 字符串与 `1.18.5` 不等本身 non-authoritative（non-gating），实际 capability 由可观察 endpoint/shape/error 决定；missing、非-string 或 malformed version/health evidence 保持 unknown/error（fail-closed），不 silent 升级。这里不承诺 blanket future-version support。

Capability 以真实 endpoint 和 body shape 为准：

- integrated V1 的 `GET /doc` 提供 feature detection；capability 由 endpoint status/body shape 决定，不绑定 `1.18.5` 字面值。
- `GET /session/{id}/message` 不发送正 limit；无 limit 的完整 chronological history 是主消息投影。
- direct `GET /session/{id}/children` 提供 immediate children。
- direct children 404/405 时可读取无分页 `GET /session` fallback listing 作为可访问补充，但 direct capability 仍是 unsupported，完整性仍为 partial。
- `/doc` 或 `/children` 的 404/405 是 honest unsupported；401/403/429、5xx、malformed body、分页 cursor 和 foreign identity 是 error/partial，不是空成功。

Trace 是 raw、recursive、transactional、bounded、outcome-neutral。Accessible data 不脱敏且在接受后不截断；超过 byte/graph bounds、未知 part/tool state、不可访问 outputPath、provider-hidden reasoning、unsupported endpoint、分页或 transport error 都保留 truthful partial reason。详见 [`full_agent_io_logging_design.md`](full_agent_io_logging_design.md)。

## 10. 可选、非 gating 集成检查

Mandatory gate 不需要 OpenCode、Docker、网络、credentials、Torch、CUDA、NPU 或任何硬件。以下检查默认 `SKIP`，必须显式 opt-in；服务、credentials、runtime 或本地 image 不可用时也 `SKIP`，不应使 mandatory gate 失败。

Real OpenCode Phase 0-3 使用现有 integration fixture：

```bash
SEAM_RUN_REAL_OPENCODE_PHASE_0_3=1 PYTHONPATH=src \
  python -m pytest src/tests/test_documented_cli_contracts.py \
  -k optional_real_opencode_phase_0_to_3 -q
```

Generic CPU Docker 不使用 device passthrough，不 pull image，不访问网络。它通过 bind mount 在临时 workspace 写入结果，并由 `--rm` 清理容器。调用者必须指定已经存在的本地、包含 `python` 命令的 CPU image；`docker run --pull=never` 关闭 inspect/run 之间的隐式 pull：

```bash
SEAM_RUN_GENERIC_CPU_DOCKER=1 \
SEAM_GENERIC_CPU_DOCKER_IMAGE=local/python-cpu:test \
PYTHONPATH=src python -m pytest src/tests/test_documented_cli_contracts.py \
  -k optional_generic_cpu_docker -q
```

## 11. Mandatory documentation gate

```bash
PYTHONPATH=src python -m pytest \
  src/tests/test_documented_cli_contracts.py \
  src/tests/e2e/test_e2e_v3_root_entrypoint.py -q
```

该 gate 解析所有 tagged Python examples，比较完整参数表，验证 parser defaults/conflicts，执行 strict/compatibility `RunOutcome` matrix，并确认两个 optional checks 默认 clean skip。

## 12. 可选实时仪表盘

实时终端仪表盘是可选功能，dashboard extra 默认不安装。使用前先安装：

```bash
python -m pip install -e "./src[dashboard]"
```

`--dashboard-mode auto|on|off`（或 `--dashboard` / `--no-dashboard`）控制行为：

- `auto`（默认）：仅在非 CI 的交互式 TTY 上启用仪表盘；否则运行行为与无仪表盘版本完全一致，不创建任何 UI 事件文件。
- `on`：强制启用。未安装任何渲染器时（textual 优先，rich 回退），会在任何副作用之前以 `DashboardBackendUnavailableError` 报错并给出上述安装命令。
- `off`：完全关闭，运行行为与无仪表盘版本完全一致。

仪表盘激活时按 `q` 仅退出仪表盘视图；迁移和日志继续运行。事件遥测仅在仪表盘激活时写入报告目录下的 `ui_events.jsonl`；`off` 或未激活的 `auto` 运行不创建该文件，USAGE.md 也不引用它。
