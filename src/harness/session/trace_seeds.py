from __future__ import annotations

from dataclasses import dataclass as frozen_dataclass
from dataclasses import replace
from enum import Enum
from typing import Literal, final

SessionLifecycle = Literal["persistent", "reusable", "ephemeral", "auto"]


class TraceSeedMetadataState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@final
@frozen_dataclass(frozen=True)
class TraceSeed:
    __slots__ = (
        "session_id",
        "logical_role",
        "scope",
        "lifecycle",
        "agent",
        "working_directory",
        "metadata_state",
    )
    session_id: str
    logical_role: str | None
    scope: str | None
    lifecycle: SessionLifecycle
    agent: str
    working_directory: str
    metadata_state: TraceSeedMetadataState


class TraceSeedRegistry:
    def __init__(self) -> None:
        self._seeds: dict[str, TraceSeed] = {}

    def record(
        self,
        session_id: str,
        logical_role: str,
        lifecycle: SessionLifecycle,
        agent: str,
        working_directory: str,
    ) -> TraceSeed:
        existing = self._seeds.get(session_id)
        if existing is not None:
            return existing
        role = self._optional_text(logical_role)
        scope = f"role:{role}" if role is not None else None
        seed = TraceSeed(
            session_id=session_id,
            logical_role=role,
            scope=scope,
            lifecycle=lifecycle,
            agent=agent,
            working_directory=working_directory,
            metadata_state=self._metadata_state(role, scope),
        )
        self._seeds[session_id] = seed
        return seed

    def annotate(
        self,
        session_id: str,
        logical_role: str | None,
        scope: str | None,
    ) -> TraceSeed | None:
        existing = self._seeds.get(session_id)
        if existing is None:
            return None
        role = self._optional_text(logical_role) or existing.logical_role
        resolved_scope = self._optional_text(scope) or existing.scope
        updated = replace(
            existing,
            logical_role=role,
            scope=resolved_scope,
            metadata_state=self._metadata_state(role, resolved_scope),
        )
        self._seeds[session_id] = updated
        return updated

    def snapshot(self) -> tuple[TraceSeed, ...]:
        return tuple(self._seeds.values())

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _metadata_state(
        role: str | None,
        scope: str | None,
    ) -> TraceSeedMetadataState:
        if role is not None and scope is not None:
            return TraceSeedMetadataState.COMPLETE
        return TraceSeedMetadataState.PARTIAL
