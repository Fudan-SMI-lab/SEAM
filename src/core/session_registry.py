from __future__ import annotations

import threading
from typing import Callable, Protocol, final, runtime_checkable

from harness.session.manager import SessionRecord
from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.trace_seeds import SessionLifecycle


class ContextExhaustedError(Exception):
    """Structured signal that the recovery budget for a session was reached.

    Raised by the manager compaction state machine (Task 6) when the bounded
    recovery limit (``max_recoveries_per_command``) is hit and the session
    context cannot be reclaimed in place. Carries everything the executor
    needs to rotate: which session/agent was affected, how much context was
    consumed, how many compactions were attempted, and why recovery stopped.

    ``old_session_id``/``new_session_id`` are populated by the rotation
    callers (Task 7/8) once a successor session exists; both default to
    ``None`` when the error is raised before rotation.
    """

    def __init__(
        self,
        session_id: str,
        agent_id: str,
        tokens_used: int,
        compaction_count: int,
        reason: str,
        old_session_id: str | None = None,
        new_session_id: str | None = None,
    ) -> None:
        super().__init__(
            f"Context exhausted for session {session_id!r} (agent={agent_id!r}): "
            f"tokens_used={tokens_used}, compaction_count={compaction_count}, "
            f"reason={reason!r}"
        )
        self.session_id = session_id
        self.agent_id = agent_id
        self.tokens_used = tokens_used
        self.compaction_count = compaction_count
        self.reason = reason
        self.old_session_id = old_session_id
        self.new_session_id = new_session_id


