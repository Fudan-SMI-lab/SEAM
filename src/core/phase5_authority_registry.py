from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, final

from core.phase5_attempt_authority import (
    Phase5AttemptAuthority,
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
        self._authorities[receipt_path] = _AuthorityRegistration(
            authority,
            receipt.run_id,
            receipt.attempt_id,
            receipt.reservation_nonce,
            receipt_path,
            immutable_digest,
            finalized_digest,
        )
        return authority

    def authority_is_registered(
        self,
        authority: Phase5AttemptAuthority,
    ) -> bool:
        registration = self._authorities.get(authority.receipt_path)
        return (
            registration
            == _AuthorityRegistration(
                authority,
                authority.run_id,
                authority.attempt_id,
                authority.reservation_nonce,
                authority.receipt_path,
                authority.immutable_digest,
                authority.finalized_digest,
            )
            and registration is not None
            and registration.authority is authority
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
