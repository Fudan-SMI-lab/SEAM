"""Unit and mock tests for the container execution backend."""

from __future__ import annotations

import logging
import os
import subprocess
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from core.types import ExecutionBackendConfig, WorkflowDefinition, PhaseDefinition
from core.config import load_workflow
from core.execution_backend import (
    ContainerBackend,
    ContainerNotFoundError,
    ContainerNotRunningError,
    ExecResult,
    LocalBackend,
    auto_select_backend,
    get_container_prompt_context,
)
from core.workflow_executor import WorkflowExecutor
from core.artifact_store import ArtifactStore

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / "workflows"

# ── ExecutionBackendConfig parsing ─────────────────────────────────────────


class TestExecutionBackendConfigParsing:
    def test_from_dict_none_returns_local(self):
        cfg = ExecutionBackendConfig.from_dict(None)
        assert cfg.mode == "local"

    def test_from_dict_empty_returns_local(self):
        cfg = ExecutionBackendConfig.from_dict({})
        assert cfg.mode == "local"

    def test_from_dict_local_ignores_fields(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "local", "image": "ignored", "container_name": "ignored"}
        )
        assert cfg.mode == "local"
        assert cfg.image is None

    def test_from_dict_container_defaults(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "ascendhub:24.03"}
        )
        assert cfg.mode == "container"
        assert cfg.source == "image"
        assert cfg.image == "ascendhub:24.03"
        assert cfg.runtime == "docker"
        assert cfg.container_workdir == "/workspace"
        assert cfg.timeout == 7200
        assert cfg.cleanup is True

    def test_from_dict_container_custom_source_image(self):
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "image",
                "image": "pytorch:latest",
                "runtime": "podman",
                "container_workdir": "/app",
                "timeout": 3600,
                "cleanup": False,
            }
        )
        assert cfg.source == "image"
        assert cfg.runtime == "podman"
        assert cfg.container_workdir == "/app"
        assert cfg.timeout == 3600
        assert cfg.cleanup is False

    def test_from_dict_existing_container(self):
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
                "required_env_vars": ["ASCEND_VISIBLE_DEVICES"],
                "required_devices": ["/dev/davinci_manager"],
            }
        )
        assert cfg.source == "existing_container"
        assert cfg.container_name == "my-dev-01"
        assert cfg.required_env_vars == ["ASCEND_VISIBLE_DEVICES"]
        assert cfg.required_devices == ["/dev/davinci_manager"]

    def test_existing_container_without_name_raises(self):
        with pytest.raises(ValueError, match="container_name is required"):
            ExecutionBackendConfig.from_dict(
                {"mode": "container", "source": "existing_container"}
            )

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid execution_backend.mode"):
            ExecutionBackendConfig.from_dict({"mode": "invalid"})

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="Invalid execution_backend.source"):
            ExecutionBackendConfig.from_dict(
                {"mode": "container", "source": "invalid", "image": "x"}
            )

    def test_all_fields_populated(self):
        raw = {
            "mode": "container",
            "source": "image",
            "runtime": "podman",
            "image": "test:latest",
            "container_name_prefix": "seam-test",
            "devices": ["/dev/davinci_manager"],
            "volumes": ["/data:/data:ro"],
            "env_vars": {"FOO": "bar"},
            "required_env_vars": ["FOO"],
            "required_devices": ["/dev/davinci_manager"],
            "container_workdir": "/opt",
            "network_mode": "host",
            "runtime_flags": ["--cap-add=SYS_PTRACE"],
            "timeout": 600,
            "cleanup": False,
        }
        cfg = ExecutionBackendConfig.from_dict(raw)
        assert cfg.mode == "container"
        assert cfg.source == "image"
        assert cfg.runtime == "podman"
        assert cfg.image == "test:latest"
        assert cfg.container_name_prefix == "seam-test"
        assert cfg.devices == ["/dev/davinci_manager"]
        assert cfg.volumes == ["/data:/data:ro"]
        assert cfg.env_vars == {"FOO": "bar"}
        assert cfg.required_env_vars == ["FOO"]
        assert cfg.required_devices == ["/dev/davinci_manager"]
        assert cfg.container_workdir == "/opt"
        assert cfg.network_mode == "host"
        assert cfg.runtime_flags == ["--cap-add=SYS_PTRACE"]
        assert cfg.timeout == 600
        assert cfg.cleanup is False


# ── Config integration: load_workflow ──────────────────────────────────────


class TestConfigIntegration:
    def test_workflow_without_execution_backend(self, tmp_path: Path):
        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            "name: test\nversion: '1.0'\nphases:\n  - id: p1\n    name: P1\n    prompt_template: x\n    transitions:\n      on_success: complete\nterminals: [complete]\n",
            encoding="utf-8",
        )
        wf = load_workflow(str(wf_path))
        assert wf.execution_backend is None

    def test_workflow_with_execution_backend(self, tmp_path: Path):
        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            "name: test\nversion: '1.0'\n"
            "execution_backend:\n"
            "  mode: container\n"
            "  source: image\n"
            "  image: ascendhub:24.03\n"
            "phases:\n"
            "  - id: p1\n    name: P1\n    prompt_template: x\n    transitions:\n      on_success: complete\n"
            "terminals: [complete]\n",
            encoding="utf-8",
        )
        wf = load_workflow(str(wf_path))
        assert wf.execution_backend is not None
        assert wf.execution_backend.mode == "container"
        assert wf.execution_backend.source == "image"
        assert wf.execution_backend.image == "ascendhub:24.03"

    def test_workflow_invalid_mode_raises(self, tmp_path: Path):
        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            "name: test\nversion: '1.0'\n"
            "execution_backend:\n  mode: bad\n"
            "phases:\n"
            "  - id: p1\n    name: P1\n    prompt_template: x\n    transitions:\n      on_success: complete\n"
            "terminals: [complete]\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Invalid execution_backend.mode"):
            load_workflow(str(wf_path))

    def test_workflow_existing_container_missing_name(self, tmp_path: Path):
        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            "name: test\nversion: '1.0'\n"
            "execution_backend:\n  mode: container\n  source: existing_container\n"
            "phases:\n"
            "  - id: p1\n    name: P1\n    prompt_template: x\n    transitions:\n      on_success: complete\n"
            "terminals: [complete]\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="container_name is required"):
            load_workflow(str(wf_path))

    def test_workflow_execution_backend_wrong_type(self, tmp_path: Path):
        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            "name: test\nversion: '1.0'\n"
            "execution_backend: unexpected_string\n"
            "phases:\n"
            "  - id: p1\n    name: P1\n    prompt_template: x\n    transitions:\n      on_success: complete\n"
            "terminals: [complete]\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="must be a mapping"):
            load_workflow(str(wf_path))


