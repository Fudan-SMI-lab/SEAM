from __future__ import annotations

from collections import deque
from dataclasses import dataclass as frozen_dataclass
from typing import Final, final

from harness.session.opencode_contract import JsonObject
from harness.session.trace_export_index import TraceExportIndex
from harness.session.trace_seeds import TraceSeed

MAX_TRACE_SESSIONS: Final = 10_000
MAX_TRACE_EDGES: Final = 100_000
MAX_TRACE_DEPTH: Final = 256
MAX_SESSION_ID_CHARS: Final = 4_096


@final
@frozen_dataclass(frozen=True)
class QueuedSession:
    __slots__ = ("session_id", "session_info", "path")
    session_id: str
    session_info: JsonObject | None
    path: tuple[str, ...]


@final
class GraphTraversal:
    """Mutable bounded BFS state bound to one artifact index."""

    def __init__(
        self,
        seeds: dict[str, tuple[TraceSeed, ...]],
        index: TraceExportIndex,
    ) -> None:
        self.index: TraceExportIndex = index
        self.queue = deque(
            QueuedSession(session_id, None, (session_id,)) for session_id in seeds
        )
        self.enqueued: set[str] = set(seeds)
        self.visited: set[str] = set()
        self.adjacency: dict[str, set[str]] = {}
        self.edge_limit_sessions: set[str] = set()

    def enqueue_child(self, parent: QueuedSession, raw: JsonObject) -> None:
        child_id = raw.get("id")
        parent_id = raw.get("parentID")
        if not isinstance(child_id, str) or not child_id:
            self.index.add_error("malformed_child_id", parent.session_id, "")
            return
        if len(child_id) > MAX_SESSION_ID_CHARS:
            self.index.add_error(
                "session_id_limit_exceeded", parent.session_id, child_id[:128]
            )
            return
        if parent_id != parent.session_id:
            self.index.add_error("child_parent_mismatch", parent.session_id, child_id)
            return
        if self.index.child_edge_count >= MAX_TRACE_EDGES:
            if parent.session_id not in self.edge_limit_sessions:
                self.index.add_error(
                    "trace_edge_limit_exceeded", parent.session_id, child_id[:128]
                )
                self.edge_limit_sessions.add(parent.session_id)
            return
        parent_edges = self.adjacency.setdefault(parent.session_id, set())
        if child_id in parent_edges:
            self.index.add_error("duplicate_child_id", parent.session_id, child_id)
            return
        parent_edges.add(child_id)
        self.index.child_edge_count += 1
        if child_id in parent.path or self._has_path(child_id, parent.session_id):
            self.index.add_error("cycle_detected", parent.session_id, child_id)
            return
        if child_id in self.enqueued:
            self.index.add_error("duplicate_child_id", parent.session_id, child_id)
            return
        if len(parent.path) >= MAX_TRACE_DEPTH:
            self.index.add_error(
                "trace_depth_limit_exceeded", parent.session_id, child_id
            )
            return
        if len(self.enqueued) >= MAX_TRACE_SESSIONS:
            self.index.add_error(
                "trace_session_limit_exceeded", parent.session_id, child_id
            )
            return
        self.enqueued.add(child_id)
        self.queue.append(QueuedSession(child_id, raw, (*parent.path, child_id)))

    def _has_path(self, start: str, target: str) -> bool:
        pending = [start]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self.adjacency.get(current, ()))
        return False
