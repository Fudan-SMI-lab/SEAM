from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_task16_production_path_executes_on_cpython_38() -> None:
    # Given the project's declared minimum CPython runtime.
    uv = shutil.which("uv")
    assert uv is not None
    script = textwrap.dedent(
        """
        import importlib
        import tempfile
        from enum import Enum
        from pathlib import Path

        modules = (
            "core.execution_env_context",
            "core.execution_env_records",
            "core.execution_env_references",
            "core.resource_manifest_models",
            "core.resource_manifest_paths",
            "core.resource_manifest_provenance",
            "core.resource_manifest_semantics",
            "core.resource_manifest_validation",
            "core.resource_manifest_io",
            "core.resource_manifest_lock",
            "core.resource_manifest",
            "harness.run.resource_manifest_hook",
        )
        for module in modules:
            importlib.import_module(module)

        from core.resource_manifest import (
            BackendFactRequest,
            OpenCodeFactRequest,
            ResourceManifestContext,
            ResourceManifestIdentity,
            ResourceManifestStore,
            build_backend_facts,
            build_initial_manifest,
            build_opencode_facts,
            capture_launcher_facts,
        )
        from harness.run.resource_manifest_hook import resource_manifest_finalization_hook

        class Outcome(str, Enum):
            PASSED = "passed"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "run-cpython38"
            report.mkdir()
            identity = ResourceManifestIdentity(
                run_id="run-cpython38",
                workflow_digest="a" * 64,
                workspace_digest="b" * 64,
            )
            context = ResourceManifestContext.bind(report, identity)
            launcher = context.capture_launcher()
            facts = (
                launcher.facts
                + build_backend_facts(BackendFactRequest(
                    requested_workflow="wf.yaml",
                    effective_workflow="wf.yaml",
                    requested_backend="local",
                    effective_backend="local",
                ))
                + build_opencode_facts(OpenCodeFactRequest(
                    endpoint="http://127.0.0.1:4096",
                    owner_kind="framework",
                ))
            )
            store = ResourceManifestStore.create(
                context, build_initial_manifest(identity, facts, (launcher.receipt,))
            )
            update = resource_manifest_finalization_hook(store)(Outcome.PASSED)
            assert update.telemetry_paths == (
                ("resource_manifest_json", str(store.path)),
            )
        """
    )
    environment = dict(os.environ)
    environment.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})

    # When every Task 16 module and the finalization hook execute under 3.8.
    result = subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--python",
            "3.8",
            "--with",
            "pydantic==2.8.2",
            "--with",
            "typing_extensions>=4.12",
            "python",
            "-c",
            script,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then no newer dependency syntax leaks into the supported runtime path.
    assert result.returncode == 0, result.stderr