# ── LocalBackend ──────────────────────────────────────────────────────────


class TestLocalBackend:
    def test_run_echo(self):
        backend = LocalBackend()
        result = backend.run("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.stderr == ""
        assert result.duration >= 0

    def test_run_exit_code(self):
        backend = LocalBackend()
        result = backend.run("exit 42")
        assert result.exit_code == 42

    def test_run_captures_stderr(self):
        backend = LocalBackend()
        result = backend.run("echo error >&2")
        assert "error" in result.stderr

    def test_run_with_cwd(self, tmp_path: Path):
        backend = LocalBackend()
        result = backend.run("pwd", cwd=str(tmp_path))
        assert result.exit_code == 0
        assert str(tmp_path) in result.stdout

    def test_run_timeout_raises(self):
        backend = LocalBackend()
        with pytest.raises(subprocess.TimeoutExpired):
            backend.run("sleep 10", timeout=0.1)

    def test_is_available(self):
        assert LocalBackend().is_available() is True

    def test_cleanup_noop(self):
        backend = LocalBackend()
        backend.cleanup()  # should not raise


# ── ContainerBackend: mocked image creation ──────────────────────────────


class TestContainerBackendImage:
    @patch("subprocess.run")
    def test_create_from_image(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="container-123\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        cid = backend._ensure_container()
        assert cid == "container-123"
        assert backend._initialized is True

        create_call = mock_run.call_args
        cmd = create_call[0][0]
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert cmd[2] == "-d"
        assert "--name" in cmd

    @patch("subprocess.run")
    def test_create_from_image_podman(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="c1\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "runtime": "podman"}
        )
        backend = ContainerBackend(cfg)
        backend._ensure_container()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"

    @patch("subprocess.run")
    def test_create_failure_raises(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="image not found"
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        with pytest.raises(RuntimeError, match="Failed to create container"):
            backend._ensure_container()

    @patch("subprocess.run")
    def test_exec_command_string(self, mock_run: MagicMock):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running\n", stderr=""),
            MagicMock(returncode=0, stdout="ok\n", stderr=""),
        ]
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True
        result = backend.run("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "ok\n"
        cmd = mock_run.call_args_list[1][0][0]
        assert "docker" == cmd[0]
        assert "exec" == cmd[1]
        assert "-i" in cmd
        assert "c1" in cmd
        assert "bash" in cmd
        assert "-c" in cmd

    @patch("subprocess.run")
    def test_exec_command_list(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True
        result = backend.run(["echo", "hello"])
        assert result.exit_code == 0
        cmd = mock_run.call_args[0][0]
        assert "echo" in cmd
        assert "hello" in cmd

    @patch("subprocess.run")
    def test_exec_cwd_mapping(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True
        backend.set_project_dir("/tmp/proj")
        # cwd inside project dir should get mapped
        _ = backend.run("echo hello", cwd=str(Path("/tmp/proj/subdir")))
        cmd = mock_run.call_args[0][0]
        w_idx = cmd.index("-w") if "-w" in cmd else None
        assert w_idx is not None
        assert cmd[w_idx + 1] == "/workspace/subdir"

    @patch("subprocess.run")
    def test_cleanup_image(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "cleanup": True}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend.cleanup()
        calls = [c[0][0] for c in mock_run.call_args_list]
        cmds = [" ".join(c) for c in calls]
        assert any("stop" in c for c in cmds)
        assert any("rm" in c for c in cmds)

    @patch("subprocess.run")
    def test_cleanup_skip_when_no_cleanup(self, mock_run: MagicMock):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "cleanup": False}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend.cleanup()
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_is_available_true(self, mock_run: MagicMock):
        backend = ContainerBackend(
            ExecutionBackendConfig.from_dict({"mode": "container", "image": "x"})
        )
        mock_run.return_value = MagicMock(returncode=0)
        assert backend.is_available() is True
        assert mock_run.call_args[0][0] == ["docker", "--version"]

    @patch("subprocess.run")
    def test_is_available_false(self, mock_run: MagicMock):
        backend = ContainerBackend(
            ExecutionBackendConfig.from_dict({"mode": "container", "image": "x"})
        )
        mock_run.side_effect = FileNotFoundError("docker not found")
        assert backend.is_available() is False


# ── ContainerBackend: mocked existing container ──────────────────────────


class TestContainerBackendExisting:
    @patch("subprocess.run")
    def test_existing_container_running(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="running\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
            }
        )
        backend = ContainerBackend(cfg)
        backend._check_existing_container()
        assert backend._container_id == "my-dev-01"
        assert backend._initialized is True
        call_args = mock_run.call_args[0][0]
        assert "inspect" in call_args
        assert "my-dev-01" in call_args

    @patch("subprocess.run")
    def test_existing_container_not_found(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="no such container"
        )
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "ghost",
            }
        )
        backend = ContainerBackend(cfg)
        with pytest.raises(ContainerNotFoundError, match="ghost"):
            backend._check_existing_container()

    @patch("subprocess.run")
    def test_existing_container_not_running(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="exited\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "dead",
            }
        )
        backend = ContainerBackend(cfg)
        with pytest.raises(ContainerNotRunningError, match="status is 'exited'"):
            backend._check_existing_container()

    @patch("subprocess.run")
    def test_existing_container_cleanup_noop(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="running\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
                "cleanup": True,
            }
        )
        backend = ContainerBackend(cfg)
        backend._check_existing_container()
        mock_run.reset_mock()
        backend.cleanup()
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_existing_required_env_warning(self, mock_run: MagicMock):
        side_effects = [
            MagicMock(returncode=0, stdout="running\n", stderr=""),
            MagicMock(returncode=0, stdout="PATH=/usr/bin\n", stderr=""),
        ]
        mock_run.side_effect = side_effects
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
                "required_env_vars": ["ASCEND_MISSING"],
            }
        )
        backend = ContainerBackend(cfg)
        backend._check_existing_container()
        assert backend._container_id == "my-dev-01"

    @patch("subprocess.run")
    def test_existing_required_device_warning(self, mock_run: MagicMock):
        side_effects = [
            MagicMock(returncode=0, stdout="running\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
        ]
        mock_run.side_effect = side_effects
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
                "required_devices": ["/dev/missing_device"],
            }
        )
        backend = ContainerBackend(cfg)
        backend._check_existing_container()
        assert backend._container_id == "my-dev-01"


