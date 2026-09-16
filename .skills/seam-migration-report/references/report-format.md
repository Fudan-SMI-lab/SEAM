# SEAM Migration Report Generator

Generate structured migration adaptation evaluation reports in the SEAM (Smart Engine for AI Migration) unified format. This skill transforms migration data from various source formats into a standardized SEAM migration evaluation report across NPU, PPU, and MACA/MetaX (MUXI) platforms.

## When to Use

- Generating NPU/PPU/GPU migration adaptation reports from raw migration log artifacts
- Converting multi-phase SEAM migration outputs into a single unified report
- Standardizing migration reports across different platforms
- Creating aggregate summary reports from individual model migration reports
- When user asks to "generate a SEAM report", "create migration report", or "write migration evaluation"

## Report Structure (SEAM Unified Format)

A complete SEAM migration report MUST follow this exact 9-section structure. Every section is mandatory.

---

### Title Format

Adapt the platform name in the title based on target:

```
# {MODEL_NAME} 昇腾 NPU 迁移适配测评报告（SEAM）          # For Ascend NPU
# {MODEL_NAME} MACA/MetaX 迁移适配测评报告（SEAM）         # For MACA/MetaX (MUXI)
# {MODEL_NAME} PPU 迁移适配测评报告（SEAM）               # For PPU
```

---

### Section 1: 报告元信息 (Report Metadata)

Two tables: report meta-information + model summary.

**Table 1 - 报告元信息：**

| 字段 | 内容 |
| --- | --- |
| 模型名称 | {model_display_name} ({repo_identifier}) |
| 目标平台 | {target_platform_spec} |
| GPU厂商 | {Ascend / MACA/MetaX / PPU} |
| 框架 | {PyTorch / vLLM / SGLang / ...} |
| 算子类型 | {LLM / Vision / ...} |
| GitHub链接 | {repo_url} |
| 种类 | {生命科学 / OCR / NLP / ...} |
| 报告版本 | v1.0 |
| 数据截止日期 | {YYYY-MM-DD} |
| 机密级别 | 内部 |

**Table 2 - 模型信息摘要：**

| 字段 | 内容 |
| --- | --- |
| 模型名称 | {model_display_name} |
| 任务类型 | {生命科学 / 光学字符识别 / 自然语言处理 / ...} |
| 运行芯片 | {Ascend 910B2C NPU / MetaX C550 / PPU-ZW810 / ...} |
| 精度格式 | {FP32 / FP16 / N/A} |
| 框架 | {PyTorch / PyTorch, transformers / ...} |
| 模型参数规模 | {0.5B / 7B / —} |
| 模型链接 | {repo_url} |
| 模型来源 | {机构名称 / —} |
| 模型作者 | {作者 / —} |
| 模型描述 | {1-2 sentence description} |

---

### Section 2: 测试时间 (Test Timeline)

For single-run migrations, use one table. For multi-run migrations, add a lead-in sentence and adapt columns.

**Single-run table:**

| 时间节点 | 时间戳 |
| --- | --- |
| 任务起始时间 | {YYYY-MM-DD HH:MM:SS} |
| 环境测试结束时间 | {timestamp}（环境探测内含于 phase_0） |
| 迁移适配起始时间 | {timestamp} |
| 迁移适配结束时间 | {timestamp} |
| 总耗时（环境测试） | {duration} |
| 总耗时（迁移适配） | {duration} |

**Multi-run table (with lead-in sentence like "本次迁移包含 2 次运行（Run 1: ..., Run 2: ...）"):**

| 时间节点 | 时间戳 |
| --- | --- |
| Run 1 任务起始时间 | {timestamp} ({run_id}) |
| Run 1 环境测试结束时间 | Phase 0 耗时 {duration}（环境探测内含于 phase_0） |
| Run 1 迁移适配结束时间 | Phase 5 耗时 {duration}（smoke test） |
| Run 2 任务起始时间 | {timestamp} ({run_id}) |
| Run 2 迁移适配结束时间 | Phase 5 success ({detail}) |
| 总耗时（Run 1 迁移适配） | {summary} |
| 总耗时（Run 2 迁移适配） | {summary} |

---

### Section 3: 报告简述 (Executive Summary)

A 2-4 sentence narrative paragraph describing: what model was migrated, to which platform, how many SEAM rounds, key results. Follow with the assessment matrix:

| 评估维度 | 结果 | 备注 |
| --- | --- | --- |
| 适配状态 | ✅ 成功 / ❌ 失败 / 🚫 阻塞 | {detail} |
| 精度达标 | ✅ / ❌ / N/A | {accuracy_notes} |
| 性能提升 | N/A / {specific} | {performance_notes} |
| 显存优化 | N/A / {specific} | {memory_notes} |
| SEAM 迁移回合数 | {N} 轮 | {rounds_notes} |

