"""Types, enums, protocols, and constants for the OpenCode runtime lifecycle."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Final, Protocol, final, runtime_checkable

from seam_init.models import SafeDetail

__all__ = [
    "DiagnoseResult", "DiagnoseRunner", "EnvPatch", "OwnedProcessRef",
    "ReadinessFact", "ReadinessMode", "RuntimePorts", "RuntimeRequest",
    "ServerLifecyclePort", "ServerOwnership", "classify_diagnose_exit",
]

_ALLOWED_ENV_KEYS: Final[frozenset[str]] = frozenset({"NO_PROXY", "no_proxy", "PYTHONUNBUFFERED"})
DEFAULT_URL: Final[str] = "http://127.0.0.1:4098"
DEFAULT_HOSTNAME: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 4098
DEFAULT_START_TIMEOUT: Final[float] = 30.0
DEFAULT_POLL_INTERVAL: Final[float] = 1.0


@unique
class ReadinessFact(str, Enum):
    READY = "ready"
    BASIC_READY = "basic_ready"
    SERVER_UNREACHABLE = "server_unreachable"
    AGENT_UNAVAILABLE = "agent_unavailable"
    SESSION_UNAVAILABLE = "session_unavailable"
    MESSAGE_UNAVAILABLE = "message_unavailable"
    INVALID_ARGUMENT = "invalid_argument"
    UNKNOWN = "unknown"


_EXIT_FACT_MAP: Final[dict[int, ReadinessFact]] = {
    0: ReadinessFact.READY, 20: ReadinessFact.BASIC_READY,
    40: ReadinessFact.SERVER_UNREACHABLE, 41: ReadinessFact.AGENT_UNAVAILABLE,
    42: ReadinessFact.SESSION_UNAVAILABLE, 43: ReadinessFact.MESSAGE_UNAVAILABLE,
    50: ReadinessFact.INVALID_ARGUMENT,
}
READY_FACTS: Final[frozenset[ReadinessFact]] = frozenset({ReadinessFact.READY, ReadinessFact.BASIC_READY})


@unique
class ServerOwnership(str, Enum):
    NONE = "none"
    REUSED_FOREIGN = "reused_foreign"
    OWNED = "owned"


@unique
class ReadinessMode(str, Enum):
    BASIC = "basic"
    MESSAGE = "message"


@final
@dataclass(frozen=True, slots=True)
class EnvPatch:
    entries: tuple[tuple[str, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def apply_to(self, base: Mapping[str, str]) -> dict[str, str]:
        return {**base, **dict(self.entries)}


@final
@dataclass(frozen=True, slots=True)
class DiagnoseResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: SafeDetail
    stderr: SafeDetail


@final
@dataclass(frozen=True, slots=True)
class OwnedProcessRef:
    id: int


@runtime_checkable
class DiagnoseRunner(Protocol):
    def run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> DiagnoseResult: ...


@runtime_checkable
class ServerLifecyclePort(Protocol):
    def start(self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str) -> OwnedProcessRef: ...
    def stop(self, ref: OwnedProcessRef) -> SafeDetail: ...
    def is_running(self, ref: OwnedProcessRef) -> bool: ...


@final
@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    diagnose_argv_prefix: tuple[str, ...]
    opencode_executable: str
    server_url: str = DEFAULT_URL
    server_hostname: str = DEFAULT_HOSTNAME
    server_port: int = DEFAULT_PORT
    readiness_mode: ReadinessMode = ReadinessMode.BASIC
    base_env: Mapping[str, str] = field(default_factory=dict)
    start_timeout: float = DEFAULT_START_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL
    work_dir: str = "."


@final
@dataclass(frozen=True, slots=True)
class RuntimePorts:
    diagnose_runner: DiagnoseRunner
    lifecycle: ServerLifecyclePort
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


def classify_diagnose_exit(returncode: int) -> ReadinessFact:
    return _EXIT_FACT_MAP.get(returncode, ReadinessFact.UNKNOWN)
