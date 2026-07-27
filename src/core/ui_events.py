from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<REDACTED_API_KEY>"),
    (
        re.compile(
            r"(?i)\b(HF_TOKEN|HUGGINGFACE_TOKEN|OPENAI_API_KEY|API_KEY|TOKEN|"
            r"PASSWORD|PASSWD|SECRET)\s*([:=])\s*([^\s'\"`,;]+)"
        ),
        r"\1\2<REDACTED>",
    ),
)


@dataclass(frozen=True)
class PhaseDisplay:
    title: str
    description: str


PHASE_DISPLAY: dict[str, PhaseDisplay] = {
    "phase_0_env_detect": PhaseDisplay(
        "环境检测",
        "检测目标机器、GPU/驱动、Python、容器和平台 SDK 是否可用于迁移。",
    ),
    "phase_1_project_analysis": PhaseDisplay(
        "项目分析",
        "分析项目结构、依赖、CUDA 使用点、入口脚本和自定义算子风险。",
    ),
    "phase_1_5_constraint_summary": PhaseDisplay(
        "用户约束",
        "整理用户提供的迁移要求，例如必须运行的测试、指定镜像、禁止修改项。",
    ),
    "phase_2_venv_create": PhaseDisplay(
        "依赖准备",
        "准备迁移后的 Python/容器依赖，使项目具备可运行基础。",
    ),
    "phase_3_entry_script": PhaseDisplay(
        "入口命令",
        "生成迁移后的验证入口命令，这是后续反复运行和修复的依据。",
    ),
    "phase_35_static_validate": PhaseDisplay(
        "入口静态检查",
        "检查入口命令是否真的能验证迁移质量，而不是只做 smoke/report-only。",
    ),
    "phase_4_rule_migration": PhaseDisplay(
        "规则迁移",
        "执行确定性平台规则迁移，例如 CUDA API、设备字符串、框架调用适配。",
    ),
    "phase_5_validation": PhaseDisplay(
        "运行验证与自动修复",
        "运行迁移后的项目，根据真实报错自动分析、路由、修复和重试。",
    ),
    "phase_6_report": PhaseDisplay(
        "报告与使用说明",
        "生成迁移报告，并告诉用户如何使用迁移后的项目。",
    ),
    "phase_7a_evaluate": PhaseDisplay(
        "经验学习",
        "评估本次迁移中哪些经验可复用。",
    ),
    "phase_7b_refine": PhaseDisplay(
        "经验学习",
        "把本次迁移中可复用的经验沉淀下来，供后续项目使用。",
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_text(text: object, limit: int = 160) -> str:
    raw = "" if text is None else str(text)
    raw = " ".join(raw.split())
    for pattern, replacement in _SECRET_PATTERNS:
        raw = pattern.sub(replacement, raw)
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


def dashboard_enabled(
    mode: str,
    *,
    is_tty: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    normalized = str(mode or "auto").strip().lower()
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    env = environ if environ is not None else os.environ
    ci_value = str(env.get("CI", "")).strip().lower()
    return bool(is_tty and ci_value not in {"1", "true", "yes", "on"})


class UIEventSink:
    """Best-effort append-only writer for real-time UI events."""

    def __init__(
        self,
        output_dir: str | Path,
        run_id: str,
        *,
        filename: str = "ui_events.jsonl",
        create_dir: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.path = self.output_dir / filename
        self.enabled = True
        if create_dir:
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False

    def emit(
        self,
        event_type: str,
        *,
        phase_id: str | None = None,
        subphase_id: str | None = None,
        agent_role: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        artifact_path: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        record = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": _utc_now(),
            "run_id": self.run_id,
            "event_type": event_type,
            "phase_id": phase_id,
            "subphase_id": subphase_id,
            "agent_role": agent_role,
            "session_id": session_id,
            "status": status,
            "message": summarize_text(message, 500) if message else "",
            "details": dict(details or {}),
            "artifact_path": artifact_path,
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            self.enabled = False

    def as_env(self) -> dict[str, str]:
        return {"SEAM_UI_EVENTS_PATH": str(self.path), "SEAM_RUN_ID": self.run_id}
