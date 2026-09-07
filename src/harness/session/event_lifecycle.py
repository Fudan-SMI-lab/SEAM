from __future__ import annotations

import logging
import time
from typing import Callable

from core.compat import TypeAlias

from .events import (
    ActiveTransportAttempt,
    PreparedTransportAttempt,
    RetryDecision,
    TransportAttemptEvent,
    TransportDisposition,
    TransportEventReason,
    TransportInvocation,
    TransportInvocationId,
    TransportObserver,
    attempt_completed,
    attempt_errored,
    attempt_started,
    attempt_timed_out,
    attempts_exhausted,
)

MonotonicClock: TypeAlias = Callable[[], float]
logger = logging.getLogger("harness.session.events")


class TransportLifecycle:
    def __init__(
        self,
        observer: TransportObserver | None,
        clock: MonotonicClock = time.perf_counter,
    ) -> None:
        self._observer = observer
        self._clock = clock
        self._next_invocation = 1
        self._active: dict[
            tuple[TransportInvocationId, int], ActiveTransportAttempt
        ] = {}

    def new_invocation(
        self,
        session_id: str,
        timeout_s: int | float | None,
        max_attempts: int,
    ) -> TransportInvocation:
        invocation_id = TransportInvocationId(f"transport-{self._next_invocation:06d}")
        self._next_invocation += 1
        configured_timeout = 600.0 if timeout_s is None else float(timeout_s)
        return TransportInvocation(
            session_id=session_id,
            invocation_id=invocation_id,
            max_attempts=max_attempts,
            timeout_s=configured_timeout,
        )

    @staticmethod
    def prepare(
        invocation: TransportInvocation,
        attempt: int,
    ) -> PreparedTransportAttempt:
        return PreparedTransportAttempt(invocation=invocation, attempt=attempt)

    def start(self, prepared: PreparedTransportAttempt) -> ActiveTransportAttempt:
        active = ActiveTransportAttempt(prepared=prepared, started_at=self._clock())
        self._active[self._key(prepared)] = active
        try:
            self._publish(attempt_started(prepared))
        except (KeyboardInterrupt, SystemExit):
            self._active.pop(self._key(prepared), None)
            raise
        return active

    def complete(self, active: ActiveTransportAttempt) -> None:
        prepared = active.prepared
        registered = self._active.pop(self._key(prepared), None)
        if registered is None:
            return
        self._publish(attempt_completed(registered, self._elapsed(registered)))

    def timed_out(
        self,
        prepared: PreparedTransportAttempt,
        disposition: TransportDisposition,
    ) -> None:
        active = self._active.get(self._key(prepared))
        if active is None:
            return
        try:
            self._publish(attempt_timed_out(active, disposition, self._elapsed(active)))
        except (KeyboardInterrupt, SystemExit):
            self._active.pop(self._key(prepared), None)
            raise
        if not disposition.exhausted:
            self._active.pop(self._key(prepared), None)

    def errored(
        self,
        prepared: PreparedTransportAttempt,
        disposition: TransportDisposition,
    ) -> None:
        active = self._active.get(self._key(prepared))
        if active is None:
            return
        try:
            self._publish(attempt_errored(active, disposition, self._elapsed(active)))
        except (KeyboardInterrupt, SystemExit):
            self._active.pop(self._key(prepared), None)
            raise
        if not disposition.exhausted:
            self._active.pop(self._key(prepared), None)

    def failed(
        self,
        prepared: PreparedTransportAttempt,
        disposition: TransportDisposition,
        exhaustion_reason: TransportEventReason,
    ) -> None:
        self.errored(prepared, disposition)
        if disposition.exhausted:
            self.exhausted(
                prepared,
                TransportDisposition(
                    retry_decision=disposition.retry_decision,
                    reason=exhaustion_reason,
                    exhausted=True,
                ),
            )

    def hard_error(self, prepared: PreparedTransportAttempt) -> None:
        disposition = TransportDisposition(
            retry_decision=RetryDecision.NO_REPOST,
            reason=TransportEventReason.HARD_ERROR,
            exhausted=True,
        )
        self.failed(prepared, disposition, TransportEventReason.HARD_ERROR)

    def transport_failure(
        self,
        prepared: PreparedTransportAttempt,
        timed_out: bool,
        will_retry: bool,
    ) -> None:
        disposition = TransportDisposition(
            retry_decision=(
                RetryDecision.RETRY_SAME_SESSION if will_retry else RetryDecision.STOP
            ),
            reason=(
                TransportEventReason.REQUEST_TIMEOUT
                if timed_out
                else TransportEventReason.TRANSPORT_ERROR
            ),
            exhausted=not will_retry,
        )
        if timed_out:
            self.timed_out(prepared, disposition)
            if not will_retry:
                self.exhausted(
                    prepared,
                    TransportDisposition(
                        retry_decision=RetryDecision.STOP,
                        reason=TransportEventReason.RETRIES_EXHAUSTED,
                        exhausted=True,
                    ),
                )
            return
        self.failed(
            prepared,
            disposition,
            TransportEventReason.RETRIES_EXHAUSTED,
        )

    def session_failure(
        self,
        prepared: PreparedTransportAttempt,
        will_retry: bool,
    ) -> None:
        self.failed(
            prepared,
            TransportDisposition(
                retry_decision=(
                    RetryDecision.RETRY_SAME_SESSION
                    if will_retry
                    else RetryDecision.STOP
                ),
                reason=TransportEventReason.SESSION_ERROR,
                exhausted=not will_retry,
            ),
            TransportEventReason.RETRIES_EXHAUSTED,
        )

    def post_acceptance_timeout(
        self,
        prepared: PreparedTransportAttempt,
    ) -> None:
        disposition = TransportDisposition(
            retry_decision=RetryDecision.NO_REPOST,
            reason=TransportEventReason.POST_ACCEPTANCE_TIMEOUT,
            exhausted=True,
        )
        self.timed_out(prepared, disposition)
        self.exhausted(prepared, disposition)

    def post_acceptance_transport_failure(
        self,
        prepared: PreparedTransportAttempt,
        timed_out: bool,
    ) -> None:
        reason = (
            TransportEventReason.REQUEST_TIMEOUT
            if timed_out
            else TransportEventReason.TRANSPORT_ERROR
        )
        disposition = TransportDisposition(
            retry_decision=RetryDecision.NO_REPOST,
            reason=reason,
            exhausted=True,
        )
        if timed_out:
            self.timed_out(prepared, disposition)
            self.exhausted(prepared, disposition)
            return
        self.failed(prepared, disposition, reason)

    def post_acceptance_session_failure(
        self,
        prepared: PreparedTransportAttempt,
    ) -> None:
        disposition = TransportDisposition(
            retry_decision=RetryDecision.NO_REPOST,
            reason=TransportEventReason.SESSION_ERROR,
            exhausted=True,
        )
        self.failed(prepared, disposition, TransportEventReason.SESSION_ERROR)

    def is_active(self, prepared: PreparedTransportAttempt) -> bool:
        return self._key(prepared) in self._active

    def exhausted(
        self,
        prepared: PreparedTransportAttempt,
        disposition: TransportDisposition,
    ) -> None:
        active = self._active.pop(self._key(prepared), None)
        if active is None:
            return
        self._publish(attempts_exhausted(active, disposition, self._elapsed(active)))

    def _elapsed(self, active: ActiveTransportAttempt) -> float:
        return max(0.0, self._clock() - active.started_at)

    @staticmethod
    def _key(
        prepared: PreparedTransportAttempt,
    ) -> tuple[TransportInvocationId, int]:
        return prepared.invocation.invocation_id, prepared.attempt

    def _publish(self, event: TransportAttemptEvent) -> None:
        if self._observer is not None:
            try:
                self._observer(event)
            except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                logger.warning(
                    "Transport observer failed for %s",
                    event.phase.value,
                )
