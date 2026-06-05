from __future__ import annotations

import glob as _glob_module
import json
import logging
import os as _os_module
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from core.types import ExecutionBackendConfig

logger = logging.getLogger(__name__)

_CONTAINER_NOT_FOUND_MSG = (
    "execution_backend.container_name is required when source=existing_container"
)

# ── Configurable Python version scan range ─────────────────────────────
PYTHON_VERSION_CANDIDATES_MIN = 8
"""Minimum minor version to probe when scanning for a Python interpreter."""

PYTHON_VERSION_CANDIDATES_MAX = 14
"""Maximum minor version (exclusive) to probe when scanning for a Python interpreter."""

# ── NPU device auto-discovery ──────────────────────────────────────────

_AUTO_MARKER_PREFIX = "auto:"


def _make_auto_marker(platform_id: str) -> str:
    """Build an ``auto:{platform_id}`` marker string for device resolution.

    This replaces the NPU-specific ``_AUTO_NPU_MARKER`` constant; callers
    should use ``_make_auto_marker("npu_ascend")`` etc. instead.
    """
    return f"{_AUTO_MARKER_PREFIX}{platform_id}"


# Deprecated: kept for backward compat with existing callers (e.g. tests).
# Prefer ``_make_auto_marker("npu_ascend")`` or pass the marker string directly.
_AUTO_NPU_MARKER = _make_auto_marker("npu")

_ASCEND_DEVICE_CANDIDATES: list[str] = [
    "/dev/davinci_manager",
    "/dev/devmm_svm",
    "/dev/hisi_hdc",
]

# Deprecated backward-compat alias — prefer ``_ASCEND_DEVICE_CANDIDATES``.
_NPU_DEVICE_CANDIDATES = _ASCEND_DEVICE_CANDIDATES


def _get_npu_devices_from_env() -> list[str] | None:
    """Return device paths from SEAM_DEVICES (generic) or SEAM_NPU_DEVICES (legacy).

    Precedence:
    1. ``SEAM_DEVICES`` (generic, platform-agnostic)
    2. ``SEAM_NPU_DEVICES`` (legacy, with deprecation warning)
    """
    # 1. Generic platform-agnostic env var (preferred)
    raw = _os_module.environ.get("SEAM_DEVICES")
    if raw is not None:
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        if paths:
            return paths

    # 2. Legacy NPU-specific env var (backward compat with deprecation warning)
    raw = _os_module.environ.get("SEAM_NPU_DEVICES")
    if raw is not None:
        logger.warning(
            "SEAM_NPU_DEVICES is deprecated — use SEAM_DEVICES instead."
        )
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        if paths:
            return paths

    return None


def _discover_npu_devices_via_glob(
    glob_pattern: str | None = None,
    fallback_devices: list[str] | None = None,
) -> list[str]:
    """Discover NPU device files via a configurable glob pattern.

    Covers Ascend 910A/B, 310P, and future chips that follow the davinci
    naming convention.  Falls back to the candidate list when nothing is
    found (the container runtime will still get --device flags; it just
    may fail if the host genuinely has no NPU hardware).

    The default glob pattern is controlled by the ``SEAM_NPU_GLOB_PATTERN``
    environment variable (default ``"/dev/davinci*"``) so that each platform
    deployment can supply its own device-glob convention.

    Args:
        glob_pattern: Glob pattern to discover device files.
                      If *None*, reads ``SEAM_NPU_GLOB_PATTERN``
                      (default ``"/dev/davinci*"``).
        fallback_devices: Fallback device paths when the glob finds nothing.
                          Defaults to ``_NPU_DEVICE_CANDIDATES``.
    """
    _glob = glob_pattern or _os_module.environ.get(
        "SEAM_NPU_GLOB_PATTERN", "/dev/davinci*"
    )
    _fallback = fallback_devices if fallback_devices is not None else _ASCEND_DEVICE_CANDIDATES
    try:
        found = sorted(
            p for p in _glob_module.glob(_glob)
            if Path(p).exists()
        )
        if found:
            return found
    except OSError:
        pass
    return list(_fallback)


