"""State machine primitives for workflow phase transitions."""

from __future__ import annotations

from core.types import PhaseDefinition, WorkflowDefinition


class StateMachine:
    """Track workflow progress without executing any phase logic."""

    def __init__(self, workflow: WorkflowDefinition):
        if not workflow.phases:
            raise ValueError("Workflow must define at least one phase")

        self.workflow: WorkflowDefinition = workflow
        self._phases: dict[str, PhaseDefinition] = {}
        for phase in workflow.phases:
            if phase.id in self._phases:
                raise ValueError(f"Duplicate phase id: {phase.id}")
            self._phases[phase.id] = phase

        self._terminals: set[str] = {str(terminal) for terminal in (workflow.terminals or [])}
        self._failure_counts: dict[str, int] = {phase_id: 0 for phase_id in self._phases}
        self._last_errors: dict[str, str] = {}
        self._max_retry_per_phase: int = _parse_max_retry(workflow.globals)
        self._current_phase_id: str | None = workflow.phases[0].id
        self._terminal_state: str | None = None

    @property
    def current_phase(self) -> str | None:
        """Return the active phase ID, or None when terminal."""
        return self._current_phase_id

    def record_success(self, phase_id: str) -> tuple[bool, str | None]:
        """Advance on a successful phase completion."""
        phase = self._require_active_phase(phase_id)
        next_target = self._resolve_transition(phase, "on_success")
        self._move_to(next_target)
        return True, next_target

    def record_failure(self, phase_id: str, error: str) -> tuple[bool, str | None]:
        """Record a phase failure and transition only when retries are exhausted."""
        phase = self._require_active_phase(phase_id)
        self._failure_counts[phase_id] += 1
        self._last_errors[phase_id] = error

        if self._failure_counts[phase_id] < self._max_retry_per_phase:
            return True, None

        next_target = self._resolve_transition(phase, "on_failure")
        self._move_to(next_target)
        return False, next_target

    def is_terminal(self) -> bool:
        """Return True once the machine reaches a terminal state."""
        return self._terminal_state is not None

    def current_terminal(self) -> str | None:
        """Return the active terminal ID, if any."""
        return self._terminal_state

    def get_failure_count(self, phase_id: str) -> int:
        """Return the number of recorded failures for a phase."""
        if phase_id not in self._failure_counts:
            raise ValueError(f"Unknown phase id: {phase_id}")
        return self._failure_counts[phase_id]

    def _require_active_phase(self, phase_id: str) -> PhaseDefinition:
        if self.is_terminal():
            raise RuntimeError(f"State machine already reached terminal state '{self._terminal_state}'")
        if phase_id != self._current_phase_id:
            raise ValueError(
                f"Expected active phase '{self._current_phase_id}', got '{phase_id}'"
            )
        return self._phases[phase_id]

    def _resolve_transition(self, phase: PhaseDefinition, event: str) -> str:
        target = phase.transitions.get(event)
        if not target:
            raise ValueError(f"Phase '{phase.id}' missing required transition '{event}'")
        if target not in self._phases and target not in self._terminals:
            raise ValueError(f"Phase '{phase.id}' transition '{event}' points to unknown target '{target}'")
        return target

    def _move_to(self, target: str) -> None:
        if target in self._terminals:
            self._current_phase_id = None
            self._terminal_state = target
            return

        self._current_phase_id = target
        self._terminal_state = None


def _parse_max_retry(globals_cfg: dict[str, object] | None) -> int:
    """Read max_retry_per_phase from workflow globals with a safe default."""
    if not isinstance(globals_cfg, dict):
        return 3

    raw_value = globals_cfg.get("max_retry_per_phase", 3)
    if isinstance(raw_value, bool):
        max_retry = int(raw_value)
    elif isinstance(raw_value, int):
        max_retry = raw_value
    elif isinstance(raw_value, str):
        try:
            max_retry = int(raw_value)
        except ValueError as exc:
            raise ValueError("Workflow globals 'max_retry_per_phase' must be an integer") from exc
    else:
        raise ValueError("Workflow globals 'max_retry_per_phase' must be an integer")

    if max_retry < 1:
        raise ValueError("Workflow globals 'max_retry_per_phase' must be >= 1")
    return max_retry


__all__ = ["StateMachine"]
