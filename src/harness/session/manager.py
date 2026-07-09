from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("harness.session.manager")

RUNNING_TOKENS = {
    "running",
    "queued",
    "processing",
    "thinking",
    "in_progress",
    "active",
    "busy",
    "retry",
    "compacting",
}
COMPACTION_TOKENS = {"compaction", "summary"}
HARD_HTTP_STATUSES = {401, 403, 500, 502, 503, 504}
FALLBACK_AGENT_NAME = "sisyphus"
_DEFAULT_HTTP_TIMEOUT = object()
DEFAULT_SESSION_WAIT_TIMEOUT = 30000.0
DEFAULT_HARD_ERROR_WAIT_TIMEOUT = 300.0
# 停止生成但 TODO 非空时，二次确认前等待的时间（秒）
DEFAULT_TODO_STABILIZE_WAIT_S = 10.0
# 同一轮请求最多发送多少次 TODO 追问 nudge
DEFAULT_MAX_TODO_NUDGES = 2
# 是否默认启用 TODO 追问
DEFAULT_TODO_NUDGE_ENABLED = True


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class IdleOutcome(str, Enum):
    IDLE = "idle"                  # 非 running 且 TODO 非未完成态
    TODO_PENDING = "todo_pending"  # 非 running 但 TODO 明确未完成（仅 _todo == True）
    TIMEOUT = "timeout"            # 超时仍未收敛
    RUNNING = "running"            # 退出时仍 running


class SessionManagerError(RuntimeError):
    pass


class SessionTransportError(SessionManagerError):
    pass


class SessionAuthError(SessionManagerError):
    pass


class SessionServerError(SessionManagerError):
    pass


class SessionCompacted(SessionManagerError):
    pass


def extract_json_response(text: str) -> dict[str, Any]:
    if not text:
        return {}

    candidates = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    ]
    candidates.reverse()
    candidates.append(text.strip())

    for candidate in candidates:
        parsed = _parse_json_candidate(candidate)
        if parsed:
            return parsed
    return {}


def _parse_json_candidate(candidate: str) -> dict[str, Any]:
    candidate = candidate.strip()
    if not candidate:
        return {}

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    return _parse_last_json_object(candidate)


def _parse_last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    best: tuple[int, int, dict[str, Any]] | None = None

    for match in re.finditer(r"{", text):
        start = match.start()
        try:
            parsed, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict):
            continue

        absolute_end = start + end
        if best is None or absolute_end > best[1] or (absolute_end == best[1] and start < best[0]):
            best = (start, absolute_end, parsed)

    return best[2] if best is not None else {}


@dataclass
class SessionRecord:
    session_id: str
    role: str
    agent: str
    lifecycle: Literal["persistent", "reusable", "ephemeral"]
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    command_count: int = 0
    working_dir: str = ""