# ── Auto select backend ──────────────────────────────────────────────────


class TestAutoSelectBackend:
    @patch("subprocess.run")
    def test_auto_selects_container_when_runtime_available(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0)
        base = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "image": "test:latest"}
        )
        result = auto_select_backend(base)
        assert result.mode == "container"

    @patch("subprocess.run")
    def test_auto_falls_back_to_local_when_runtime_missing(self, mock_run: MagicMock):
        mock_run.side_effect = FileNotFoundError("docker not found")
        base = ExecutionBackendConfig.from_dict({"mode": "auto"})
        result = auto_select_backend(base)
        assert result.mode == "local"


# ── WorkflowExecutor backward compatibility ──────────────────────────────


class TestWorkflowExecutorBackwardCompat:
    def test_shell_phase_no_backend(self, tmp_path: Path):
        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"])
        executor = WorkflowExecutor(
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
            exec_backend=None,
        )
        phase = PhaseDefinition(
            id="shell", name="S", prompt_template="", output_schema={},
            type="shell", on_failure="continue",
        )
        setattr(phase, "command", "echo hello")

        assert executor.exec_backend is None

    def test_shell_phase_local_backend(self, tmp_path: Path):
        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"])
        executor = WorkflowExecutor(
            workflow,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            project_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )

        phase = PhaseDefinition(
            id="shell", name="S", prompt_template="", output_schema={},
            type="shell", on_failure="continue",
        )
        setattr(phase, "command", "echo hello")

        status, output = executor._execute_shell_phase(phase, {}, {}, loop_state={})
        assert status == "success"
        assert "hello" in output["stdout"]

    def test_container_backend_routes_in_shell(self, tmp_path: Path):
        """ContainerBackend is invoked by _execute_shell_phase when set."""
        workflow = WorkflowDefinition(name="test", version="1.0", phases=[], terminals=["complete"])
        mock_backend = MagicMock()
        mock_backend.run.return_value = ExecResult(
            exit_code=0, stdout="container ok\n", stderr="", duration=0.5
        )

        # Need isinstance check to work, so use a ContainerBackend with mocked internal methods
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True

        with patch.object(backend, "run", return_value=ExecResult(
            exit_code=0, stdout="container ok\n", stderr="", duration=0.5
        )):
            executor = WorkflowExecutor(
                workflow,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                project_dir=str(tmp_path),
                output_dir=str(tmp_path),
                exec_backend=backend,
            )
            phase = PhaseDefinition(
                id="shell", name="S", prompt_template="", output_schema={},
                type="shell", on_failure="continue",
            )
            setattr(phase, "command", "echo hello")

            status, output = executor._execute_shell_phase(phase, {}, {}, loop_state={})
            assert status == "success"
            assert "container ok" in output["stdout"]

    def test_phase5_entry_script_safety_unaffected(self, tmp_path: Path):
        """Phase 5 entry script safety tests from existing test suite still pass."""
        target_script = tmp_path / "expanded_target.py"
        target_script.write_text(
            "from pathlib import Path\nPath('expanded-ran').write_text('yes')\n",
            encoding="utf-8",
        )
        old_val = os.environ.get("PY_SCRIPT")
        os.environ["PY_SCRIPT"] = str(target_script)
        try:
            workflow = WorkflowDefinition(name="entry-no-shell", version="1.0", phases=[], terminals=["complete"])
            executor = WorkflowExecutor(
                workflow,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                project_dir=str(tmp_path),
                output_dir=str(tmp_path),
            )
            phase = PhaseDefinition(
                id="run_entry_script", name="Run Entry", prompt_template="",
                output_schema={}, type="shell", on_failure="continue",
            )
            setattr(phase, "command", "${loop_vars.entry_script}")

            status, output = executor._execute_shell_phase(
                phase, state={}, context={},
                loop_vars={"entry_script": "python $PY_SCRIPT"},
                loop_state={},
            )

            assert status == "success"
            assert output["exit_code"] != 0
            assert "expanded_target.py" not in output["stderr"]
            assert not (tmp_path / "expanded-ran").exists()
        finally:
            if old_val is None:
                os.environ.pop("PY_SCRIPT", None)
            else:
                os.environ["PY_SCRIPT"] = old_val


# ── RepairLoopEngine backward compatibility ──────────────────────────────


class TestRepairLoopBackwardCompat:
    def test_repair_loop_accepts_exec_backend(self):
        from core.repair_loop import RepairLoopEngine

        engine = RepairLoopEngine(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            config={}, exec_backend=MagicMock(),
        )
        assert engine.exec_backend is not None

    def test_repair_loop_works_without_backend(self):
        from core.repair_loop import RepairLoopEngine

        engine = RepairLoopEngine(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            config={},
        )
        assert engine.exec_backend is None


# ── ContainerBackend invalid config type ─────────────────────────────────


class TestContainerBackendValidation:
    def test_rejects_non_config(self):
        with pytest.raises(TypeError, match="ExecutionBackendConfig"):
            ContainerBackend({"mode": "container"})

    def test_missing_image_for_source_image(self):
        cfg = ExecutionBackendConfig.from_dict({"mode": "container"})
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        with pytest.raises(ValueError, match="image or execution_backend.images"):
            backend._ensure_container()


# ── RepairLoopEngine: container path does not use subprocess.CompletedProcess

class TestRepairLoopContainerPath:
    def test_repair_loop_entry_script_safety_with_container_backend(self, tmp_path: Path):
        from core.repair_loop import RepairLoopEngine
        from core.execution_backend import ExecResult

        mock_backend = MagicMock()
        mock_backend.run.return_value = ExecResult(
            exit_code=0,
            stdout="container success\n",
            stderr="",
            duration=1.5,
        )

        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        cb = ContainerBackend(cfg)
        cb.set_project_dir(str(tmp_path))
        cb._container_id = "c1"
        cb._initialized = True
        # Override run with mocked result
        cb.run = MagicMock(return_value=ExecResult(
            exit_code=0,
            stdout="container success\n",
            stderr="",
            duration=1.5,
        ))

        engine = RepairLoopEngine(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            config={}, exec_backend=cb,
        )
        # Just verify that the engine accepts the container backend
        # and that the isinstance check would work in the container path.
        assert isinstance(engine.exec_backend, ContainerBackend)
        assert engine.exec_backend is cb


