

class SessionRegistry:
    """Pool manager for agent sessions. Reuses persistent sessions."""

    def __init__(self, agents_config: dict[str, dict], session_mgr, observer=None):
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
        lifecycle = agent_config.get("lifecycle", "persistent")

        if lifecycle in ("auto", "ephemeral"):
            sid = self._session_mgr.get_or_create(role=agent_id, lifecycle=lifecycle)
            self._record_session(agent_id, sid, lifecycle)
            return sid

        if agent_id in self._cache:
            return self._cache[agent_id]

        session_id = self._session_mgr.get_or_create(role=agent_id, lifecycle=lifecycle)
        self._cache[agent_id] = session_id
        self._record_session(agent_id, session_id, lifecycle)
        return session_id

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
