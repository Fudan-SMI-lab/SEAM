"""Internal repair-loop state machine.

Extracted from :mod:`seam_init.repair` to keep the public API module under
the 250-pure-LOC ceiling. Owns the mutable :class:`_Loop` that drives the
bounded edit/restore/revalidate cycle plus the redaction and outcome helpers.

Redaction ordering: :func:`_redact` applies a generous safety bound FIRST
(to prevent catastrophic regex backtracking per Task 6 notepad wisdom),
then redacts the bounded string (so patterns are complete), then applies
the display bound on the already-redacted result. This ordering ensures
boundary-straddling secrets leak zero characters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, final

from core.compat import assert_never
from core.secret_redaction import redact_sensitive_text
from seam_init.config_transaction import ConfigTransaction, TransactionError
from seam_init.models import FailureKind, SafeDetail
from seam_init.repair_classify import (
    MAX_ROUNDS,
    RepairCategory,
    RepairOutcome,
    RepairRequest,
    RepairStatus,
    RepairValidation,
    RepairableDomain,
    classify_repair,
    omo_terminal_fact,
    opencode_terminal_fact,
    repairable_domain_for,
)

_REDACT_SAFETY_BOUND: Final[int] = 4096
_MAX_PROMPT_DETAIL: Final[int] = 200
_MAX_DETAIL: Final[int] = 300
_TERMINAL_GUIDE: Final[str] = (
    "config unchanged; resolve runtime/install/transport issue and rerun")


def _redact(raw: str, bound: int) -> SafeDetail:
    safe = raw[:_REDACT_SAFETY_BOUND]
    redacted = redact_sensitive_text(safe)
    bounded = redacted[:bound]
    suffix = "...[truncated]" if len(redacted) > bound else ""
    return SafeDetail(bounded + suffix)


@final
@dataclass(frozen=True, slots=True)
class _Snapshot:
    target: Path
    existed: bool
    original_bytes: bytes = field(repr=False)


@final
class _Loop:
    """Mutable state for one repair invocation; never exposed publicly."""

    __slots__ = (
        "_req", "_cur", "_snaps", "_rounds", "_edits",
        "_restores", "_committed",
    )

    def __init__(self, request: RepairRequest) -> None:
        self._req = request
        self._cur = request.initial
        self._snaps: dict[RepairableDomain, _Snapshot] = {}
        self._rounds = 0
        self._edits = 0
        self._restores = 0
        self._committed: RepairableDomain | None = None

    def run(self) -> RepairOutcome:
        try:
            _ = ConfigTransaction(self._req.project_root).recover_interrupted()
        except (TransactionError, OSError):
            return self._terminal("interrupted-state recovery failed")
        while True:
            match classify_repair(self._cur):
                case RepairCategory.TERMINAL:
                    return self._done(RepairStatus.TERMINAL, False)
                case RepairCategory.SUCCESS:
                    return self._done(RepairStatus.READY, False)
                case RepairCategory.PENDING_AUTH:
                    return self._done(RepairStatus.PENDING_AUTH, False)
                case RepairCategory.REPAIRABLE:
                    if self._rounds >= MAX_ROUNDS:
                        return self._done(RepairStatus.EXHAUSTED, True)
                    outcome = self._prompt_and_act()
                    if outcome is not None:
                        return outcome
                    self._revalidate()
                case unreachable:
                    assert_never(unreachable)

    def _prompt_and_act(self) -> RepairOutcome | None:
        restorable = self._restorable_snapshot() is not None
        choices = "edit, restore, or stop" if restorable else "edit or stop"
        choice = self._req.prompt.ask(
            self._build_prompt(choices)).strip().lower()
        match choice:
            case "stop":
                return self._done(RepairStatus.STOPPED, False)
            case "edit":
                return self._do_edit()
            case "restore" if restorable:
                return self._do_restore()
            case _:
                return self._done_detail(
                    RepairStatus.STOPPED, False,
                    "invalid or unavailable choice; config unchanged")

    def _do_edit(self) -> RepairOutcome | None:
        domain = repairable_domain_for(self._cur)
        if domain is None:
            return self._done_detail(
                RepairStatus.STOPPED, False, "no repairable domain")
        self._edits += 1
        self._rounds += 1
        try:
            self._snapshot(domain)
        except OSError:
            return self._terminal("config snapshot read failed")
        try:
            match domain:
                case RepairableDomain.OPENCODE:
                    result = self._req.edit_opencode()
                case RepairableDomain.OMO:
                    result = self._req.edit_omo()
                case unreachable:
                    assert_never(unreachable)
        except (TransactionError, OSError):
            return self._terminal("edit callback failed")
        if result.committed:
            self._committed = domain
        return None

    def _do_restore(self) -> RepairOutcome | None:
        snap = self._restorable_snapshot()
        if snap is None:
            return self._done_detail(
                RepairStatus.STOPPED, False, "restore unavailable")
        self._rounds += 1
        try:
            tx = ConfigTransaction(self._req.project_root)
            tx_id = tx.begin((snap.target,))
            result = tx.commit(tx_id, {snap.target: snap.original_bytes})
        except (TransactionError, OSError, ValueError):
            return self._terminal("restore transaction failed")
        if not result.committed:
            return self._terminal("restore rolled back; config unchanged")
        self._restores += 1
        self._committed = None
        return None

    def _restorable_snapshot(self) -> _Snapshot | None:
        domain = self._committed
        if domain is None:
            return None
        snap = self._snaps.get(domain)
        if snap is None or not snap.existed:
            return None
        return snap

    def _snapshot(self, domain: RepairableDomain) -> None:
        if domain in self._snaps:
            return
        target = self._target(domain)
        existed = target.is_file()
        original = target.read_bytes() if existed else b""
        self._snaps[domain] = _Snapshot(target, existed, original)

    def _target(self, domain: RepairableDomain) -> Path:
        match domain:
            case RepairableDomain.OPENCODE:
                return self._req.opencode_target
            case RepairableDomain.OMO:
                return self._req.omo_target
            case unreachable:
                assert_never(unreachable)

    def _revalidate(self) -> None:
        self._cur = RepairValidation(
            opencode=self._req.revalidate_opencode(),
            omo=self._req.revalidate_omo(),
        )

    def _build_prompt(self, choices: str) -> str:
        domain = repairable_domain_for(self._cur)
        match domain:
            case RepairableDomain.OPENCODE:
                raw = str(self._cur.opencode.failure_detail)
            case RepairableDomain.OMO:
                raw = str(self._cur.omo.failure_detail)
            case _:
                raw = "config issue"
        safe = raw[:_REDACT_SAFETY_BOUND]
        redacted = redact_sensitive_text(safe)
        bounded = redacted[:_MAX_PROMPT_DETAIL]
        suffix = "..." if len(redacted) > _MAX_PROMPT_DETAIL else ""
        return f"Config issue ({bounded}{suffix}). Choose: {choices}"

    def _done(self, status: RepairStatus, exhausted: bool) -> RepairOutcome:
        return self._done_detail(status, exhausted, self._detail_text(status))

    def _done_detail(
        self, status: RepairStatus, exhausted: bool, detail: str,
    ) -> RepairOutcome:
        return RepairOutcome(
            status=status, rounds_used=self._rounds, edits=self._edits,
            restorations=self._restores, final=self._cur,
            failure_kind=self._failure_kind(status),
            exhausted=exhausted, safe_detail=_redact(detail, _MAX_DETAIL),
        )

    def _terminal(self, detail: str) -> RepairOutcome:
        return self._done_detail(
            RepairStatus.TERMINAL, False, f"{detail}; {_TERMINAL_GUIDE}")

    def _failure_kind(self, status: RepairStatus) -> FailureKind | None:
        match status:
            case RepairStatus.READY | RepairStatus.PENDING_AUTH:
                return None
            case RepairStatus.STOPPED | RepairStatus.EXHAUSTED | RepairStatus.TERMINAL:
                oc, omo = self._cur.opencode, self._cur.omo
                if omo.failure_kind is not None and omo_terminal_fact(omo.fact):
                    return omo.failure_kind
                if oc.failure_kind is not None and opencode_terminal_fact(oc.fact):
                    return oc.failure_kind
                return (oc.failure_kind or omo.failure_kind
                        or FailureKind.OPENCODE_VALIDATION)
            case unreachable:
                assert_never(unreachable)

    def _detail_text(self, status: RepairStatus) -> str:
        oc = self._cur.opencode.fact.value
        omo = self._cur.omo.fact.value
        match status:
            case RepairStatus.READY:
                return "repair complete; both validators pass"
            case RepairStatus.PENDING_AUTH:
                return "no repairable failure; auth deferred"
            case RepairStatus.TERMINAL:
                return f"terminal non-config failure; opencode={oc}, omo={omo}; {_TERMINAL_GUIDE}"
            case RepairStatus.STOPPED:
                return f"user stopped after {self._rounds} round(s)"
            case RepairStatus.EXHAUSTED:
                return f"repair exhausted after {self._rounds} rounds; oc={oc}, omo={omo}"
            case unreachable:
                assert_never(unreachable)