---

### Section 4: 模型资料查询 (Model Research)

#### 4.1 模型仓库信息

| 属性 | 值 |
| --- | --- |
| 参数量 | {params or "详见模型仓库" or "—"} |
| 架构 | {architecture or "—"} |
| 隐藏维度 | {hidden_dim or "—"} |
| 层数 | {layers or "—"} |
| 注意力头数 | {attention_heads or "—"} |
| 上下文长度 | {context_length or "—"} |
| 词表大小 | {vocab_size or "—"} |
| 训练数据 | {training_data or "详见模型仓库 {repo}" or "—"} |
| 精度格式 | {precision or "—"} |
| 许可证 | {license or "详见模型仓库" or "—"} |
| 仓库地址 | {repo_url or "—"} |

Follow with a one-line summary sentence (e.g., "{Model} 的架构为 {arch}，隐藏维度 {dim}，{layers} 层，{heads} 个注意力头。详细信息参见模型仓库。").

#### 4.2 互联网公开信息

| 来源 | 关键信息 | 量化数据 |
| --- | --- | --- |
| HuggingFace / ModelScope | 模型仓库 {repo} | 参数量 {params} |
| 模型 config.json | {arch}, hidden={dim}, layers={layers} | 架构规格 |
| 本次 {platform} 迁移 | SEAM 自动迁移至 {platform} | 见本报告第 7-8 节 |

Follow with a one-line summary sentence.

#### 4.3 配图

Model architecture description. Reference to `files/` subdirectory for architecture diagrams and run screenshots. For models where architecture info is unavailable: state so and refer to original repository documentation.

---

### Section 5: 测试运行环境 (Test Environment)

#### 5.1 实际探测环境

| 环境项 | 实际值 |
| --- | --- |
| OS | {os or "—"} |
| 内核版本 | {kernel or "—"} |
| CPU 型号 | {cpu_model or "—"} |
| CPU 核心数 | {cpu_cores or "—"} |
| 内存 | {ram or "—"} |
| GPU/NPU/加速器 型号 | {accelerator_model} |
| GPU/NPU/加速器 数量 | {count} |
| 单卡显存 | {vram or "—"} |
| 驱动版本 | {driver_version or "—"} |
| 异构计算库版本 | {compute_lib} |
| PyTorch 版本 | {pytorch_version} |
| torch_{npu/musa/ppu} 版本 | {torch_accel_version or "N/A"} |
| Python 版本 | {python_version} |

**Platform-specific field notes:**
- NPU: `torch_npu 版本`, `CANN {version}` as compute lib
- MUXI: `torch 后端版本` (CUDA-compatible via MetaX), `MACA SDK` as compute lib
- PPU: `torch_ppu 版本`, `PPU-SDK` or `CUDA {version} (PPU compatibility layer)` as compute lib

#### 5.2 互联网参考环境（A100/H100 对比）

| 环境项 | A100 典型配置 | 本环境 | 差异 |
| --- | --- | --- | --- |
| GPU 型号 | NVIDIA A100 80GB | {accelerator_model} | 不同厂商 |
| 显存 | 80GB HBM2e | {vram} | {diff} |
| PyTorch | 2.x+cu118 | {pytorch_version} | {platform} 适配版 |
| 计算库 | CUDA 11.8+ | {compute_lib} | 异构 |
| 推理框架 | transformers (CUDA) | {framework_detail} | 自定义算子替换 / CUDA API 兼容透传 |

---

### Section 6: 历史测评报告查询 (Historical Reports)

Lead-in sentence stating data cutoff date and whether this is the first migration. Table:

| 序号 | 报告名称 | 时间 | 简要结论 | 与本报告关联 |
| --- | --- | --- | --- | --- |
| 1 | {report or "无"} | {date or "—"} | {conclusion or "首次 {platform} 适配" or "—"} | {relation or "本报告为初始记录" or "首次测评"} |

If there are prior runs on other platforms, enumerate them here.

---

### Section 7: 初始适配测评报告 (Initial Adaptation Evaluation)

#### 7.1 适配性维度 (Adaptability)

Qualitative conclusion line: `{是/否}（{explanation}）`

适配矩阵：

| 适配项 | 状态 | 详情 |
| --- | --- | --- |
| 模型加载 | ✅ / ❌ | {detail with timing if available} |
| 推理运行 | ✅ / ❌ | {detail with timing if available} |
| 训练运行 | N/A / ✅ / ❌ | {detail} |
| 多卡并行 | N/A / ✅ / ❌ | {detail} |

If errors occurred, add error classification:

| 错误类型 | 报错原文（截取关键行） | 涉及文件 | 可能原因 |
| --- | --- | --- | --- |

If no errors: "本次迁移未触发运行时错误。"

#### 7.2 精度维度 (Accuracy)

