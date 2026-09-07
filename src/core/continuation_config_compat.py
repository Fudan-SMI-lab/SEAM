from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, runtime_checkable

from core.continuation_lock_identity import read_verified_bytes
from core.types import WorkflowDefinition

_MAX_CONFIG_SOURCE_BYTES = 1024 * 1024


@runtime_checkable
class _WorkflowLoaderModule(Protocol):
    def load_workflow(self, path: str) -> WorkflowDefinition: ...


class WorkflowCompatibilityError(RuntimeError):
    pass


def load_workflow_compat(content: bytes) -> WorkflowDefinition:
    config_path = Path(__file__).with_name("config.py")
    config_source = read_verified_bytes(config_path, _MAX_CONFIG_SOURCE_BYTES)
    with TemporaryDirectory(prefix="seam-config-compat-") as temporary:
        temporary_path = Path(temporary)
        module_path = temporary_path / "config_compat.py"
        workflow_path = temporary_path / "workflow.yaml"
        _ = module_path.write_bytes(
            b"from __future__ import annotations\n" + config_source
        )
        _ = workflow_path.write_bytes(content)
        spec = importlib.util.spec_from_file_location(
            "core._continuation_config_compat_runtime", module_path
        )
        if spec is None or spec.loader is None:
            raise WorkflowCompatibilityError("workflow parser module is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not isinstance(module, _WorkflowLoaderModule):
            raise WorkflowCompatibilityError("workflow parser contract is unavailable")
        workflow = module.load_workflow(str(workflow_path))
    return workflow
