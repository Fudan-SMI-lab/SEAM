from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence


class FakeContainerRuntime:
    """Mutable command ledger emulating only ContainerBackend's runtime boundary."""

    def __init__(self, *, fail_remove: bool = False) -> None:
        self.fail_remove = fail_remove
        self.calls: list[tuple[str, ...]] = []
        self.labels: dict[str, str] = {}
        self.removed = False

    def __call__(
        self, args: Sequence[str], **_kwargs
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(str(part) for part in args)
        self.calls.append(command)
        if command[:2] == ("docker", "--version"):
            return self._completed(command, stdout="Docker version fake\n")
        if command[:3] == ("docker", "run", "-d"):
            self._capture_labels(command)
            return self._completed(command, stdout="immutable-id\n")
        if command[:2] == ("docker", "inspect"):
            return self._inspect(command)
        if command[:2] == ("docker", "exec"):
            return self._exec(command)
        if command[:2] == ("docker", "stop"):
            return self._completed(command, stdout="immutable-id\n")
        if command[:2] == ("docker", "rm"):
            if self.fail_remove:
                return self._completed(command, returncode=1, stderr="remove failed")
            self.removed = True
            return self._completed(command, stdout="immutable-id\n")
        raise AssertionError(f"unexpected container command: {command}")

    def _inspect(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if self.removed:
            return self._completed(command, returncode=1, stderr="not found")
        format_value = command[command.index("--format") + 1]
        if "Config.Labels" in format_value:
            labels = json.dumps(self.labels, sort_keys=True)
            return self._completed(command, stdout=f"running|immutable-id|{labels}\n")
        if "{{.Id}}" in format_value:
            return self._completed(command, stdout="running|immutable-id\n")
        return self._completed(command, stdout="running\n")

    def _exec(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if any(part.startswith("SEAM_CONTAINER_PROBE_SCRIPT=") for part in command):
            payload = {
                "status": "ok",
                "interpreter_path": "/usr/bin/python3",
                "interpreter_realpath": "/usr/bin/python3.12",
                "sys_executable": "/usr/bin/python3",
                "sys_prefix": "/usr",
                "sys_base_prefix": "/usr",
                "python_implementation": "CPython",
                "python_version": "3.12.7",
                "platform": "Linux",
                "platform_machine": "x86_64",
                "package_inventory_hash": "f" * 64,
            }
            return self._completed(command, stdout=json.dumps(payload) + "\n")
        return self._completed(command, stdout="validated runtime\n")

    def _capture_labels(self, command: tuple[str, ...]) -> None:
        for index, part in enumerate(command):
            if part != "--label":
                continue
            key, separator, value = command[index + 1].partition("=")
            assert separator == "="
            self.labels[key] = value

    @staticmethod
    def _completed(
        command: tuple[str, ...],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def count(self, action: str) -> int:
        return sum(len(call) > 1 and call[1] == action for call in self.calls)