class SessionPool(Protocol):
    def get_or_create(
        self,
        role: str,
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
    """Pool manager for agent sessions. Reuses persistent sessions.

    Rotation contract — dual-layer sync (Metis Q5):
    ``rotate()`` atomically swaps ONLY this registry's ``_cache`` under a
    single :class:`threading.Lock`. The caller (executor) is REQUIRED to
    also keep ``manager._sessions`` consistent so the two layers never
    diverge::

        record = registry.rotate(agent_id, reason, handoff_snapshot)
        manager.register_session(record)   # manager-side helper (Task 6)

    When rotate creates the new session through ``session_mgr.get_or_create``
    the manager layer is already populated by ``create_session``; callers that
    pass a custom ``session_factory`` must register the returned record
    themselves. ``invalidate()`` only clears the registry cache entry — it
    does NOT delete the underlying session (no TTL/GC/expiry, out of scope).
    """

    def __init__(
        self,
        agents_config: dict[str, JsonObject] | None = None,
        session_mgr: SessionPool | None = None,
        observer: SessionObserver | None = None,
    ) -> None:
        """
        agents_config: {"main_engineer": {"role": "main_engineer", "lifecycle": "persistent"}}
        session_mgr: MigrationSessionManager instance (has get_or_create method)
        observer: Optional TelemetryObserver to record session creation events

        All arguments are optional so the registry stays unit-testable
        standalone (``SessionRegistry()``) without a live manager.
        """
        self._agents = agents_config if agents_config is not None else {}
        self._session_mgr = session_mgr
        self._cache: dict[str, str] = {}  # agent_id -> session_id
        self._observer = observer
        # Single explicit lock guards rotate/invalidate (codebase is
        # single-threaded, but the explicit lock satisfies the
        # §5.5 "single-winner" atomicity contract).
        self._lock = threading.Lock()
        self._rotation_counters: dict[str, int] = {}  # agent_id -> rotations performed
        self._rotation_history: dict[str, list[JsonObject]] = {}  # agent_id -> old/new records

    def resolve(self, agent_id: str) -> str:
        """Return cached session_id or create new one.

        First call: session_mgr.get_or_create(role=agent_id, lifecycle=...)
        Subsequent calls: return cached session_id
        """
        agent_config = self._agents[agent_id]
        lifecycle = self._lifecycle(agent_config.get("lifecycle"))

        if lifecycle in ("auto", "ephemeral"):
            sid = self._session_mgr.get_or_create(agent_id, lifecycle)
            self._annotate_trace_seed(agent_id, agent_config, sid)
            self._record_session(agent_id, sid, lifecycle)
            return sid

        if agent_id in self._cache:
            return self._cache[agent_id]

        session_id = self._session_mgr.get_or_create(agent_id, lifecycle)
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

    def rotate(
        self,
        agent_id: str,
        reason: str,
        handoff_snapshot: JsonObject,
        session_factory: Callable[[str], str] | None = None,
    ) -> SessionRecord:
        """Atomically rotate *agent_id* to a fresh session.

        Under a single :class:`threading.Lock` (single-winner, §5.5):
          1. Create the NEW session first — via ``session_factory(role)``,
             else ``session_mgr.get_or_create(role=f"{agent_id}_rotated_{n}")``,
             else a deterministic synthetic id (standalone/testability).
          2. Build a fully valid 8-field ``SessionRecord`` for the new
             session and attach rotation metadata (``old_session_id``,
             ``rotation_reason``, ``handoff_snapshot``, ``rotation_count``).
          3. Swap ``self._cache[agent_id]`` to the new id and record
             old/new in ``self._rotation_history``.

        If step 1 fails the cache is untouched (no half-update).

        Dual-layer sync (Metis Q5): this only updates ``registry._cache``.
        The caller MUST ALSO keep ``manager._sessions`` consistent — the
        manager-side ``register_session`` helper registers the returned
        record::

            rec = registry.rotate(agent_id, reason, handoff_snapshot)
            manager.register_session(rec)

        Returns:
            The new :class:`SessionRecord` with rotation metadata attached.
        """
        with self._lock:
            old_session_id = self._cache.get(agent_id)
            rotation_count = self._rotation_counters.get(agent_id, 0) + 1
            new_role = f"{agent_id}_rotated_{rotation_count}"

            if session_factory is not None:
                new_session_id = session_factory(new_role)
                lifecycle: SessionLifecycle = "persistent"
            elif self._session_mgr is not None:
                agent_config = self._agents.get(agent_id, {})
                lifecycle = self._lifecycle(agent_config.get("lifecycle"))
                new_session_id = self._session_mgr.get_or_create(new_role, lifecycle)
                self._annotate_trace_seed(agent_id, agent_config, new_session_id)
            else:
                new_session_id = new_role
                lifecycle = "persistent"

            agent_name = str(
                getattr(self._session_mgr, "active_agent", "") or agent_id
            )
            record = SessionRecord(
                session_id=new_session_id,
                role=new_role,
                agent=agent_name,
                lifecycle=lifecycle,
                working_dir=str(getattr(self._session_mgr, "work_dir", "") or ""),
            )
            record.old_session_id = old_session_id
            record.rotation_reason = reason
            record.handoff_snapshot = handoff_snapshot
            record.rotation_count = rotation_count

            self._cache[agent_id] = new_session_id
            self._rotation_counters[agent_id] = rotation_count
            self._rotation_history.setdefault(agent_id, []).append(
                {
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                    "reason": reason,
                    "rotation_count": rotation_count,
                    "handoff_snapshot": handoff_snapshot,
                }
            )
            if self._observer and hasattr(self._observer, "record_event"):
                self._observer.record_event(
                    "session_registry_rotated",
                    agent_id=agent_id,
                    old_session_id=old_session_id,
                    new_session_id=new_session_id,
                    reason=reason,
                    rotation_count=rotation_count,
                )
            return record

    def invalidate(self, agent_id: str, reason: str) -> None:
        """Remove the cached session mapping for *agent_id*.

        Only clears ``self._cache``; the underlying session is NOT deleted
        (no TTL/GC/expiry — out of scope). Callers that must also drop the
        manager-side record do so explicitly (dual-layer contract).
        """
        with self._lock:
            old_session_id = self._cache.pop(agent_id, None)
            if self._observer and hasattr(self._observer, "record_event"):
                self._observer.record_event(
                    "session_registry_invalidated",
                    agent_id=agent_id,
                    session_id=old_session_id,
                    reason=reason,
                )

    def get_rotation_history(self, agent_id: str) -> list[JsonObject]:
        """Return old/new rotation records for *agent_id* (oldest first)."""
        return list(self._rotation_history.get(agent_id, []))

    def get_all_session_ids(self) -> dict[str, str]:
        """Return dict of agent_id -> session_id for all resolved agents."""
        return dict(self._cache)

    def cleanup_all(self) -> int:
        """Clean up all managed sessions via session_mgr.cleanup_all()."""
        return self._session_mgr.cleanup_all()