# ── WorkflowExecutor: auto-creation of exec_backend from workflow config ──


class TestWorkflowExecutorAutoBackend:
    @patch("core.execution_backend.ContainerBackend")
    def test_auto_creates_container_backend_from_workflow_config(self, MockBackend, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        MockBackend.assert_called_once_with(cfg)
        backend_instance = MockBackend.return_value
        assert executor.exec_backend is backend_instance
        backend_instance.set_project_dir.assert_called_once_with(str(tmp_path))
        backend_instance.preflight.assert_called_once()
        backend_instance.probe_environment.assert_called_once()

    def test_local_mode_keeps_exec_backend_none(self, tmp_path: Path):
        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        assert executor.exec_backend is None

    def test_local_explicit_config_keeps_exec_backend_none(self, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict({"mode": "local"})
        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        assert executor.exec_backend is None

    @patch("subprocess.run")
    def test_auto_mode_containers_when_runtime_available(self, mock_run, tmp_path: Path):
        mock_run.return_value = MagicMock(returncode=0)
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "image": "test:latest"}
        )
        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        assert isinstance(executor.exec_backend, ContainerBackend)

    @patch("subprocess.run")
    def test_auto_mode_falls_back_to_local_when_unavailable(self, mock_run, tmp_path: Path):
        mock_run.side_effect = FileNotFoundError("docker not found")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "image": "test:latest"}
        )
        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        assert executor.exec_backend is None

    def test_explicit_backend_overrides_config(self, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "ignored"}
        )
        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        explicit = LocalBackend()
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
            exec_backend=explicit,
        )
        assert executor.exec_backend is explicit


