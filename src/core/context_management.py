"""Context management primitives for Phase 5 / repair recovery (bug #16).

Task 3 of the bug #16 plan: a versioned ``context_snapshot.v1.json`` schema and
an atomic writer so a rotated session can resume Phase 5 / repair state after
context compaction or session rotation.

Scope is intentionally limited to Phase 5 / repair context — this is NOT a
generic checkpoint system. The module is standalone (no config_loader or heavy
deps) so the ContextBudgetEstimator appended by Task 5 and the Wave 2 consumers
(WorkflowExecutor / RepairLoop) can import it without dependency cycles.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Final

from core.atomic_file import atomic_write_bytes
from core.config_loader import ContextManagementConfig
from core.secret_redaction import redact_json_value

logger = logging.getLogger(__name__)

#: Versioned snapshot schema id. Bump when the dataclass layout changes.
CONTEXT_SNAPSHOT_SCHEMA_VERSION: Final = "context_snapshot.v1"

#: Canonical snapshot filename under ``.sm-artifacts/<run_id>/``.
CONTEXT_SNAPSHOT_FILENAME: Final = "context_snapshot.v1.json"

#: Canonical full loop-history filename under ``.sm-artifacts/<run_id>/``.
#: The loop phase persists the UNBOUNDED history here (Task 7), while prompt
#: call sites receive only the bounded window.
LOOP_HISTORY_FILENAME: Final = "loop_history.v1.json"

#: Serialized snapshot size guard. Oversized snapshots drop recent_turns
#: rather than blocking recovery (Metis Edge 2).
SNAPSHOT_MAX_BYTES: Final = 100_000


@dataclass
class ContextSnapshot:
    """Bounded Phase 5 / repair context handed off across session rotation.

    Every collection field uses ``field(default_factory=...)`` so empty lists /
    dicts serialize and deserialize losslessly. Scalar fields default to simple
    values so callers can construct partial snapshots ergonomically.
    """

    schema_version: str = CONTEXT_SNAPSHOT_SCHEMA_VERSION
    run_id: str = ""
    phase: str = ""
    agent_role: str = ""
    iteration: int = 0
    task_contract: str = ""
    current_error_signature: str = ""
    current_repair_role: str = ""
    changed_files: list[str] = field(default_factory=list)
    dependency_changes: list[str] = field(default_factory=list)
    environment_facts: list[str] = field(default_factory=list)
    gate_status: dict = field(default_factory=dict)
    open_todos: list[str] = field(default_factory=list)
    recent_turns: list[dict] = field(default_factory=list)
    artifact_references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializable dict with sensitive content redacted.

        Redaction rewrites secret-looking values in place but never strips
        semantic fields: artifact_references and current_error_signature are
        preserved (Metis Edge 8).
        """
        return redact_json_value(dataclasses.asdict(self))

    def to_json(self) -> str:
        """Redacted JSON document (deterministic key order, pretty-printed)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> ContextSnapshot:
        if data.get("schema_version") != CONTEXT_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                "unsupported snapshot schema_version: "
                f"{data.get('schema_version')!r} "
                f"(expected {CONTEXT_SNAPSHOT_SCHEMA_VERSION!r})"
            )
        missing = [name for name in cls.__dataclass_fields__ if name not in data]
        if missing:
            raise ValueError(f"snapshot missing required fields: {missing}")
        return cls(**data)

    @classmethod
    def from_json(cls, payload: str) -> ContextSnapshot:
        """Parse a snapshot JSON document; raises ValueError on missing fields."""
        return cls.from_dict(json.loads(payload))


def snapshot_path_for_run(
    run_id: str,
    artifacts_root: str | Path = ".sm-artifacts",
) -> Path:
    """Canonical write path: ``<artifacts_root>/<run_id>/context_snapshot.v1.json``."""
    return Path(artifacts_root) / run_id / CONTEXT_SNAPSHOT_FILENAME


def write_snapshot_atomic(snapshot: ContextSnapshot, path: str | Path) -> None:
    """Persist a snapshot atomically, reusing ``core.atomic_file``.

    Redaction is applied during serialization. If the serialized payload exceeds
    SNAPSHOT_MAX_BYTES the snapshot's recent_turns are dropped and a warning is
    logged — an oversized snapshot never blocks recovery.
    """
    path = Path(path)
    payload = snapshot.to_json()
    payload_bytes = payload.encode("utf-8")
    if len(payload_bytes) > SNAPSHOT_MAX_BYTES:
        logger.warning(
            "snapshot %s is %d bytes (limit %d); dropping recent_turns to stay bounded",
            path.name,
            len(payload_bytes),
            SNAPSHOT_MAX_BYTES,
        )
        snapshot = replace(snapshot, recent_turns=[])
        payload = snapshot.to_json()
        payload_bytes = payload.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, payload_bytes)


# ---------------------------------------------------------------------------
# Task 5: bounded context budget estimator (bug #16 §5.6)
#
# The only wiring into core.config_loader is a type-only re-export so the plan
# QA scenario and Wave 2 consumers can import ``ContextManagementConfig`` from
# this module too. config_loader does not import context_management, so this
# introduces no import cycle.
# ---------------------------------------------------------------------------

#: Conservative context window when ``context_tokens == "auto"`` — no provider
#: metadata is reachable from this module; callers can pass an explicit
#: ``context_tokens`` in the config to override.
CONTEXT_LIMIT_AUTO_DEFAULT: Final = 128_000

#: Rough tokens-per-character factor for the conservative estimation path.
ESTIMATED_TOKENS_PER_CHAR: Final = 0.25

#: Provider callback shape — stands in for trace_client polling
#: ``/session/{id}/message``; callers close over the session id.
TokenProvider = Callable[[], object]


class ContextBudgetState(str, enum.Enum):
    """Budget state derived from threshold comparison (§5.6)."""

    NORMAL = "normal"
    COMPACT = "compact"
    ROTATE = "rotate"


@dataclass(frozen=True)
class ContextBudgetResult:
    """Immutable budget verdict; ``estimated`` marks a degraded path."""

    tokens_used: int
    context_limit: int
    compact_threshold: int
    rotate_threshold: int
    estimated: bool
    state: ContextBudgetState


def _finite_number(value: object) -> bool:
    """True for real numeric values (rejects bool / inf / nan)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _valid_tokens_dict(value: object) -> dict | None:
    """Return ``value`` when it satisfies the ``valid_tokens`` contract.

    Mirrors ``harness.session.opencode_contract_values.valid_tokens``: finite
    numeric ``input``/``output``/``reasoning`` plus a ``cache`` sub-dict with
    finite numeric ``read``/``write``; ``total`` optional. Values must also be
    strictly positive — negative/zero token values are untrustworthy and route
    to the estimation path (Metis Edge 6).
    """
    if not isinstance(value, dict):
        return None
    cache = value.get("cache")
    if not isinstance(cache, dict):
        return None
    for key in ("input", "output", "reasoning"):
        number = value.get(key)
        if not _finite_number(number) or number <= 0:
            return None
    for key in ("read", "write"):
        number = cache.get(key)
        if not _finite_number(number) or number <= 0:
            return None
    if "total" in value and not _finite_number(value.get("total")):
        return None
    return value


