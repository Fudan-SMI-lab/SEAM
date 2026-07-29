from __future__ import annotations

import hashlib
import json
from typing import NoReturn, Protocol, final

from typing_extensions import override

from core.phase5_attempt_models import (
    Phase5AttemptId,
    Phase5AttemptReceipt,
    Sha256Digest,
)


class _AuthorityIssuer(Protocol):
    def authority_is_registered(
        self, authority: Phase5AttemptAuthority
    ) -> bool: ...


class _RejectingIssuer:
    def authority_is_registered(
        self, authority: Phase5AttemptAuthority
    ) -> bool:
        _ = authority
        return False


_REJECTING_ISSUER = _RejectingIssuer()


class Phase5AuthorityError(TypeError): ...


@final
class Phase5AttemptAuthority:
    __slots__ = (
        "_attempt_id",
        "_finalized_digest",
        "_immutable_digest",
        "_issuer",
        "_receipt_path",
        "_reservation_nonce",
        "_run_id",
    )
    _attempt_id: Phase5AttemptId
    _finalized_digest: Sha256Digest | None
    _immutable_digest: Sha256Digest
    _issuer: _AuthorityIssuer
    _receipt_path: str
    _reservation_nonce: str
    _run_id: str

    def __init__(self) -> None:
        self._attempt_id = Phase5AttemptId("")
        self._finalized_digest = None
        self._immutable_digest = Sha256Digest("")
        self._issuer = _REJECTING_ISSUER
        self._receipt_path = ""
        self._reservation_nonce = ""
        self._run_id = ""
        raise Phase5AuthorityError(
            "Phase5AttemptAuthority is issued by Phase5ArtifactStore"
        )

    @override
    def __setattr__(
        self,
        name: str,
        value: str | Phase5AttemptId | Sha256Digest | None,
    ) -> None:
        if hasattr(self, name):
            raise AttributeError("Phase5AttemptAuthority is immutable")
        object.__setattr__(self, name, value)

    def __copy__(self) -> NoReturn:
        raise Phase5AuthorityError("Phase5AttemptAuthority cannot be copied")

    def __deepcopy__(self, _memo: dict[int, Phase5AttemptAuthority]) -> NoReturn:
        raise Phase5AuthorityError("Phase5AttemptAuthority cannot be copied")

    @override
    def __reduce__(self) -> NoReturn:
        raise Phase5AuthorityError("Phase5AttemptAuthority cannot be serialized")

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def attempt_id(self) -> Phase5AttemptId:
        return self._attempt_id

    @property
    def reservation_nonce(self) -> str:
        return self._reservation_nonce

    @property
    def receipt_path(self) -> str:
        return self._receipt_path

    @property
    def immutable_digest(self) -> Sha256Digest:
        return self._immutable_digest

    @property
    def finalized_digest(self) -> Sha256Digest | None:
        return self._finalized_digest

    def is_registered(self) -> bool:
        return self._issuer.authority_is_registered(self)


def immutable_receipt_digest(receipt: Phase5AttemptReceipt) -> Sha256Digest:
    payload = json.dumps(
        {
            "run_id": receipt.run_id,
            "reservation_nonce": receipt.reservation_nonce,
            "attempt_id": receipt.attempt_id,
            "attempt_number": receipt.attempt_number,
            "invocation": receipt.invocation.model_dump(mode="json"),
            "backend": receipt.backend.model_dump(mode="json"),
            "artifacts": receipt.artifacts.model_dump(mode="json"),
            "shell_exit_code": receipt.shell_exit_code,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return Sha256Digest(hashlib.sha256(payload).hexdigest())


def finalized_receipt_digest(receipt: Phase5AttemptReceipt) -> Sha256Digest:
    payload = json.dumps(
        {
            "immutable_digest": immutable_receipt_digest(receipt),
            "custom_op_gate": receipt.custom_op_gate.model_dump(mode="json"),
            "review": receipt.review.model_dump(mode="json"),
            "complete": receipt.complete,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return Sha256Digest(hashlib.sha256(payload).hexdigest())


def receipt_matches_authority(
    receipt: Phase5AttemptReceipt, authority: Phase5AttemptAuthority
) -> bool:
    return (
        authority.is_registered()
        and receipt.run_id == authority.run_id
        and receipt.attempt_id == authority.attempt_id
        and receipt.reservation_nonce == authority.reservation_nonce
        and immutable_receipt_digest(receipt) == authority.immutable_digest
        and authority.finalized_digest is not None
        and finalized_receipt_digest(receipt) == authority.finalized_digest
    )
