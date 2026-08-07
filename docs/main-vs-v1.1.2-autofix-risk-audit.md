# main vs v1.1.2 pylint_autofix 风险审计

## 审计范围

| 项目 | 内容 |
| --- | --- |
| 新克隆目录 | `/home/zihang/opencode_test/SEAM-main-analysis` |
| v1.1.2 基线 | `378f70083b0714ebc3e965b4b1652d3a22eeede9` |
| 最新 main | `f09456e253e166d567bb442cd392011c8270531c` |
| 新增提交 | `5acdaa0`, `26f6177`, `d5a5486`, `f09456e` |
| 变更规模 | 56 files, 5684 insertions, 1993 deletions |
| 变更来源 | 两组 `pylint_autofix` 提交及其 merge commit |

本文只分析 `378f700..f09456e` 的差异。原始 `v1.1.2` 工作目录 `/home/zihang/opencode_test/SEAM` 未被修改；本文档写在新克隆目录中，用于后续修复前的风险清单。

## 总体结论

最新 `main` 相对 `v1.1.2` 的变更目标主要是 lint/format 自动修复，不包含新的 workflow、prompt、README 或实验文档能力更新。但这些自动修复不是纯格式化：至少存在多处由字符串拆分、逗号、日志参数、列表缩进等引入的运行时或提示语义问题。

建议在修复前不要把 `f09456e` 作为稳定运行版本使用。发布、复现实验和生产式 E2E 更适合继续固定在 `v1.1.2` tag 对应的 `378f700`。若需要跟进最新 `main`，应优先修复本文列出的 P0/P1 问题，再跑 container auto image、custom-op project analysis、validation final、parse correction retry、runtime artifacts 和 E2E smoke。

## 自动检查结果

- `git diff --check 378f700..HEAD`：通过，未发现 whitespace error。
- 55 个变更 Python 文件均可 `py_compile`，因此问题不是语法错误，而是 Python 合法语法下的语义改变。
- AST 对比发现 23 个 Python 文件存在语义级 AST 变化；其中一部分是无害的 lint pragma、字符串常量或测试 fixture 变化，另一部分是本文确认的问题。
- AST 扫描发现 5 个多参数 `list.append(...)` 调用，均为确定运行时 crash 风险。
- Logging 扫描发现 6 个参数/占位符不匹配，其中 4 个位于核心执行路径，2 个位于测试项目样例。

## 确认问题清单

### P0/P1 运行时错误

#### 1. validation correction parse-failure prompt 返回 tuple

- 文件：`src/core/validation_correction.py:100`
- 位置：`build_validation_correction_prompt(..., is_parse_failure=True)`
- 类型：字符串拆分时误加逗号，`return (...)` 返回 tuple 而不是 str。
- 触发条件：LLM 输出无法解析 JSON，进入 parse correction retry。
- 影响路径：`src/core/phase_runner.py`、`src/core/workflow_executor.py` 中的 parse retry 都依赖该函数。
- 运行影响：session manager 可能收到 tuple 作为 prompt，或 prompt 被转换为 tuple repr；轻则修正提示质量下降，重则发送命令失败，导致本可修复的 JSON parse failure 直接失败。
- 修复建议：移除第 102 行后的逗号，将所有片段合成一个字符串；增加单测断言 `isinstance(prompt, str)`。

#### 2. WorkflowExecutor auto image selection guidance 变成 tuple

- 文件：`src/core/workflow_executor.py:548`
- 位置：`WorkflowExecutor._send_image_selection_prompt`
- 类型：字符串拆分时误加逗号，`guidance` 从 str 变成 tuple。
- 触发条件：`execution_backend.mode=auto/container` 且候选镜像需要 agent 选择。
- 影响：
  - `is_discovered=True` 时，`src/core/workflow_executor.py:554` 执行字符串加 tuple，会直接 `TypeError`。
  - `is_discovered=False` 但有多个 configured images 时，prompt 中 `selection_guidance` 可能变成 tuple repr，降低镜像选择可靠性。
- 修复建议：去掉 `"target runtime environment.",` 后的 tuple 结构，合并为普通字符串；增加 auto image selector 单测覆盖 discovered images 和 configured multi-image 两条路径。

#### 3. Orchestrator auto image selection guidance 变成 tuple

- 文件：`src/core/orchestrator.py:855`
- 位置：`Orchestrator._send_image_selection_prompt`
- 类型：与上一项相同。
- 触发条件：legacy orchestrator 路径中执行 auto container image selection。
- 影响：
  - `is_discovered=True` 时，`src/core/orchestrator.py:861` 直接 `TypeError`。
  - 非 discovered 多候选路径下 prompt 质量下降。
