from __future__ import annotations

import ctypes
import errno
import os
import platform
import sys
from pathlib import Path
from typing import Final, NoReturn, Protocol

_AT_FDCWD: Final = -100
_RENAME_NOREPLACE: Final = 1
_RENAMEAT2_SYSCALLS: Final = {
    "aarch64": 276,
    "armv7l": 382,
    "i386": 353,
    "i686": 353,
    "ppc64": 357,
    "ppc64le": 357,
    "riscv64": 276,
    "s390x": 347,
    "x86_64": 316,
}


class _RenameAt2(Protocol):
    def __call__(
        self,
        old_directory: int,
        old_path: bytes,
        new_directory: int,
        new_path: bytes,
        flags: int,
    ) -> int: ...


class _RenameAt2Syscall(Protocol):
    def __call__(
        self,
        number: int,
        old_directory: int,
        old_path: bytes,
        new_directory: int,
        new_path: bytes,
        flags: int,
    ) -> int: ...


def rename_directory_no_replace(source: Path, destination: Path) -> None:
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        try:
            raw_renameat2 = library.renameat2
        except AttributeError:
            syscall_number = _RENAMEAT2_SYSCALLS.get(platform.machine().lower())
            if syscall_number is None:
                raise OSError(
                    errno.ENOTSUP,
                    "atomic no-replace directory publication is unavailable",
                    destination,
                )
            raw_syscall = library.syscall
            raw_syscall.restype = ctypes.c_long
            syscall: _RenameAt2Syscall = raw_syscall
            if (
                syscall(
                    syscall_number,
                    _AT_FDCWD,
                    os.fsencode(source),
                    _AT_FDCWD,
                    os.fsencode(destination),
                    _RENAME_NOREPLACE,
                )
                != 0
            ):
                _raise_last_error(destination)
        else:
            raw_renameat2.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            raw_renameat2.restype = ctypes.c_int
            renameat2: _RenameAt2 = raw_renameat2
            if (
                renameat2(
                    _AT_FDCWD,
                    os.fsencode(source),
                    _AT_FDCWD,
                    os.fsencode(destination),
                    _RENAME_NOREPLACE,
                )
                != 0
            ):
                _raise_last_error(destination)
        return
    if os.name == "nt":
        os.rename(source, destination)
        return
    raise OSError(
        errno.ENOTSUP,
        "atomic no-replace directory publication is unavailable",
        destination,
    )


def _raise_last_error(destination: Path) -> NoReturn:
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number), destination)
