from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from core.execution_backend import ContainerBackend, LocalBackend
from core.types import ExecutionBackendConfig
from validators.validate_venv import validate


def test_local_backend_characterization_reports_local_without_resource_probe() -> None:
    # Given the existing host execution backend.
    backend = LocalBackend()

    # When its environment seam is queried.
    result = backend.probe_environment()

    # Then local execution stays explicit rather than resembling a container probe.
    assert result == {
        "status": "local",
        "error": "probe not applicable in local mode",
    }


@patch("core.execution_backend.subprocess.run")
def test_image_backend_characterization_probe_is_read_only(
    run: MagicMock,
) -> None:
    # Given an image-created container already allocated by the backend.
    run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "status": "ok",
                "interpreter_path": "/usr/bin/python3",
                "python_version": "3.10.12",
            }
        ),
        stderr="",
    )
    backend = ContainerBackend(
        ExecutionBackendConfig(mode="container", source="image", image="cpu:test")
    )
    backend._container_id = "created-123"

    # When the existing environment probe runs.
    result = backend.probe_environment()

    # Then it probes the bound identity and never invokes container creation.
    assert result["container_id"] == "created-123"
    assert result["status"] == "ok"
    command = run.call_args.args[0]
    assert command[:2] == ["docker", "exec"]
    assert "run" not in command[:2]


@patch("core.execution_backend.subprocess.run")
def test_existing_container_characterization_preserves_user_resource(
    run: MagicMock,
) -> None:
    # Given a user-selected running container even when legacy cleanup is true.
    run.return_value = MagicMock(
        returncode=0, stdout="running|immutable-user-dev\n", stderr=""
    )
    backend = ContainerBackend(
        ExecutionBackendConfig(
            mode="container",
            source="existing_container",
            container_name="user-dev",
            cleanup=True,
        )
    )
    backend.preflight()
    calls_after_preflight = run.call_count

    # When backend cleanup executes.
    backend.cleanup()

    # Then attachment identity remains and no stop/remove command is issued.
    assert backend._container_id == "immutable-user-dev"
    assert run.call_count == calls_after_preflight


def test_phase2_characterization_accepts_report_without_promoting_it() -> None:
    # Given the current Agent Phase 2 response contract.
    report = json.loads(
        '{"venv_path":"/workspace/.venv",'
        '"python_path":"/workspace/.venv/bin/python",'
        '"installed_packages":["torch==2.1.0"]}'
    )

    # When the existing structural validator runs.
    result = validate(report)

    # Then it accepts the report but adds no framework-observed runtime fields.
    assert result["passed"] is True
    assert set(report) == {"venv_path", "python_path", "installed_packages"}
