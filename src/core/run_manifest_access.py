from __future__ import annotations

from typing import Protocol

from core.run_manifest_models import ManifestErrorKind
from core.run_manifest_validation import require


class RegisteredManifestHandle(Protocol):
    pass


class RunManifestHandleError(TypeError): ...


_STORE_ACCESS: dict[int, tuple[RegisteredManifestHandle, bool]] = {}


def register_store(store: RegisteredManifestHandle, writable: bool) -> None:
    _STORE_ACCESS[id(store)] = (store, writable)


def require_store(store: RegisteredManifestHandle) -> None:
    registration = _STORE_ACCESS.get(id(store))
    require(
        registration is not None and registration[0] is store,
        ManifestErrorKind.READ_ONLY,
        "manifest handle was not issued by a public factory",
    )


def store_is_writable(store: RegisteredManifestHandle) -> bool:
    registration = _STORE_ACCESS.get(id(store))
    return registration is not None and registration[0] is store and registration[1]
