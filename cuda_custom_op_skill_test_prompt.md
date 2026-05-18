完成 CUDA/C++ custom op 到 Ascend NPU custom op 的全量迁移任务，做到以下的8个要求。

迁移规范：本文下列 8 个要求是本任务的强制执行规则；如果当前 workspace root 下存在 `.skills/cuda-custom-op-to-npu-custom-op/`，其中的 checklist、template、validation gate、report parity、fallback 检查、coverage 检查和性能检查也必须落实。

注意：上述迁移规范不是“参考文档”或“经验资料”，而是本任务的强制执行规则。规范中的 checklist、template、validation gate、report parity、fallback 检查、coverage 检查、性能检查都必须落实到目标项目的真实 artifacts、真实运行日志和 `migration_reports/` 证据中。

## 1. Manifest 和 scope 锁定

1. 先完成 CUDA/C++ custom op inventory 和 migration manifest。
2. manifest 必须覆盖每个 in-scope 算子、public entry、framework alias，并映射：
   `source public entry -> semantic operator -> generated OPP entry -> adapter callable -> coverage key -> parity scope -> benchmark scope`。
3. scope discovery 必须至少检查：CUDA/C++ binding/registration、pybind/export table、Python wrapper、autograd forward/backward、framework alias、所有 `.cu/.cuh/.cpp/.cc` launch site、setup/build 配置和项目测试入口。
4. manifest 创建后即冻结 scope。每个 manifest row 都是必须完成的 in-scope 条目。
5. 除非用户明确书面批准删除某个 row，否则不得把任何 row 降级为 out-of-scope、optional、future work、blocked 后收尾。
6. 如果用户批准 scope 变更，必须在 `migration_reports/scope_change_decision.md` 中记录：原始 row、删除/降级原因、用户批准原文、剩余 scope、对最终 DONE 判断的影响。
7. 任何“framework alias 多于真实 OPP 数”的情况都必须在 manifest 中解释清楚，但 alias 合并不等于删除 semantic operator；forward、backward、grad、training-only path 只要是 public/custom-op 语义入口，默认都在 scope 内。
8. inventory 和 manifest 必须同时记录以下机器可检查字段：`inventory_count`、`manifest_entries`、`closed_pass_entries`、`remaining_entries`、`full_migration_status`。
9. `inventory_count` 必须等于 `manifest_entries`；若 inventory 中发现条目但 manifest 未覆盖，final evidence validation 必须失败。
10. `remaining_entries > 0`、`closed_pass_entries < manifest_entries` 或 `full_migration_status != FULL_PASS` 时，不得声明 SUCCESS / DONE。
11. 不得通过创建 single-entry manifest、sample manifest、MVP manifest 或只覆盖已完成条目的 manifest 来规避全量迁移范围。

## 2. MVP 只允许作为启动阶段，禁止作为最终交付

1. 若全量过大，可以先选择一个最小可行 semantic operator 跑通端到端闭环：
   `OPP 源码 -> 生成/安装 -> adapter -> direct/reference test -> public API/integration/e2e -> runtime_coverage 正整数调用 > 0 -> baseline/custom 性能`。
2. MVP / 最小可行 semantic operator 只是 bootstrap/debug 阶段，不是最终交付物，也不是停止点。
3. MVP 通过后，必须立即继续处理 manifest 中下一个未完成 row，直到全部 in-scope rows 完成。
4. 不得因为一个算子、一个 public route、一个 smoke test、一个 evidence JSON、一次 report 更新或一次 review 通过就 final 停止。
5. 若使用 MVP，报告中必须明确标注 `MVP_ONLY` 或 `MVP_PASS_FULL_MIGRATION_INCOMPLETE`，并列出所有 remaining rows；该状态不能作为最终成功。
6. MVP 的性能、coverage、parity、adapter hash 只对 MVP row 有效，不得外推为其它算子、其它 public entry、其它 dtype、backward/grad/gradgrad 或项目级端到端迁移证据。

## 3. 全量迁移执行规则

1. 按 manifest 逐项迁移，直到所有 in-scope rows 达到 DONE 定义。
2. 每个 in-scope row 都必须完成并记录以下真实证据：
   - Ascend OPP 源码、生成、安装 artifacts；
   - adapter/import/link 成功证据；
   - direct kernel/reference parity test；
   - public API 或 framework integration 中真实语义由 custom op 驱动的证据；
   - 项目级 regression/e2e/training/benchmark 覆盖；
   - same-run `runtime_coverage` 正整数 custom call count；
   - baseline/custom 实测 `speedup_vs_baseline`、速度比或 slowdown；
   - no fallback / no baseline-only / no zero-call / no builtin-op contamination 证据。