| 测试项 | 基准值 | 实测值 | 相对误差 | 是否达标 |
| --- | --- | --- | --- | --- |
| 前向推理输出形状 | 预期张量 | {shape} | {error}%（形状一致） | ✅ / — |
| 输出数据类型 | {expected} | {actual} | — | ✅ / — |
| 输出均值 (mean) | — | {value} | — | 实测值 |
| 输出标准差 (std) | — | {value} | — | 实测值 |
| 输出最小值 (min) | — | {value} | — | 实测值 |
| 输出最大值 (max) | — | {value} | — | 实测值 |
| 输出 L2 范数 (norm) | — | {value} | — | 实测值 |

When accuracy data is unavailable: state "本次迁移未进行精度对比测试，精度数据待补充。" and fill table with "—".

#### 7.3 性能维度 (Performance)

| 指标 | 实测值 | 数据来源 |
| --- | --- | --- |
| 推理耗时（精确） | {value}s | entry 脚本 timed second pass |
| 推理耗时（phase 计时） | {value} | SEAM phase_5_validation |
| 模型加载耗时 | {value} | SEAM phase 计时 |
| 总耗时 | {value} | 含全部 SEAM phases |
| phase_5 验证耗时 | {value} | SEAM phase_5 计时 |
| HBM 峰值占用 | {value} MB | torch.{npu/musa}.max_memory_allocated |
| HBM 当前占用 | {value} MB | torch.{npu/musa}.memory_allocated |
| HBM 峰值占比 | {value}% | 占 {total} MB 总量 |
| 吞吐量 (tok/s) | {value} | {n_tokens} tokens / {inference_time}s |
| 输入 token 数 | {value} | {context} |
| 吞吐量 (samples/s) | {value} | 1 / 推理耗时 |

When performance data is unavailable, fill with "—".

#### 7.4 稳定性维度 (Stability)

| 测试项 | 结果 | 备注 |
| --- | --- | --- |
| 长时间运行 (>1h) | 未测试 / ✅ / ❌ | {detail} |
| 并发请求 | 未测试 / ✅ / ❌ | {detail} |
| 异常恢复 | 未测试 / ✅ / ❌ | {detail} |

Default when not tested: all "未测试" with note "本次迁移仅验证单次前向推理".

---

### Section 8: 迁移优化适配报告（SEAM）(Migration Optimization Report)

#### 8.1 SEAM 适配过程

| 项目 | 内容 |
| --- | --- |
| 使用的工作流 | SEAM YAML-driven workflow v2.0（opencode 后端） |
| SEAM 版本 | {version or "见 src/scripts/run_seam.sh" or "{platform}_general v2.0"} |
| 完整 CLI 命令 | `run_seam.sh <project> --server_type opencode --server-conflict-action error --no-review` |
| 修改文件总数 | {N} 轮迭代 / Run 1: {N} 文件; Run 2: {N} 文件 |
| 迁移回合数 | {N} 轮 |
| SEAM 输出目录 | {path or "见 status.json output_projects_path"} |

修改文件清单：

| 文件路径 | 修改类型 | 说明 |
| --- | --- | --- |
| {file_path} | {创建 / SEAM 自动迁移 / 修改} | {description} |

优化策略：

| 策略 | 说明 | 效果 |
| --- | --- | --- |
| {strategy_name} | {description} | {effect} |

#### 8.2 最终运行验证

| 验证维度 | 结果 |
| --- | --- |
| 适配状态 | ✅ 成功 / ❌ 失败 / 🚫 阻塞 |
| {NPU/PPU/MetaX} 验证 | {status_detail} |
| 推理输出 | {output_description} |
| CPU fallback | {无 / 有} |

运行日志关键片段：

```
{key_log_lines}
```

Note after log block: "运行截屏请放置于 `files/image_run.png`。"

---

### 附录 (Appendix)

#### A. 参考文献

Bullet list:
* 模型仓库：{repo_url}
* {Platform} 文档：{doc_url}
* SEAM 迁移框架：src/scripts/run_seam.sh

#### B. 工具版本

| 工具 | 版本 |
| --- | --- |
| SEAM | {version} |
| OpenCode Server | Sisyphus ({model}) |
| Python | {version} |
| PyTorch | {version} |
| torch_{npu/musa/ppu} | {version} |
| {compute_lib_name} | {version} |
| {driver_name} | {version} |

#### C. 相关链接

| 说明 | 链接 |
| --- | --- |
| 模型仓库 | {repo} |
| SEAM 运行日志 | pipeline/state/{project}/logs/seam_run.log |
| 迁移证据 | pipeline/state/{project}/evidence/status.json |
| Agent 迁移轨迹 | pipeline/state/{project}/migration_trace/ |
| 迁移后代码 | pipeline/state/{project}/migrated_code/ |