- 修复建议：同上一项；如果 legacy Orchestrator 仍需支持，应补充对应测试。

#### 4. Container probe 的 python_version 在 host 侧求值

- 文件：`src/core/execution_backend.py:785`
- 位置：`ContainerBackend.probe_environment`
- 类型：构造容器内 probe script 时，内层 f-string 被拆成外层 f-string。
- 当前代码片段：第 785 行是普通字符串，第 786 行是外层 `f'{sys.version_info.minor}...'`。
- 触发条件：容器已创建后调用 `probe_environment()`。
- 影响：
  - 模块顶部没有 `import sys`，因此很可能在 host 侧构造 `probe_script` 时 `NameError`。
  - 即使未来 host 侧导入了 `sys`，也会把 host Python minor/micro 写入容器 probe script，导致容器 `python_version` 事实错误。
- 修复建议：确保完整文本 `f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"` 保持在容器内脚本字符串中；不要让外层 Python 插值。

#### 5. project analysis validator 多参数 append crash

- 文件：`src/validators/validate_project_analysis.py:176`
- 位置：`validate()` custom-op surface 校验。
- 类型：`errors.append()` 传入两个字符串参数。
- 触发条件：`custom_op_surface.custom_op_detected is True` 且 `discovery_complete is not True`。
- 影响：validator 抛 `TypeError: list.append() takes exactly one argument`，而不是返回结构化 validation error；Phase 1/项目分析 correction loop 可能被异常中断。
- 修复建议：拼接为一个字符串；增加负例单测。

#### 6. fine_grained_operator_unit_evidence 空列表 append crash

- 文件：`src/validators/validate_project_analysis.py:254`
- 位置：`_validate_fine_grained_unit_evidence()`。
- 类型：`errors.append()` 传入两个字符串参数。
- 触发条件：`fine_grained_operator_units` 存在，`fine_grained_operator_unit_evidence=[]`。
- 影响：custom-op 发现信息缺失时 validator crash，阻断 correction prompt。
- 修复建议：拼接为单字符串；增加空 evidence 负例测试。

#### 7. fine_grained evidence identity mismatch append crash

- 文件：`src/validators/validate_project_analysis.py:304`
- 位置：`_validate_fine_grained_unit_evidence()`。
- 类型：`errors.append()` 传入三个参数。
- 触发条件：`fine_grained_operator_units` 与 `fine_grained_operator_unit_evidence[*].unit_identity` 不一致。
- 影响：无法正常反馈 missing/extra unit evidence，validator crash。
- 修复建议：拼接为一个完整字符串；测试 missing 和 extra 两类 mismatch。

#### 8. validation final performance_report 缺失时 append crash

- 文件：`src/validators/validate_validation_final.py:964`
- 位置：`_validate_performance_report_completeness()`。
- 类型：`errors.append()` 传入两个字符串参数。
- 触发条件：`performance_report` 和 `performance_report_evidence` 缺失，或不是 mapping。
- 影响：final validation 应返回“performance_report must be object”的错误，但现在会 crash；custom-op final gate/Phase 5 可能被异常中断。
- 修复建议：拼接为一个字符串；增加缺失 performance report 负例。

#### 9. validation final surrogate performance_report append crash

- 文件：`src/validators/validate_validation_final.py:978`
- 位置：`_validate_performance_report_completeness()`。
- 类型：`errors.append()` 传入两个字符串参数。
- 触发条件：`performance_report` 命中 `_mapping_is_disallowed_surrogate()`，例如 report-only、benchmark-only、synthetic、mock、manifest-only。
- 影响：本应给出明确证据不合格错误，现在会 crash。
- 修复建议：拼接为一个字符串；增加 surrogate report 负例。

#### 10. runtime artifacts 在无 operator units 时可能 TypeError

- 文件：`src/core/runtime_artifacts.py:293`
- 位置：`_operator_repair_context_markdown()` 的 bounded guidance 列表。
- 类型：list 中混入 tuple 元素。
- 触发条件：没有发现 per-operator units，走 `else` 分支。
- 影响：函数最后 `"\n".join(lines)` 会因为 list 内含 tuple 抛 `TypeError: sequence item ... expected str instance, tuple found`；operator repair context artifact 生成失败。
- 修复建议：把第 294-296 行合成单一字符串元素；增加 no-units 场景单测。

### P1/P2 语义退化或提示质量问题

#### 11. runtime artifacts 的 final-gate progress/guidance 缩进改变

