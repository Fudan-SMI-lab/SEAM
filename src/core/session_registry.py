from __future__ import annotations

from typing import Protocol, final, runtime_checkable

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.trace_seeds import SessionLifecycle


class SessionPool(Protocol):
    def get_or_create(
        self,
        role: str,
        *,
        lifecycle: SessionLifecycle = "persistent",
    ) -> str: ...

    def cleanup_all(self) -> int: ...


@runtime_checkable
class TraceSeedAnnotator(Protocol):
    def annotate_trace_seed(
        self,
        session_id: str,
        logical_role: str | None,
        scope: str | None,
    ) -> None: ...


class SessionObserver(Protocol):
    def record_event(self, event_type: str, **details: JsonValue) -> None: ...


@final
class SessionRegistry:
    """Pool manager for agent sessions. Reuses persistent sessions."""

    def __init__(
        self,
        agents_config: dict[str, JsonObject],
        session_mgr: SessionPool,
        observer: SessionObserver | None = None,
    ) -> None:
        """
        agents_config: {"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}}
        session_mgr: MigrationSessionManager instance (has get_or_create method)
        observer: Optional TelemetryObserver to record session creation events
        """
        self._agents = agents_config
        self._session_mgr = session_mgr
        self._cache: dict[str, str] = {}  # agent_id -> session_id
        self._observer = observer

    def resolve(self, agent_id: str) -> str:
        """Return cached session_id or create new one.

        First call: session_mgr.get_or_create(role=agent_id, lifecycle=...)
        Subsequent calls: return cached session_id
        """
        agent_config = self._agents[agent_id]
        lifecycle = self._lifecycle(agent_config.get("lifecycle"))

        if lifecycle in ("auto", "ephemeral"):
            sid = self._session_mgr.get_or_create(role=agent_id, lifecycle=lifecycle)
            self._annotate_trace_seed(agent_id, agent_config, sid)
            self._record_session(agent_id, sid, lifecycle)
            return sid

        if agent_id in self._cache:
            return self._cache[agent_id]

        session_id = self._session_mgr.get_or_create(role=agent_id, lifecycle=lifecycle)
        self._cache[agent_id] = session_id
        self._annotate_trace_seed(agent_id, agent_config, session_id)
        self._record_session(agent_id, session_id, lifecycle)
        return session_id

    def _annotate_trace_seed(
        self,
        agent_id: str,
        agent_config: JsonObject,
        session_id: str,
    ) -> None:
        if not isinstance(self._session_mgr, TraceSeedAnnotator):
            return
        role_value = agent_config.get("role")
        logical_role = role_value if isinstance(role_value, str) else None
        self._session_mgr.annotate_trace_seed(
            session_id,
            logical_role=logical_role,
            scope=f"agent:{agent_id}",
        )

    @staticmethod
    def _lifecycle(value: JsonValue) -> SessionLifecycle:
        if value == "auto":
            return "auto"
        if value == "ephemeral":
            return "ephemeral"
        if value == "reusable":
            return "reusable"
        return "persistent"

    def _record_session(self, agent_id: str, session_id: str, lifecycle: str) -> None:
        """Record session creation via TelemetryObserver if available."""
        if self._observer and hasattr(self._observer, "record_event"):
            self._observer.record_event(
                "session_registry_created",
                agent_id=agent_id,
                session_id=session_id,
                lifecycle=lifecycle,
            )

    def get_all_session_ids(self) -> dict[str, str]:
        """Return dict of agent_id -> session_id for all resolved agents."""
        return dict(self._cache)

    def cleanup_all(self) -> int:
        """Clean up all managed sessions via session_mgr.cleanup_all()."""
        return self._session_mgr.cleanup_all()
