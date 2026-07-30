from __future__ import annotations

from pathlib import Path
import sys

import pytest
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from tests import documented_cli_contract_cases as documented_cases
from tests.e2e import e2e_v3_continuation_shell_cases
from tests.e2e import e2e_v3_runtime_continuation_cases
from tests.documented_cli_contract_support import (
    run_optional_generic_cpu_docker,
    run_optional_real_opencode,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPOSITORY_ROOT / "src" / "pyproject.toml"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "hardware-free-pytest.yml"
PYLINT_WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "pylint.yml"
JUNIT_NAME = "task-26-seam-runtime-continuation-trace.xml"
SUPPORTED_PYTHON_VERSIONS = ("3.10", "3.12")
HARDWARE_FREE_COMMAND = (
    "python -m pytest tests -q -m 'not opencode and not docker and not slow' "
    f"--junitxml=../../.omo/evidence/{JUNIT_NAME}"
)
INSTALL_DEV_COMMAND = "python -m pip install '.[dev]'"
EXPECTED_DEV_EXTRAS = (
    "pytest>=7.4,<9",
    "PyYAML>=6.0,<7",
    "pydantic>=2.8,<3",
    "typing_extensions>=4.12,<5",
    "tomli>=2.0,<3; python_version < '3.11'",
)
EXPECTED_PACKAGES = (
    "core*",
    "harness*",
    "migrator*",
    "rule_strategies*",
    "scripts*",
    "validators*",
    "workflows*",
)
EXPECTED_MARKERS = {
    "integration": (
        "crosses process, filesystem, or component boundaries; remains hardware-free "
        "unless combined with another marker"
    ),
    "opencode": "requires a live OpenCode service and usable model credentials",
    "docker": (
        "requires a local Docker-compatible daemon and a pre-existing generic CPU image"
    ),
    "slow": (
        "may provision an alternate runtime or exceed the mandatory gate's runtime budget"
    ),
    "e2e": (
        "exercises a public SEAM workflow end to end and does not imply external hardware"
    ),
}


def _load_pyproject():
    with PYPROJECT_PATH.open("rb") as stream:
        return tomllib.load(stream)


def _load_workflow(path: Path = WORKFLOW_PATH):
    assert path.is_file(), f"CI contract missing: expected {path}"
    with path.open(encoding="utf-8") as stream:
        workflow = yaml.load(stream, Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)
    return workflow


def test_ci_project_declares_hardware_free_dev_contract() -> None:
    # Given the package metadata used by contributors and CI.
    project = _load_pyproject()

    # When the development extra and pytest marker registry are inspected.
    dev = project["project"]["optional-dependencies"]["dev"]
    markers = project["tool"]["pytest"]["ini_options"]["markers"]
    packages = project["tool"]["setuptools"]["packages"]["find"]["include"]

    # Then dependencies and marker meanings are explicit and hardware-neutral.
    assert project["project"]["requires-python"] == ">=3.10"
    assert tuple(dev) == EXPECTED_DEV_EXTRAS
    assert markers == [
        f"{name}: {description}" for name, description in EXPECTED_MARKERS.items()
    ]
    assert tuple(packages) == EXPECTED_PACKAGES
    assert all(
        forbidden not in " ".join(dev).lower()
        for forbidden in ("cuda", "torch", "npu", "vendor", "docker")
    )


def test_pull_request_workflow_runs_exact_hardware_free_gate() -> None:
    # Given the Task 26 workflow parsed without YAML 1.1's `on` coercion.
    workflow = _load_workflow()

    # When its mandatory job and steps are selected structurally.
    assert workflow["on"] == "pull_request"
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert list(jobs) == ["hardware-free"]
    job = jobs["hardware-free"]
    steps = job["steps"]
    setup = next(
        step for step in steps if step.get("uses") == "actions/setup-python@v5"
    )
    install = next(step for step in steps if step.get("name") == "Install dev extras")
    test = next(step for step in steps if step.get("name") == "Run hardware-free suite")
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")

    # Then a standard CPU runner installs the declared extra and runs the exact gate.
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "20"
    assert job["env"] == {"PYTHONUTF8": "1"}
    assert job["strategy"] == {
        "matrix": {"python-version": list(SUPPORTED_PYTHON_VERSIONS)}
    }
    assert job["defaults"]["run"]["working-directory"] == "src"
    assert setup["name"] == "Set up Python ${{ matrix.python-version }}"
    assert setup["with"] == {"python-version": "${{ matrix.python-version }}"}
    assert install["run"] == INSTALL_DEV_COMMAND
    assert test["run"] == HARDWARE_FREE_COMMAND
    assert checkout["with"] == {"persist-credentials": "false"}
    assert "continue-on-error" not in job
    assert "continue-on-error" not in test
    assert any(step.get("uses") == "actions/checkout@v4" for step in steps)
    assert any(step.get("uses") == "actions/setup-python@v5" for step in steps)


def test_workflow_uploads_failure_evidence_without_privileged_resources() -> None:
    # Given the structurally parsed mandatory workflow.
    workflow = _load_workflow()
    job = workflow["jobs"]["hardware-free"]

    # When the failure artifact step and job capabilities are inspected.
    upload = next(
        step
        for step in job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    )

    # Then failed pytest evidence is retained without services, secrets, or devices.
    assert upload["if"] == "failure()"
    assert upload["with"] == {
        "name": "hardware-free-pytest-junit-${{ matrix.python-version }}",
        "path": f"../.omo/evidence/{JUNIT_NAME}",
        "if-no-files-found": "error",
        "include-hidden-files": "true",
        "retention-days": "7",
    }
    assert not {"services", "container", "environment"} & set(job)
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    assert all(
        forbidden not in workflow_text
        for forbidden in (
            "secrets.",
            "--device",
            "/dev/",
            "cuda_visible_devices",
            "npu_visible_devices",
            "privileged:",
        )
    )


def test_pylint_workflow_uses_supported_linux_interpreters() -> None:
    # Given the push-time lint workflow.
    workflow = _load_workflow(PYLINT_WORKFLOW_PATH)

    # When its runner and interpreter matrix are inspected.
    job = workflow["jobs"]["build"]
    versions = job["strategy"]["matrix"]["python-version"]

    # Then lint runs only on supported Linux Python versions, including the floor.
    assert job["runs-on"] == "ubuntu-latest"
    assert tuple(versions) == SUPPORTED_PYTHON_VERSIONS
    assert "3.8" not in versions
    assert "3.9" not in versions


def test_optional_resource_tests_are_marked_and_skip_without_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given no explicit OpenCode or Docker opt-in in the mandatory environment.
    monkeypatch.delenv("SEAM_RUN_REAL_OPENCODE_PHASE_0_3", raising=False)
    monkeypatch.delenv("SEAM_RUN_GENERIC_CPU_DOCKER", raising=False)
    monkeypatch.delenv("SEAM_GENERIC_CPU_DOCKER_IMAGE", raising=False)

    # When optional helpers and their collection markers are inspected.
    with pytest.raises(pytest.skip.Exception, match="opt in with"):
        run_optional_real_opencode(tmp_path)
    with pytest.raises(pytest.skip.Exception, match="opt in with"):
        run_optional_generic_cpu_docker(tmp_path)
    opencode_marks = {
        marker.name
        for marker in getattr(
            documented_cases.test_optional_real_opencode_phase_0_to_3,
            "pytestmark",
            (),
        )
    }
    docker_marks = {
        marker.name
        for marker in getattr(
            documented_cases.test_optional_generic_cpu_docker,
            "pytestmark",
            (),
        )
    }
    deterministic_marks = {
        marker.name
        for marker in getattr(
            documented_cases.test_generic_cpu_docker_contract_never_pulls,
            "pytestmark",
            (),
        )
    }
    hardware_free_e2e_marks = {
        marker.name
        for test in (
            e2e_v3_runtime_continuation_cases.test_v3_runtime_continuation_anchor_matrix_runs_fresh_child_only,
            e2e_v3_runtime_continuation_cases.test_v3_runtime_child_trace_references_parent_hash_without_copying_payload,
            e2e_v3_continuation_shell_cases.test_v3_launcher_defers_session_diagnostics_until_after_ownership,
        )
        for marker in getattr(test, "pytestmark", ())
    }

    # Then real resources are optional while the deterministic Docker contract is not.
    assert opencode_marks == {"integration", "opencode", "slow"}
    assert docker_marks == {"docker", "integration", "slow"}
    assert not {"docker", "opencode", "slow"} & deterministic_marks
    assert "slow" not in hardware_free_e2e_marks