- 文件：`src/core/runtime_artifacts.py:275`
- 位置：`_operator_repair_context_markdown()`。
- 类型：`lines.extend([... Current Final-Gate Progress ...])` 被缩进到 `else` 分支内。
- 触发条件：存在 `units` 时。
- 影响：当发现 operator units 时，输出将不再包含 `Current Final-Gate Progress`、`Bounded Parallelization Guidance` 和 `Warnings` 标题。虽然第 306 行仍会追加 warning 列表，但缺少标题和指导语，repair agent 上下文质量下降。
- 修复建议：将 final-gate progress/guidance/warnings 标题恢复到 `if/else` 之后，对有 units 和无 units 两种场景都输出。

#### 12. runtime artifacts 无 units 文案被拆成两条列表项

- 文件：`src/core/runtime_artifacts.py:269`
- 类型：一句 bullet 被拆成两个 list 元素。
- 触发条件：没有 per-operator units。
- 影响：生成 markdown 中 `- No per-operator units found...` 和 `discovered inventory paths...` 被拆成两行，后者不是 bullet，提示可读性下降。
- 修复建议：合成一个字符串。

#### 13. error analyzer history markdown 表头被拆坏

- 文件：`src/core/workflow_executor.py:2321`
- 位置：`WorkflowExecutor._format_error_analyzer_history()`。
- 类型：markdown table header 和 separator row 被拆成四个 list 元素。
- 触发条件：Phase 5 repair loop 有 previous `loop_history`。
- 影响：error analyzer prompt 中历史表格格式破坏，可能降低 LLM 对历史修复记录的理解。
- 修复建议：恢复每个 markdown table row 为一个完整字符串。

#### 14. Phase 6 fallback summary 句子被拆成两行

- 文件：`src/core/phase6_fallback.py:195`
- 类型：一句普通段落拆成两个 list 元素。
- 触发条件：LLM Phase 6 report fallback。
- 影响：报告文本可读性轻微下降，不影响控制流。
- 修复建议：合成一个字符串。

### P2/P3 logging 问题

#### 15. auto image fallback 日志参数错误

- 文件：`src/core/workflow_executor.py:494`
- 类型：`logger.info()` 第一个字符串无 `%` placeholder，但传入第二个字符串作为参数。
- 触发条件：auto mode 没有 configured images，且本地也未发现 images。
- 影响：logging 内部报 `TypeError: not all arguments converted during string formatting`；通常不阻断 fallback 返回 local，但会污染日志，某些严格 logging 配置下可能造成失败。
- 修复建议：相邻字符串直接拼接，不作为 logger 参数。

#### 16. duplicate experience skip 日志参数错配

- 文件：`src/core/workflow_executor.py:1180`
- 类型：format string 有 2 个 placeholder，却传入 3 个参数；第二个字符串被误当成参数。
- 触发条件：dynamic experience 中存在已由 explicit runtime skill 覆盖的重复项。
- 影响：logging error；不影响过滤逻辑本身。
- 修复建议：把 `already covered...` 拼到 format string 中，参数只保留 `phase_id, skipped`。

#### 17. sub-workflow LLM command 日志参数错配

- 文件：`src/core/workflow_executor.py:1682`
- 类型：format string 有 2 个 placeholder，实际传入 6 个参数；第二个字符串被误当成参数。
- 触发条件：任何 sub-workflow LLM command。
- 影响：这是高频路径，会产生大量 logging formatting error；核心 LLM 调用仍会继续，但日志噪声大，可能影响诊断。
- 修复建议：把两段 format string 合成一个字符串，参数保留 `phase_id, agent_id, session_id, timeout, len(prompt_text)`。

#### 18. last-iteration validation-only rerun 日志参数错配

- 文件：`src/core/workflow_executor.py:4033`
- 类型：format string 第一段无 placeholder，后续字符串和变量被当成多余参数。
- 触发条件：最后一次 repair 后触发 validation-only rerun。
- 影响：logging error；该路径是 v1.1.2 关键能力之一，日志问题会影响复盘。
- 修复建议：合并 format string。

#### 19. test project trainer 日志扫描疑似误报

- 文件：`src/test_project_complex/src/training/trainer.py:55`, `src/test_project_complex/src/training/trainer.py:113`
- 类型：自动扫描报告 placeholder mismatch，但人工复核显示 placeholder 和参数数量匹配。
- 影响：无确认运行问题。
- 处理建议：无需修复，保留为扫描误报说明。

### P3 工具链和测试质量问题

#### 20. scripts/check.sh 检查覆盖明显收窄

