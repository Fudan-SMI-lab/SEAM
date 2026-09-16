"""Evidence-first renderer for the seam-migration-report skill's nine sections."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.migration_report_evidence import ReportEvidence, flatten

UNKNOWN = "—（未采集）"
HEADINGS = (
    "1. 报告元信息",
    "2. 测试时间",
    "3. 报告简述",
    "4. 模型资料查询",
    "5. 测试运行环境",
    "6. 历史测评报告查询",
    "7. 初始适配测评报告",
    "8. 迁移优化适配报告（SEAM）",
    "附录",
)


def cell(value: Any) -> str:
    if value is None or value == "":
        return UNKNOWN
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("\n", "<br>")
    )


def table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    return (
        "\n".join(
            [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join("---" for _ in headers) + " |",
                *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
            ]
        )
        + "\n"
    )


def code_block(text: str, language: str = "text") -> str:
    runs = re.findall(r"`+", text)
    fence = "`" * max(3, max((len(run) + 1 for run in runs), default=3))
    return f"{fence}{language}\n{text.rstrip()}\n{fence}\n"


def build_facts(
    evidence: ReportEvidence,
    *,
    prior_outputs: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    project_dir: str = "",
) -> dict[str, Any]:
    phases: dict[str, Any] = {}
    phase_sources: dict[str, str] = {}
    for source in evidence.sources:
        match = re.fullmatch(r"(phase_.+)_canonical\.json", Path(source.path).name)
        if match and Path(source.path).parent.name == "validated" and isinstance(source.data, dict):
            phases[match[1]] = source.data
            phase_sources[match[1]] = source.id
    for phase, data in (prior_outputs or {}).items():
        if isinstance(data, dict) and phase.startswith("phase_"):
            phases[phase] = data
            phase_sources[phase] = "运行上下文:" + phase
    summary, summary_source = evidence.document("summary.json", root_only=True)
    # Generic performance summary files must not impersonate a framework run summary.
    if not summary.get("run_id"):
        summary, summary_source = {}, "—"
    disk_timeline, timeline_source = evidence.document("run_timeline.json")
    timing = timeline if timeline is not None else disk_timeline
    if timeline is not None:
        timeline_source = "运行时间线上下文"
    phase5 = phases.get("phase_5_validation", {})
    runtime = summary.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    outcome = str(summary.get("overall_status", "")).lower()
    runtime_outcome = runtime.get("outcome_status")
    if not isinstance(runtime_outcome, str):
        runtime_outcome = ""
    use_runtime_outcome = (
        outcome in {"pass", "passed"} and runtime_outcome == "passed_with_reviews"
    ) or (not outcome and runtime_outcome in {"passed", "passed_with_reviews", "failed"})
    if use_runtime_outcome:
        outcome = str(runtime_outcome)
    if outcome in {"pass", "passed", "passed_with_reviews"}:
        status = (
            "✅ 成功（框架终态，含评审记录）"
            if outcome == "passed_with_reviews"
            else "✅ 成功（框架最终终态）"
        )
        status_source = summary_source + (
            ":/runtime/outcome_status" if use_runtime_outcome else ":/overall_status"
        )
    elif outcome in {"fail", "failed", "failure"}:
        status = "❌ 失败（框架最终终态）"
        status_source = summary_source + (
            ":/runtime/outcome_status" if use_runtime_outcome else ":/overall_status"
        )
    elif outcome in {"blocked", "blocking"}:
        status = "🚫 阻塞"
        status_source = summary_source + ":/overall_status"
    elif phase5.get("success") is True or phase5.get("status") in ("success", "passed"):
        status = "✅ Phase 5 验证通过（尚无最终终态证据）"
        status_source = phase_sources.get("phase_5_validation", "—")
    elif phase5.get("success") is False or phase5.get("status") in ("failed", "failure"):
        status = "❌ Phase 5 验证失败"
        status_source = phase_sources.get("phase_5_validation", "—")
    else:
        status, status_source = "未验证（证据不足）", "—"
    if outcome in {"fail", "failed", "failure"} and phase5.get("success") is True:
        evidence.warnings.append(
            "Phase 5 success 与最终失败终态不同；正文使用最终终态，保留阶段记录。"
        )
    attempts = []
    for source in evidence.sources:
        data = source.data
        if (
            isinstance(data, dict)
            and data.get("schema_name") == "seam.phase5-attempt-receipt"
            and data.get("run_id") == evidence.run_id
        ):
            number = data.get("attempt_number")
            if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                attempts.append({**data, "source": source.id})
            else:
                evidence.warnings.append(f"忽略无效 attempt 编号：{source.id}")
    attempts.sort(key=lambda attempt: attempt.get("attempt_number", 0))
    observations = []
    metric_pattern = re.compile(
        r"(?P<name>inference_time(?:_seconds|_s)?|latency(?:_ms|_s)?|"
        r"tokens_per_second|throughput_tokens_per_second|samples_per_second|"
        r"output_(?:mean|std|min|max|norm)|max_abs_error|max_rel_error|relative_error|"
        r"peak_memory_(?:bytes|mb|gb)|max_memory_allocated|memory_allocated)"
        r'["\']?\s*[:=]\s*["\']?(?P<value>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
        r"(?P<unit>\s*(?:ms|seconds|s|MB|GB|bytes|tok/s|tokens/s|samples/s|%))?",
        re.I,
    )
    for source in evidence.sources:
        if Path(source.path).suffix.lower() not in {".log", ".txt", ".md"}:
            continue
        for line_number, line in enumerate(source.text.splitlines(), 1):
            for match in metric_pattern.finditer(line):
                observations.append(
                    {
                        "name": match["name"],
                        "value": match["value"],
                        "unit": (match["unit"] or "").strip(),
                        "source": f"{source.id}:"
                        + ("fragment-" if source.truncated else "")
                        + f"L{line_number}",
                    }
                )
    return {
        "schema_version": "1.0",
        "run_id": evidence.run_id,
        "project_dir": project_dir,
        "phases": phases,
        "phase_sources": phase_sources,
        "summary": summary,
        "summary_source": summary_source,
        "timeline": timing,
        "timeline_source": timeline_source,
        "assessment": {"status": status, "source": status_source},
        "log_measurements": observations,
        "attempts": attempts,
        "warnings": evidence.warnings,
    }


def render_report(evidence: ReportEvidence, facts: dict[str, Any], reason: str = "") -> str:
    phases = facts["phases"]
    phase_sources = facts["phase_sources"]
    summary = facts["summary"]
    timing = facts["timeline"]
    assessment = facts["assessment"]
    runtime = summary.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    records = [
        (phase, pointer, value, phase_sources[phase])
        for phase, data in phases.items()
        for pointer, value in flatten(data)
    ]

    def fact(keys: tuple[str, ...], prefix: str = "") -> tuple[Any, str]:
        for phase, pointer, value, source in records:
            if (
                phase.startswith(prefix)
                and pointer.rsplit("/", 1)[-1] in keys
                and value is not None
            ):
                return value, f"{source}:{pointer}"
        return UNKNOWN, "—"

    def value(keys: tuple[str, ...], prefix: str = "") -> Any:
        return fact(keys, prefix)[0]

    def measured_rows(
        fields: list[tuple[str, tuple[str, ...]]], prefix: str = ""
    ) -> list[tuple[Any, ...]]:
        return [(label, *fact(keys, prefix)) for label, keys in fields]

    model = value(("model_name", "model_display_name", "project_name"), "phase_1")
    if model == UNKNOWN and facts["project_dir"]:
        model = Path(facts["project_dir"]).name + "（项目目录名）"
    platform = value(("target_platform", "platform", "accelerator_type"), "phase_0")
    repo = value(("repo_url", "repository_url", "model_url"), "phase_1")
    lines = [
        f"# {cell(model)} {cell(platform)} 迁移适配测评报告（SEAM）",
        "",
        "> 本报告按 seam-migration-report skill 合成。正文结论取自结构化证据；原始 MD 与日志摘录单独标注，不替代最终验收。",
        "",
        "## " + HEADINGS[0],
        "",
        table(
            ("字段", "内容"),
            [
                ("模型名称", model),
                ("目标平台", platform),
                ("GPU厂商", value(("vendor", "accelerator_vendor"), "phase_0")),
                ("框架", value(("framework", "framework_name"))),
                ("算子类型", value(("model_type", "task_type"), "phase_1")),
                ("GitHub/模型链接", repo),
                ("种类", value(("category",), "phase_1")),
                ("报告版本", "v1.0"),
                ("数据截止时间", timing.get("run_ended_at", UNKNOWN)),
                ("机密级别", "未指定"),
                ("Run ID", facts["run_id"]),
                ("生成方式", "本地证据合成；Phase 6 的 skill 报告如存在则收录于附录"),
                ("生成说明", reason or "未重新执行迁移或性能测试"),
            ],
        ),
        "### 模型信息摘要",
        "",
        table(
            ("字段", "内容", "来源"),
            measured_rows(
                [
                    ("模型参数规模", ("parameter_count", "num_parameters", "model_size")),
                    ("任务类型", ("task_type",)),
                    ("精度格式", ("torch_dtype", "dtype", "precision")),
                    ("模型来源", ("organization", "model_source")),
                    ("模型作者", ("author",)),
                    ("模型描述", ("model_description", "description")),
                ],
                "phase_1",
            ),
        ),
        "## " + HEADINGS[1],
        "",
        "时间保留来源时区。阶段耗时不等于模型推理耗时；缺失的开始/结束时间不使用文件修改时间代替。",
        "",
        table(
            ("时间节点", "值", "来源"),
            [
                ("任务起始时间", timing.get("run_started_at"), facts["timeline_source"]),
                ("任务结束时间", timing.get("run_ended_at"), facts["timeline_source"]),
                (
                    "总耗时（秒）",
                    summary.get("total_duration_seconds"),
                    facts["summary_source"] + ":/total_duration_seconds",
                ),
            ],
        ),
    ]
    phase_times = timing.get("phases") or summary.get("phases") or []
    lines += [
        table(
            ("阶段", "状态", "开始时间", "结束时间", "耗时（秒）"),
            [
                (
                    p.get("phase_id"),
                    p.get("status"),
                    p.get("started_at"),
                    p.get("ended_at"),
                    p.get("duration_seconds"),
                )
                for p in phase_times
                if isinstance(p, dict)
            ]
            or [(UNKNOWN,) * 5],
        ),
        "## " + HEADINGS[2],
        "",
        f"本报告整理 {cell(model)} 在 {cell(platform)} 上的迁移证据，共收录 {len(evidence.sources)} 个源文件。"
        f"当前结论为 **{assessment['status']}**（来源：{cell(assessment['source'])}）。"
        "精度、性能、稳定性需要各自的测量依据，不能由阶段通过状态推断。",
        "",
        table(
            ("评估维度", "结果", "备注"),
            [
                ("适配状态", assessment["status"], assessment["source"]),
                ("精度达标", "未独立判定", "实测数据见第 7 节；形状和 dtype 一致不足以判定精度"),
                ("性能提升", "N/A（未计算比较结论）", "保留实测性能指标；无可比基准不计算加速比"),
                ("显存优化", "未独立判定", "显存实测值与优化收益分别记录"),
                (
                    "SEAM 迁移回合数",
                    value(("iteration_count", "repair_rounds"), "phase_5"),
                    "仅采用显式记录，不按文件或阶段数量推算",
                ),
            ],
        ),
        "## " + HEADINGS[3],
        "",
        "### 4.1 模型仓库信息",
        "",
        table(
            ("属性", "值", "来源"),
            measured_rows(
                [
                    ("参数量", ("parameter_count", "num_parameters")),
                    ("架构", ("architectures", "architecture", "model_type")),
                    ("隐藏维度", ("hidden_size", "hidden_dim")),
                    ("层数", ("num_hidden_layers", "num_layers")),
                    ("注意力头数", ("num_attention_heads",)),
                    ("上下文长度", ("max_position_embeddings", "context_length")),
                    ("词表大小", ("vocab_size",)),
                    ("训练数据", ("training_data",)),
                    ("精度格式", ("torch_dtype", "dtype")),
                    ("许可证", ("license",)),
                    ("仓库地址", ("repo_url", "repository_url", "model_url")),
                ],
                "phase_1",
            ),
        ),
        "以上仅列本次运行记录的模型规格；缺失规格不根据模型名称推断。",
        "",
        "### 4.2 互联网公开信息",
        "",
        table(
            ("来源", "关键信息", "量化数据"),
            [(repo, "未执行联网资料检索；以已有模型资料为准", UNKNOWN)],
        ),
        "未提供的公开资料保留缺失，不代表互联网上不存在相应信息。",
        "",
        "### 4.3 配图",
        "",
        "未自动生成架构图或运行截图；模型架构以已记录规格及原始仓库资料为准。",
        "",
        "## " + HEADINGS[4],
        "",
        "### 5.1 实际探测环境",
        "",
        table(
            ("环境项", "实际值", "来源"),
            measured_rows(
                [
                    ("OS", ("os", "os_name", "os_version")),
                    ("内核版本", ("kernel", "kernel_version")),
                    ("CPU 型号", ("cpu_model",)),
                    ("CPU 核心数", ("cpu_count", "cpu_cores")),
                    ("内存", ("ram", "total_memory_gb", "memory_gb")),
                    ("加速器型号", ("accelerator_model", "gpu_model", "npu_model", "device_name")),
                    ("加速器数量", ("device_count", "gpu_count", "npu_count")),
                    ("单卡显存", ("vram", "vram_gb", "device_memory_gb")),
                    ("驱动版本", ("driver_version",)),
                    ("计算库版本", ("cann_version", "maca_version", "sdk_version", "cuda_version")),
                    ("PyTorch 版本", ("torch_version", "pytorch_version")),
                    (
                        "加速器后端版本",
                        (
                            "torch_npu_version",
                            "torch_musa_version",
                            "torch_ppu_version",
                            "backend_version",
                        ),
                    ),
                    ("Python 版本", ("python_version",)),
                ],
                "phase_0",
            ),
        ),
        "环境数据来自 Phase 0 探测，不自动视为最终执行容器/解释器。以下另列框架记录的实际环境与 namespace。",
        "",
        table(
            ("环境 ID", "namespace", "事实", "值", "状态"),
            [
                (
                    env.get("environment_id"),
                    item.get("namespace"),
                    item.get("name"),
                    item.get("value"),
                    item.get("status"),
                )
                for env in runtime.get("environments", [])
                if isinstance(env, dict)
                for item in env.get("facts", [])
                if isinstance(item, dict)
            ]
            or [("—", "—", "未提供运行环境投影", "—", "未采集")],
        ),
        "### 5.2 互联网参考环境（A100/H100 对比）",
        "",
        "未提供同条件 A100/H100 实测基准，不填入模板中的示例硬件、软件版本或性能比较。",
        "",
        "## " + HEADINGS[5],
        "",
        "本报告仅处理所选 run。未提供历史记录不等于本次是首次迁移。",
        "",
        table(
            ("序号", "报告名称", "时间", "简要结论", "与本报告关联"),
            [("—", "未提供独立历史报告", "—", "—", "—")],
        ),
        "## " + HEADINGS[6],
        "",
        "### 7.1 适配性维度",
        "",
        (
            "当前证据中最早的 attempt 记录如下；退出码为 0 仅表示脚本正常退出，不自动证明加载、推理、精度各项达标。"
            if facts["attempts"]
            else "未单独确认首次适配测试结果。最终 canonical 验证结果不用于冒充首次测试。"
        ),
        "",
        table(
            ("最早记录的 attempt", "编号", "退出码", "来源"),
            [
                (
                    a.get("attempt_id"),
                    a.get("attempt_number"),
                    a.get("shell_exit_code"),
                    a["source"],
                )
                for a in facts["attempts"][:1]
            ]
            or [("—", "—", "—", "未提供 attempt receipt")],
        ),
        table(
            ("适配项", "状态", "详情"),
            [
                (name, "未独立验证", "见实际 attempt 日志，不能仅由整体状态推断")
                for name in ("模型加载", "推理运行", "训练运行", "多卡并行")
            ],
        ),
        "### 7.2 精度维度",
        "",
        "数值精度需要真实基准、同一输入和容差。以下是 canonical 验证记录的现有测量值，不冒充首次测试结果，也不将输出统计量或形状一致标成精度达标。",
        "",
        table(
            ("测试项", "实测值", "来源"),
            measured_rows(
                [
                    ("前向推理输出形状", ("output_shape",)),
                    ("输出数据类型", ("output_dtype",)),
                    ("输出均值", ("output_mean",)),
                    ("输出标准差", ("output_std",)),
                    ("输出最小值", ("output_min",)),
                    ("输出最大值", ("output_max",)),
                    ("输出 L2 范数", ("output_norm",)),
                    ("最大绝对误差", ("max_abs_error",)),
                    ("相对误差（来源原单位）", ("relative_error", "max_rel_error")),
                ],
                "phase_5",
            ),
        ),
        "### 7.3 性能维度",
        "",
        "以下数据来自 canonical 验证阶段；是否属于初始测试需结合来源判断。",
        "",
        table(
            ("指标", "实测值（保留来源单位）", "来源"),
            measured_rows(
                [
                    (
                        "推理耗时（秒）",
                        ("inference_time_seconds", "inference_time_s", "inference_time"),
                    ),
                    ("模型加载耗时（秒）", ("model_load_time_seconds", "load_time_s")),
                    ("峰值显存（字节）", ("max_memory_allocated", "peak_memory_bytes")),
                    ("峰值显存（MB）", ("peak_memory_mb",)),
                    ("当前显存（字节）", ("memory_allocated",)),
                    ("吞吐量（tok/s）", ("tokens_per_second", "throughput_tokens_per_second")),
                    ("吞吐量（samples/s）", ("samples_per_second",)),
                ],
                "phase_5",
            ),
        ),
        "其他结构化数值和单位保留在附录证据中；日志中的计时保持原文，不将阶段耗时替换为推理耗时。",
        "",
        "其他原文测量记录（可能属于初始测试或中间重试，不自动归为最终验证）：",
        "",
        table(
            ("原始指标名", "值", "显式单位", "来源"),
            [
                (item["name"], item["value"], item["unit"] or "见指标名/原文", item["source"])
                for item in facts["log_measurements"]
            ]
            or [("—", "—", "—", "未发现可解析的命名测量记录")],
        ),
        "### 7.4 稳定性维度",
        "",
        table(
            ("测试项", "结果", "备注"),
            [
                (name, "未独立验证", "需要专门测试记录")
                for name in ("长时间运行（>1h）", "并发请求", "异常恢复")
            ],
        ),
        "## " + HEADINGS[7],
        "",
        "### 8.1 SEAM 适配过程",
        "",
        table(
            ("项目", "内容", "来源"),
            [
                ("工作流", summary.get("workflow_path"), facts["summary_source"]),
                ("实际运行命令", *fact(("run_command",), "phase_3")),
                (
                    "修改文件总数",
                    *fact(("files_migrated", "files_modified", "modified_files"), "phase_4"),
                ),
                ("迁移回合数", *fact(("iteration_count",), "phase_5")),
                ("迁移策略", *fact(("strategy", "strategy_file"), "phase_4")),
            ],
        ),
        "修改清单、工具操作及优化建议保留在附录。建议不等于已经实施，修改数量不等于迁移回合数。",
        "",
        table(
            ("attempt", "退出码", "记录完整", "框架接受标记", "来源"),
            [
                (
                    a.get("attempt_id"),
                    a.get("shell_exit_code"),
                    a.get("complete"),
                    a.get("accepted"),
                    a["source"],
                )
                for a in facts["attempts"]
            ]
            or [("—", "—", "—", "—", "未提供 attempt receipt")],
        ),
        "### 8.2 最终运行验证",
        "",
        f"最终可确认结论：**{assessment['status']}**。",
        "",
        table(
            ("验证维度", "结果", "来源"),
            [
                ("适配状态", assessment["status"], assessment["source"]),
                ("Phase 5 状态", *fact(("success", "status"), "phase_5")),
                ("CPU fallback", *fact(("cpu_fallback_detected", "cpu_fallback"), "phase_5")),
                ("推理输出", *fact(("output_shape", "output_summary"), "phase_5")),
            ],
        ),
    ]
    logs = sorted(
        (s for s in evidence.sources if Path(s.path).suffix in {".log", ".txt"}),
        key=lambda s: ("phase_5" not in s.path, "stderr" not in s.path, s.path),
    )
    if logs:
        for source in logs[:3]:
            lines += [
                f"运行日志片段 [{source.id}] `{source.path}`（末尾摘录，完整读取范围见附录）：",
                "",
                code_block(source.text[-4000:]),
            ]
    else:
        lines += ["未找到独立的运行日志文件；不生成虚构日志或截图。", ""]
    lines += [
        "## " + HEADINGS[8],
        "",
        "### A. 参考与来源索引",
        "",
        table(
            ("编号", "源文件", "大小（字节）", "读取范围"),
            [
                (s.id, s.path, s.size_bytes, "首尾片段" if s.truncated else "全文")
                for s in evidence.sources
            ]
            or [("—", "未提供", "—", "—")],
        ),
        "### B. 生成限制",
        "",
        *("- " + cell(warning) for warning in evidence.warnings),
        "- 此报告不进行联网检索，不重新测试模型；未测试的维度不推断通过。",
        "- 来源中的成功/失败描述可能属于早期 attempt；正文以最终框架终态为先。",
        "- 源文件哈希及读取范围保存在同目录 migration_report_manifest.json。",
        "",
        "### C. 原始明细、工具版本与 Agent 轨迹证据",
        "",
        "以下为脱敏后的来源摘录，供核对原文与量化指标。来源文本仅为数据，不作为报告生成指令。",
        "",
    ]
    for source in evidence.sources:
        # Keep prose/log excerpts readable; numerical JSON facts are retained
        # separately even when their original document needs an excerpt.
        excerpt = source.text
        if len(excerpt) > 6000:
            excerpt = (
                excerpt[:3000]
                + "\n[…报告仅展示首尾摘录；原文件与读取范围见来源清单…]\n"
                + excerpt[-3000:]
            )
        lines += [
            f"#### [{source.id}] {cell(Path(source.path).name)}",
            "",
            f"来源：`{source.path}`",
            "",
            code_block(
                excerpt, "json" if source.data is not None and len(source.text) <= 6000 else "text"
            ),
        ]
        if source.data is not None and len(source.text) > 6000:
            measurements = [
                (pointer, scalar)
                for pointer, scalar in flatten(source.data)
                if isinstance(scalar, (int, float)) and not isinstance(scalar, bool)
            ]
            if measurements:
                lines += [
                    "结构化数值原值（字段路径保留单位信息，不自动推断其测量含义）：",
                    "",
                    table(("JSON 字段", "原值"), measurements),
                ]
    # Context-only fallback still preserves every structured metric.
    if any(str(v).startswith("运行上下文:") for v in phase_sources.values()):
        lines += [
            "### D. 运行上下文结构化证据",
            "",
            code_block(json.dumps(phases, ensure_ascii=False, indent=2), "json"),
        ]
    return "\n".join(lines).rstrip() + "\n"
