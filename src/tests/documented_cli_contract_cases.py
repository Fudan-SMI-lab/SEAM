from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest

from core.continuation_evidence_io import allocate_child_evidence_namespace
from harness.session.trace_export_models import TraceExportRequest
from harness.session.trace_export_transaction import TraceExportTransaction
from tests import documented_cli_contract_support as support
from tests.documented_cli_contract_support import (
    ROOT,
    run_optional_generic_cpu_docker,
    run_optional_real_opencode,
)

TRACE_TABLE = re.compile(
    "".join(
        (
            r"<!-- trace-contract:boundaries:start -->(?P<body>.*?)",
            r"<!-- trace-contract:boundaries:end -->",
        )
    ),
    re.DOTALL,
)
TRACE_ROW = re.compile(
    r"^\| `(?P<condition>[a-z_]+)` \| `(?P<state>[a-z]+)` \|$",
    re.MULTILINE,
)


def test_documented_artifact_tree_uses_writer_names(tmp_path: Path) -> None:
    report_dir = tmp_path / "child-run-001"
    report_dir.mkdir()
    namespace = allocate_child_evidence_namespace(report_dir)

    archive_path = namespace.migration_archive_dir.relative_to(report_dir).as_posix()
    manifest_path = namespace.migration_archive_manifest_path.relative_to(
        report_dir
    ).as_posix()
    assert archive_path == "artifacts/pre-continuation/migration-reports"
    assert manifest_path == (
        "artifacts/pre-continuation/migration-reports.manifest.json"
    )

    trace_parent = tmp_path / "trace-parent"
    trace_parent.mkdir()
    request = TraceExportRequest(destination=trace_parent / "trace", seeds=())
    with TraceExportTransaction(request) as transaction:
        assert (transaction.request.destination / "sessions").is_dir()
        assert (transaction.request.destination / "overflows").is_dir()

    guide = (ROOT / "src" / "docs" / "E2E_TESTING.md").read_text(encoding="utf-8")
    assert f"`{archive_path}/`" in guide
    assert f"`{manifest_path}`" in guide
    assert "`trace/sessions/`" in guide
    assert "`trace/overflows/`" in guide


def test_documented_trace_boundaries_are_truthful() -> None:
    design = (ROOT / "src" / "docs" / "full_agent_io_logging_design.md").read_text(
        encoding="utf-8"
    )
    table = TRACE_TABLE.search(design)
    assert table is not None
    assert set(TRACE_ROW.findall(table.group("body"))) == {
        ("direct_children_unsupported_with_fallback", "partial"),
        ("provider_hidden_reasoning", "unavailable"),
        ("trace_controls_run_outcome", "false"),
        ("trace_controls_continuation", "false"),
    }


def test_optional_real_opencode_phase_0_to_3(tmp_path: Path) -> None:
    run_optional_real_opencode(tmp_path)


def test_generic_cpu_docker_contract_never_pulls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def find_docker(_name: str) -> str:
        return "docker"

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, timeout
        calls.append(args)
        if args[1] == "run":
            _ = (tmp_path / "seam-cpu-docker.txt").write_text(
                "SEAM_CPU_DOCKER_OK", encoding="utf-8"
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("SEAM_RUN_GENERIC_CPU_DOCKER", "1")
    monkeypatch.setenv("SEAM_GENERIC_CPU_DOCKER_IMAGE", "local/python-cpu:test")
    monkeypatch.setattr(support, "find_executable", find_docker)
    monkeypatch.setattr(support, "run_command", fake_run)

    run_optional_generic_cpu_docker(tmp_path)

    assert calls[0][:3] == ["docker", "image", "inspect"]
    assert "--rm" in calls[1]
    assert "--pull=never" in calls[1]
    assert "--network" in calls[1]
    assert "none" in calls[1]
    assert "--mount" in calls[1]
    assert all(call[1] != "pull" for call in calls)


def test_optional_generic_cpu_docker(tmp_path: Path) -> None:
    run_optional_generic_cpu_docker(tmp_path)
