from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<REDACTED_API_KEY>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}"), "<REDACTED_GITHUB_TOKEN>"),
    (
        re.compile(
            r"(?i)([\"']?(?:HF_TOKEN|HUGGINGFACE_TOKEN|OPENAI_API_KEY|API_KEY|TOKEN|PASSWORD|PASSWD|SECRET)[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])"
        ),
        r"\1<REDACTED>\3",
    ),
    (
        re.compile(
            r"(?i)\b(HF_TOKEN|HUGGINGFACE_TOKEN|OPENAI_API_KEY|API_KEY|TOKEN|PASSWORD|PASSWD|SECRET)\s*([:=])\s*([^\s\'\"`,;]+)"
        ),
        r"\1\2<REDACTED>",
    ),
)


def _env_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(parsed, 0)


class AgentIOLogger:
    """Append-only sidecar logger for full Agent prompt/response payloads."""

    def __init__(
        self,
        output_dir: str | Path,
        run_id: str = "",
        *,
        enabled: bool = False,
        max_bytes: int = 0,
        redact: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.enabled = enabled
        self.max_bytes = max(max_bytes, 0)
        self.redact = redact
        self.base_dir = self.output_dir / "agent_io"
        self.payload_dir = self.base_dir / "payloads"
        self.jsonl_path = self.base_dir / "agent_io.jsonl"
        # Directory creation is deferred to record(), where observer isolates failures.

    @classmethod
    def from_env(cls, output_dir: str | Path, run_id: str = "") -> AgentIOLogger | None:
        if not _env_enabled(os.environ.get("SM_ADAPT_FULL_AGENT_IO")):
            return None
        return cls(
            output_dir=output_dir,
            run_id=run_id,
            enabled=True,
            max_bytes=_safe_int(os.environ.get("SM_ADAPT_FULL_AGENT_IO_MAX_BYTES")),
            redact=_env_enabled(os.environ.get("SM_ADAPT_FULL_AGENT_IO_REDACT", "1")),
        )

    def paths(self) -> dict[str, str]:
        return {
            "jsonl": str(self.jsonl_path),
            "payload_dir": str(self.payload_dir),
        }

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
        timeout_seconds: int | float | None,
        status: str,
        command: str,
        response: str,
        error: str | None,
    ) -> dict[str, str]:
        if not self.enabled:
            return {}

        self.payload_dir.mkdir(parents=True, exist_ok=True)
        sequence_name = f"{sequence:06d}"
        command_payload, command_truncated = self._prepare_payload(command)
        response_payload, response_truncated = self._prepare_payload(response)

        command_path = self.payload_dir / f"{sequence_name}_prompt.txt"
        response_path = self.payload_dir / f"{sequence_name}_response.txt"
        command_path.write_text(command_payload, encoding="utf-8")
        response_path.write_text(response_payload, encoding="utf-8")

        record = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "sequence": sequence,
            "phase_id": phase_id,
            "session_id": session_id,
            "role": role,
            "agent": agent,
            "lifecycle": lifecycle,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "timeout_seconds": timeout_seconds,
            "status": status,
            "error": error,
            "redacted": self.redact,
            "max_bytes": self.max_bytes,
            "command_length": len(command),
            "response_length": len(response),
            "command_stored_length": len(command_payload),
            "response_stored_length": len(response_payload),
            "command_truncated": command_truncated,
            "response_truncated": response_truncated,
            "command_sha256": self._sha256(command_payload),
            "response_sha256": self._sha256(response_payload),
            "command_path": self._relative_payload_path(command_path),
            "response_path": self._relative_payload_path(response_path),
            "command_preview": self._preview(command_payload),
            "response_preview": self._preview(response_payload),
        }

        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        return {
            "agent_io_jsonl": str(self.jsonl_path),
            "agent_io_command_path": str(command_path),
            "agent_io_response_path": str(response_path),
        }

    def _prepare_payload(self, text: str) -> tuple[str, bool]:
        payload = self._redact(text) if self.redact else text
        return self._truncate_utf8(payload)

    def _redact(self, text: str) -> str:
        redacted = text
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def _truncate_utf8(self, text: str) -> tuple[str, bool]:
        if self.max_bytes <= 0:
            return text, False
        encoded = text.encode("utf-8")
        if len(encoded) <= self.max_bytes:
            return text, False
        return encoded[: self.max_bytes].decode("utf-8", errors="ignore"), True

    def _relative_payload_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.output_dir).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _preview(text: str, limit: int = 500) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."
