from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import BinaryIO, NamedTuple, TypeAlias

ShellCommand: TypeAlias = str | list[str]
_MAX_TAIL = 500_000


class RetainedShellCapture(NamedTuple):
    exit_code: int
    duration: float
    stdout: str
    stderr: str
    stdout_source: BinaryIO
    stderr_source: BinaryIO


@contextmanager
def capture_shell_output(
    command: ShellCommand,
    *,
    shell: bool,
    cwd: str,
    environment: Mapping[str, str] | None,
    timeout: int | None,
) -> Iterator[RetainedShellCapture]:
    start = time.time()
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout_source,
        tempfile.TemporaryFile(mode="w+b") as stderr_source,
    ):
        try:
            process_environment = {**os.environ, **environment} if environment else None
            result = subprocess.run(
                command,
                shell=shell,
                cwd=cwd,
                env=process_environment,
                stdout=stdout_source,
                stderr=stderr_source,
                timeout=timeout,
            )
            capture = RetainedShellCapture(
                result.returncode,
                time.time() - start,
                _read_tail(stdout_source),
                _read_tail(stderr_source),
                stdout_source,
                stderr_source,
            )
        except subprocess.TimeoutExpired:
            capture = RetainedShellCapture(
                124,
                timeout if timeout is not None else 0,
                _read_tail(stdout_source),
                _read_tail(stderr_source),
                stdout_source,
                stderr_source,
            )
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            capture = RetainedShellCapture(
                1,
                time.time() - start,
                "",
                str(exc),
                stdout_source,
                stderr_source,
            )
        yield capture


def _read_tail(source: BinaryIO, max_bytes: int = _MAX_TAIL) -> str:
    try:
        initial = os.fstat(source.fileno())
        if not stat.S_ISREG(initial.st_mode):
            return ""
        _ = source.seek(max(0, initial.st_size - max_bytes))
        content = source.read(max_bytes)
        final = os.fstat(source.fileno())
    except OSError:
        return ""
    stable = (
        initial.st_dev,
        initial.st_ino,
        initial.st_size,
        initial.st_mtime_ns,
        initial.st_ctime_ns,
    ) == (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
        final.st_ctime_ns,
    )
    return content.decode("utf-8", errors="replace") if stable else ""