class TestWorkflowExecutorCleanup:
    @patch("core.execution_backend.ContainerBackend")
    def test_cleanup_called_for_container_backend(self, MockBackend, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )

        workflow = WorkflowDefinition(
            name="cleanup_test", version="1.0", phases=[], terminals=["done"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        backend_instance = MockBackend.return_value
        assert executor.exec_backend is backend_instance
        backend_instance.preflight.assert_called_once()
        backend_instance.probe_environment.assert_called_once()

        executor._cleanup_execution_backend()
        backend_instance.cleanup.assert_called_once()

    def test_cleanup_skipped_for_local_config(self, tmp_path: Path):
        workflow = WorkflowDefinition(
            name="local_test", version="1.0", phases=[], terminals=["done"],
            execution_backend=ExecutionBackendConfig.from_dict({"mode": "local"}),
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        assert executor.exec_backend is None
        executor._cleanup_execution_backend()  # should not raise

    def test_existing_container_cleanup_is_noop(self, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
                "cleanup": True,
            }
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "my-dev-01"
        with patch("subprocess.run") as mock_run:
            backend.cleanup()
            mock_run.assert_not_called()

    def test_image_container_cleanup_stops_and_removes(self, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "image",
                "image": "test:latest",
                "cleanup": True,
            }
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "test-123"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            backend.cleanup()
        cmds = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("stop" in c for c in cmds)
        assert any("rm" in c for c in cmds)

    def test_cleanup_failure_does_not_crash_workflow(self, tmp_path: Path):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest", "cleanup": True}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "test-123"
        with patch("subprocess.run", side_effect=RuntimeError("docker daemon error")):
            backend.cleanup()  # should log warning, not raise

    def test_backend_cleanup_logged_warning_on_failure(self, tmp_path: Path, caplog):
        caplog.set_level(logging.ERROR, logger="core.workflow_executor")

        mock_backend = MagicMock()
        mock_backend.cleanup.side_effect = RuntimeError("cannot stop container")

        workflow = WorkflowDefinition(
            name="cleanup_fail_test", version="1.0", phases=[], terminals=["done"],
        )
        executor = WorkflowExecutor(
            workflow,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
            exec_backend=mock_backend,
        )
        executor._cleanup_execution_backend()
        assert any("cleanup failed" in r.message for r in caplog.records)


# ── ContainerBackend: host-to-container path rewriting ─────────────────────


class TestContainerBackendHostPathRewriting:
    """Regression: host paths in commands must be mapped to /workspace before docker exec."""

    # -- _rewrite_single_path (list command tokens) ------------------------

    def test_rewrite_single_path_inside_project(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._container_id = "c1"
        backend._initialized = True

        rewritten = backend._rewrite_single_path("/tmp/proj/smoke_validate.py")
        assert rewritten == "/workspace/smoke_validate.py"

    def test_rewrite_single_path_deep_subdir(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        rewritten = backend._rewrite_single_path("/tmp/proj/tests/test_unit.py")
        assert rewritten == "/workspace/tests/test_unit.py"

    def test_rewrite_single_path_outside_project_unchanged(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        rewritten = backend._rewrite_single_path("/usr/bin/python3")
        assert rewritten == "/usr/bin/python3"

    def test_rewrite_single_path_no_project_dir(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x"}
        )
        backend = ContainerBackend(cfg)
        # project dir not set
        rewritten = backend._rewrite_single_path("/tmp/proj/smoke_validate.py")
        assert rewritten == "/tmp/proj/smoke_validate.py"

    # -- _rewrite_command_paths (string commands) --------------------------

    def test_rewrite_command_paths_string(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        rewritten = backend._rewrite_command_paths("python /tmp/proj/smoke_validate.py")
        assert "/workspace/smoke_validate.py" in rewritten

    def test_rewrite_command_paths_outside_unchanged(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        rewritten = backend._rewrite_command_paths("python3 /usr/bin/test.py")
        assert "/usr/bin/test.py" in rewritten

    # -- run() with list command (subprocess mocked) -----------------------

    @patch("subprocess.run")
    def test_run_list_command_rewrites_host_paths(self, mock_run: MagicMock):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running\n", stderr=""),
            MagicMock(returncode=0, stdout="ok\n", stderr=""),
        ]
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True
        backend.set_project_dir("/tmp/proj")

        _ = backend.run(["python", "/tmp/proj/smoke_validate.py"])
        cmd = mock_run.call_args_list[1][0][0]

        assert "docker" == cmd[0]
        assert "exec" == cmd[1]
        assert "c1" in cmd
        assert "python" in cmd
        assert "/workspace/smoke_validate.py" in cmd
        assert "/tmp/proj/smoke_validate.py" not in cmd

    # -- run() with string command (subprocess mocked) ---------------------

    @patch("subprocess.run")
    def test_run_string_command_rewrites_host_paths(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True
        backend.set_project_dir("/tmp/proj")

        _ = backend.run("python /tmp/proj/smoke_validate.py")
        cmd = mock_run.call_args[0][0]

        assert "docker" == cmd[0]
        assert "exec" == cmd[1]
        assert "bash" in cmd
        assert "-c" in cmd
        # Find the script portion after -c
        c_idx = cmd.index("-c")
        script_part = cmd[c_idx + 1]
        assert "/workspace/smoke_validate.py" in script_part
        assert "/tmp/proj/smoke_validate.py" not in script_part

    # -- describe_command() shows mapped paths -----------------------------

    def test_describe_command_list_shows_workspace_paths(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._container_id = "c1"

        desc = backend.describe_command(["python", "/tmp/proj/smoke_validate.py"])
        assert "/workspace/smoke_validate.py" in desc
        assert "/tmp/proj/smoke_validate.py" not in desc

    def test_describe_command_string_shows_workspace_paths(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._container_id = "c1"

        desc = backend.describe_command("python /tmp/proj/smoke_validate.py")
        assert "/workspace/smoke_validate.py" in desc
        assert "/tmp/proj/smoke_validate.py" not in desc

    # -- tmp_path-based integration test -----------------------------------

    def test_run_list_command_with_real_tmp_path(self, tmp_path: Path):
        """Use tmp_path so host directory is guaranteed to exist on this system."""
        project = tmp_path / "proj"
        project.mkdir()
        script = project / "smoke_validate.py"
        script.write_text("print('ok')")

        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "x", "container_workdir": "/workspace"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True
        backend.set_project_dir(str(project))

        # _rewrite_single_path should map the real tmp_path to /workspace/
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            _ = backend.run(["python", str(script)])
            cmd = mock_run.call_args[0][0]
            assert "/workspace/smoke_validate.py" in cmd, (
                f"Expected /workspace path in {cmd}"
            )


# ── ContainerBackend: preflight ────────────────────────────────────────


class TestContainerBackendPreflight:
    @patch("subprocess.run")
    def test_preflight_creates_container_for_image_source(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="preflight-cid\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend.preflight()
        assert backend._container_id == "preflight-cid"
        assert backend._initialized is True

    @patch("subprocess.run")
    def test_preflight_validates_existing_container(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="running\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
            }
        )
        backend = ContainerBackend(cfg)
        backend.preflight()
        assert backend._container_id == "my-dev-01"
        assert backend._initialized is True

    @patch("subprocess.run")
    def test_preflight_is_idempotent(self, mock_run: MagicMock):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cid-idem\n", stderr=""),
            MagicMock(returncode=0, stdout="running\n", stderr=""),
        ]
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend.preflight()
        backend.preflight()
        # Should only call subprocess.run once for creation, once for inspect
        create_calls = [
            c for c in mock_run.call_args_list
            if "run" in c[0][0] and "-d" in c[0][0]
        ]
        assert len(create_calls) == 1

    @patch("subprocess.run")
    def test_preflight_then_run_no_double_create(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-dup\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend.preflight()
        mock_run.reset_mock()
        mock_run.return_value = MagicMock(returncode=0, stdout="running\n", stderr="")
        _ = backend.run("echo test")
        # No second container creation
        create_calls = [
            c for c in mock_run.call_args_list
            if "run" in c[0][0] and "-d" in c[0][0]
        ]
        assert len(create_calls) == 0

    @patch("subprocess.run")
    def test_preflight_propagates_creation_failure(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="image pull failed"
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        with pytest.raises(RuntimeError, match="Failed to create container"):
            backend.preflight()


# ── ContainerBackend: cached container liveness revalidation ───────────


class TestContainerBackendLivenessRevalidation:
    @patch("subprocess.run")
    def test_cached_image_container_running_no_recreate(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="running\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True

        cid = backend._ensure_container()
        assert cid == "c1"
        assert backend._container_id == "c1"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "inspect" in call_args
        assert "c1" in call_args

    @patch("subprocess.run")
    def test_cached_image_container_exited_recreates(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="exited\n", stderr=""),
            MagicMock(returncode=0, stdout="new-cid\n", stderr=""),
        ]
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._container_id = "old-cid"
        backend._initialized = True

        cid = backend._ensure_container()
        assert cid == "new-cid"
        assert mock_run.call_count == 2
        # First call: inspect exited container
        inspect_call = mock_run.call_args_list[0][0][0]
        assert "inspect" in inspect_call
        assert "old-cid" in inspect_call
        # Second call: docker run -d to recreate
        create_call = mock_run.call_args_list[1][0][0]
        assert "run" in create_call
        assert "-d" in create_call

    @patch("subprocess.run")
    def test_cached_image_container_missing_recreates(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="No such container"),
            MagicMock(returncode=0, stdout="fresh-cid\n", stderr=""),
        ]
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._container_id = "ghost-cid"
        backend._initialized = True

        cid = backend._ensure_container()
        assert cid == "fresh-cid"
        assert mock_run.call_count == 2
        # After recreate, old state is cleared
        assert backend._container_id == "fresh-cid"
        assert backend._initialized is True

    @patch("subprocess.run")
    def test_cached_existing_not_found_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="No such container"
        )
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
            }
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "my-dev-01"
        backend._initialized = True

        with pytest.raises(ContainerNotFoundError, match="my-dev-01"):
            backend._ensure_container()

    @patch("subprocess.run")
    def test_cached_existing_exited_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="exited\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {
                "mode": "container",
                "source": "existing_container",
                "container_name": "my-dev-01",
            }
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "my-dev-01"
        backend._initialized = True

        with pytest.raises(ContainerNotRunningError, match="exited"):
            backend._ensure_container()

    @patch("subprocess.run")
    def test_run_revalidates_then_execs(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running\n", stderr=""),   # inspect
            MagicMock(returncode=0, stdout="hello\n", stderr=""),      # exec
        ]
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "c1"
        backend._initialized = True

        result = backend.run("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert mock_run.call_count == 2
        # First call: inspect
        call1 = mock_run.call_args_list[0][0][0]
        assert "inspect" in call1
        assert "c1" in call1
        # Second call: exec
        call2 = mock_run.call_args_list[1][0][0]
        assert "exec" in call2
        assert "c1" in call2


# ── ContainerBackend: probe_environment ────────────────────────────────


class TestContainerBackendProbeEnvironment:
    @patch("subprocess.run")
    def test_probe_returns_facts_on_success(self, mock_run: MagicMock):
        probe_output = (
            '{"status": "ok", "interpreter_path": "/usr/local/bin/python3", "python_version": "3.10.0", '
            '"platform": "Linux", "platform_machine": "x86_64", '
            '"cwd": "/workspace", "env_keys": ["PATH"], '
            '"torch_version": "2.1.0", "torch_cuda_available": true, '
            '"torch_device_count": 1}'
        )
        mock_run.return_value = MagicMock(
            returncode=0, stdout=probe_output + "\n", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "probe-cid"
        backend._initialized = True
        result = backend.probe_environment()
        assert result["status"] == "ok"
        assert result["python_version"] == "3.10.0"
        assert result["interpreter_path"] == "/usr/local/bin/python3"
        assert result["container_id"] == "probe-cid"
        cmd = mock_run.call_args.args[0]
        assert cmd[-4:-1] == ["probe-cid", "sh", "-lc"]
        assert "SEAM_CONTAINER_PROBE_SCRIPT=" in " ".join(cmd)

    @patch("subprocess.run")
    def test_probe_returns_error_on_failure(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="sh: command not found"
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "probe-cid"
        result = backend.probe_environment()
        assert result["status"] == "probe_failed"
        assert "sh" in result["error"]

    @patch("subprocess.run")
    def test_probe_reports_missing_python_from_container_path(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"status":"probe_failed","error":"No Python interpreter found on container PATH"}\n',
            stderr="",
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "probe-cid"

        result = backend.probe_environment()

        assert result["status"] == "probe_failed"
        assert "No Python interpreter" in result["error"]

    @patch("subprocess.run")
    def test_probe_graceful_without_container(self, mock_run: MagicMock):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        # container_id is None
        result = backend.probe_environment()
        assert result["status"] == "skipped"
        assert "preflight" in result["error"]
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_probe_parse_error_does_not_crash(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="not json at all", stderr=""
        )
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "test:latest"}
        )
        backend = ContainerBackend(cfg)
        backend._container_id = "probe-cid"
        result = backend.probe_environment()
        assert result["status"] == "parse_error"


# ── LocalBackend: preflight and probe are no-ops ───────────────────────


class TestLocalBackendPreflightAndProbe:
    def test_preflight_is_noop(self):
        LocalBackend().preflight()  # should not raise

    def test_probe_returns_local_status(self):
        result = LocalBackend().probe_environment()
        assert result["status"] == "local"


class TestGetContainerPromptContext:
    def test_returns_empty_for_none_backend(self):
        assert get_container_prompt_context(None) == {}

    def test_returns_empty_for_local_backend(self):
        assert get_container_prompt_context(LocalBackend()) == {}

    def test_merges_backend_ctx_and_probe_facts(self, monkeypatch):
        import json as _json
        mock_backend = MagicMock()
        mock_backend.get_execution_context.return_value = {
            "execution_backend_mode": "container",
            "container_name_or_id": "c1",
        }
        probe = {"status": "ok", "python_version": "3.10.0", "torch_version": "2.1.0"}
        result = get_container_prompt_context(mock_backend, probe)
        assert result["execution_backend_mode"] == "container"
        assert result["container_name_or_id"] == "c1"
        assert result["container_python_version"] == "3.10.0"
        assert result["container_torch_version"] == "2.1.0"
        assert result["container_env_facts"] == _json.dumps(probe, ensure_ascii=False, default=str)

    def test_handles_failed_probe_gracefully(self, monkeypatch):
        import json as _json
        mock_backend = MagicMock()
        mock_backend.get_execution_context.return_value = {
            "execution_backend_mode": "container",
            "container_name_or_id": "c2",
        }
        probe = {"status": "probe_failed", "error": "python not found"}
        result = get_container_prompt_context(mock_backend, probe)
        assert result["execution_backend_mode"] == "container"
        assert result["container_env_facts"] == _json.dumps(probe, ensure_ascii=False, default=str)

    def test_no_probe_facts_when_none(self):
        mock_backend = MagicMock()
        mock_backend.get_execution_context.return_value = {
            "execution_backend_mode": "container",
            "container_name_or_id": "c3",
        }
        result = get_container_prompt_context(mock_backend, None)
        assert result["container_name_or_id"] == "c3"
        assert "container_env_facts" not in result


# ── Image list config parsing ────────────────────────────────────────


class TestImageListConfigParsing:
    def test_from_dict_single_image_populates_images(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "ascendhub:24.03"}
        )
        assert cfg.image == "ascendhub:24.03"
        assert cfg.images == ["ascendhub:24.03"]

    def test_from_dict_images_list(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["img-a:1.0", "img-b:2.0"]}
        )
        assert cfg.images == ["img-a:1.0", "img-b:2.0"]
        assert cfg.image == "img-a:1.0"

    def test_from_dict_both_image_and_images(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "legacy:old", "images": ["new:v1", "new:v2"]}
        )
        assert cfg.images == ["new:v1", "new:v2"]
        # config.image is the first candidate from images for compatibility
        assert cfg.image == "new:v1"

    def test_from_dict_auto_with_images(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "images": ["auto-img:1", "auto-img:2"]}
        )
        assert cfg.mode == "auto"
        assert cfg.images == ["auto-img:1", "auto-img:2"]

    def test_backward_compat_single_image(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "source": "image", "image": "test:latest"}
        )
        assert cfg.mode == "container"
        assert cfg.source == "image"
        assert cfg.image == "test:latest"
        assert cfg.images == ["test:latest"]

    def test_from_dict_image_as_list(self):
        """YAML `image: [a, b]` should be accepted and normalized to images list."""
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": ["img-x:1", "img-y:2"]}
        )
        assert cfg.images == ["img-x:1", "img-y:2"]
        assert cfg.image == "img-x:1"

    def test_images_list_wins_over_single_image(self):
        """When both `image` and `images` are provided, images list wins for candidate resolution."""
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "legacy:old", "images": ["chosen:a", "chosen:b"]}
        )
        assert cfg.images == ["chosen:a", "chosen:b"]
        # The key contract: _resolve_candidate_images must return the images list, not legacy
        backend = ContainerBackend(cfg)
        assert backend._resolve_candidate_images() == ["chosen:a", "chosen:b"]
        assert "legacy:old" not in backend._resolve_candidate_images()

    def test_from_dict_image_list_wins_for_fallback(self):
        """`image` as list should populate images and config.image to first entry."""
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": ["fallback:1", "fallback:2"]}
        )
        assert cfg.images == ["fallback:1", "fallback:2"]
        assert cfg.image == "fallback:1"
        backend = ContainerBackend(cfg)
        assert backend._resolve_candidate_images() == ["fallback:1", "fallback:2"]

    def test_image_list_filters_none_values(self):
        """String 'None' should not appear as a candidate."""
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["good:1", None, "None", ""]}
        )
        # Normalization in from_dict: [str(x) for x in raw_images]
        # should handle None gracefully
        assert cfg.images is not None
        assert "good:1" in cfg.images
        # Check that "None" and empty strings are not in resolved images
        nones = [x for x in cfg.images if str(x) in ("None", "")]
        assert len(nones) == 0, f"Unexpected None entries: {nones}"