def _coerce_ratio(value: object, default: float) -> float:
    """Finite ratio in (0, 1) or ``default`` — never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        ratio = float(value)
    except (OverflowError, ValueError):
        return default
    if math.isfinite(ratio) and 0 < ratio < 1:
        return ratio
    return default


def _coerce_nonneg(value: object, default: float) -> float:
    """Finite non-negative number or ``default`` — never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return default
    if math.isfinite(number) and number >= 0:
        return number
    return default


class ContextBudgetEstimator:
    """Bounded context budget estimator for Phase 5 / repair (§5.6).

    Token sourcing priority:
      1. ``message_info["tokens"]`` when it satisfies the ``valid_tokens``
         contract (cache not double-counted).
      2. ``token_provider()`` callback when provided and it yields a valid
         dict; provider raises/malformed data degrade to estimation.
      3. Conservative character-based estimation, marked ``estimated=True``.

    ``estimate()`` never raises: every malformed or missing input degrades to
    the bounded estimation path.
    """

    def __init__(
        self,
        config: ContextManagementConfig | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        if config is None:
            config = ContextManagementConfig()
        self._config = config
        self._token_provider = token_provider if callable(token_provider) else None
        self._compact_ratio = _coerce_ratio(
            getattr(config, "compact_threshold_ratio", 0.72), 0.72
        )
        self._rotate_ratio = _coerce_ratio(
            getattr(config, "rotate_threshold_ratio", 0.88), 0.88
        )

    def estimate(self, message_info: dict | None = None) -> ContextBudgetResult:
        """Estimate the current budget usage; never raises.

        ``tokens_used`` is always ``>= 0`` and thresholds follow the config
        ratios: ``compact_threshold = int(context_limit * ratio)``,
        ``rotate_threshold = int(context_limit * ratio)``.
        """
        tokens = None
        if isinstance(message_info, dict):
            tokens = _valid_tokens_dict(message_info.get("tokens"))
        if tokens is None and self._token_provider is not None:
            try:
                tokens = _valid_tokens_dict(self._token_provider())
            except Exception:  # noqa: BLE001 — provider outage degrades
                tokens = None

        estimated = False
        if tokens is not None:
            tokens_used = max(
                0, int(tokens["input"] + tokens["output"] + tokens["reasoning"])
            )
        else:
            tokens_used = self._estimate_bounded(message_info)
            estimated = True

        context_limit = self._resolve_context_limit()
        if context_limit is None:
            context_limit = CONTEXT_LIMIT_AUTO_DEFAULT
            estimated = True

        compact_threshold = int(context_limit * self._compact_ratio)
        rotate_threshold = int(context_limit * self._rotate_ratio)

        if tokens_used >= rotate_threshold:
            state = ContextBudgetState.ROTATE
        elif tokens_used >= compact_threshold:
            state = ContextBudgetState.COMPACT
        else:
            state = ContextBudgetState.NORMAL

        return ContextBudgetResult(
            tokens_used=tokens_used,
            context_limit=context_limit,
            compact_threshold=compact_threshold,
            rotate_threshold=rotate_threshold,
            estimated=estimated,
            state=state,
        )

    def _estimate_bounded(self, message_info: dict | None) -> int:
        """Conservative bounded estimate: ``estimated_chars * 0.25 + history``.

        Character data comes from ``estimated_chars`` or the length of any
        ``text``/``content`` payload; ``history_length_factor`` is an optional
        tuning input. Both default to 0, so ``estimate(None)`` and
        ``estimate({})`` still return a bounded non-negative result.
        """
        estimated_chars = 0.0
        history_factor = 0.0
        if isinstance(message_info, dict):
            estimated_chars = _coerce_nonneg(
                message_info.get("estimated_chars"), 0.0
            )
            if estimated_chars <= 0:
                text = message_info.get("text") or message_info.get("content")
                if isinstance(text, str):
                    estimated_chars = float(len(text))
            history_factor = _coerce_nonneg(
                message_info.get("history_length_factor"), 0.0
            )
        tokens_used = int(
            estimated_chars * ESTIMATED_TOKENS_PER_CHAR + history_factor
        )
        return max(0, tokens_used)

    def _resolve_context_limit(self) -> int | None:
        """Explicit ``context_tokens`` int, or ``None`` for the auto default."""
        value = getattr(self._config, "context_tokens", "auto")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None