3. custom op 能单独运行不是完成条件。public API 的真实语义必须由 custom op 驱动，并且必须用 same-run call count 证明。
4. direct-only、artifact-only、compile-only、smoke-only、baseline-only、fallback-routed、zero-call 的结果只能作为中间证据，不能作为迁移成功。
5. 每完成或失败一个 row，都必须更新 manifest、runtime coverage、baseline/custom result、implementation resolution log，并选择下一个未完成 row 继续推进。
6. 如果某个 semantic operator 太复杂，允许把它拆成多个子 OPP、子 kernel、adapter 子路径或验证切片；但父级 manifest row 仍保持未完成，直到完整 public semantics 达到 DONE。
7. timeout、build error、CANN 限制、tiling 不稳定、forward/backward buffer 复杂、scan/sort/atomic/autograd 难点，都只能触发拆解和继续推进，不能触发 MVP 收尾。
8. 每轮执行前必须列出当前 `remaining_rows`；每轮执行后必须列出 `completed_rows`、`failed_rows`、`next_row` 和下一步 implementation plan。
9. 如果 OPP package 或安装目录中生成了多个 entry，只能把已经具备 adapter、public route、coverage、parity、benchmark 和 no-fallback 证据的 entry 标为 PASS；其它 generated-only entry 必须保持 incomplete。
10. 每个 row 必须按 dtype、direction 和 public entry 分开验收。float32 PASS 不能代表 float64 PASS；forward PASS 不能代表 backward、grad 或 gradgrad PASS；direct `torch.ops` PASS 不能代表 higher-level descriptor/model/e2e route PASS。
11. 若 TensorFlow、PyTorch、torch-npu、CANN、数据集、硬件、权限或依赖缺失，不得自动把相关 row 移出 scope。必须保留 row，并记录为 `BLOCKED_BY_ENV`、`UNVALIDATED` 或 `FAILED`，然后继续推进其它独立 row 或向用户提出明确决策问题。
12. 每个 row 的性能 baseline 必须说明 baseline 类型，例如 CUDA baseline、CPU reference、framework builtin、Python reference 或项目级 e2e baseline。不得把 Python CPU micro-benchmark speedup 描述为 CUDA 生产路径或全项目端到端 speedup。

## 4. DONE 的唯一定义

最终 SUCCESS / DONE 只允许在同时满足以下条件时给出：

1. manifest 中每个 in-scope row 都有真实 Ascend OPP artifacts；
2. 每个 row 都有 adapter/import/link 成功证据；
3. 每个 row 都有 direct/reference parity；
4. 每个 row 都有 public API 或 framework integration 的真实 custom-op 路由证据；
5. 每个 row 都有 same-run runtime coverage，且 custom-path call count > 0；
6. 每个 row 都有 baseline/custom 可比性能实测，报告 speedup 或 slowdown；
7. 项目级 e2e / regression / training / benchmark 测试通过；
8. fallback、baseline-only、zero-call、stub、framework builtin op 均未被计为成功；
9. manifest unresolved_count = 0；
10. migration_reports 中的 JSON 和 Markdown 与真实证据一致，并通过 report parity 检查。
11. `inventory_count == manifest_entries == closed_pass_entries`；
12. `remaining_entries == 0`；
13. `full_migration_status == FULL_PASS`；
14. final evidence validation 已验证以上字段，并在不满足时以非零退出。

只要任一 in-scope row 处于 `INCOMPLETE`、`FAILED`、`DIRECT_ONLY`、`ARTIFACT_ONLY`、`PASS_IN_SCOPE_ONLY`、`UNMEASURED`、`BLOCKED`、`TODO`、`FOLLOW_UP`、`FUTURE_WORK`，就不得声明全量迁移完成。

如果最终无法达到 DONE，最终状态必须写成 `FULL_MIGRATION_INCOMPLETE` 或 `FAILED`，并且 final answer 只能请求用户对 scope、环境、依赖或继续策略做明确决策，不能把 incomplete 结果包装成完成交付。

## 5. 失败和阻塞处理规则

1. 如果某个 row 失败，必须先 debug/retry，并记录失败证据、尝试路径和下一步。
2. 如果一个 row 暂时失败，继续推进其他独立 rows，不得因此停止整个全量迁移。
3. 只有遇到以下情况才可以询问用户：
   - 需要用户改变 scope；
   - 需要外部资源、权限、数据、硬件或凭证；
   - 需要批准破坏性操作；
   - 三次不同技术方案仍无法推进，并且没有其他独立 rows 可继续。