# ── ContainerBackend: image candidate resolution ─────────────────────


class TestCandidateImageResolution:
    def test_resolve_from_images_list(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["a:1", "b:2"]}
        )
        backend = ContainerBackend(cfg)
        assert backend._resolve_candidate_images() == ["a:1", "b:2"]

    def test_resolve_from_single_image(self):
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "single:latest"}
        )
        backend = ContainerBackend(cfg)
        assert backend._resolve_candidate_images() == ["single:latest"]

    def test_resolve_empty(self):
        cfg = ExecutionBackendConfig.from_dict({"mode": "container"})
        backend = ContainerBackend(cfg)
        assert backend._resolve_candidate_images() == []

    def test_resolve_images_list_wins_over_single_image(self):
        """When both image and images are set, images list takes precedence."""
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "legacy:old", "images": ["a:1", "b:2"]}
        )
        backend = ContainerBackend(cfg)
        assert backend._resolve_candidate_images() == ["a:1", "b:2"]
        assert "legacy:old" not in backend._resolve_candidate_images()


# ── ContainerBackend: sequential create fallback ─────────────────────


class TestSequentialCreateFallback:
    @patch("subprocess.run")
    def test_logs_each_candidate_image_before_create(self, mock_run, caplog):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "first:1" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="pull failed")
            return MagicMock(returncode=0, stdout="cid-2\n", stderr="")

        caplog.set_level(logging.INFO, logger="core.execution_backend")
        mock_run.side_effect = side_effect
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["first:1", "second:2"]}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")

        backend._create_container_from_image()

        messages = [record.message for record in caplog.records]
        assert any(
            "Container create attempt: runtime=docker" in msg
            and " name=" in msg
            and "image=first:1" in msg
            for msg in messages
        )
        assert any(
            "Container create attempt: runtime=docker" in msg
            and " name=" in msg
            and "image=second:2" in msg
            for msg in messages
        )

    @patch("subprocess.run")
    def test_first_image_succeeds(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["first:1", "second:2"]}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._create_container_from_image()
        assert backend._container_id == "cid-ok"
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_second_image_succeeds(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "run" in cmd and "-d" in cmd:
                if "first:1" in cmd:
                    return MagicMock(returncode=1, stdout="", stderr="pull failed")
                return MagicMock(returncode=0, stdout="cid-2\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["first:1", "second:2"]}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._create_container_from_image()
        assert backend._container_id == "cid-2"
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_all_images_fail(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fail")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "images": ["bad:1", "worse:2"]}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        with pytest.raises(RuntimeError, match="All images failed"):
            backend._create_container_from_image()

    @patch("subprocess.run")
    def test_no_image_or_images_raises(self, mock_run):
        cfg = ExecutionBackendConfig.from_dict({"mode": "container"})
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        with pytest.raises(ValueError, match="image or execution_backend.images"):
            backend._create_container_from_image()

    @patch("subprocess.run")
    def test_images_list_wins_over_legacy_image(self, mock_run):
        """When both `image` and `images` are present, sequential fallback uses `images` list."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": "legacy:old", "images": ["new:1", "new:2"]}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._create_container_from_image()
        # Verify the command used the first image from `images`, not the legacy single image
        create_call = mock_run.call_args[0][0]
        assert "new:1" in create_call
        assert "legacy:old" not in create_call

    @patch("subprocess.run")
    def test_image_as_list_uses_first_candidate(self, mock_run):
        """yaml `image: [a, b]` populates images; first is tried first."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")
        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "container", "image": ["first-tried:1", "fallback:2"]}
        )
        backend = ContainerBackend(cfg)
        backend.set_project_dir("/tmp/proj")
        backend._create_container_from_image()
        assert backend._container_id == "cid-ok"
        assert mock_run.call_count == 1
        create_call = mock_run.call_args[0][0]
        assert "first-tried:1" in create_call