- 文件：`scripts/check.sh:9`
- 原行为：`pylint src/ tests/`。
- 新行为：`pylint --disable=all --enable=少量规则 $(find src/ -name "*.py" -not -path "*/tests/*" ...)`。
- 影响：
  - `tests/` 和 `src/tests/*` 不再 lint。
  - 大多数 pylint 规则被关闭。
  - 本次已经出现的多参数 `append`、tuple prompt、logger 参数错配均无法被该检查覆盖。
- 修复建议：明确 CI 目标。如果目的是临时 autofix，可不要替代原有质量门禁；建议增加 `python -m compileall`、关键 AST lint、pytest，或恢复更完整的 lint 配置。

#### 21. rule_based test fixture 缩进变得不真实

- 文件：`src/tests/test_rule_based.py:92`
- 类型：三引号字符串被自动格式化为带缩进内容。
- 触发条件：运行 `test_inject_after_imports`。
- 影响：测试输入不再模拟正常顶层 Python 文件；可能掩盖 import 注入位置的真实回归。
- 修复建议：使用括号字符串拼接，或 `textwrap.dedent(...).lstrip()`。

#### 22. PPU rule_based error report 有重复 key

- 文件：`src/migrator/rule_based_ppu.py:104` 和 `src/migrator/rule_based_ppu.py:105`
- 类型：dict literal 中重复 `"rules": {}`。
- 影响：当前两个值相同，所以运行结果不变；但后续编辑容易误判，lint 也应发现。
- 修复建议：删除重复 key。

#### 23. e2e_smoke_test 重复 import

- 文件：`src/scripts/e2e_smoke_test.py:11` 和 `src/scripts/e2e_smoke_test.py:12`
- 类型：重复 `Path` import。
- 影响：无运行影响，代码质量问题。
- 修复建议：删除重复 import。

## 逐文件审查表

### scripts

| 文件 | 修改区域 | 判断 | 问题 |
| --- | --- | --- | --- |
| `scripts/check.sh` | lint command | 工具链语义改变 | 检查覆盖收窄，见问题 20 |

### src/core

| 文件 | 修改区域 | 判断 | 问题 |
| --- | --- | --- | --- |
| `src/core/agent_io_logger.py` | logger 常量和 setup 格式化 | 未发现运行问题 | 无 |
| `src/core/config.py` | YAML parsing、validation message、runtime skill parsing、transition validation 换行 | AST 有变化但未发现具体运行 bug | 建议回归 `test_config*` |
| `src/core/config_loader.py` | config loading 换行 | 格式化为主 | 无 |
| `src/core/execution_backend.py` | container lifecycle/probe/context helper 换行 | 有运行风险 | 问题 4 |
| `src/core/experience_classifier.py` | classify/normalize/prompt helper 换行 | AST 有变化但未发现具体运行 bug | 建议跑 experience 相关测试 |
| `src/core/experience_dispatcher.py` | dispatch logging/source loading 换行 | AST 有变化但未发现具体运行 bug | 无确认问题 |
| `src/core/experience_evaluator.py` | evaluator session/evaluate/artifact context 换行 | 格式化为主 | 无 |
| `src/core/experience_injector.py` | card filtering/formatting 换行 | 格式化为主 | 无 |
| `src/core/experience_promoter.py` | batch promotion/grouping/load helper 换行 | 格式化为主 | 无 |
| `src/core/experience_query.py` | query prefiltering、file-path derivation、prompt replacement loop | AST 有变化但未发现具体运行 bug | 建议跑 experience query 测试 |
| `src/core/experience_refiner.py` | refine/fallback/classification/prompt/assets 换行 | AST 有变化但未发现具体运行 bug | 无确认问题 |
| `src/core/experience_registry.py` | catalog/scan/cleanup/merge helper 换行 | 格式化为主 | 无 |
| `src/core/experience_solidifier.py` | constructors/render helpers 换行 | 格式化为主 | 无 |
| `src/core/experience_store.py` | store/index/promotion/merge helper 换行 | 格式化为主 | 无 |
| `src/core/hook_manager.py` | hook registration/builtin formatting、pylint comments | AST 有变化但未发现具体运行 bug | 无确认问题 |
| `src/core/orchestrator.py` | backend/image selection prompt helpers | 有运行风险 | 问题 3 |
| `src/core/paths.py` | path helpers 换行 | 格式化为主 | 无 |
| `src/core/phase6_fallback.py` | fallback report generation | 低风险文本语义变化 | 问题 14 |
| `src/core/phase_runner.py` | phase execution、correction prompt 使用、runtime skill context | 自身多为格式化，但受 validation_correction 影响 | 问题 1 的下游 |
| `src/core/platform_policy.py` | policy constants/parsing 换行 | 格式化为主 | 无 |
| `src/core/prompt_loader.py` | optional section patterns/default artifact strings/repair role descriptions | AST 有变化但未发现具体运行 bug | 建议跑 prompt loader 测试 |
| `src/core/repair_loop.py` | routing patterns、prompt construction、history formatting、loop helpers | AST 有变化但未发现具体运行 bug | 建议跑 repair loop 测试 |
| `src/core/runtime_artifacts.py` | runtime markdown artifact rendering | 有运行和提示质量风险 | 问题 10, 11, 12 |
| `src/core/runtime_skill_resolver.py` | config merge/skill formatting/name validation | 格式化为主 | 无 |
| `src/core/state_machine.py` | init/transition/retry parsing 换行 | 格式化为主 | 无 |
| `src/core/telemetry_bridge.py` | init/save metrics 换行 | 格式化为主 | 无 |
| `src/core/types.py` | dataclass/enum lint pragmas、config parsing 换行 | lint/格式化为主 | 无 |
| `src/core/validation_correction.py` | correction prompt construction | 有运行风险 | 问题 1 |
| `src/core/validator_engine.py` | result normalization types/helpers 换行 | 格式化为主 | 无 |
| `src/core/variable_resolver.py` | resolver regex/string quote normalization | 格式化为主 | 无 |
| `src/core/workflow_executor.py` | auto image、experience injection、LLM/sub-workflow、history、repair loop、logging | 多处运行/日志/提示问题 | 问题 2, 13, 15, 16, 17, 18 |
| `src/core/workflow_selector.py` | selector schema/event/fallback/project summary 换行 | 格式化为主 | 无 |