4. 询问用户时必须给出一个明确决策问题和推荐选项，不得用泛泛的“是否继续”结束任务。
5. 如果某个 row 三次不同技术方案失败，必须把该 row 拆解成更小的 implementation slices，并继续能推进的 slice；只有当所有剩余 rows 都被外部条件阻塞时，才允许向用户请求决策。
6. 任何 `FAILED/INCOMPLETE` 状态都必须附带可执行下一步，格式为：`unfinished row -> root cause -> next implementation slice -> required evidence -> expected validation command`。
7. 不得把“已经诚实报告失败”当作任务完成。诚实失败报告只是证据；全量迁移任务仍必须继续，除非满足 final answer 规则。
8. 如果需要环境配置、Python 依赖隔离或测试运行环境，不需要征得用户同意；必须直接在目标项目所在目录创建项目本地虚拟环境并安装所需依赖，同时记录创建命令、激活方式、依赖安装日志和验证命令。不得修改系统 Python、全局环境或其他项目环境；只有需要管理员权限、外部凭证、系统级软件安装或破坏性操作时才询问用户。

## 6. 禁止事项

1. 严禁伪造 artifacts、coverage、speedup、parity、e2e、fallback 检查或性能数据。
2. 严禁把框架内置算子、CPU/GPU fallback path、baseline-only 测试、stub、零调用路径当作 Ascend custom-op 迁移成功。
3. 严禁把 `BLOCKED`、`UNMEASURED`、`INCOMPLETE`、`PASS_IN_SCOPE_ONLY` 当作最终完成。
4. 严禁只因为报告已经生成、MVP 已经通过、review 已经通过就停止。
5. 严禁把历史 accuracy、历史 adapter hash、历史 speedup、其他项目 artifacts 当作当前全量迁移证据。

## 7. 报告要求

报告必须放在目标项目 `migration_reports/`，至少包含：

1. operator inventory；
2. migration manifest；
3. preflight；
4. baseline/custom result；
5. runtime coverage；
6. speedup or slowdown report；
7. build attempts；
8. implementation resolution log；
9. report parity / final evidence validation；
10. final Chinese summary。

final evidence validation 必须至少检查：

1. inventory 与 manifest 数量一致；
2. manifest 中无遗漏 in-scope public entry、framework alias、semantic operator；
3. 每个 row 都有 OPP artifact、adapter、direct parity、public integration/e2e、runtime coverage、benchmark 和 no-fallback 证据；
4. 每个 coverage key 的 custom-path call count 是 same-run 正整数；
5. 每个 benchmark 都标明 baseline 类型和 custom route；
6. 所有 evidence 文件存在且 hash、path、status 与 manifest、Markdown 报告一致；
7. `remaining_entries == 0` 且 `full_migration_status == FULL_PASS`；
8. 不允许 single-entry manifest 在 `inventory_count > 1` 时通过。

最终中文总结必须覆盖：

1. 迁移范围；
2. 总 in-scope row 数；
3. PASS row 数；
4. 未完成 row 数；
5. 每个 in-scope row 的 artifacts、adapter、parity、coverage、integration/e2e、性能证据；
6. baseline/custom 结果和实测 speedup_vs_baseline 或 slowdown；
7. no fallback / no zero-call / no baseline-only 检查结果；
8. 项目级 e2e / regression / training / benchmark 状态；
9. 每个未完成 row 的阻塞原因和下一步；
10. 是否达到全量 DONE。若未达到，必须明确写 `FAILED/INCOMPLETE`，并且不得把它描述成完成。

最终中文总结必须包含以下 per-row 表格，不得只写 prose summary：

| row | semantic operator | public entries / aliases | OPP artifact | adapter callable | coverage key/count | parity | integration/e2e | baseline/custom performance | status | next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

每个未完成 row 的 `next action` 必须是具体实现动作，不能写成泛泛的 `future work`、`follow up`、`optimize later` 或 `blocked`。

## 8. final answer 规则

final answer 只能在以下两种情况下给出：

1. 全部 in-scope rows 达到 DONE 定义；
2. 需要用户做 scope-changing、外部资源、权限、数据、硬件、凭证或破坏性操作相关的明确决策。

如果只是 MVP 通过、局部 smoke 通过、direct probe 通过、source/receiver 小范围通过、报告已更新、review 通过、或状态诚实写成 `INCOMPLETE`，都不得 final 停止，必须继续推进下一个未完成 row。

若 final answer 因需要用户决策而提前给出，必须只包含：当前 locked scope、已完成 rows、未完成 rows、阻塞的外部条件、一个明确问题、推荐选项。不得把局部 PASS 包装成项目完成。