# ── ContainerBackend: discover local images ──────────────────────────


class TestDiscoverLocalImages:
    @patch("subprocess.run")
    def test_discover_returns_images(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ascendhub:24.03\npytorch:latest\n<none>:<none>\n",
            stderr="",
        )
        cfg = ExecutionBackendConfig.from_dict({"mode": "container"})
        backend = ContainerBackend(cfg)
        images = backend._discover_local_images()
        assert "ascendhub:24.03" in images
        assert "pytorch:latest" in images
        assert "<none>:<none>" not in images

    @patch("subprocess.run")
    def test_discover_failure_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        cfg = ExecutionBackendConfig.from_dict({"mode": "container"})
        backend = ContainerBackend(cfg)
        images = backend._discover_local_images()
        assert images == []

    @patch("subprocess.run")
    def test_discover_exception_returns_empty(self, mock_run):
        mock_run.side_effect = FileNotFoundError("docker not found")
        cfg = ExecutionBackendConfig.from_dict({"mode": "container"})
        backend = ContainerBackend(cfg)
        images = backend._discover_local_images()
        assert images == []


# ── Workflow YAML with image list ────────────────────────────────────


class TestConfigIntegrationImageList:
    def test_workflow_with_images_list(self, tmp_path: Path):
        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            "name: test\nversion: '1.0'\n"
            "execution_backend:\n"
            "  mode: container\n"
            "  source: image\n"
            "  images:\n"
            "    - ascendhub:24.03\n"
            "    - pytorch:latest\n"
            "phases:\n"
            "  - id: p1\n    name: P1\n    prompt_template: x\n    transitions:\n      on_success: complete\n"
            "terminals: [complete]\n",
            encoding="utf-8",
        )
        wf = load_workflow(str(wf_path))
        assert wf.execution_backend is not None
        assert wf.execution_backend.images == ["ascendhub:24.03", "pytorch:latest"]
        assert wf.execution_backend.image == "ascendhub:24.03"