### harness / migrator / rule strategies / scripts

| 文件 | 修改区域 | 判断 | 问题 |
| --- | --- | --- | --- |
| `src/harness/session/__init__.py` | import wrapping | 格式化为主 | 无 |
| `src/harness/session/manager.py` | JSON extraction、session send/wait/status、sqlite helpers | 格式化为主 | 无 |
| `src/migrator/rule_based.py` | pyright comments、migrate、migrate_directory wrapping | 格式化为主 | 无确认问题 |
| `src/migrator/rule_based_ppu.py` | migrate_directory exception report | 代码质量问题 | 问题 22 |
| `src/migrator/rule_based_report_only.py` | pattern tuple、summary dict | 格式化为主 | 无 |
| `src/rule_strategies/__init__.py` | log message wrapping | AST 有变化但未发现具体运行 bug | 无确认问题 |
| `src/scripts/e2e_smoke_test.py` | imports、MockSessionManager、helpers | 轻微代码质量问题 | 问题 23 |
| `src/scripts/sm_adapt_cli.py` | argparse epilog/help wrapping | 格式化为主 | 无 |

### test project / tests

| 文件 | 修改区域 | 判断 | 问题 |
| --- | --- | --- | --- |
| `src/test_project_complex/scripts/prepare_data.py` | pylint disable before delayed import | 工具注释 | 无 |
| `src/test_project_complex/src/data/dataset.py` | pyright comments、RNG call wrapping | 格式化为主 | 无 |
| `src/test_project_complex/src/models/backbone.py` | pyright comments | 格式化为主 | 无 |
| `src/test_project_complex/src/models/classifier.py` | pyright comments、constructor wrapping | 格式化为主 | 无 |
| `src/test_project_complex/src/training/optimizer_factory.py` | pyright comments | 格式化为主 | 无 |
| `src/test_project_complex/src/training/runner.py` | pyright comments、pylint disable、signature wrapping | 工具注释/格式化 | 无 |
| `src/test_project_complex/src/training/trainer.py` | logger call wrapping | 人工复核无问题 | 问题 19 为扫描误报 |
| `src/tests/test_loop_subworkflow.py` | import order、wrapped literals/setup | AST 有变化但未发现具体测试语义问题 | 建议跑测试确认 |
| `src/tests/test_rule_based.py` | import order、fixture rewrite | 测试语义退化 | 问题 21 |

### validators

