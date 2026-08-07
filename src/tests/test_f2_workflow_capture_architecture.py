from __future__ import annotations

from pathlib import Path

from core import workflow_executor


def test_workflow_executor_delegates_retained_output_capture() -> None:
    source = Path(workflow_executor.__file__).read_text(encoding="utf-8")

    assert "capture_shell_output(" in source
    assert "TemporaryFile" not in source
    assert "def _read_tail" not in source