#### D. Agent 迁移轨迹（提示词/工具调用/技能调用）

Lead-in sentence: "以下文件保存了 SEAM 迁移过程中 Agent（LLM）的完整操作记录，包括每阶段的提示词、工具调用、技能调用、执行结果及中间产物："

List of `.sm-artifacts/` paths:
```
.sm-artifacts/{run_id}/execution_journal.jsonl
.sm-artifacts/{run_id}/raw/phase_0_env_detect_attempt0.json
.sm-artifacts/{run_id}/raw/phase_1_project_analysis_attempt0.json
.sm-artifacts/{run_id}/raw/phase_2_venv_create_attempt0.json
.sm-artifacts/{run_id}/raw/phase_3_entry_script_attempt0.json
.sm-artifacts/{run_id}/raw/phase_35_static_validate_attempt0.json
.sm-artifacts/{run_id}/raw/phase_4_rule_migration_attempt0.json
.sm-artifacts/{run_id}/raw/phase_5_validation_attempt0.json
.sm-artifacts/{run_id}/raw/phase_6_report_attempt0.json
```

#### E. 多 Run 详情 (ONLY for multi-run reports)

**Run 1 ({run_id})**: {description of what this run did}.
**Run 2 ({run_id})**: {description of what this run did}.

Phase execution summary table:

| Phase | Run 1 | Run 2 |
| --- | --- | --- |
| Phase 0 - Env Detect | {status} | {status} |
| Phase 1 - Project Analysis | {status} | {status} |
| Phase 2 - Venv Create | {status} | {status} |
| Phase 3 - Entry Script | {status} | {status} |
| Phase 3.5 - Static Validate | {status} | {status} |
| Phase 4 - Rule Migration | {status} | {status} |
| Phase 5 - Validation | {status} | {status} |
| Phase 6 - Report | {status} | {status} |

#### F. Air-Gap 环境说明 (ONLY for air-gapped environments)

Narrative description of air-gapped constraints and workarounds.

---

## Report Generation Rules

### Status Determination
- **PASS / ✅ 成功**: Model loaded and ran inference successfully on target platform
- **FAILED / ❌ 失败**: Migration attempted but errors occurred (timeout, operator incompatibility, etc.)
- **BLOCKED / 🚫 阻塞**: Prerequisites not met (missing dependencies, staging issues, etc.)

### Platform-Specific Naming and Fields

| Platform | Title Keyword | GPU厂商 | 运行芯片 | 加速器型号 | 计算库 |
| --- | --- | --- | --- | --- | --- |
| 昇腾 NPU | 昇腾 NPU | Ascend | Ascend 910B2C NPU | 910B2C | CANN {version} |
| MACA/MetaX | MACA/MetaX | MACA/MetaX | MUXI/MetaX (C550) | MetaX C550 | MACA SDK |
| PPU | PPU | PPU | PPU (登临 ZW810) | PPU-ZW810 | PPU-SDK v{version} or CUDA {version} (PPU compat) |

### When Extracting from Multi-File Sources
1. Collect all phase outcomes and consolidate into Section 3
2. Merge tool execution data across files into Sections 7 and 8
3. Cross-reference operations logs with summary reports for consistency
4. When data is missing, use "—" or "N/A" rather than fabricating values
5. Preserve all quantitative metrics found; do not omit measured values

### Quality Standards
- All tables must use consistent `| --- | --- |` separators
- Status emojis: ✅ (pass/success), ❌ (fail/error), 🚫 (blocked), ⚠️ (warning/fallback)
- Time values should be in human-readable format (seconds or minutes, with precision where available)
- Performance timings from entry scripts should preserve full float precision (e.g., `0.014227636158466339 s`)
- Error messages should be cited verbatim with original formatting preserved
- If a value is not available from source data, use "—" not "N/A" for table cells where data was expected but missing
- Never fabricate benchmark comparison data; use "N/A" when no baseline exists

### Narrative Sections (Must Not Be Empty)
The following sections MUST include narrative text, not just tables:
- **Section 3**: A paragraph describing the migration journey and key outcomes
- **Section 4.1 & 4.2**: A one-line summary after each table
- **Section 4.3**: A sentence or two describing the model architecture
- **Section 6**: A lead-in sentence about historical context
- **Section 7.1**: A qualitative conclusion line before the matrix
- **Section 8.2**: Verify status is restated and log excerpts are included

### Common Patterns for Fallback Reports
When Phase 6 (LLM report generation) returns empty results (common on MUXI/PPU):
- Note in Section 3: "由于 Phase 6 LLM 报告生成阶段返回空结果，本报告基于 phase artifact 的 fallback 渲染器生成"
- Accuracy/performance data will likely be "—" throughout
- Still report Phase 5 success clearly if validation passed
- Use "Phase 5 验证阶段状态为 success" as the key success signal
