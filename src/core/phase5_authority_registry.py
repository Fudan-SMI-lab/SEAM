from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Final, NamedTuple, NoReturn, Protocol, final
from weakref import ReferenceType, ref

from core.phase5_attempt_authority import (
    Phase5AttemptAuthority,
    Phase5AuthorityError,
    finalized_receipt_digest,
    immutable_receipt_digest,
)
from core.phase5_attempt_models import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    Phase5AttemptId,
    Phase5AttemptReceipt,
)


class _AuthorityRegistration(NamedTuple):
    authority: Phase5AttemptAuthority
    run_id: str
    attempt_id: Phase5AttemptId
    reservation_nonce: str
    receipt_path: str
    immutable_digest: str
    finalized_digest: str | None
    accepted: bool


class ReceiptOwnershipTransitionError(Phase5AuthorityError): ...


@final
class _RejectedIssuerMutation:
    __slots__: tuple[str, ...] = ()

    def __setitem__(
        self,
        _receipt_path: str,
        _issuer: ReferenceType[Phase5AuthorityRegistry],
    ) -> NoReturn:
        raise ReceiptOwnershipTransitionError(
            "Phase 5 receipt ownership cannot be reassigned"
        )


class _ReceiptOwnership(Protocol):
    @property
    def _issuers(self) -> _RejectedIssuerMutation: ...

    def bind(self, receipt_path: str, issuer: Phase5AuthorityRegistry) -> None: ...

    def is_owner(self, receipt_path: str, issuer: Phase5AuthorityRegistry) -> bool: ...


def _creator_bound_receipt_ownership() -> _ReceiptOwnership:
    issuers: dict[str, ReferenceType[Phase5AuthorityRegistry]] = {}
    lock = Lock()
    rejected_mutation = _RejectedIssuerMutation()

    @final
    class CreatorBoundReceiptOwnership:
        __slots__: tuple[str, ...] = ()

        @property
        def _issuers(self) -> _RejectedIssuerMutation:
            return rejected_mutation

        def bind(
            self,
            receipt_path: str,
            issuer: Phase5AuthorityRegistry,
        ) -> None:
            with lock:
                if receipt_path not in issuers:
                    issuers[receipt_path] = ref(issuer)

        def is_owner(
            self,
            receipt_path: str,
            issuer: Phase5AuthorityRegistry,
        ) -> bool:
            with lock:
                owner = issuers.get(receipt_path)
                return owner is not None and owner() is issuer

    return CreatorBoundReceiptOwnership()


_RECEIPT_OWNERSHIP: Final = _creator_bound_receipt_ownership()
del _creator_bound_receipt_ownership


@final
class Phase5AuthorityRegistry:
    def __init__(self) -> None:
        self._authorities: dict[str, _AuthorityRegistration] = {}

    def register(
        self,
        path: Path,
        receipt: Phase5AttemptReceipt,
    ) -> Phase5AttemptAuthority:
        authority = object.__new__(Phase5AttemptAuthority)
        receipt_path = str(path.resolve())
        immutable_digest = immutable_receipt_digest(receipt)
        finalized_digest = (
            finalized_receipt_digest(receipt) if receipt.complete else None
        )
        object.__setattr__(authority, "_issuer", self)
        object.__setattr__(authority, "_run_id", receipt.run_id)
        object.__setattr__(authority, "_attempt_id", receipt.attempt_id)
        object.__setattr__(authority, "_reservation_nonce", receipt.reservation_nonce)
        object.__setattr__(authority, "_receipt_path", receipt_path)
        object.__setattr__(authority, "_immutable_digest", immutable_digest)
        object.__setattr__(authority, "_finalized_digest", finalized_digest)
        _RECEIPT_OWNERSHIP.bind(receipt_path, self)
        self._authorities[receipt_path] = _AuthorityRegistration(
            authority,
            receipt.run_id,
            receipt.attempt_id,
            receipt.reservation_nonce,
            receipt_path,
            immutable_digest,
            finalized_digest,
            False,
        )
        return authority

    def authority_is_registered(
        self,
        authority: Phase5AttemptAuthority,
    ) -> bool:
        registration = self._authorities.get(authority.receipt_path)
        return bool(
            _RECEIPT_OWNERSHIP.is_owner(authority.receipt_path, self)
            and registration is not None
            and registration.authority is authority
            and registration.run_id == authority.run_id
            and registration.attempt_id == authority.attempt_id
            and registration.reservation_nonce == authority.reservation_nonce
            and registration.receipt_path == authority.receipt_path
            and registration.immutable_digest == authority.immutable_digest
            and registration.finalized_digest == authority.finalized_digest
        )

    def authority_for(self, receipt_path: str) -> Phase5AttemptAuthority | None:
        registration = self._authorities.get(str(Path(receipt_path).resolve()))
        return registration.authority if registration is not None else None

    def authority_for_attempt(self, attempt_id: str) -> Phase5AttemptAuthority | None:
        matches = tuple(
            registration.authority
            for registration in self._authorities.values()
            if registration.attempt_id == attempt_id
        )
        return matches[0] if len(matches) == 1 else None

    def finalize(self, receipt_path: str, receipt: Phase5AttemptReceipt) -> None:
        key = str(Path(receipt_path).resolve())
        previous = self._authorities.get(key)
        if (
            previous is None
            or previous.immutable_digest != immutable_receipt_digest(receipt)
            or not receipt.complete
        ):
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.IDENTITY_MISMATCH, receipt_path
            )
        _ = self.register(Path(receipt_path), receipt)

    def mark_accepted(self, receipt_path: Path, receipt: Phase5AttemptReceipt) -> None:
        key = str(receipt_path.resolve())
        previous = self._authorities.get(key)
        if (
            previous is None
            or not receipt.accepted
            or previous.immutable_digest != immutable_receipt_digest(receipt)
            or previous.finalized_digest != finalized_receipt_digest(receipt)
        ):
            raise AttemptReceiptError(AttemptReceiptErrorKind.IDENTITY_MISMATCH, key)
        self._authorities[key] = previous._replace(accepted=True)

    def accepted_receipt_paths(self) -> tuple[str, ...]:
        return tuple(
            registration.receipt_path
            for registration in self._authorities.values()
            if registration.accepted
        )