def _discover_npu_device_count_via_smi(
    smi_command: str | None = None,
    chip_pattern: str | None = None,
) -> int:
    """Return the number of NPU chips reported by the Ascend SMI tool.

    Returns 0 when the SMI command is not found or fails.

    The default SMI command and chip-pattern are controlled by the
    ``SEAM_NPU_SMI_COMMAND`` and ``SEAM_NPU_SMI_CHIP_PATTERN`` environment
    variables (default ``"npu-smi"`` / ``"NPU"``) so that each platform
    deployment can supply its own tooling conventions.

    Args:
        smi_command: Platform SMI command name or path.
                     If *None*, reads ``SEAM_NPU_SMI_COMMAND``
                     (default ``"npu-smi"``).
        chip_pattern: Substring to match chip/card rows in the output.
                      If *None*, reads ``SEAM_NPU_SMI_CHIP_PATTERN``
                      (default ``"NPU"``).
    """
    _smi = smi_command or _os_module.environ.get(
        "SEAM_NPU_SMI_COMMAND", "npu-smi"
    )
    _chip = chip_pattern or _os_module.environ.get(
        "SEAM_NPU_SMI_CHIP_PATTERN", "NPU"
    )
    try:
        proc = subprocess.run(
            [_smi, "info", "-m"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 0
    if proc.returncode != 0:
        return 0
    count = sum(1 for line in proc.stdout.splitlines() if _chip in line)
    return count


def _discover_ascend_npu_devices(**kwargs: str) -> list[str]:
    """Best-effort NPU device path discovery for Ascend platforms.

    Resolution order (first non-empty wins):
    1. ``SEAM_DEVICES`` / ``SEAM_NPU_DEVICES`` environment variable (comma-separated paths)
    2. ``glob_pattern`` glob (live device files on the host, default ``/dev/davinci*``)
    3. Canonical candidate list (best-effort fallback for container --device)

    Rationale for not removing the candidate fallback: on hosts where
    /dev/davinci* devices exist but the glob somehow fails (e.g. inside a
    restricted container that cannot list /dev), the explicit --device
    flags give the runtime its best chance to mount what is needed.

    Keyword Args:
        glob_pattern: Override the glob pattern used for device discovery.
                      Forwarded to ``_discover_npu_devices_via_glob()``.
        smi_command: Override the SMI command for chip count discovery.
                     Forwarded to ``_discover_npu_device_count_via_smi()``.
        chip_pattern: Override the chip pattern for SMI output parsing.
                      Forwarded to ``_discover_npu_device_count_via_smi()``.
    """
    _glob_pattern: str = kwargs.get("glob_pattern") or _os_module.environ.get(
        "SEAM_NPU_GLOB_PATTERN", "/dev/davinci*"
    )
    # 1. Explicit env override
    env_devices = _get_npu_devices_from_env()
    if env_devices:
        logger.info("Using NPU devices from environment: %s", env_devices)
        return env_devices

    # 2. Live glob discovery
    glob_devices = _discover_npu_devices_via_glob(glob_pattern=_glob_pattern)
    if glob_devices:
        logger.info("Discovered %d NPU device(s) via %r", len(glob_devices), _glob_pattern)
        return glob_devices

    # 3. Canonical fallback
    logger.warning(
        "No NPU devices found via env or glob; using canonical fallback: %s",
        _ASCEND_DEVICE_CANDIDATES,
    )
    return list(_ASCEND_DEVICE_CANDIDATES)


def _discover_generic_device() -> list[str]:
    """Fallback device discovery for platforms without specialised hardware.
    Returns an empty device list — the caller should handle this gracefully.
    """
    return []


# ── PPU device discovery ──────────────────────────────────────────────

_PPU_DEVICE_CANDIDATES: list[str] = ["/dev/kfd"]


def _discover_ppu_devices() -> list[str]:
    """Discover PPU GPU devices via /dev/kfd."""
    try:
        found = sorted(
            p for p in _glob_module.glob("/dev/kfd*")
            if Path(p).exists()
        )
        if found:
            logger.info("Discovered %d PPU device(s) via /dev/kfd*", len(found))
            return found
    except OSError:
        pass
    logger.warning("No PPU devices found; using canonical fallback: %s", _PPU_DEVICE_CANDIDATES)
    return list(_PPU_DEVICE_CANDIDATES)


# ── MUSA device discovery ─────────────────────────────────────────────

_MUSA_DEVICE_CANDIDATES: list[str] = ["/dev/musa0"]


def _discover_musa_devices() -> list[str]:
    """Discover MUSA GPU devices via /dev/musa* glob."""
    try:
        found = sorted(
            p for p in _glob_module.glob("/dev/musa*")
            if Path(p).exists()
        )
        if found:
            logger.info("Discovered %d MUSA device(s) via /dev/musa*", len(found))
            return found
    except OSError:
        pass
    logger.warning("No MUSA devices found; using canonical fallback: %s", _MUSA_DEVICE_CANDIDATES)
    return list(_MUSA_DEVICE_CANDIDATES)


# ── ROCm device discovery ─────────────────────────────────────────────

_ROCM_DEVICE_CANDIDATES: list[str] = [
    "/dev/kfd",
    "/dev/dri/renderD128",
]


def _discover_rocm_devices() -> list[str]:
    """Discover ROCm GPU devices via /dev/kfd + /dev/dri/renderD*.

    Falls back to /dev/dri/renderD* when /dev/kfd is absent.
    """
    try:
        kfd = sorted(
            p for p in _glob_module.glob("/dev/kfd*")
            if Path(p).exists()
        )
        dri = sorted(
            p for p in _glob_module.glob("/dev/dri/renderD*")
            if Path(p).exists()
        )
        found = kfd + dri
        if found:
            logger.info("Discovered %d ROCm device(s) via /dev/kfd + /dev/dri", len(found))
            return found
    except OSError:
        pass
    logger.warning("No ROCm devices found; using canonical fallback: %s", _ROCM_DEVICE_CANDIDATES)
    return list(_ROCM_DEVICE_CANDIDATES)


# ── MLU device discovery ──────────────────────────────────────────────

_MLU_DEVICE_CANDIDATES: list[str] = ["/dev/cambricon0"]


def _discover_mlu_devices() -> list[str]:
    """Discover Cambrian MLU devices via /dev/cambricon* glob."""
    try:
        found = sorted(
            p for p in _glob_module.glob("/dev/cambricon*")
            if Path(p).exists()
        )
        if found:
            logger.info("Discovered %d MLU device(s) via /dev/cambricon*", len(found))
            return found
    except OSError:
        pass
    logger.warning("No MLU devices found; using canonical fallback: %s", _MLU_DEVICE_CANDIDATES)
    return list(_MLU_DEVICE_CANDIDATES)


# ── CUDA device discovery ─────────────────────────────────────────────

_CUDA_DEVICE_CANDIDATES: list[str] = [
    "/dev/nvidia0",
    "/dev/nvidiactl",
    "/dev/nvidia-uvm",
]


def _discover_cuda_devices() -> list[str]:
    """Discover NVIDIA CUDA GPU devices via /dev/nvidia* glob."""
    try:
        found = sorted(
            p for p in _glob_module.glob("/dev/nvidia*")
            if Path(p).exists()
        )
        if found:
            logger.info("Discovered %d CUDA device(s) via /dev/nvidia*", len(found))
            return found
    except OSError:
        pass
    logger.warning("No CUDA devices found; using canonical fallback: %s", _CUDA_DEVICE_CANDIDATES)
    return list(_CUDA_DEVICE_CANDIDATES)


# ── Dispatch table ────────────────────────────────────────────────────

_DEVICE_DISCOVERY_DISPATCH: dict[str, Callable[[], list[str]]] = {
    "ascend_npu": lambda: _discover_ascend_npu_devices(),
    # Legacy shorthand — prefer ``"ascend_npu"`` for new code.
    "npu": lambda: _discover_ascend_npu_devices(),
    "ppu_cuda_compatible": lambda: _discover_ppu_devices(),
    "musa_muxi": lambda: _discover_musa_devices(),
    "rocm_amd": lambda: _discover_rocm_devices(),
    "mlu_cambrian": lambda: _discover_mlu_devices(),
    "cuda_nvidia": lambda: _discover_cuda_devices(),
}
"""Dispatch table mapping platform ids to device-discovery functions."""


def discover_devices(policy: object | None = None) -> list[str]:
    """Platform-policy-driven device discovery entry point.

    When *policy* is None, falls back to generic device discovery with a
    warning (callers should pass a PlatformPolicy for platform-specific
    discovery).

    When *policy* has an ``id`` attribute that matches a key in
    ``_DEVICE_DISCOVERY_DISPATCH``, the corresponding discovery function
    is invoked.  Otherwise the generic fallback is returned.
    """
    if policy is None:
        logger.warning(
            "No platform policy provided for device discovery — using generic fallback. "
            "Provide a PlatformPolicy for platform-specific discovery."
        )
        return _discover_generic_device()
    try:
        platform_id = getattr(policy, "id", None)
    except AttributeError:
        platform_id = None
    if platform_id and platform_id in _DEVICE_DISCOVERY_DISPATCH:
        discovery_func = _DEVICE_DISCOVERY_DISPATCH[platform_id]
        return discovery_func()  # type: ignore[operator]
    return _discover_generic_device()


# Deprecated backward-compat alias — prefer ``_discover_ascend_npu_devices``.
_discover_npu_devices = _discover_ascend_npu_devices


def _resolve_auto_marker(marker: str, policy: object | None = None) -> list[str]:
    """Resolve an ``auto:{platform_id}`` marker to discovered device paths.

    * ``"auto:npu"``, ``"auto:ppu_cuda_compatible"``, ``"auto:musa_muxi"``, … —
      dispatched via ``_DEVICE_DISCOVERY_DISPATCH`` by stripping the ``auto:``
      prefix and looking up the resulting platform ID.
    * When no dispatch key matches and *policy* is provided, falls through
      to ``discover_devices(policy)``.  Otherwise returns an empty list.

    Non-auto markers are NOT handled here — the caller should pass them
    through unchanged.
    """
    if not marker.startswith(_AUTO_MARKER_PREFIX):
        return [marker]
    platform_id = marker[len(_AUTO_MARKER_PREFIX):]
    if platform_id in _DEVICE_DISCOVERY_DISPATCH:
        discovered = _DEVICE_DISCOVERY_DISPATCH[platform_id]()
        logger.info("Resolved %s → %d device(s): %s", marker, len(discovered), discovered)
        return discovered
    if policy is not None:
        discovered = discover_devices(policy)
        logger.info("Resolved %s (via policy) → %d device(s): %s", marker, len(discovered), discovered)
        return discovered
    logger.warning("Unknown auto marker %r — no devices resolved", marker)
    return []


def _resolve_device_markers(devices: list[str], policy: object | None = None) -> list[str]:
    """Expand ``auto:{platform_id}`` markers in a device list to discovered paths.

    Explicit device paths are passed through unchanged.
    When the list is empty, returns the empty list unchanged.

    The optional *policy* argument is forwarded to ``discover_devices()``
    for platforms not yet in the dispatch table.
    """
    if not devices:
        return []
    resolved: list[str] = []
    for dev in devices:
        if dev.startswith(_AUTO_MARKER_PREFIX):
            resolved.extend(_resolve_auto_marker(dev, policy=policy))
        else:
            resolved.append(dev)
    return resolved


class ContainerNotFoundError(RuntimeError):
    pass


class ContainerNotRunningError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float


class ExecutionBackend(Protocol):
    def run(
        self,
        command: str | list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | float | None = None,
    ) -> ExecResult:
        ...

    def is_available(self) -> bool:
        ...

    def cleanup(self) -> None:
        ...

    def preflight(self) -> None:
        ...

    def probe_environment(self) -> dict[str, Any]:
        ...


class LocalBackend:
    """subprocess-backed implementation matching existing SEAM behaviour."""

    def run(
        self,
        command: str | list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | float | None = None,
    ) -> ExecResult:
        start = time.monotonic()
        run_env = None
        if env:
            run_env = {**__import__("os").environ, **env}
        if isinstance(command, str):
            completed = subprocess.run(
                command, shell=True, cwd=cwd, env=run_env,
                capture_output=True, text=True,
                timeout=timeout,
            )
        else:
            completed = subprocess.run(
                command, shell=False, cwd=cwd, env=run_env,
                capture_output=True, text=True,
                timeout=timeout,
            )
        elapsed = time.monotonic() - start
        return ExecResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration=round(elapsed, 3),
        )

    def is_available(self) -> bool:
        return True

    def cleanup(self) -> None:
        pass

    def preflight(self) -> None:
        pass

    def probe_environment(self) -> dict[str, Any]:
        return {"status": "local", "error": "probe not applicable in local mode"}

    def get_execution_context(
        self,
        cwd: str | None = None,
        command: str | list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        _ = cwd, env
        cmd_str = command
        if isinstance(command, list):
            cmd_str = " ".join(command)
        return {
            "execution_backend_mode": "local",
            "actual_execution_command": "(local execution; run entry_script directly)",
            "container_probe_command_prefix": "(local execution; no container probe command)",
            "container_name_or_id": "(local execution; no container)",
            "container_workdir": "(local execution; uses project cwd)",
            "host_project_dir": "(local execution; run entry_script directly)",
            "container_project_dir": "(local execution; run entry_script directly)",
        }


class ContainerBackend:
    """Docker/Podman container execution.

    ``config.source`` selects the behaviour:
    - ``image``: creates a new container from ``config.image`` on first ``run()``.
    - ``existing_container``: validates the named container exists and is running.
    """

    def __init__(self, config: ExecutionBackendConfig) -> None:
        if not isinstance(config, ExecutionBackendConfig):
            raise TypeError(
                f"ContainerBackend requires ExecutionBackendConfig, got {type(config).__name__}"
            )
        self.config = config
        self._container_id: str | None = None
        self._initialized = False
        self._runtime_cmd = "docker" if config.runtime == "docker" else "podman"
        self._host_project_dir: str | None = None

    def _resolve_candidate_images(self) -> list[str]:
        """Return the ordered list of candidate images, normalized.

        ``images`` list takes full precedence.  Single ``image`` string is a
        fallback.  Empty strings and ``"None"`` are filtered out.
        """
        if self.config.images:
            return [
                c for c in self.config.images
                if str(c).strip() and str(c).strip() != "None"
            ]
        if self.config.image:
            return [self.config.image]
        return []

    def _discover_local_images(self) -> list[str]:
        """Read-only listing of local container runtime images.

        Returns image names as reported by ``docker images`` / ``podman images``,
        excluding ``<none>`` tags and the ``REPOSITORY:TAG`` header.
        """
        try:
            result = subprocess.run(
                [self._runtime_cmd, "images", "--format", "{{.Repository}}:{{.Tag}}"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning("Image listing failed (%s): %s", self._runtime_cmd, result.stderr.strip())
                return []
            images = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and "<none>" not in line:
                    images.append(line)
            return images
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("Image listing error (%s): %s", self._runtime_cmd, exc)
            return []

    # -- lifecycle ---------------------------------------------------------

    def set_project_dir(self, project_dir: str) -> None:
        self._host_project_dir = str(Path(project_dir).resolve())

    def is_available(self) -> bool:
        try:
            subprocess.run(
                [self._runtime_cmd, "--version"],
                capture_output=True, check=True, timeout=10,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    def _create_container_from_image(self) -> None:
        """Create a container using normalized candidate images with sequential fallback."""
        candidates = self._resolve_candidate_images()
        if not candidates:
            raise ValueError("execution_backend.image or execution_backend.images is required when source=image")
        if len(candidates) == 1:
            self._do_create_container(candidates[0])
            return
        errors: list[tuple[str, str]] = []
        for candidate in candidates:
            try:
                self._do_create_container(candidate)
                return
            except RuntimeError as exc:
                errors.append((candidate, str(exc)))
                logger.warning("Container create failed with image %s: %s", candidate, exc)
        detail = "; ".join(f"{img}: {err}" for img, err in errors)
        raise RuntimeError(f"All images failed: {detail}")

    def _ensure_container(self) -> str:
        if self._container_id:
            return self._container_id
        if self.config.source == "existing_container":
            self._check_existing_container()
            assert self._container_id is not None
            return self._container_id
        self._create_container_from_image()
        assert self._container_id is not None
        return self._container_id

    def _create_selected_container(self, image_name: str) -> None:
        """Create a container from a specific image chosen by auto-selection.

        Used by the ``mode: auto`` path after the agent selects an image from
        the candidate list.  The chosen image is stored on config before calling
        the normal creation path so that all downstream code continues to use
        ``self.config.image``.
        """
        self.config.image = image_name
        self._create_container_from_image()

    def _do_create_container(self, image_name: str) -> None:
        """Create a single container from a specific image name."""
        run_id = str(int(time.monotonic() * 1000))
        cname = f"{self.config.container_name_prefix}-{run_id}"
        cmd: list[str] = [self._runtime_cmd, "run", "-d", "--name", cname]

        resolved_devices = _resolve_device_markers(self.config.devices)
        for dev in resolved_devices:
            cmd.extend(["--device", dev])

        proj = self._host_project_dir or "."
        cmd.extend(["-v", f"{proj}:{self.config.container_workdir}:rw"])
        for vol in self.config.volumes:
            cmd.extend(["-v", vol])
        for k, v in self.config.env_vars.items():
            cmd.extend(["-e", f"{k}={v}"])
        if self.config.network_mode:
            cmd.extend(["--network", self.config.network_mode])
        for flag in self.config.runtime_flags:
            cmd.append(flag)
        if self.config.cleanup:
            cmd.append("--rm")

        cmd.extend([image_name, "tail", "-f", "/dev/null"])

        logger.info("Container create: %s", " ".join(cmd[:6]))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create container: {result.stderr.strip()}"
            )
        self._container_id = result.stdout.strip()
        self._initialized = True
        logger.info("Container created: %s", self._container_id)

    def _check_existing_container(self) -> None:
        cname = self.config.container_name
        if not cname:
            raise ContainerNotFoundError(
                f"Container name is empty. {_CONTAINER_NOT_FOUND_MSG}"
            )

        result = subprocess.run(
            [self._runtime_cmd, "inspect", "--format", "{{.State.Status}}", cname],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise ContainerNotFoundError(
                f"Container '{cname}' not found. {result.stderr.strip()}"
            )
        status = result.stdout.strip()
        if status != "running":
            raise ContainerNotRunningError(
                f"Container '{cname}' status is '{status}', expected 'running'"
            )

        if self.config.required_devices:
            for dev in self.config.required_devices:
                check = subprocess.run(
                    [self._runtime_cmd, "exec", cname, "test", "-e", dev],
                    capture_output=True, timeout=10,
                )
                if check.returncode != 0:
                    logger.warning(
                        "Required device %s not found in container %s", dev, cname
                    )

        if self.config.required_env_vars:
            env_result = subprocess.run(
                [self._runtime_cmd, "exec", cname, "env"],
                capture_output=True, text=True, timeout=10,
            )
            container_env = set()
            if env_result.returncode == 0:
                for line in env_result.stdout.splitlines():
                    if "=" in line:
                        container_env.add(line.split("=", 1)[0])
            for var in self.config.required_env_vars:
                if var not in container_env:
                    logger.warning(
                        "Required env var %s not found in container %s", var, cname
                    )

        self._container_id = cname
        self._initialized = True
        logger.info("Existing container validated: %s", cname)

    def _rewrite_host_path(self, path_str: str) -> str:
        if not self._host_project_dir:
            return path_str
        host = str(Path(path_str).resolve()) if path_str else path_str
        if host.startswith(self._host_project_dir):
            rel = Path(host).relative_to(self._host_project_dir)
            return str(Path(self.config.container_workdir) / rel)
        return path_str

    def _rewrite_single_path(self, token: str) -> str:
        if not self._host_project_dir:
            return token
        if token.startswith(self._host_project_dir):
            rel = token[len(self._host_project_dir):].lstrip("/")
            return str(Path(self.config.container_workdir) / rel) if rel else self.config.container_workdir
        return token

    def _rewrite_command_paths(self, command: str) -> str:
        if not self._host_project_dir:
            return command
        host_dir = self._host_project_dir
        container_dir = self.config.container_workdir
        try:
            tokens = shlex.split(command)
        except ValueError:
            return command
        rewritten = []
        for token in tokens:
            path = Path(token)
            try:
                resolved = str(path.resolve())
            except OSError:
                resolved = token
            if resolved.startswith(host_dir):
                rel = Path(resolved).relative_to(host_dir)
                rewritten.append(str(Path(container_dir) / rel))
            else:
                rewritten.append(token)
        return shlex.join(rewritten)

    def run(
        self,
        command: str | list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | float | None = None,
    ) -> ExecResult:
        cid = self._ensure_container()
        exec_cmd: list[str] = [self._runtime_cmd, "exec", "-i"]
        workdir = self.config.container_workdir
        if cwd and self._host_project_dir:
            try:
                host = str(Path(cwd).resolve())
                if host.startswith(self._host_project_dir):
                    rel = Path(host).relative_to(self._host_project_dir)
                    workdir = str(Path(self.config.container_workdir) / rel)
            except (ValueError, OSError):
                pass
        if workdir:
            exec_cmd.extend(["-w", workdir])
        if env:
            for k, v in env.items():
                exec_cmd.extend(["-e", f"{k}={v}"])

        if isinstance(command, list):
            rewritten = [self._rewrite_single_path(token) for token in command]
            exec_cmd.extend([cid] + rewritten)
        else:
            rewritten = self._rewrite_command_paths(command)
            exec_cmd.extend([cid, "bash", "-c", rewritten])

        effective_timeout = timeout or self.config.timeout
        start = time.monotonic()
        proc = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=effective_timeout,
        )
        elapsed = time.monotonic() - start
        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration=round(elapsed, 3),
        )

    def cleanup(self) -> None:
        if self.config.source == "existing_container":
            return
        if not self.config.cleanup or not self._container_id:
            return
        try:
            subprocess.run(
                [self._runtime_cmd, "stop", self._container_id],
                capture_output=True, timeout=30,
            )
        except Exception as exc:
            logger.warning("Container stop failed: %s", exc)
        try:
            subprocess.run(
                [self._runtime_cmd, "rm", self._container_id],
                capture_output=True, timeout=30,
            )
        except Exception as exc:
            logger.warning("Container rm failed: %s", exc)
        self._container_id = None

    # -- prompt context helpers (no container creation) -------------------------

    def describe_command(
        self,
        command: str | list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Build a readable docker/podman exec description WITHOUT creating a container.

        This is safe to call during prompt rendering — it never invokes subprocess.
        """
        cid = self._container_id
        if cid is None:
            if self.config.source == "existing_container":
                cid = self.config.container_name or "(will be created on first execution)"
            else:
                cid = "(will be created on first execution)"

        exec_parts: list[str] = [self._runtime_cmd, "exec", "-i"]

        workdir = self.config.container_workdir
        if cwd and self._host_project_dir:
            try:
                host = str(Path(cwd).resolve())
                if host.startswith(self._host_project_dir):
                    rel = Path(host).relative_to(self._host_project_dir)
                    workdir = str(Path(self.config.container_workdir) / rel)
            except (ValueError, OSError):
                pass
        if workdir:
            exec_parts.extend(["-w", workdir])
        if env:
            for k, v in sorted(env.items()):
                exec_parts.extend(["-e", f"{k}={v}"])

        exec_parts.append(cid)

        if isinstance(command, list):
            rewritten = [self._rewrite_single_path(token) for token in command]
            exec_parts.extend(rewritten)
        else:
            rewritten = self._rewrite_command_paths(command)
            exec_parts.extend(["bash", "-c", rewritten])

        return " ".join(exec_parts)

    def preflight(self) -> None:
        """Eagerly create or validate the container (idempotent).

        - For ``source=image``: creates the new exclusive container via
          ``_ensure_container()``.
        - For ``source=existing_container``: validates the named container
          exists and is running; cleanup remains no-op.

        Calling preflight() then run() will NOT create a second container
        because ``_ensure_container()`` is itself idempotent.
        """
        self._ensure_container()

    def probe_environment(self) -> dict[str, Any]:
        """Best-effort probe of the container's runtime environment.

        Returns structured facts regardless of probe success.  If the
        container probe command fails the dict still contains status/error
        keys so callers do not crash after a successful preflight.

        Does NOT create the container; must be called after preflight().
        """
        cid = self._container_id
        result: dict[str, Any] = {"container_id": cid or "(none)"}
        if cid is None:
            result["status"] = "skipped"
            result["error"] = "Container not created — call preflight() first"
            return result

        probe_script = """
import json
import os
import platform
import sys

facts = {
    "status": "ok",
    "interpreter_path": sys.executable,
    "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    "platform": platform.system(),
    "platform_machine": platform.machine(),
    "cwd": os.getcwd(),
    "env_keys": sorted(os.environ.keys()),
}
try:
    import torch
    facts["torch_version"] = torch.__version__
    facts["torch_cuda_available"] = getattr(torch.cuda, "is_available", lambda: False)()
    facts["torch_device_count"] = getattr(torch.cuda, "device_count", lambda: 0)()
    facts["torch_npu_available"] = getattr(torch.npu, "is_available", lambda: False)()
    facts["torch_npu_device_count"] = getattr(torch.npu, "device_count", lambda: 0)()
    facts["torch_musa_available"] = getattr(torch.musa, "is_available", lambda: False)()
    facts["torch_musa_device_count"] = getattr(torch.musa, "device_count", lambda: 0)()
    facts["torch_mlu_available"] = getattr(torch.mlu, "is_available", lambda: False)()
    facts["torch_mlu_device_count"] = getattr(torch.mlu, "device_count", lambda: 0)()
except Exception:
    facts["torch_version"] = "not_installed"
    facts["torch_cuda_available"] = False
    facts["torch_device_count"] = 0
    facts["torch_npu_available"] = False
    facts["torch_npu_device_count"] = 0
    facts["torch_musa_available"] = False
    facts["torch_musa_device_count"] = 0
    facts["torch_mlu_available"] = False
    facts["torch_mlu_device_count"] = 0
print(json.dumps(facts))
""".strip()

        _py_versions = " ".join(
            f"python3.{v}"
            for v in range(PYTHON_VERSION_CANDIDATES_MAX - 1, PYTHON_VERSION_CANDIDATES_MIN - 1, -1)
        )
        shell_probe = f"""
set -eu
probe_python=""
for candidate in python3 python {_py_versions}; do
    if command -v "$candidate" >/dev/null 2>&1; then
        probe_python="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$probe_python" ]; then
    printf '%s\n' '{{"status":"probe_failed","error":"No Python interpreter found on container PATH"}}'
    exit 0
fi
exec "$probe_python" -c "$SEAM_CONTAINER_PROBE_SCRIPT"
""".strip()

        probe_cmd: list[str] = [
            self._runtime_cmd, "exec", "-i", "--workdir",
            self.config.container_workdir,
            "-e", f"SEAM_CONTAINER_PROBE_SCRIPT={probe_script}",
            cid, "sh", "-lc", shell_probe,
        ]

        try:
            proc = subprocess.run(
                probe_cmd, capture_output=True, text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                facts = json.loads(proc.stdout.strip().splitlines()[-1])
                result.update(facts)
            else:
                result["status"] = "probe_failed"
                result["error"] = proc.stderr.strip()[:500]
        except subprocess.TimeoutExpired:
            result["status"] = "probe_timeout"
            result["error"] = "Probe timed out after 30 s"
        except json.JSONDecodeError:
            result["status"] = "parse_error"
            result["error"] = f"Unexpected stdout: {proc.stdout[:200]!r}"
        except Exception as exc:
            result["status"] = "probe_failed"
            result["error"] = str(exc)

        return result

    def get_execution_context(
        self,
        cwd: str | None = None,
        command: str | list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Return a dict of execution context fields for prompt injection.

        - Does NOT create or start any container.
        - Returns ``(local execution; ...)`` placeholder strings if project dir
          is not yet bound to the backend.
        """
        host_proj = self._host_project_dir or "(not yet set)"
        container_proj = self.config.container_workdir
        if cwd and self._host_project_dir:
            try:
                host = str(Path(cwd).resolve())
                if host.startswith(self._host_project_dir):
                    rel = Path(host).relative_to(self._host_project_dir)
                    container_proj = str(Path(self.config.container_workdir) / rel)
            except (ValueError, OSError):
                pass

        cid = self._container_id
        if cid is None:
            if self.config.source == "existing_container":
                cid = self.config.container_name or "(will be created on first execution)"
            else:
                cid = "(will be created on first execution)"

        description = "(no specific command provided)"
        if command is not None:
            description = self.describe_command(command, cwd=cwd, env=env)

        probe_parts = [self._runtime_cmd, "exec", "-i"]
        if container_proj:
            probe_parts.extend(["-w", container_proj])
        probe_parts.append(cid)

        return {
            "execution_backend_mode": "container",
            "actual_execution_command": description,
            "container_probe_command_prefix": " ".join(probe_parts),
            "container_name_or_id": cid,
            "container_workdir": self.config.container_workdir,
            "host_project_dir": host_proj,
            "container_project_dir": container_proj,
        }


_LOCAL_CTX: dict[str, str] = {
    "execution_backend_mode": "local",
    "actual_execution_command": "(local execution; run entry_script directly)",
    "container_probe_command_prefix": "(local execution; no container probe command)",
    "container_name_or_id": "(local execution; no container)",
    "container_workdir": "(local execution; uses project cwd)",
    "host_project_dir": "(local execution; run entry_script directly)",
    "container_project_dir": "(local execution; run entry_script directly)",
}


def get_execution_context(
    backend: ExecutionBackend | None,
    *,
    command: str | list[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return execution-context dict safe for prompt injection.

    When backend is None or LocalBackend, returns harmless placeholder strings.
    When backend is ContainerBackend, returns real docker/podman command description.

    Does NOT create any container.
    """
    if backend is None or isinstance(backend, LocalBackend):
        return dict(_LOCAL_CTX)
    if hasattr(backend, "get_execution_context"):
        return backend.get_execution_context(command=command, cwd=cwd, env=env)
    return dict(_LOCAL_CTX)


def auto_select_backend(config: ExecutionBackendConfig) -> ExecutionBackendConfig:
    """Heuristic for ``mode=auto``: container if runtime available, else local."""
    runtime_cmd = "docker" if config.runtime == "docker" else "podman"
    try:
        subprocess.run(
            [runtime_cmd, "--version"],
            capture_output=True, check=True, timeout=10,
        )
        return ExecutionBackendConfig(
            mode="container",
            source=config.source,
            runtime=config.runtime,
            image=config.image,
            images=config.images,
            container_name=config.container_name,
            container_name_prefix=config.container_name_prefix,
            devices=config.devices,
            volumes=config.volumes,
            env_vars=config.env_vars,
            required_env_vars=config.required_env_vars,
            required_devices=config.required_devices,
            container_workdir=config.container_workdir,
            network_mode=config.network_mode,
            runtime_flags=config.runtime_flags,
            timeout=config.timeout,
            cleanup=config.cleanup,
        )
    except (subprocess.SubprocessError, OSError):
        return ExecutionBackendConfig(mode="local")


def get_execution_environment_context(
    backend: ExecutionBackend | None,
    probe_facts: dict[str, Any] | None = None,
) -> str:
    is_container = (
        backend is not None
        and not isinstance(backend, LocalBackend)
        and hasattr(backend, "get_execution_context")
    )
    if is_container:
        parts: list[str] = []
        parts.append("## Execution Environment Context")
        parts.append("")
        parts.append("- **execution_backend_mode**: container")
        parts.append("- **Target runtime**: the target runtime phase executes inside the framework-created container.")
        host_proj = getattr(backend, "_host_project_dir", None) or "(not yet set)"
        parts.append(f"- **Host project dir**: {host_proj}")
        container_proj = getattr(getattr(backend, "config", None), "container_workdir", "(unknown)")
        parts.append(f"- **Container project dir**: {container_proj}")
        if probe_facts and probe_facts.get("status") == "ok":
            probe_summary = []
            for key in ("interpreter_path", "python_version", "torch_version", "platform", "cwd"):
                if key in probe_facts:
                    probe_summary.append(f"{key}: {probe_facts[key]}")
            if probe_summary:
                parts.append(f"- **Container probe facts**: {', '.join(probe_summary)}")
            interp = probe_facts.get("interpreter_path", "a Python interpreter discovered on the container PATH")
            parts.append(f"- **Probe interpreter**: the probe ran `{interp}` inside the container; this command is confirmed callable in the target runtime.")
        elif probe_facts:
            status = probe_facts.get("status", "unknown")
            error = probe_facts.get("error", "")
            extra = f" — {error}" if error else ""
            parts.append(f"- **Container probe**: status={status}{extra}")
        parts.append("- **Tooling note**: OpenCode file tools (read, grep, etc.) observe the host filesystem, not the container. For target-runtime execution, use paths and commands valid inside the target container environment.")
        return "\n".join(parts)

    # LocalBackend or None
    return (
        "## Execution Environment Context\n\n"
        "- **execution_backend_mode**: local\n"
        "- **Target runtime**: the target runtime phase executes on the host/local environment directly.\n"
        "- **Tooling note**: OpenCode tools and the target runtime observe the same local environment.\n"
        "  File paths, Python interpreters, and commands you see are exactly what the target runtime will use."
    )


def get_container_prompt_context(
    backend: ExecutionBackend | None,
    probe_facts: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build a prompt-safe context dict from a backend and optional probe facts.

    Returns a merged dict with:
    - ``execution_backend_mode``, ``container_name_or_id``, ``container_workdir``,
      ``host_project_dir``, ``container_project_dir`` from the backend.
    - ``container_env_facts`` (JSON) and ``container_<key>`` entries from probe.
    - Empty dict when backend is None or local.
    """
    if backend is None or isinstance(backend, LocalBackend):
        return {}
    ctx: dict[str, str] = {}
    for k, v in get_execution_context(backend).items():
        ctx[k] = str(v)
    if probe_facts:
        ctx["container_env_facts"] = json.dumps(probe_facts, ensure_ascii=False, default=str)
        for key in ("interpreter_path", "python_version", "platform", "platform_machine", "cwd", "torch_version"):
            if key in probe_facts:
                ctx[f"container_{key}"] = str(probe_facts[key])
    return ctx