# ── Auto backend with images carried through ─────────────────────────


class TestAutoSelectWithImages:
    @patch("subprocess.run")
    def test_auto_carries_images_through(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        base = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "images": ["a:1", "b:2"]}
        )
        result = auto_select_backend(base)
        assert result.mode == "container"
        assert result.images == ["a:1", "b:2"]

        # Verify auto_select_backend also carries the new field
        assert result.images is not None


# ── Auto image selection (workflow_executor mocked) ───────────────────


class TestAutoImageSelection:
    """Test the _auto_select_image path in WorkflowExecutor with mocked session."""

    def _mock_response(self, selected_image):
        return f'\n```json\n{{"selected_image": "{selected_image}"}}\n```'

    @patch("subprocess.run")
    def test_auto_select_from_configured_list(
        self, mock_run, tmp_path,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")

        mock_session = MagicMock()
        mock_session.get_or_create.return_value = "sel-sid"
        mock_session.send_command.return_value = self._mock_response("img-c:3")

        mock_prompt_loader = MagicMock()
        mock_prompt_loader.load_prompt.return_value = "fake prompt"

        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "images": ["img-a:1", "img-b:2", "img-c:3"]}
        )

        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            mock_session, MagicMock(), mock_prompt_loader, MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        assert executor.exec_backend is not None
        assert isinstance(executor.exec_backend, ContainerBackend)
        assert executor.exec_backend.config.image == "img-c:3"
        assert executor.exec_backend.config.images == ["img-c:3", "img-a:1", "img-b:2"]

        # Verify the prompt loader was called with the candidates
        call_ctx = mock_prompt_loader.load_prompt.call_args[0][1]
        assert "img-a:1" in call_ctx["candidate_images"]
        assert "img-b:2" in call_ctx["candidate_images"]
        assert "img-c:3" in call_ctx["candidate_images"]

    @patch("subprocess.run")
    def test_auto_select_invalid_out_of_list_falls_back(
        self, mock_run, tmp_path,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")

        mock_session = MagicMock()
        mock_session.get_or_create.return_value = "sel-sid"
        mock_session.send_command.return_value = self._mock_response("rogue:latest")

        mock_prompt_loader = MagicMock()
        mock_prompt_loader.load_prompt.return_value = "fake prompt"

        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "images": ["good:1", "also-good:2"]}
        )

        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            mock_session, MagicMock(), mock_prompt_loader, MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        assert executor.exec_backend is None

    @patch("subprocess.run")
    def test_auto_select_from_discovered_local_images(
        self, mock_run, tmp_path,
    ):
        def _discover_side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "images" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="local-hub:v1\nlocal-hub:v2\n<none>:<none>\n",
                    stderr="",
                )
            elif isinstance(cmd, list) and cmd[1] in ("run", "--version"):
                return MagicMock(returncode=0, stdout="cid-ok\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = _discover_side_effect

        mock_session = MagicMock()
        mock_session.get_or_create.return_value = "sel-sid"
        mock_session.send_command.return_value = self._mock_response("local-hub:v2")

        mock_prompt_loader = MagicMock()
        mock_prompt_loader.load_prompt.return_value = "fake prompt"

        cfg = ExecutionBackendConfig.from_dict({"mode": "auto"})

        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            mock_session, MagicMock(), mock_prompt_loader, MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        assert executor.exec_backend is not None
        assert isinstance(executor.exec_backend, ContainerBackend)
        assert executor.exec_backend.config.image == "local-hub:v2"
        assert executor.exec_backend.config.images == ["local-hub:v2", "local-hub:v1"]

    @patch("subprocess.run")
    def test_auto_no_images_no_discovered_falls_back_to_local(
        self, mock_run, tmp_path,
    ):
        def _no_discovery_side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "images" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="error")
            elif isinstance(cmd, list) and cmd[1] == "--version":
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = _no_discovery_side_effect

        mock_session = MagicMock()
        mock_prompt_loader = MagicMock()

        cfg = ExecutionBackendConfig.from_dict({"mode": "auto"})

        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            mock_session, MagicMock(), mock_prompt_loader, MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        assert executor.exec_backend is None

    @patch("subprocess.run")
    def test_auto_select_ignores_compatibility_image_with_multiple_candidates(
        self, mock_run, tmp_path,
    ):
        """mode: auto + images list + config.image set to first → still does agent selection."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")

        mock_session = MagicMock()
        mock_session.get_or_create.return_value = "sel-sid"
        mock_session.send_command.return_value = self._mock_response("img-b:2")

        mock_prompt_loader = MagicMock()
        mock_prompt_loader.load_prompt.return_value = "fake prompt"

        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "images": ["img-a:1", "img-b:2"]}
        )

        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            mock_session, MagicMock(), mock_prompt_loader, MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        assert executor.exec_backend is not None
        assert executor.exec_backend.config.image == "img-b:2"
        assert executor.exec_backend.config.images == ["img-b:2", "img-a:1"]

        call_ctx = mock_prompt_loader.load_prompt.call_args[0][1]
        assert "img-a:1" in call_ctx["candidate_images"]
        assert "img-b:2" in call_ctx["candidate_images"]

    @patch("subprocess.run")
    def test_auto_selection_filters_none_values_in_images(
        self, mock_run, tmp_path,
    ):
        """mode: auto + images with None → filters them out before selection."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid-ok\n", stderr="")

        mock_session = MagicMock()
        mock_session.get_or_create.return_value = "sel-sid"
        mock_session.send_command.return_value = self._mock_response("good:1")

        mock_prompt_loader = MagicMock()
        mock_prompt_loader.load_prompt.return_value = "rendered prompt"

        cfg = ExecutionBackendConfig.from_dict(
            {"mode": "auto", "images": ["good:1", "also-good:2"]}
        )
        cfg.images = ["good:1", None, "None", "also-good:2"]

        workflow = WorkflowDefinition(
            name="test", version="1.0", phases=[], terminals=["complete"],
            execution_backend=cfg,
        )
        executor = WorkflowExecutor(
            workflow,
            mock_session, MagicMock(), mock_prompt_loader, MagicMock(),
            project_dir=str(tmp_path), output_dir=str(tmp_path),
        )

        assert executor.exec_backend is not None
        call_ctx = mock_prompt_loader.load_prompt.call_args[0][1]
        assert "None" not in call_ctx["candidate_images"]
        assert "good:1" in call_ctx["candidate_images"]
        assert "also-good:2" in call_ctx["candidate_images"]
