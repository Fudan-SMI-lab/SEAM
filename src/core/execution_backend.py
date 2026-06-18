from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from core.types import ExecutionBackendConfig

logger = logging.getLogger(__name__)

_CONTAINER_NOT_FOUND_MSG = (
    "execution_backend.container_name is required when source=existing_container"
)


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
    ) -> ExecResult: ...

    def is_available(self) -> bool: ...

    def cleanup(self) -> None: ...

    def preflight(self) -> None: ...

    def probe_environment(self) -> dict[str, Any]: ...

    def recreate_execution_environment(self, reason: str = "") -> dict[str, Any]: ...


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
                command,
                shell=True,
                cwd=cwd,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        else:
            completed = subprocess.run(
                command,
                shell=False,
                cwd=cwd,
                env=run_env,
                capture_output=True,
                text=True,
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

    def recreate_execution_environment(self, reason: str = "") -> dict[str, Any]:
        raise RuntimeError(
            "Execution environment reset is only supported for image-backed "
            "container backends"
        )

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
            "container_probe_command_prefix": (
                "(local execution; no container probe command)"
            ),
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
                f"ContainerBackend requires ExecutionBackendConfig, "
                f"got {type(config).__name__}"
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
                c
                for c in self.config.images
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
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(
                    "Image listing failed (%s): %s",
                    self._runtime_cmd,
                    result.stderr.strip(),
                )
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
                capture_output=True,
                check=True,
                timeout=10,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    def _create_container_from_image(self) -> None:
        """Create a container using normalized candidate images with sequential fallback."""
        candidates = self._resolve_candidate_images()
        if not candidates:
            raise ValueError(
                "execution_backend.image or execution_backend.images is required "
                "when source=image"
            )
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
                logger.warning(
                    "Container create failed with image %s: %s", candidate, exc
                )
        detail = "; ".join(f"{img}: {err}" for img, err in errors)
        raise RuntimeError(f"All images failed: {detail}")

    def _ensure_container(self) -> str:
        if self._container_id:
            if self._revalidate_container():
                return self._container_id
            # For source=image, _revalidate_container() cleared _container_id
            # and _initialized; fall through to recreate.
            # For source=existing_container, _revalidate_container() raised
            # ContainerNotFoundError or ContainerNotRunningError.
        if self.config.source == "existing_container":
            self._check_existing_container()
            assert self._container_id is not None
            return self._container_id
        self._create_container_from_image()
        assert self._container_id is not None
        return self._container_id

    def _revalidate_container(self) -> bool:
        """Inspect the cached container before reuse.

        Returns True if the container is running and can be reused.

        For ``source=image``: if the container is missing or not running,
        clears ``_container_id`` and ``_initialized``, then returns False
        so ``_ensure_container()`` recreates it.

        For ``source=existing_container``: raises ``ContainerNotFoundError``
        or ``ContainerNotRunningError``.  Never attempts to recreate a
        user-owned container.
        """
        cid = self._container_id
        assert cid is not None  # guarded by caller
        result = subprocess.run(
            [self._runtime_cmd, "inspect", "--format", "{{.State.Status}}", cid],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            if self.config.source == "existing_container":
                raise ContainerNotFoundError(
                    f"Cached container '{cid}' no longer exists. "
                    f"{result.stderr.strip()}"
                )
            logger.warning("Cached container '%s' not found — will recreate", cid)
            self._container_id = None
            self._initialized = False
            return False

        status = result.stdout.strip()
        if status != "running":
            if self.config.source == "existing_container":
                raise ContainerNotRunningError(
                    f"Cached container '{cid}' status is '{status}', "
                    f"expected 'running'"
                )
            logger.warning(
                "Cached container '%s' status is '%s' — will recreate",
                cid,
                status,
            )
            self._container_id = None
            self._initialized = False
            return False

        return True

    def _create_selected_container(self, image_name: str) -> None:
        """Create a container from a specific image chosen by auto-selection.

        Used by the ``mode: auto`` path after the agent selects an image from
        the candidate list.  The chosen image is stored on config before calling
        the normal creation path so that all downstream code continues to use
        ``self.config.image``.
        """
        self.config.image = image_name
        self._create_container_from_image()

    def _inspect_container(self, container_id: str) -> dict[str, Any]:
        result = subprocess.run(
            [self._runtime_cmd, "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ContainerNotFoundError(
                f"Container '{container_id}' not found. {result.stderr.strip()}"
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Container inspect output was not valid JSON: {exc}"
            ) from exc
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        raise RuntimeError("Container inspect output did not describe a container")

    def _verify_framework_owned_image_container(
        self, container_id: str
    ) -> dict[str, Any]:
        inspect_data = self._inspect_container(container_id)
        name = str(inspect_data.get("Name") or "").lstrip("/")
        prefix = str(self.config.container_name_prefix or "")
        if prefix and name and not name.startswith(f"{prefix}-"):
            raise RuntimeError(
                f"Refusing to reset container '{container_id}': name '{name}' "
                f"does not match framework prefix '{prefix}-'"
            )

        config_data = (
            inspect_data.get("Config")
            if isinstance(inspect_data.get("Config"), dict)
            else {}
        )
        inspected_image = str(config_data.get("Image") or "")
        candidates = set(self._resolve_candidate_images())
        if inspected_image and candidates and inspected_image not in candidates:
            raise RuntimeError(
                f"Refusing to reset container '{container_id}': image "
                f"'{inspected_image}' is not one of configured images"
            )

        if self._host_project_dir:
            expected_source = str(Path(self._host_project_dir).resolve())
            expected_dest = str(self.config.container_workdir).rstrip("/")
            mounts = (
                inspect_data.get("Mounts")
                if isinstance(inspect_data.get("Mounts"), list)
                else []
            )
            matching_mount = False
            for mount in mounts:
                if not isinstance(mount, dict):
                    continue
                source = str(mount.get("Source") or "")
                destination = str(mount.get("Destination") or "").rstrip("/")
                try:
                    source = str(Path(source).resolve()) if source else source
                except OSError:
                    pass
                if source == expected_source and destination == expected_dest:
                    matching_mount = True
                    break
            if mounts and not matching_mount:
                raise RuntimeError(
                    f"Refusing to reset container '{container_id}': expected "
                    f"project mount {expected_source}:{expected_dest} not found"
                )

        working_dir = str(config_data.get("WorkingDir") or "")
        if working_dir and working_dir.rstrip("/") != str(
            self.config.container_workdir
        ).rstrip("/"):
            raise RuntimeError(
                f"Refusing to reset container '{container_id}': workdir "
                f"'{working_dir}' does not match '{self.config.container_workdir}'"
            )
        return inspect_data

    def recreate_execution_environment(self, reason: str = "") -> dict[str, Any]:
        if self.config.source != "image":
            raise RuntimeError(
                "Execution environment reset is only supported for source=image "
                "containers"
            )
        old_container_id = self._container_id
        if not old_container_id:
            raise RuntimeError(
                "Execution environment reset requires an existing "
                "framework-created container id"
            )

        inspect_data = self._verify_framework_owned_image_container(old_container_id)
        config_data = (
            inspect_data.get("Config")
            if isinstance(inspect_data.get("Config"), dict)
            else {}
        )
        image = str(config_data.get("Image") or self.config.image or "")

        stop_result = subprocess.run(
            [self._runtime_cmd, "stop", old_container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if stop_result.returncode != 0:
            raise RuntimeError(
                f"Failed to stop container '{old_container_id}' before reset: "
                f"{stop_result.stderr.strip()}"
            )
        rm_result = subprocess.run(
            [self._runtime_cmd, "rm", old_container_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if rm_result.returncode != 0:
            rm_err = rm_result.stderr.strip()
            if not (
                self.config.cleanup
                and ("No such" in rm_err or "not found" in rm_err.lower())
            ):
                raise RuntimeError(
                    f"Failed to remove container '{old_container_id}' before reset: "
                    f"{rm_err}"
                )

        self._container_id = None
        self._initialized = False
        self._create_container_from_image()
        assert self._container_id is not None
        return {
            "old_container_id": old_container_id,
            "new_container_id": self._container_id,
            "source": self.config.source,
            "image": image or self.config.image or "",
            "reason": reason,
            "preserved_notes": [
                "host project directory remains mounted into the recreated container",
                (
                    "entry script command and workflow state are preserved by the "
                    "executor"
                ),
                (
                    "configured devices, env vars, network mode, runtime flags, and "
                    "volumes are reused"
                ),
            ],
            "lost_notes": [
                (
                    "in-container package installs and edits outside mounted volumes "
                    "are discarded"
                ),
                "process state from the old container is discarded",
            ],
        }

    def _do_create_container(self, image_name: str) -> None:
        """Create a single container from a specific image name."""
        run_id = str(int(time.monotonic() * 1000))
        cname = f"{self.config.container_name_prefix}-{run_id}"
        cmd: list[str] = [self._runtime_cmd, "run", "-d", "--name", cname]

        for dev in self.config.devices:
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

        logger.info(
            "Container create attempt: runtime=%s name=%s image=%s",
            self._runtime_cmd,
            cname,
            image_name,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create container: {result.stderr.strip()}")
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
            capture_output=True,
            text=True,
            timeout=30,
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
                    capture_output=True,
                    timeout=10,
                )
                if check.returncode != 0:
                    logger.warning(
                        "Required device %s not found in container %s",
                        dev,
                        cname,
                    )

        if self.config.required_env_vars:
            env_result = subprocess.run(
                [self._runtime_cmd, "exec", cname, "env"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            container_env = set()
            if env_result.returncode == 0:
                for line in env_result.stdout.splitlines():
                    if "=" in line:
                        container_env.add(line.split("=", 1)[0])
            for var in self.config.required_env_vars:
                if var not in container_env:
                    logger.warning(
                        "Required env var %s not found in container %s",
                        var,
                        cname,
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
            rel = token[len(self._host_project_dir) :].lstrip("/")
            if rel:
                return str(Path(self.config.container_workdir) / rel)
            return self.config.container_workdir
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
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
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
                capture_output=True,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("Container stop failed: %s", exc)
        try:
            subprocess.run(
                [self._runtime_cmd, "rm", self._container_id],
                capture_output=True,
                timeout=30,
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
                cid = self.config.container_name or (
                    "(will be created on first execution)"
                )
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

        probe_script = (
            "import json\n"
            "import os\n"
            "import platform\n"
            "import sys\n"
            "\n"
            "facts = {\n"
            '    "status": "ok",\n'
            '    "interpreter_path": sys.executable,\n'
            '    "python_version": f"{sys.version_info.major}.'
            f'{sys.version_info.minor}.{sys.version_info.micro}",\n'
            '    "platform": platform.system(),\n'
            '    "platform_machine": platform.machine(),\n'
            '    "cwd": os.getcwd(),\n'
            '    "env_keys": sorted(os.environ.keys()),\n'
            "}\n"
            "try:\n"
            "    import torch\n"
            '    facts["torch_version"] = torch.__version__\n'
            '    facts["torch_cuda_available"] = getattr(torch.cuda, '
            '"is_available", lambda: False)()\n'
            '    facts["torch_device_count"] = getattr(torch.cuda, '
            '"device_count", lambda: 0)()\n'
            "except Exception:\n"
            '    facts["torch_version"] = "not_installed"\n'
            '    facts["torch_cuda_available"] = False\n'
            '    facts["torch_device_count"] = 0\n'
            "print(json.dumps(facts))\n"
        )

        shell_probe = (
            "set -eu\n"
            'probe_python=""\n'
            "for candidate in python3 python python3.12 python3.11 "
            "python3.10 python3.9 python3.8; do\n"
            '    if command -v "$candidate" >/dev/null 2>&1; then\n'
            '        probe_python="$(command -v "$candidate")"\n'
            "        break\n"
            "    fi\n"
            "done\n"
            'if [ -z "$probe_python" ]; then\n'
            '    printf \'%s\\n\' \'{"status":"probe_failed",'
            '"error":"No Python interpreter found on container PATH"}\'\n'
            "    exit 0\n"
            "fi\n"
            'exec "$probe_python" -c "$SEAM_CONTAINER_PROBE_SCRIPT"\n'
        )

        probe_cmd: list[str] = [
            self._runtime_cmd,
            "exec",
            "-i",
            "--workdir",
            self.config.container_workdir,
            "-e",
            f"SEAM_CONTAINER_PROBE_SCRIPT={probe_script}",
            cid,
            "sh",
            "-lc",
            shell_probe,
        ]

        try:
            proc = subprocess.run(
                probe_cmd,
                capture_output=True,
                text=True,
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
                cid = self.config.container_name or (
                    "(will be created on first execution)"
                )
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
    "container_probe_command_prefix": ("(local execution; no container probe command)"),
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
            capture_output=True,
            check=True,
            timeout=10,
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
        parts.append(
            "- **Target runtime**: the target runtime phase executes inside the "
            "framework-created container."
        )
        host_proj = getattr(backend, "_host_project_dir", None) or "(not yet set)"
        parts.append(f"- **Host project dir**: {host_proj}")
        container_proj = getattr(
            getattr(backend, "config", None), "container_workdir", "(unknown)"
        )
        parts.append(f"- **Container project dir**: {container_proj}")
        if probe_facts and probe_facts.get("status") == "ok":
            probe_summary = []
            for key in (
                "interpreter_path",
                "python_version",
                "torch_version",
                "platform",
                "cwd",
            ):
                if key in probe_facts:
                    probe_summary.append(f"{key}: {probe_facts[key]}")
            if probe_summary:
                parts.append(f"- **Container probe facts**: {', '.join(probe_summary)}")
            interp = probe_facts.get(
                "interpreter_path",
                "a Python interpreter discovered on the container PATH",
            )
            parts.append(
                "- **Probe interpreter**: the probe ran `{}` inside the container; "
                "this command is confirmed callable in the target runtime.".format(
                    interp
                )
            )
        elif probe_facts:
            status = probe_facts.get("status", "unknown")
            error = probe_facts.get("error", "")
            extra = f" — {error}" if error else ""
            parts.append(f"- **Container probe**: status={status}{extra}")
        parts.append(
            "- **Tooling note**: OpenCode file tools (read, grep, etc.) observe "
            "the host filesystem, not the container. For target-runtime execution, "
            "use paths and commands valid inside the target container environment."
        )
        return "\n".join(parts)

    # LocalBackend or None
    return (
        "## Execution Environment Context\n\n"
        "- **execution_backend_mode**: local\n"
        "- **Target runtime**: the target runtime phase executes on the host/local "
        "environment directly.\n"
        "- **Tooling note**: OpenCode tools and the target runtime observe the same "
        "local environment.\n"
        "  File paths, Python interpreters, and commands you see are exactly what "
        "the target runtime will use."
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
        ctx["container_env_facts"] = json.dumps(
            probe_facts, ensure_ascii=False, default=str
        )
        for key in (
            "interpreter_path",
            "python_version",
            "platform",
            "platform_machine",
            "cwd",
            "torch_version",
        ):
            if key in probe_facts:
                ctx[f"container_{key}"] = str(probe_facts[key])
    return ctx