| 文件 | 修改区域 | 判断 | 问题 |
| --- | --- | --- | --- |
| `src/validators/validate_entry_script.py` | custom-op contract、run-command message wrapping | AST 有变化但未发现具体运行 bug | 建议跑 validator 测试 |
| `src/validators/validate_entry_static.py` | static validator message wrapping | 格式化为主 | 无 |
| `src/validators/validate_env_detect.py` | platform error wrapping | 格式化为主 | 无 |
| `src/validators/validate_project_analysis.py` | custom-op surface、fine-grained evidence errors | 有运行风险 | 问题 5, 6, 7 |
| `src/validators/validate_rule_migration.py` | integer validation condition wrapping | 格式化为主 | 无 |
| `src/validators/validate_validation_final.py` | final gate/performance validation wrapping | 有运行风险 | 问题 8, 9 |

## 建议修复顺序

1. 修复所有 P0/P1 crash：`validation_correction.py`、`workflow_executor.py` image guidance、`orchestrator.py` image guidance、`execution_backend.py` probe script、两个 validator 文件、`runtime_artifacts.py` tuple。
2. 修复高频 logging 错配：`workflow_executor.py:1682` 优先，其次 `494`、`1180`、`4033`。
3. 修复提示质量退化：`runtime_artifacts.py` 缩进和 markdown bullet、`workflow_executor.py` history table、`phase6_fallback.py` 文案。
4. 修复工具和测试质量问题：`scripts/check.sh` 覆盖范围、`test_rule_based.py` fixture、重复 key/import。
5. 增加回归测试：
   - `build_validation_correction_prompt(is_parse_failure=True)` 返回 str。
   - auto image selector discovered images 不崩溃。
   - container probe script 字符串不在 host 侧插值。
   - project analysis 三个 custom-op 负例只返回 errors，不抛异常。
   - validation final 两个 performance report 负例只返回 errors，不抛异常。
   - runtime artifacts no-units 和 units 两个分支都可生成 markdown。
   - logging 关键路径不触发 formatting error。

## 修复后建议验证命令

```bash
PYTHONPATH=src python -m pytest src/tests/test_execution_backend.py -k AutoImageSelection -q
PYTHONPATH=src python -m pytest src/tests/test_workflow_executor.py src/tests/test_loop_subworkflow.py -q
PYTHONPATH=src python -m pytest src/tests/test_prompt_loader.py src/tests/test_container_phase5_prompts.py -q
PYTHONPATH=src python -m pytest src/tests/test_rule_based.py -q
PYTHONPATH=src python -m pytest src/tests -q
```

如需快速静态兜底，可增加一个小型 AST 检查，至少覆盖：

- `errors.append()` 不允许多个 positional args。
- `build_validation_correction_prompt()` 返回值必须是 str。
- logger format string placeholder 数量与参数数量匹配。
- prompt/guidance 等变量不应是 tuple。

## 补充审计：固定 Python 版本 / 解释器路径问题

### 结论

用户反馈的固定 Python 版本问题存在，而且不只表现为 `python3.10`。当前代码和提示词中同时存在以下几类硬绑定：

- E2E launcher 默认使用 `python3.10`，会直接影响用户运行。
- 部分 Phase 3 prompt 示例把 `run_command` 写成 `/opt/conda/bin/python3.10 ...`，会引导 agent 在容器中选择不存在或错误的解释器。
- 部分 repair prompt 仍要求用 `{project_dir}/.venv/bin/python` 验证，和 v1.1.2 已经建立的 base-env/container-aware 设计冲突。
- 文档和实验记录中有大量 `python3.10`、`python3.11.x` 绝对解释器路径，其中实验记录可保留为证据，但用户指南/运行命令不应继续作为推荐写法。
- 测试中大量 `3.10.x` 是 fixture 数据，本身不是运行硬编码，但应避免测试断言框架必须是 3.10。

当前已有正确的泛化方向：Phase 2 输出 `python_path`，Phase 3/3.5 要求使用 Phase 2 选择的解释器或 target runtime 中可执行的等价解释器。但这个契约没有被所有 launcher、prompt、repair guidance 和 docs 贯彻，因此会出现“前面选择了 base env，后面又被示例/修复指令拉回 python3.10 或 .venv”的问题。

### 确认位置

#### A. 真实运行入口硬编码

- `src/scripts/run_e2e_v3.sh:239`：dry-run 展示 `${PYTHON:-python3.10}`。
- `src/scripts/run_e2e_v3.sh:286`：实际执行 `${PYTHON:-python3.10} -m tests.e2e.e2e_test_v3`。
- 影响：如果用户环境没有 `python3.10`，直接运行失败；即使有 `python3` 或当前 venv Python，也不会使用。
- 泛化修复：默认应使用 `${PYTHON:-python3}`，更稳妥的是在脚本开头解析一次 `SEAM_PYTHON="${PYTHON:-}"`，若未设置则按顺序选择当前可用解释器：`python3`、`python`，必要时再检查 `python3.12/3.11/3.10/3.9/3.8`，但不把任何 minor 版本作为唯一默认。