class MigrationSessionManager:
    def __init__(
        self,
        work_dir: str = ".",
        base_url: str = "http://127.0.0.1:4096",
        timeout: float = 30.0,
        password: str | None = None,
        username: str = "opencode",
        auto_detect_agent: bool = True,
        todo_nudge_enabled: bool = DEFAULT_TODO_NUDGE_ENABLED,
        todo_stabilize_wait_s: float = DEFAULT_TODO_STABILIZE_WAIT_S,
        max_todo_nudges: int = DEFAULT_MAX_TODO_NUDGES,
    ) -> None:
        self._work_dir = Path(work_dir).resolve()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._auth_header: str | None = None
        if password is not None:
            token = f"{username}:{password}".encode()
            self._auth_header = "Basic " + base64.b64encode(token).decode()
        self._sessions: dict[str, SessionRecord] = {}
        self._detected_agent: str | None = None
        self._cached_agent_list: list[str] | None = None
        self._todo_nudge_enabled = _env_flag(
            "SEAM_TODO_NUDGE_ENABLED", bool(todo_nudge_enabled)
        )
        self._todo_stabilize_wait_s = max(
            0.0, _env_float("SEAM_TODO_STABILIZE_WAIT_S", float(todo_stabilize_wait_s))
        )
        self._max_todo_nudges = max(
            0, _env_int("SEAM_MAX_TODO_NUDGES", int(max_todo_nudges))
        )
        self._last_todo_summary = ""
        if auto_detect_agent:
            self._detect_agent()

    @property
    def available_agents(self) -> list[str]:
        """Return the cached list of canonical agent names from the OpenCode server."""
        if self._cached_agent_list is None:
            self._cached_agent_list = self._fetch_agent_list()
        return self._cached_agent_list

    @property
    def active_agent(self) -> str:
        return self._detected_agent or FALLBACK_AGENT_NAME

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    def _detect_agent(self) -> None:
        agent_names = self.available_agents
        for name in agent_names:
            if name.lower() == "sisyphus":
                self._detected_agent = name
                return
        for name in agent_names:
            if "sisyphus" in name.lower():
                self._detected_agent = name
                return
        if agent_names:
            self._detected_agent = agent_names[0]

    def _fetch_agent_list(self) -> list[str]:
        """Query /agent and return canonical agent names.

        Returns an empty list when the endpoint is unreachable or returns
        unexpected data.  The result is cached via ``available_agents``.
        """
        resp = self._http("GET", "/agent")
        if not resp.get("ok") or not isinstance(resp.get("data"), list):
            return []
        names: list[str] = []
        for item in resp["data"]:
            if isinstance(item, dict):
                name = str(item.get("name", ""))
                if name:
                    names.append(name)
        return names

    def resolve_agent_name(self, name: str) -> str:
        """Resolve a partial or exact agent name to its canonical form.

        Resolution order:
        1. Exact match against cached available agents.
        2. Case-insensitive exact match.
        3. Partial (substring) match — returns the single canonical name
           when unambiguous.
        4. If multiple partial matches exist, prefers the one whose
           lowercased name *equals* the lowercased input.
        5. Raises ``ValueError`` when no match is found or when the
           name is ambiguous.

        Args:
            name: Short alias like ``"Atlas"`` or canonical name like
                  ``"Atlas - Plan Executor"``.

        Returns:
            The canonical agent name as reported by the server.

        Raises:
            ValueError: No agents available or no match found.
        """
        agents = self.available_agents
        if not agents:
            raise ValueError(
                "Cannot resolve agent name: no agents available from the server. "
                "Ensure the OpenCode server is running and /agent is reachable."
            )

        # 1. Exact match
        if name in agents:
            return name

        name_lower = name.lower()

        # 2. Case-insensitive exact match
        for agent in agents:
            if agent.lower() == name_lower:
                return agent

        # 3. Partial (substring) match
        matching = [a for a in agents if name_lower in a.lower()]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            exact_word = [a for a in matching if name_lower == a.lower()]
            if exact_word:
                return exact_word[0]
            raise ValueError(
                f"Ambiguous agent name '{name}' matches {len(matching)} agents: "
                f"{sorted(matching)}. Use a more specific name."
            )

        raise ValueError(
            f"Agent name '{name}' not found. Available agents: {sorted(agents)}"
        )

    def override_agent(self, name: str) -> str:
        """Validate and set *name* as the active agent after resolving aliases.

        This is the preferred way for external callers (CLI, E2E harness) to
        override the auto-detected agent.  It resolves partial names like
        ``"Atlas"`` to their canonical form and stores the result.

        Returns:
            The canonical agent name that was resolved and stored.

        Raises:
            ValueError: When *name* cannot be resolved (same as
                        :meth:`resolve_agent_name`).
        """
        canonical = self.resolve_agent_name(name)
        self._detected_agent = canonical
        return canonical

    def create_session(
        self,
        role: str,
        agent: str = "",
        lifecycle: Literal["persistent", "reusable", "ephemeral"] = "ephemeral",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        payload = {"title": title or f"migration-{role}"}
        resp = self._http("POST", "/session", body=payload)
        if not resp.get("ok") or not isinstance(resp.get("data"), dict):
            raise RuntimeError(
                f"Failed to create session: {resp.get('error') or resp.get('details')}"
            )

        session_id = str(resp["data"]["id"])
        record = SessionRecord(
            session_id=session_id,
            role=role,
            agent=agent or self.active_agent,
            lifecycle=lifecycle,
            working_dir=working_dir or str(self._work_dir),
        )
        self._sessions[session_id] = record
        if initial_prompt:
            self._send_message_raw(session_id, initial_prompt, agent=record.agent, timeout=120)
        return session_id

    def attach_session(
        self,
        session_id: str,
        role: str = "",
        lifecycle: Literal["persistent", "reusable", "ephemeral"] = "persistent",
    ) -> bool:
        resp = self._http("GET", f"/session/{session_id}")
        if not resp.get("ok"):
            return False
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionRecord(
                session_id=session_id,
                role=role,
                agent=self.active_agent,
                lifecycle=lifecycle,
                working_dir=str(self._work_dir),
            )
        return True

    def get_or_create(
        self,
        role: str,
        agent: str = "",
        lifecycle: Literal["persistent", "reusable", "ephemeral"] = "persistent",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        selected_agent = agent or self.active_agent
        for session_id, record in self._sessions.items():
            if (
                record.role == role
                and record.agent == selected_agent
                and record.lifecycle == lifecycle
            ):
                record.last_used_at = time.time()
                return session_id
        return self.create_session(
            role=role,
            agent=selected_agent,
            lifecycle=lifecycle,
            title=title,
            working_dir=working_dir,
            initial_prompt=initial_prompt,
        )

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int | float | None = None,
        retries: int = 2,
    ) -> str:
        record = self._sessions.get(session_id)
        selected_agent = agent or (record.agent if record else self.active_agent)
        last_error: Exception | None = None
        if record:
            record.last_used_at = time.time()
            record.command_count += 1

        for attempt in range(retries + 1):
            try:
                return self._send_message_raw(
                    session_id, command, agent=selected_agent, timeout=timeout
                )
            except (SessionAuthError, SessionServerError) as exc:
                last_error = exc
                self._wait_after_hard_error(session_id, timeout=timeout)
                break
            except TimeoutError as exc:
                last_error = exc
                break
            except (
                SessionTransportError,
                SessionCompacted,
                urllib.error.URLError,
                RuntimeError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(2 ** attempt)

        return json.dumps({"ok": False, "error": str(last_error or 'unknown session error')})

    @staticmethod
    def _effective_wait_timeout(timeout: int | float | None) -> float:
        if timeout is None:
            return DEFAULT_SESSION_WAIT_TIMEOUT
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value):
            raise ValueError("Session timeout must be finite")
        return max(1.0, timeout_value)

    def send_json_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int | float | None = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        text = self.send_command(session_id, command, agent=agent, timeout=timeout, retries=retries)
        parsed = extract_json_response(text)
        if parsed:
            return parsed
        return {"ok": False, "error": "malformed_json", "raw": text}

    def get_last_response(self, session_id: str) -> str:
        resp = self._http("GET", f"/session/{session_id}/message", query={"limit": 1})
        if not resp.get("ok"):
            return ""
        return self._extract_message_text(resp.get("data"))

    def _extract_error_text(self, payload: Any) -> str:
        if isinstance(payload, str) and payload:
            return payload
        if not isinstance(payload, dict):
            return ""

        message = payload.get("message")
        if isinstance(message, str) and message:
            return message

        data = payload.get("data")
        if isinstance(data, dict):
            data_message = data.get("message")
            if isinstance(data_message, str) and data_message:
                return data_message

            response_body = data.get("responseBody")
            if isinstance(response_body, str) and response_body:
                return response_body

        return json.dumps(payload, default=str)

    def _extract_message_text(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, list):
            collected: list[str] = []
            for item in payload:
                text = self._extract_message_text(item)
                if text:
                    collected.append(text)
            return "\n".join(collected).strip()
        if isinstance(payload, dict):
            for key in ("content", "text", "message", "response"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            error_text = self._extract_error_text(payload.get("error"))
            if error_text:
                return error_text

            info = payload.get("info")
            if isinstance(info, dict):
                error_text = self._extract_error_text(info.get("error"))
                if error_text:
                    return error_text

            parts = payload.get("parts")
            if isinstance(parts, list):
                collected: list[str] = []
                for part in parts:
                    if isinstance(part, dict):
                        if str(part.get("type", "")).lower() == "compaction":
                            continue
                        for key in ("text", "content", "message"):
                            nested = part.get(key)
                            if isinstance(nested, str) and nested.strip():
                                collected.append(nested.strip())
                                break
                        else:
                            nested = part.get("data") or part.get("response")
                            text = self._extract_message_text(nested)
                            if text:
                                collected.append(text)
                    else:
                        text = self._extract_message_text(part)
                        if text:
                            collected.append(text)
                if collected:
                    return "\n".join(collected).strip()

            for key in ("data", "body", "payload"):
                nested = payload.get(key)
                text = self._extract_message_text(nested)
                if text:
                    return text

        return ""

    def _extract_status_token(self, payload: Any, session_id: str) -> str:
        if not isinstance(payload, dict):
            return ""

        status = payload.get("status")
        if isinstance(status, dict):
            for key in ("token", "type", "state"):
                token = status.get(key)
                if isinstance(token, str) and token:
                    return token.lower()

        session_state = payload.get(session_id)
        if isinstance(session_state, dict):
            for key in ("token", "type", "status", "state"):
                token = session_state.get(key)
                if isinstance(token, str) and token:
                    return token.lower()

        data = payload.get("data")
        if isinstance(data, dict):
            token = self._extract_status_token(data, session_id)
            if token:
                return token
        if isinstance(data, list):
            for item in data:
                token = self._extract_status_token(item, session_id)
                if token:
                    return token

        return ""

    def _is_compaction_payload(self, data: Any) -> bool:
        if not isinstance(data, dict):
            return False

        info = data.get("info")
        if isinstance(info, dict):
            mode = str(info.get("mode", "")).lower()
            agent = str(info.get("agent", "")).lower()
            finish = str(info.get("finish", "")).lower()
            summary = info.get("summary")
            if mode in COMPACTION_TOKENS or agent in COMPACTION_TOKENS:
                return True
            if summary is True and (
                mode in COMPACTION_TOKENS
                or agent in COMPACTION_TOKENS
                or finish in COMPACTION_TOKENS
            ):
                return True
            if finish in COMPACTION_TOKENS:
                return True

        for part in data.get("parts", []):
            if isinstance(part, dict) and str(part.get("type", "")).lower() == "compaction":
                return True

        text = self._extract_message_text(data).lower()
        return bool(text and "compaction" in text and "summary" in text)

    def _todo_signal_from_payload(self, payload: Any) -> bool | None:
        if payload is None:
            return None
        if isinstance(payload, str):
            text = payload.lower()
            if re.search(r"(^|\n)\s*(?:[-*]\s*)?\[\s\]", text):
                return True
            if re.search(r"(^|\n)\s*(?:[-*]\s*)?(in_progress|pending|todo)\s*:", text):
                return True
            if "incomplete todo" in text or "unfinished todo" in text:
                return True
            if any(token in text for token in ("[x]", "done", "completed", "resolved", "closed")):
                return False
            return None
        if isinstance(payload, list):
            saw_completed = False
            for item in payload:
                signal = self._todo_signal_from_payload(item)
                if signal is True:
                    return True
                if signal is False:
                    saw_completed = True
            return False if saw_completed else None
        if isinstance(payload, dict):
            # 1. Explicit TODO containers take top priority. OpenCode stores the
            #    live todo list inside todowrite tool parts, e.g.
            #    parts[].state.input.todos, so a pending item there must win over
            #    an ancestor scalar status (the tool call's own state.status is
            #    "completed" even while its todos are still pending).
            explicit_keys = (
                "todos",
                "todo",
                "tasks",
                "task",
                "checklist",
                "items",
                "open_todos",
                "pending_todos",
            )
            found_explicit = False
            for key in explicit_keys:
                if key in payload:
                    found_explicit = True
                    container = payload.get(key)
                    # An empty todo container means nothing is pending.
                    if isinstance(container, (list, tuple)) and not container:
                        return False
                    signal = self._todo_signal_from_payload(container)
                    if signal is True:
                        return True
                    if signal is False:
                        return False

            # 2. Recurse into nested containers (tool state/input/metadata/parts).
            found_nested_false = False
            for key in (
                "data",
                "message",
                "response",
                "info",
                "parts",
                "body",
                "payload",
                "state",
                "input",
                "metadata",
                "arguments",
                "args",
            ):
                if key in payload:
                    signal = self._todo_signal_from_payload(payload.get(key))
                    if signal is True:
                        return True
                    if signal is False:
                        found_nested_false = True

            # 3. Scalar status for a single todo-item dict (no nested container).
            for key in ("status", "state", "type", "mode"):
                value = payload.get(key)
                if isinstance(value, str):
                    token = value.lower()
                    if token in {
                        "open",
                        "pending",
                        "todo",
                        "incomplete",
                        "in_progress",
                        "in progress",
                        "running",
                        "active",
                        "busy",
                    }:
                        return True
                    if token in {
                        "done",
                        "complete",
                        "completed",
                        "closed",
                        "resolved",
                        "success",
                        "idle",
                        "stop",
                    }:
                        return False
            for key in ("done", "completed", "closed", "resolved"):
                value = payload.get(key)
                if value is False:
                    return True
                if value is True:
                    return False

            # 4. A nested todo container resolved to "all complete".
            if found_nested_false:
                return False

            text = self._extract_message_text(payload).lower()
            if self._todo_signal_from_payload(text) is True:
                return True
        return None

    def _candidate_sqlite_paths(self) -> list[Path]:
        candidates: list[Path] = []
        env_db = os.environ.get("OPENCODE_DB")
        if env_db:
            candidates.append(Path(env_db).expanduser())
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            candidates.append(Path(xdg_data).expanduser() / "opencode" / "opencode.db")
        candidates.append(Path.home() / ".local" / "share" / "opencode" / "opencode.db")

        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    def _sqlite_table_names(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(row[0]) for row in rows if row and row[0]}

    @staticmethod
    def _normalize_sql_name(name: str) -> str:
        return name.replace("_", "").lower()

    def _resolve_sql_column(self, columns: list[str], candidates: set[str]) -> str | None:
        normalized_candidates = {self._normalize_sql_name(candidate) for candidate in candidates}
        for column in columns:
            if self._normalize_sql_name(str(column)) in normalized_candidates:
                return str(column)
        return None

    @staticmethod
    def _quote_sql_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def _sqlite_row_state(self, row: sqlite3.Row) -> bool | None:
        mapping = {key: row[key] for key in row.keys()}
        signal = self._todo_signal_from_payload(mapping)
        if signal is not None:
            return signal
        lower_mapping = {str(key).lower(): value for key, value in mapping.items()}
        for key in ("status", "state", "type"):
            value = lower_mapping.get(key)
            if isinstance(value, str):
                token = value.lower()
                if token in RUNNING_TOKENS:
                    return True
                if token in {
                    "done",
                    "complete",
                    "completed",
                    "closed",
                    "resolved",
                    "success",
                    "idle",
                    "stop",
                }:
                    return False
        return None

    def _sqlite_assistant_completion_evidence(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        session_id: str,
    ) -> bool | None:
        message_tables = [name for name in ("message", "messages") if name in tables]
        for table_name in message_tables:
            columns = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            ]
            if not columns:
                continue
            session_column = self._resolve_sql_column(
                columns,
                {"sessionID", "sessionId", "session_id", "sessionid", "session"},
            )
            data_column = self._resolve_sql_column(
                columns, {"data", "payload", "body", "message"}
            )
            if not session_column or not data_column:
                continue

            role_column = self._resolve_sql_column(columns, {"role"})
            order_column = self._resolve_sql_column(
                columns,
                {
                    "time_completed",
                    "timeCompleted",
                    "time_created",
                    "timeCreated",
                    "created_at",
                    "updated_at",
                    "id",
                },
            )
            query = (
                f"SELECT * FROM {self._quote_sql_identifier(table_name)} "
                f"WHERE {self._quote_sql_identifier(session_column)} = ?"
            )
            if role_column:
                query += f" AND lower({self._quote_sql_identifier(role_column)}) = 'assistant'"
            if order_column:
                query += f" ORDER BY {self._quote_sql_identifier(order_column)} DESC"
            query += " LIMIT 1"

            row = conn.execute(query, (session_id,)).fetchone()
            if row is not None:
                return self._sqlite_message_completion_state(row, data_column)
        return None

    def _sqlite_message_completion_state(self, row: sqlite3.Row, data_column: str) -> bool | None:
        payload = self._sqlite_json_value(row[data_column])
        if not isinstance(payload, dict):
            return None
        if self._is_compaction_payload(payload):
            return True

        role = str(payload.get("role", row["role"] if "role" in row.keys() else "")).lower()
        if role and role != "assistant":
            return None

        finish = str(payload.get("finish", "")).lower()
        if finish not in {"stop", "success"}:
            info = payload.get("info")
            if isinstance(info, dict):
                finish = str(info.get("finish", "")).lower()
        if finish not in {"stop", "success"}:
            return None

        if self._sqlite_payload_has_completed_time(payload):
            return False
        return None

    def _sqlite_json_value(self, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode(errors="replace")
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _sqlite_payload_has_completed_time(self, payload: dict[str, Any]) -> bool:
        time_value = payload.get("time")
        if isinstance(time_value, dict) and time_value.get("completed") not in (None, "", 0):
            return True
        for key in ("time_completed", "timeCompleted", "completed_at", "completedAt"):
            if payload.get(key) not in (None, "", 0):
                return True
        info = payload.get("info")
        if isinstance(info, dict):
            info_time = info.get("time")
            if isinstance(info_time, dict) and info_time.get("completed") not in (None, "", 0):
                return True
            for key in ("time_completed", "timeCompleted", "completed_at", "completedAt"):
                if info.get(key) not in (None, "", 0):
                    return True
        return False

    def _session_completion_from_sqlite(self, session_id: str) -> bool | None:
        for db_path in self._candidate_sqlite_paths():
            if not db_path.is_file():
                continue
            try:
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA query_only=ON")
                    tables = self._sqlite_table_names(conn)
                    if not tables:
                        continue

                    session_row_seen = False
                    session_not_running = False
                    session_tables = [name for name in ("session", "sessions") if name in tables]
                    for table_name in session_tables:
                        columns = [
                            row[1]
                            for row in conn.execute(
                                f"PRAGMA table_info({table_name})"
                            ).fetchall()
                        ]
                        if not columns:
                            continue
                        id_column = self._resolve_sql_column(
                            columns,
                            {
                                "id",
                                "sessionID",
                                "sessionId",
                                "session_id",
                                "sessionid",
                                "session",
                            },
                        )
                        if not id_column:
                            continue
                        quoted_table = self._quote_sql_identifier(table_name)
                        quoted_id_column = self._quote_sql_identifier(id_column)
                        row = conn.execute(
                            f"SELECT * FROM {quoted_table} WHERE {quoted_id_column} = ? LIMIT 1",
                            (session_id,),
                        ).fetchone()
                        if row is None:
                            continue
                        session_row_seen = True
                        time_compacting_column = self._resolve_sql_column(
                            columns, {"time_compacting", "timeCompacting"}
                        )
                        if time_compacting_column and row[
                            time_compacting_column
                        ] not in (None, "", 0):
                            return True
                        state = self._sqlite_row_state(row)
                        if state is True:
                            return True
                        if state is False:
                            session_not_running = True

                    saw_todo_rows = False
                    saw_completed_todo = False
                    for table_name in sorted(
                        name
                        for name in tables
                        if any(
                            token in name.lower()
                            for token in ("todo", "task", "checklist")
                        )
                    ):
                        columns = [
                            row[1]
                            for row in conn.execute(
                                f"PRAGMA table_info({table_name})"
                            ).fetchall()
                        ]
                        if not columns:
                            continue
                        session_column = self._resolve_sql_column(
                            columns,
                            {
                                "sessionID",
                                "sessionId",
                                "session_id",
                                "sessionid",
                                "session",
                            },
                        )
                        if not session_column:
                            continue
                        query = (
                            f"SELECT * FROM {self._quote_sql_identifier(table_name)} "
                            f"WHERE {self._quote_sql_identifier(session_column)} = ?"
                        )
                        rows = conn.execute(query, (session_id,)).fetchall()
                        if not rows:
                            continue
                        saw_todo_rows = True
                        for row in rows:
                            state = self._sqlite_row_state(row)
                            if state is True:
                                return True
                            if state is False:
                                saw_completed_todo = True
                    if saw_todo_rows:
                        return False if saw_completed_todo else None

                    assistant_state = self._sqlite_assistant_completion_evidence(
                        conn, tables, session_id
                    )
                    if assistant_state is True:
                        return True
                    if assistant_state is False and (session_row_seen or session_not_running):
                        return False
            except sqlite3.Error:
                continue
        return None

    def _session_has_incomplete_todos(self, session_id: str) -> bool | None:
        resp = self._http("GET", f"/session/{session_id}/message", query={"limit": 20})
        if not resp.get("ok"):
            status = resp.get("status")
            if status in {401, 403}:
                raise SessionAuthError(
                    f"GET /session/{session_id}/message unauthorized: "
                    f"{resp.get('details') or resp.get('error') or status}"
                )
            if isinstance(status, int) and status in HARD_HTTP_STATUSES:
                raise SessionServerError(
                    f"GET /session/{session_id}/message failed: "
                    f"{resp.get('details') or resp.get('error') or status}"
                )
            return self._session_completion_from_sqlite(session_id)

        data = resp.get("data")
        # Prefer the latest todowrite snapshot: a newer cleared/completed list
        # must override older pending lists still inside the 20-message window.
        latest = self._latest_todo_state_from_messages(data)
        if latest is not None:
            return latest

        signal = self._todo_signal_from_payload(data)
        if signal is not None:
            return signal
        return self._session_completion_from_sqlite(session_id)

    def _latest_todo_state_from_messages(self, data: Any) -> bool | None:
        """Return the incomplete-todo signal of the most recent todowrite tool
        call found in a /session/{id}/message list.

        OpenCode keeps each todowrite snapshot in ``parts[].state.input.todos``.
        Older snapshots stay within the limited window, so only the newest one
        reflects the current todo list. An empty list means the list was
        cleared (-> complete, ``False``).
        """
        if not isinstance(data, list):
            return None
        latest_todos: list[Any] | None = None
        for message in data:
            if not isinstance(message, dict):
                continue
            parts = message.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if str(part.get("tool", "")).lower() not in {"todowrite", "todoread"}:
                    continue
                state = part.get("state")
                if not isinstance(state, dict):
                    continue
                todos = None
                for loc in ("input", "metadata"):
                    container = state.get(loc)
                    if isinstance(container, dict) and isinstance(
                        container.get("todos"), list
                    ):
                        todos = container["todos"]
                        break
                if todos is not None:
                    latest_todos = todos
        if latest_todos is None:
            return None
        if not latest_todos:
            self._last_todo_summary = "latest todowrite: cleared (empty list)"
            return False  # cleared list -> nothing pending
        signal = self._todo_signal_from_payload(latest_todos)
        self._last_todo_summary = (
            f"latest todowrite: {self._summarize_todos(latest_todos)}"
        )
        # A populated list whose items carry no explicit state is treated as
        # pending (todowrite items default to actionable work).
        return True if signal is None else signal

    @staticmethod
    def _summarize_todos(todos: list[Any]) -> str:
        """Compact status histogram for logging, e.g. '3 items: completed=2,pending=1'."""
        counts: dict[str, int] = {}
        for item in todos:
            status = "unknown"
            if isinstance(item, dict):
                raw = item.get("status") or item.get("state")
                if isinstance(raw, str) and raw.strip():
                    status = raw.strip().lower()
            counts[status] = counts.get(status, 0) + 1
        breakdown = ",".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return f"{len(todos)} items: {breakdown}"

    def wait_for_idle(
        self,
        session_id: str,
        timeout_s: int | float | None = 300,
        interval_s: float = 2.0,
    ) -> bool:
        outcome = self._await_idle_state(
            session_id,
            timeout_s=self._effective_wait_timeout(timeout_s),
            interval_s=interval_s,
            return_on_todo_pending=False,
        )
        return outcome == IdleOutcome.IDLE

    def _await_idle_state(
        self,
        session_id: str,
        timeout_s: float,
        interval_s: float,
        return_on_todo_pending: bool,
    ) -> IdleOutcome:
        started = time.time()
        effective_timeout = self._effective_wait_timeout(timeout_s)
        while time.time() - started < effective_timeout:
            status = self._http("GET", "/session/status")
            if not status.get("ok"):
                error_status = status.get("status")
                if error_status in {401, 403}:
                    raise SessionAuthError(
                        f"GET /session/status unauthorized: "
                        f"{status.get('details') or status.get('error') or error_status}"
                    )
                if isinstance(error_status, int) and error_status in HARD_HTTP_STATUSES:
                    raise SessionServerError(
                        f"GET /session/status failed: "
                        f"{status.get('details') or status.get('error') or error_status}"
                    )
                return IdleOutcome.TIMEOUT

            data = status.get("data")
            token = self._extract_status_token(data, session_id)
            if token in RUNNING_TOKENS:
                time.sleep(interval_s)
                continue

            todo_state = self._session_has_incomplete_todos(session_id)
            if todo_state is True:
                if return_on_todo_pending:
                    logger.info(
                        "[TODO] session=%s stopped with incomplete todos (%s)",
                        session_id,
                        self._last_todo_summary or "todo signal pending",
                    )
                    return IdleOutcome.TODO_PENDING
                time.sleep(interval_s)
                continue

            if token or todo_state is False:
                return IdleOutcome.IDLE

            sqlite_state = self._session_completion_from_sqlite(session_id)
            if sqlite_state is True:
                time.sleep(interval_s)
                continue
            if sqlite_state is False:
                return IdleOutcome.IDLE
            # No running signal found: status OK, no token, no todos, no sqlite → idle.
            return IdleOutcome.IDLE
        return IdleOutcome.TIMEOUT

    def _wait_after_hard_error(
        self,
        session_id: str,
        timeout: int | float | None,
        interval_s: float = 1.0,
    ) -> None:
        started = time.time()
        hard_error_timeout = DEFAULT_HARD_ERROR_WAIT_TIMEOUT if timeout is None else min(
            self._effective_wait_timeout(timeout),
            DEFAULT_HARD_ERROR_WAIT_TIMEOUT,
        )
        deadline = started + hard_error_timeout
        saw_observation = False
        last_message_text = ""
        stable_message_count = 0

        while time.time() < deadline:
            status = self._http("GET", "/session/status")
            if status.get("ok"):
                saw_observation = True
                token = self._extract_status_token(status.get("data"), session_id)
                if token in RUNNING_TOKENS:
                    time.sleep(interval_s)
                    continue

                todo_state = self._session_has_incomplete_todos_tolerant(session_id)
                if todo_state is True:
                    time.sleep(interval_s)
                    continue
                if token or todo_state is False:
                    return
            else:
                sqlite_state = self._session_completion_from_sqlite(session_id)
                if sqlite_state is False:
                    return
                if sqlite_state is True:
                    saw_observation = True
                    time.sleep(interval_s)
                    continue

            message_text = self._last_message_text_tolerant(session_id)
            if message_text:
                saw_observation = True
                if message_text == last_message_text:
                    stable_message_count += 1
                else:
                    last_message_text = message_text
                    stable_message_count = 0
                if stable_message_count >= 2:
                    return

            sqlite_state = self._session_completion_from_sqlite(session_id)
            if sqlite_state is False:
                return
            if sqlite_state is True:
                saw_observation = True

            if not saw_observation:
                return
            time.sleep(interval_s)

    def _last_message_text_tolerant(self, session_id: str) -> str:
        resp = self._http("GET", f"/session/{session_id}/message", query={"limit": 1})
        if not resp.get("ok"):
            return ""
        return self._extract_message_text(resp.get("data"))

    def _is_usable_refetched_text(
        self,
        candidate: str,
        previous_text: str,
        command_text: str,
    ) -> bool:
        stripped = candidate.strip()
        if not stripped:
            return False
        if stripped == previous_text.strip():
            return False
        if stripped == command_text.strip():
            return False
        return True

    def _refetch_final_text(
        self,
        session_id: str,
        fallback_text: str,
        previous_text: str,
        command_text: str,
    ) -> str:
        latest = self._last_message_text_tolerant(session_id)
        if self._is_usable_refetched_text(latest, previous_text, command_text):
            wrapped = {"parts": [{"type": "text", "text": latest}]}
            if not self._is_compaction_payload(wrapped):
                if latest.strip() != fallback_text.strip():
                    logger.info(
                        "[REFETCH] session=%s replaced=True len=%d",
                        session_id,
                        len(latest),
                    )
                else:
                    logger.debug(
                        "[REFETCH] session=%s replaced=False (post text unchanged)",
                        session_id,
                    )
                return latest
        logger.debug(
            "[REFETCH] session=%s kept post text (refetch unusable)", session_id
        )
        return fallback_text

    def _session_has_incomplete_todos_tolerant(self, session_id: str) -> bool | None:
        try:
            return self._session_has_incomplete_todos(session_id)
        except (SessionAuthError, SessionServerError):
            return self._session_completion_from_sqlite(session_id)

    def abort_session(self, session_id: str) -> bool:
        return bool(self._http("POST", f"/session/{session_id}/abort").get("ok"))

    def cleanup_session(self, session_id: str) -> bool:
        record = self._sessions.get(session_id)
        if not record:
            return False
        if record.lifecycle == "ephemeral":
            self.abort_session(session_id)
            self._http("DELETE", f"/session/{session_id}")
        self._sessions.pop(session_id, None)
        return True

    def cleanup_all(self) -> int:
        doomed = [
            sid
            for sid, rec in self._sessions.items()
            if rec.lifecycle in {"ephemeral", "reusable"}
        ]
        for session_id in doomed:
            self.cleanup_session(session_id)
        return len(doomed)

    def list_sessions(self) -> list[SessionRecord]:
        return list(self._sessions.values())

    def _classify_post_failure(self, resp: dict[str, Any], session_id: str) -> None:
        """Raise the appropriate session error for a failed POST /message response."""
        status = resp.get("status")
        detail = resp.get("details") or resp.get("error") or "request failed"
        if status in {401, 403}:
            raise SessionAuthError(f"POST /session/{session_id}/message unauthorized: {detail}")
        if isinstance(status, int) and status >= 500:
            raise SessionServerError(f"POST /session/{session_id}/message failed: {detail}")
        raise SessionTransportError(f"POST /session/{session_id}/message failed: {detail}")

    def _send_message_raw(
        self,
        session_id: str,
        text: str,
        agent: str = "",
        timeout: int | float | None = None,
    ) -> str:
        command_text = text
        payload: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if agent:
            payload["agent"] = agent
        http_timeout = self._effective_wait_timeout(timeout) + 30
        previous_text = self._last_message_text_tolerant(session_id)
        resp = self._http(
            "POST", f"/session/{session_id}/message", body=payload, timeout=http_timeout
        )
        if not resp.get("ok"):
            self._classify_post_failure(resp, session_id)
        data = resp.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("Unexpected session response payload")

        info = data.get("info") or {}
        if isinstance(info, dict) and info.get("error"):
            raise RuntimeError(self._extract_error_text(info.get("error")))

        if self._is_compaction_payload(data):
            raise SessionCompacted("Compaction response is incomplete")

        finish = str(info.get("finish", "")).lower() if isinstance(info, dict) else ""
        if finish and finish not in {"stop", "success"}:
            raise RuntimeError(f"Agent finished unexpectedly: {finish}")

        text = self._extract_message_text(data)
        if not text:
            text = self._recover_empty_response_text(
                session_id, timeout, previous_text, command_text=command_text
            )
            if not text:
                raise RuntimeError("Empty session response")
            return text

        return self._await_and_finalize(
            session_id=session_id,
            post_text=text,
            previous_text=previous_text,
            command_text=command_text,
            agent=agent,
            timeout=timeout,
        )

    def _post_message_only(
        self,
        session_id: str,
        text: str,
        agent: str,
        timeout: int | float | None,
    ) -> str:
        """Send a single POST /message and return its text without entering the
        idle/nudge convergence loop. Used by TODO nudges to avoid recursion."""
        payload: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if agent:
            payload["agent"] = agent
        http_timeout = self._effective_wait_timeout(timeout) + 30
        resp = self._http(
            "POST", f"/session/{session_id}/message", body=payload, timeout=http_timeout
        )
        if not resp.get("ok"):
            self._classify_post_failure(resp, session_id)
        data = resp.get("data") or {}
        if isinstance(data, dict):
            info = data.get("info") or {}
            if isinstance(info, dict) and info.get("error"):
                raise RuntimeError(self._extract_error_text(info.get("error")))
            if self._is_compaction_payload(data):
                raise SessionCompacted("Compaction response is incomplete")
        return self._extract_message_text(data)

    def _build_todo_nudge_prompt(self) -> str:
        return (
            "System check: Your previous turn stopped, but the session still has an "
            "open TODO list. Do not start any new work beyond the original request.\n\n"
            "Please do the following now:\n"
            "1. Determine whether you have fully completed everything the ORIGINAL "
            "prompt required (judge by the original prompt, not by the TODO list).\n"
            "2. If the TODO list contains any item that was NOT required by the "
            "original prompt, remove it immediately and do not act on it.\n"
            "3. If the original task is already complete: clear the TODO list and "
            "return the final result strictly in the format the original prompt "
            "requested.\n"
            "4. If the original task is not complete: finish only the remaining work "
            "the original prompt requires, then clear the TODO list and return the "
            "result in the requested format.\n\n"
            "Return only the result required by the original prompt. Do not add extra "
            "tasks, extra files, or extra explanations."
        )

    def _send_todo_nudge(
        self,
        session_id: str,
        agent: str,
        timeout: int | float | None,
    ) -> str:
        nudge = self._build_todo_nudge_prompt()
        logger.info("[TODO NUDGE] session=%s", session_id)
        return self._post_message_only(session_id, nudge, agent=agent, timeout=timeout)

    def _await_and_finalize(
        self,
        session_id: str,
        post_text: str,
        previous_text: str,
        command_text: str,
        agent: str,
        timeout: int | float | None,
    ) -> str:
        effective_timeout = self._effective_wait_timeout(timeout)
        deadline = time.time() + effective_timeout
        nudge_count = 0
        current_post_text = post_text
        current_previous = previous_text

        while True:
            remaining = max(1.0, deadline - time.time())
            outcome = self._await_idle_state(
                session_id,
                timeout_s=remaining,
                interval_s=1.0,
                return_on_todo_pending=self._todo_nudge_enabled,
            )

            if outcome == IdleOutcome.IDLE:
                if self._last_todo_summary:
                    logger.info(
                        "[TODO] session=%s idle, todos complete (%s)",
                        session_id,
                        self._last_todo_summary,
                    )
                return self._refetch_final_text(
                    session_id, current_post_text, current_previous, command_text
                )
            if outcome in (IdleOutcome.TIMEOUT, IdleOutcome.RUNNING):
                # nudge 关闭时，TODO 非空会一路 spin 到 TIMEOUT，行为与旧逻辑一致
                raise TimeoutError("Session still running or has incomplete todos")

            # outcome == TODO_PENDING（仅在 nudge 启用时可能出现）
            if nudge_count >= self._max_todo_nudges:
                logger.warning(
                    "[TODO NUDGE] session=%s giving up after %d nudge(s); "
                    "todos still pending (%s)",
                    session_id,
                    nudge_count,
                    self._last_todo_summary or "unknown",
                )
                raise TimeoutError(
                    "Session stopped with incomplete todos after nudges"
                )

            # 等待稳定窗后二次确认；窗内回到 running 由下方分支处理。
            # 此处尚未决定发送 nudge：模型可能仍在生成（例如 todo 处于
            # in_progress），稳定窗用于消化 /session/status 的时序竞态，
            # 因此日志使用中性的 [TODO] 措辞，避免误以为已发送 nudge。
            if self._todo_stabilize_wait_s > 0:
                logger.info(
                    "[TODO] session=%s incomplete todos, re-checking after %.1fs "
                    "stabilize window before deciding on a nudge",
                    session_id,
                    self._todo_stabilize_wait_s,
                )
                time.sleep(self._todo_stabilize_wait_s)
            recheck = self._await_idle_state(
                session_id,
                timeout_s=max(1.0, deadline - time.time()),
                interval_s=1.0,
                return_on_todo_pending=True,
            )
            logger.info(
                "[TODO] session=%s recheck after stabilize -> %s",
                session_id,
                recheck.value,
            )
            if recheck == IdleOutcome.IDLE:
                return self._refetch_final_text(
                    session_id, current_post_text, current_previous, command_text
                )
            if recheck != IdleOutcome.TODO_PENDING:
                # running / timeout：回主循环按剩余 deadline 继续等待
                continue

            # 仍是“停止生成 + TODO 非空”：刷新 previous 基线后发送 nudge
            refreshed = self._last_message_text_tolerant(session_id)
            if refreshed:
                current_previous = refreshed
            nudge_count += 1
            logger.info(
                "[TODO NUDGE] session=%s sending nudge #%d/%d",
                session_id,
                nudge_count,
                self._max_todo_nudges,
            )
            nudge_text = self._send_todo_nudge(
                session_id, agent=agent, timeout=timeout
            )
            if nudge_text:
                current_post_text = nudge_text

    def _recover_empty_response_text(
        self,
        session_id: str,
        timeout: int | float | None,
        previous_text: str,
        command_text: str,
    ) -> str:
        if not self.wait_for_idle(
            session_id, timeout_s=self._effective_wait_timeout(timeout), interval_s=1.0
        ):
            raise TimeoutError("Session still running or has incomplete todos")
        recovered_text = self._last_message_text_tolerant(session_id)
        if not self._is_usable_refetched_text(recovered_text, previous_text, command_text):
            return ""
        return recovered_text

    def _http(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: Any = _DEFAULT_HTTP_TIMEOUT,
    ) -> dict[str, Any]:
        url = self._base_url + (path if path.startswith("/") else f"/{path}")
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        if self._auth_header:
            headers["Authorization"] = self._auth_header

        request = urllib.request.Request(
            url=url, headers=headers, data=payload, method=method.upper()
        )
        try:
            if timeout is _DEFAULT_HTTP_TIMEOUT:
                request_timeout: float | None = self._timeout
            elif isinstance(timeout, (int, float)) or timeout is None:
                request_timeout = timeout
            else:
                request_timeout = self._timeout
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw = response.read()
                if response.status == 204 or not raw:
                    return {"ok": True, "status": response.status, "data": None}
                text = raw.decode()
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = text
                return {"ok": True, "status": response.status, "data": parsed}
        except urllib.error.HTTPError as exc:
            details = exc.read().decode(errors="replace") if exc.fp else ""
            return {"ok": False, "status": exc.code, "error": str(exc), "details": details}
        except Exception as exc:  # pragma: no cover - network failure path
            logger.debug("HTTP error for %s %s: %s", method, path, exc)
            return {"ok": False, "error": str(exc)}


SessionManager = MigrationSessionManager
