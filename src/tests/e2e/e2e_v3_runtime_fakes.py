from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Final, final

from harness.session.manager import MigrationSessionManager
from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.opencode_trace_client import OpenCodeTraceClient
from harness.session.opencode_trace_models import SessionGraphRetrieval
from harness.session.trace_export_models import TraceGraphClient
from harness.session.trace_seeds import TraceSeed
from harness.session.events import TransportObserver
from typing_extensions import override

_SESSION_ID: Final = "ses_runtime_01"


@dataclass(frozen=True, slots=True)
class HttpCall:
    method: str
    path: str
    query: dict[str, JsonValue] | None
    body: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class SessionScript:
    responses: tuple[str, ...] = ()
    trace_client: OpenCodeTraceClient | None = None
    trace_client_error: RuntimeError | None = None
    trace_seeds: tuple[TraceSeed, ...] = ()
    remain_running: bool = False
    cleanup_error: RuntimeError | None = None


@final
class ScriptedSessionManager(MigrationSessionManager):
    """Mutable route-ledger fake for the real session transport lifecycle."""

    def __init__(
        self,
        *,
        work_dir: str,
        base_url: str,
        transport_observer: TransportObserver | None,
        script: SessionScript,
    ) -> None:
        super().__init__(
            work_dir=work_dir,
            base_url=base_url,
            auto_detect_agent=False,
            todo_nudge_enabled=False,
            transport_observer=transport_observer,
        )
        self._script = script
        self._responses = list(script.responses)
        self._latest_message: dict[str, JsonValue] | None = None
        self.calls: list[HttpCall] = []

    @property
    @override
    def trace_client(self) -> OpenCodeTraceClient:
        if self._script.trace_client_error is not None:
            raise self._script.trace_client_error
        if self._script.trace_client is None:
            return super().trace_client
        return self._script.trace_client

    @property
    @override
    def trace_seeds(self) -> tuple[TraceSeed, ...]:
        if self._script.trace_seeds:
            return self._script.trace_seeds
        return super().trace_seeds

    @override
    def cleanup_all(self) -> int:
        if self._script.cleanup_error is not None:
            raise self._script.cleanup_error
        return super().cleanup_all()

    @override
    def _http(
        self,
        method: str,
        path: str,
        query: JsonObject | None = None,
        body: JsonObject | None = None,
        timeout: JsonValue = None,
    ) -> JsonObject:
        del timeout
        self.calls.append(HttpCall(method, path, query, body))
        if (method, path) == ("POST", "/session"):
            return {"ok": True, "data": {"id": _SESSION_ID}}
        if (method, path) == ("GET", f"/session/{_SESSION_ID}/message"):
            data: list[JsonValue] = []
            if self._latest_message is not None:
                data.append(self._latest_message)
            return {"ok": True, "data": data}
        if (method, path) == ("POST", f"/session/{_SESSION_ID}/message"):
            if not self._responses:
                raise AssertionError("unexpected review repost")
            response = self._responses.pop(0)
            self._latest_message = {
                "info": {"finish": "stop"},
                "parts": [{"type": "text", "text": response}],
            }
            return {"ok": True, "data": self._latest_message}
        if (method, path) == ("GET", "/session/status"):
            state = "running" if self._script.remain_running else "idle"
            return {"ok": True, "data": {_SESSION_ID: {"type": state}}}
        raise AssertionError(f"unexpected HTTP call: {method} {path}")

    @property
    def remaining_responses(self) -> tuple[str, ...]:
        return tuple(self._responses)

    @property
    def message_post_count(self) -> int:
        return sum(
            call.method == "POST" and call.path.endswith("/message")
            for call in self.calls
        )


@final
class ScriptedTraceHttp:
    """Mutable HTTP adapter that drives the concrete trace client from fixtures."""

    __slots__ = ("_client", "_current", "_remaining", "_retrieval")

    def __init__(
        self,
        client: TraceGraphClient,
        session_ids: tuple[str, ...],
    ) -> None:
        self._client = client
        self._current: str | None = None
        self._remaining = list(session_ids)
        self._retrieval: SessionGraphRetrieval | None = None

    def __call__(
        self,
        method: str,
        path: str,
        query: JsonObject | None = None,
    ) -> JsonObject:
        assert method == "GET"
        assert query is None
        if path == "/global/health":
            assert self._remaining
            self._current = self._remaining.pop(0)
            self._retrieval = self._client.retrieve_session_graph(self._current)
            return self._endpoint("health")
        if path == "/doc":
            return self._endpoint("doc")
        current = self._current
        assert current is not None
        encoded = urllib.parse.quote(current, safe="")
        if path == f"/session/{encoded}":
            return self._client.get_session_info(current).raw
        if path == f"/session/{encoded}/message":
            return self._endpoint("messages")
        if path == f"/session/{encoded}/children":
            return self._endpoint("children")
        raise AssertionError(f"unexpected trace HTTP call: {method} {path}")

    def _endpoint(self, name: str) -> JsonObject:
        retrieval = self._retrieval
        assert retrieval is not None
        raw = retrieval.contract.raw
        assert isinstance(raw, dict)
        endpoint = raw.get(name)
        assert isinstance(endpoint, dict)
        response: JsonObject = {
            "ok": True,
            "status": endpoint.get("status"),
            "data": endpoint.get("body"),
        }
        headers = endpoint.get("headers")
        if isinstance(headers, dict):
            response["headers"] = headers
        return response


def concrete_trace_client(
    client: TraceGraphClient,
    session_ids: tuple[str, ...],
) -> OpenCodeTraceClient:
    return OpenCodeTraceClient(ScriptedTraceHttp(client, session_ids))