#### B. Phase 3 prompt 示例硬编码 `/opt/conda/bin/python3.10`

- `src/prompts/phase_3_entry_script_npu_container_baseaware_entryfix.md:95`
- `src/prompts/phase_3_entry_script_npu_container_baseaware_entryfix.md:106`
- `src/prompts/phase_3_entry_script_musa_container_baseaware_entryfix.md:85`
- `src/prompts/phase_3_entry_script_musa_container_baseaware_entryfix.md:96`
- 影响：这些 prompt 虽然前文写了“Use Phase 2's `python_path`”，但 Output Format 示例给出固定 `/opt/conda/bin/python3.10`。LLM 很容易照抄示例，导致容器里如果只有 `/usr/local/python3.11.14/bin/python3`、`/usr/bin/python3` 或 conda `python` 时失败。
- 泛化修复：示例中的 `run_command` 不应出现固定 minor 版本。推荐改为占位语义，例如 `"<phase2_python_path> /workspace/run_e2e.py"`，或普通 PATH 命令 `"python3 /workspace/run_e2e.py"`，并明确：真实输出必须使用 Phase 2 `python_path` 或 target runtime 中验证可执行的等价解释器，不得照抄占位符。

#### C. repair prompt 仍硬绑定 `.venv/bin/python`

- `src/prompts/repair_dependency_fixer.md:2`
- `src/prompts/repair_code_adapter.md:62`
- `src/prompts/repair_code_adapter.md:71`
- `src/prompts/repair_code_adapter_container.md:83`
- `src/prompts/repair_code_adapter_container.md:92`
- `src/core/repair_loop.py:302`
- `src/core/repair_loop.py:389`
- `src/core/repair_loop.py:417`
- 影响：Phase 2 可能已经选择 container/base interpreter，但 repair prompt 仍要求 `.venv/bin/python`，会造成 agent 新建/修 `.venv`、绕过 vendor torch/runtime、或用 host/project venv 验证而不是 target runtime 验证。
- 泛化修复：所有 repair guidance 应统一改为“使用 `actual_execution_command` 或 Phase 3 当前 `run_command`，不要假设 `.venv` 存在”。如果需要显式解释器，应来自 Phase 2 `python_path` 或当前 execution backend 的 `actual_execution_command`，而不是拼 `{project_dir}/.venv/bin/python`。

#### D. repair output 示例硬编码 selected_python

- `src/prompts/repair_dependency_fixer_container_musa.md:79`：`"selected_python": "/opt/conda/bin/python3.10"`。
- 影响：虽然只是诊断字段示例，但可能诱导 agent 在 summary/diagnostics 中填固定解释器，降低事实可信度。
- 泛化修复：改为 `"selected_python": "verified_target_runtime_python"` 或 `"selected_python": "<phase2_python_path_or_verified_equivalent>"`。

#### E. container probe 候选列表包含版本顺序但不是主要问题

- `src/core/execution_backend.py:809` 到 `src/core/execution_backend.py:810`：probe 在容器内按 `python3 python python3.12 python3.11 python3.10 python3.9 python3.8` 搜索。
- 判断：这不是“必须 python3.10”，因为它优先 `python3`/`python`，后面 minor 版本只是 fallback。但是建议把候选列表抽成统一 helper/常量，并避免遗漏未来版本。
- 泛化修复：使用 `command -v python3 || command -v python` 作为主路径；minor-specific fallback 可以保留但应由一个可配置候选列表提供，不应散落在字符串里。

#### F. 文档和实验记录中的固定版本

- `src/README.md:67`、`src/docs/E2E_TESTING.md`、`src/docs/e2e_test_guide_deepwave.md` 多处使用 `python3.10`。
- `docs/v1.1.2-dev-change-record.md` 中多处 `python3.10` 和 `/usr/local/python3.11.x/bin/python3` 是实验取证记录。
- 判断：实验记录中的绝对路径可以保留为历史证据，但 README / E2E guide / quickstart 应改成 `python3` 或 `${PYTHON:-python3}`，并说明可通过 `PYTHON=/path/to/python` 覆盖。

#### G. 测试 fixture 中的 3.10

- 例如 `src/tests/test_execution_backend.py`、`src/tests/test_phase_runner.py`、`src/tests/test_workflow_executor.py`、`src/tests/test_validator_engine.py` 中的 `3.10.x` 多数是模拟 probe facts 或 validator 样例。
- 判断：这些不是运行时硬编码，但测试不应断言框架只接受 3.10。建议保留少量 fixture，但新增 3.11/3.12 或 generic version 的测试，防止未来回归。

