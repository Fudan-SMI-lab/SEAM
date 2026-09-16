# SEAM 统一迁移报告

SEAM 会保留原有 `SUMMARY_REPORT.md`、操作记录和工具报告，并额外生成 `MIGRATION_REPORT.md`，按“报告元信息、测试时间、报告简述、模型资料、运行环境、历史测评、初始适配、迁移优化、附录”组织。

## 自动生成

Phase 6 会加载仓库内 `.skills/seam-migration-report/SKILL.md` 和用户格式模板，并得到当前 run 的证据文件清单。模型先写原有明细，再合成统一报告；返回的 `report_paths` 包含统一报告路径。

模型没有生成完整章节或发生调用错误时，本地合成器根据阶段 JSON、现有 Markdown 和日志补生成报告。它不调用模型，也不重新执行迁移或推理。

工作流结束后会在运行 output 目录生成统一报告。V3 finalizer 写入最终 `summary.json` 后再次合成，将最终终态和耗时纳入报告；成功运行已有的 `migration_reports/` 发布目录也会更新报告和校验清单。失败运行仍可在 output 目录查看诊断报告，不会发布为成功迁移交付。

```text
<run-output>/
├── MIGRATION_REPORT.md
├── migration_report_facts.json
├── migration_report_manifest.json
├── summary.json
└── ...原有日志与证据
```

`migration_report_facts.json` 保留结构化事实和来源引用。`migration_report_manifest.json` 记录来源路径、读取范围、SHA-256、生成方式和缺失/截断说明。最终本地合成报告将 Phase 6 的 skill 报告视作来源资料，保留摘录；不会把其中的文字描述当成最终机器验收结果。

## 为已有 output 补生成

在 SEAM 仓库根目录执行：

```bash
PYTHONPATH=src python -m scripts.generate_migration_report \
  --input /path/to/run-output
```

产物保存在输入目录，原始明细不会被覆盖。不需要 OpenCode 服务、模型 API 或加速器。

如果证据还在迁移项目的 `.sm-artifacts` 中，显式指定该 run：

```bash
PYTHONPATH=src python -m scripts.generate_migration_report \
  --input /path/to/run-output \
  --artifact-dir /path/to/model/.sm-artifacts/run-20260916 \
  --project-dir /path/to/model
```

其他选项：

- `--run-id NAME`：输入目录的 `.sm-artifacts/` 有多个 run 时选择一个。若与根目录 `summary.json` 的 run ID 不符则拒绝混合。
- `--output /path/to/new-report.md`：使用新文件名。默认报告名可以重复生成；自定义输出若已存在会拒绝覆盖，避免破坏原始 MD。
- `--max-file-bytes 1048576 --max-total-bytes 16777216`：提高读取预算。默认单文件 256 KiB、总计 4 MiB，最多 200 个文件、目录深度 8。过长日志读取首尾并标记省略，不会默默漏掉尾部错误。

`--input` 应指向一次运行的目录，而不是多个模型输出的共同父目录。来源文件和目录中的符号链接不跟随。

## 结论与数值的处理

- 最终框架终态优先于旧摘要和阶段状态；冲突会在报告中说明。
- 保留源数值精度、JSON 字段路径及可识别日志指标的行号。只解析明确命名的日志指标，不用正则猜测自然语言中的测试结论。
- Phase 5 验证通过不代表精度、性能或稳定性自动达标；首次测试和最终测试不混用。
- 模板中的芯片型号、SDK 版本、日期和基准环境不是默认事实。数据缺失时明确标注。
- 报告与日志摘录使用凭据脱敏。长来源在报告内保留首尾摘录；已解析的长 JSON 的数值另外列出，源路径与读取范围保留在清单中。
- 报告生成失败会记录诊断，不更改已经冻结的迁移结果。

目前历史目录补生成使用确定性合成器；较丰富的自然语言分析由自动运行中的 Phase 6 skill 提供。未做联网资料补全、图像生成或额外基准测试。