### 泛化修复原则

不要把 `python3.10` 机械替换成 `python3.11` 或 `python3` 后就结束。应该建立一个统一的解释器选择契约：

1. **入口脚本层**：运行 SEAM 自身时默认用当前环境可用解释器。`run_e2e_v3.sh` 使用 `${PYTHON:-python3}` 或自动探测出的 `SEAM_PYTHON`；用户可用 `PYTHON=/path/to/python` 覆盖。
2. **Phase 2 层**：Phase 2 是唯一负责选择 target runtime Python 的阶段。它必须输出 `python_path`，且该路径/命令必须在 target runtime 中可执行。container 模式下不能把 host Python 写入 `python_path`。
3. **Phase 3 层**：Phase 3 生成 `run_command` 时必须优先使用 Phase 2 `python_path`。示例只能用 `<phase2_python_path>` 或 `python3` 这类泛化形式，不得出现 fixed minor path。
4. **Phase 3.5 静态校验层**：校验 `run_command` 是否使用 Phase 2 `python_path` 或 target runtime 中已验证的等价解释器；若使用 `/opt/conda/bin/python3.10`、`.venv/bin/python` 等与 Phase 2 决策不一致的解释器，应要求修正。
5. **Phase 5/repair 层**：修复 agent 验证时使用 `actual_execution_command` 或当前 Phase 3 `run_command`。不要在 repair prompt 中重新构造 `.venv/bin/python`，也不要要求 agent 手工选择固定 minor 解释器。
6. **execution backend 层**：`actual_execution_command` 是唯一权威的目标执行描述。container backend 负责 host/container 路径映射，prompt 不应要求 `docker exec` 或固定 container interpreter。
7. **schema/validator 层**：`phase_2_venv.json` 可继续要求 `python_path`，但 validator 应增强：若 `python_path` 包含明显 host-only 路径、固定不存在解释器、或与 execution context 冲突，应返回可修复错误。
8. **文档层**：README/guide 使用 `python3` 或 `${PYTHON:-python3}`；历史实验文档保留原始命令，但标注为取证路径，不作为通用复现默认。

### 建议补充测试

- `run_e2e_v3.sh --dry-run` 在未设置 `PYTHON` 时不出现 `python3.10`，设置 `PYTHON=/custom/python` 时显示并使用该值。
- Phase 3 prompt 文本中不再包含 `/opt/conda/bin/python3.10`。
- repair prompts 和 `repair_loop.py` 生成的 guidance 不再包含 `{project_dir}/.venv/bin/python` 作为强制验证命令。
- Phase 3.5 对“Phase 2 选择 base env，但 Phase 3 返回 `.venv/bin/python` 或 `/opt/conda/bin/python3.10`”的情况给出失败或修复建议。
- container/base env 场景下，`actual_execution_command` 使用 Phase 3 `run_command`，不依赖 host Python minor version。

## 第二轮复查记录

在补充固定 Python 版本审计后，又执行了一轮独立复查，目标是确认是否还存在本文未覆盖的最新 main 引入问题。

### 复查方法

- 重新对 `378f700..f09456e` 的 55 个变更 Python 文件执行 AST 扫描。
- 扩展扫描范围：多参数 `append/extend/insert/update`、重复 dict key、logger 参数/placeholder 错配、字符串 tuple 赋值、字符串 tuple return、list 中嵌套字符串 tuple。
- 对 `src/core` 与非 core 文件分别做独立人工复查。
- 抽查更宽扫描产生的高风险误报，包括 `WorkflowExecutor` 的 `(status, output)` 返回值、`validate_entry_script._extract_env_prefix()` 的 `(env, command)` 返回值、规则表 list-of-tuples、常量 tuple、metadata tuple 等。

### 复查结论

- 未确认出本文档已列问题之外的新运行时问题。
- 第二轮扫描新增命中大多为合法结构：
  - `return ("success", output)` / `return env, command` 这类函数契约返回 tuple。
  - `[(pattern, replacement), ...]`、`[(phase_file, phase_name), ...]` 等规则表。
  - validator token 常量、platform policy token 常量、workflow phase order 常量。
  - 测试项目日志扫描中 `.4f` 格式被粗略扫描误判为 placeholder 数量不匹配，人工复核后无问题。
- 仍建议修复时优先处理本文“确认问题清单”和“固定 Python 版本 / 解释器路径问题”两部分；未确认问题不应扩大修复范围。
