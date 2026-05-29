# pyright: reportPrivateUsage=false

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# pylint: disable-next=wrong-import-position; silent
from core.phase_runner import PhaseRunner, PhaseSpec, _rewrite_container_to_host_path
from core.prompt_loader import PromptLoader  # pylint: disable=wrong-import-position; silent
from core.validator_engine import ValidatorEngine  # pylint: disable=wrong-import-position; silent
# pylint: disable-next=wrong-import-position; silent
from validators.validate_entry_script import REQUIRED_REPORT_TOKENS, validate

# ── _rewrite_container_to_host_path ────────────────────────────────────


def test_rewrite_container_to_host_path_basic():
    result = _rewrite_container_to_host_path(
        "/workspace/validate_fwi.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/srv/projects/my-project/validate_fwi.py"


def test_rewrite_container_to_host_path_deep_path():
    result = _rewrite_container_to_host_path(
        "/workspace/tests/unit/test_validate.py",
        "/src/project",
        "/workspace",
    )
    assert result == "/src/project/tests/unit/test_validate.py"


def test_rewrite_container_to_host_path_no_match():
    result = _rewrite_container_to_host_path(
        "/home/user/outside.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/home/user/outside.py"


def test_rewrite_container_to_host_path_empty_path():
    result = _rewrite_container_to_host_path(
        "",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == ""


def test_rewrite_container_to_host_path_exact_container_workdir():
    result = _rewrite_container_to_host_path(
        "/workspace",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/srv/projects/my-project"


def test_rewrite_container_to_host_path_with_trailing_slash_in_workdir():
    result = _rewrite_container_to_host_path(
        "/workspace/validate.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/srv/projects/my-project/validate.py"


def test_rewrite_container_to_host_path_prefix_boundary_no_false_match():
    result = _rewrite_container_to_host_path(
        "/workspace2/file.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/workspace2/file.py"


def test_rewrite_container_to_host_path_prefix_boundary_deeper_no_match():
    result = _rewrite_container_to_host_path(
        "/workspace_backup/deep/file.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/workspace_backup/deep/file.py"


def test_rewrite_container_to_host_path_trailing_slash_boundary_still_matches():
    result = _rewrite_container_to_host_path(
        "/workspace/validate.py",
        "/srv/projects/my-project",
        "/workspace/",
    )
    assert result == "/srv/projects/my-project/validate.py"


def test_rewrite_container_to_host_path_trailing_slash_boundary_exact():
    result = _rewrite_container_to_host_path(
        "/workspace/",
        "/srv/projects/my-project",
        "/workspace/",
    )
    assert result == "/srv/projects/my-project"


# ── Phase 3 container path normalization ──────────────────────────────


def test_normalize_phase3_container_paths_normalizes_entry_script():
    runner = PhaseRunner(_noop_session_mgr(), _null_store(), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(  # pylint: disable=protected-access; silent
        spec,
        {
            "entry_script_path": "/workspace/validate_fwi.py",
            "run_command": "python3 /workspace/validate_fwi.py",
        },
        {
            "project_dir": "/srv/projects/test-project",
            "container_workdir": "/workspace",
        },
        {"previous_outputs": {}},
    )

    assert normalized["entry_script_path"] == "/srv/projects/test-project/validate_fwi.py"
    assert normalized["run_command"] == "python3 /workspace/validate_fwi.py"


def test_normalize_phase3_container_paths_normalizes_reports_dir():
    runner = PhaseRunner(_noop_session_mgr(), _null_store(), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(  # pylint: disable=protected-access; silent
        spec,
        {
            "entry_script_path": "/srv/projects/test-project/validate_fwi.py",
            "reports_dir": "/workspace/migration_reports",
            "run_command": "python3 /workspace/validate_fwi.py",
        },
        {
            "project_dir": "/srv/projects/test-project",
            "container_workdir": "/workspace",
        },
        {"previous_outputs": {}},
    )

    assert normalized["reports_dir"] == "/srv/projects/test-project/migration_reports"


def test_normalize_phase3_container_paths_does_not_rewrite_run_command():
    runner = PhaseRunner(_noop_session_mgr(), _null_store(), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(  # pylint: disable=protected-access; silent
        spec,
        {
            "entry_script_path": "/workspace/v.py",
            "run_command": "python3 /workspace/deep/nested/script.py",
        },
        {
            "project_dir": "/srv/projects/test-project",
            "container_workdir": "/workspace",
        },
        {"previous_outputs": {}},
    )

    assert normalized["run_command"] == "python3 /workspace/deep/nested/script.py"


def test_normalize_phase3_container_paths_skips_without_context():
    runner = PhaseRunner(_noop_session_mgr(), _null_store(), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(  # pylint: disable=protected-access; silent
        spec,
        {"entry_script_path": "/workspace/v.py"},
        {"project_dir": "/tmp"},
        {"previous_outputs": {}},
    )

    assert normalized["entry_script_path"] == "/workspace/v.py"


def test_normalize_phase3_container_paths_uses_container_project_dir_as_fallback():
    runner = PhaseRunner(_noop_session_mgr(), _null_store(), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(  # pylint: disable=protected-access; silent
        spec,
        {"entry_script_path": "/workspace/v.py"},
        {
            "project_dir": "/srv/projects/test-project",
            "container_project_dir": "/workspace",
        },
        {"previous_outputs": {}},
    )

    assert normalized["entry_script_path"] == "/srv/projects/test-project/v.py"


# ── Build report token presence ───────────────────────────────────────


def test_required_report_tokens_include_build():
    assert "build" in REQUIRED_REPORT_TOKENS


def test_validator_rejects_missing_build_token_in_custom_op(tmp_path: Path):
    script = tmp_path / "validate.py"
    script.write_text("print('ok')\n")
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()

    report_paths_without_build = [
        str(reports_dir / "operator_inventory.json"),
        str(reports_dir / "migration_manifest.json"),
        str(reports_dir / "preflight.json"),
        str(reports_dir / "baseline.json"),
        str(reports_dir / "runtime_coverage.json"),
        str(reports_dir / "performance.json"),
        str(reports_dir / "implementation_resolution.json"),
        str(reports_dir / "custom_op_final_gate.json"),
        str(reports_dir / "evidence_validation.json"),
        str(reports_dir / "summary.json"),
    ]

    data = {
        "entry_script_path": str(script),
        "run_command": f"python3 {script}",
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_discovery_sources": list(
            {"source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"}
        ),
        "operator_inventory_schema": {
            "semantic_rows": "one row per fine-grained operator unit",
            "fine_grained_operator_units": ["unit1"],
            "unit_identity": "stable id",
            "variant_or_signature": "v1",
            "native_operator_symbols": ["sym1"],
            "kernel_functions": ["kernel1"],
            "kernel_launch_sites": ["launch1"],
            "public_entry_mapping": "api1",
            "source_evidence": "evidence1",
            "inventory_granularity": "fine_grained",
            "out_of_scope_source_groups": "none",
        },
        "required_report_paths": report_paths_without_build,
        "required_checks": [
            "inventory_manifest_equality",
            "closed_pass_count_equals_manifest_entries",
            "remaining_entries_zero",
            "full_migration_status_full_pass",
            "fine_grained_operator_unit_inventory",
            "kernel_launch_site_inventory",
            "public_entry_mapping",
            "inventory_granularity_fine",
            "per_entry_opp_custom_op_artifact_evidence",
            "per_entry_adapter_evidence",
            "per_entry_parity_evidence",
            "integration_e2e_evidence",
            "same_run_runtime_coverage",
            "performance_evidence",
            "complete_performance_report",
            "overall_speedup_report",
            "no_fallback_no_zero_call_no_builtin_contamination",
            "native_operator_symbol_inventory",
        ],
        "validation_obligations": [
            "project_local_artifact",
            "runtime_project_api",
            "numeric_performance",
            "complete_speedup_report",
            "overall_speedup_report",
            "no_fallback",
        ],
        "phase5_entry_script_revision_allowed": True,
    }

    result = validate(data)
    assert result["passed"] is False, f"Expected failure but got: {result}"
    assert any("build" in error for error in result["errors"])


def test_validator_passes_when_build_token_present(tmp_path: Path):
    script = tmp_path / "validate.py"
    script.write_text("print('ok')\n")
    reports_dir = tmp_path / "migration_reports"
    reports_dir.mkdir()

    report_paths = [
        str(reports_dir / p)
        for p in [
            "operator_inventory.json",
            "migration_manifest.json",
            "preflight.json",
            "baseline.json",
            "runtime_coverage.json",
            "performance.json",
            "build.json",
            "implementation_resolution.json",
            "custom_op_final_gate.json",
            "evidence_validation.json",
            "summary.json",
        ]
    ]

    data = {
        "entry_script_path": str(script),
        "run_command": f"python3 {script}",
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_discovery_sources": list(
            {"source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"}
        ),
        "operator_inventory_schema": {
            "semantic_rows": "one row per fine-grained operator unit",
            "fine_grained_operator_units": ["unit1"],
            "unit_identity": "stable id",
            "variant_or_signature": "v1",
            "native_operator_symbols": ["sym1"],
            "kernel_functions": ["kernel1"],
            "kernel_launch_sites": ["launch1"],
            "public_entry_mapping": "api1",
            "source_evidence": "evidence1",
            "inventory_granularity": "fine_grained",
            "out_of_scope_source_groups": "none",
        },
        "required_report_paths": report_paths,
        "required_checks": [
            "inventory_manifest_equality",
            "closed_pass_count_equals_manifest_entries",
            "remaining_entries_zero",
            "full_migration_status_full_pass",
            "fine_grained_operator_unit_inventory",
            "kernel_launch_site_inventory",
            "public_entry_mapping",
            "inventory_granularity_fine",
            "per_entry_opp_custom_op_artifact_evidence",
            "per_entry_adapter_evidence",
            "per_entry_parity_evidence",
            "integration_e2e_evidence",
            "same_run_runtime_coverage",
            "performance_evidence",
            "complete_performance_report",
            "overall_speedup_report",
            "no_fallback_no_zero_call_no_builtin_contamination",
            "native_operator_symbol_inventory",
        ],
        "validation_obligations": [
            "project_local_artifact",
            "runtime_project_api",
            "numeric_performance",
            "complete_speedup_report",
            "overall_speedup_report",
            "no_fallback",
        ],
        "phase5_entry_script_revision_allowed": True,
    }

    result = validate(data)
    assert result["passed"] is True


# ── E2E: normalized container path passes Phase 3 validation ──────────


def test_phase3_container_path_normalization_allows_validator_to_find_file(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    script = project_dir / "validate_fwi.py"
    script.write_text("print('ok')\n")
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir()

    runner = PhaseRunner(_noop_session_mgr(), _null_store(), PromptLoader(), ValidatorEngine())
    spec = PhaseSpec("phase_3", "phase_3_entry_script", "entry_script")

    normalized = runner._normalize_output(  # pylint: disable=protected-access; silent
        spec,
        {
            "entry_script_path": "/workspace/validate_fwi.py",
            "run_command": "python3 /workspace/validate_fwi.py",
            "entry_script_kind": "custom_op_full_validation",
            "reports_dir": "/workspace/migration_reports",
            "operator_discovery_sources": list(
                {
                    "source",
                    "bindings",
                    "wrappers",
                    "autograd",
                    "aliases",
                    "launch",
                    "setup",
                    "tests",
                }
            ),
            "operator_inventory_schema": {
                "semantic_rows": "one row per fine-grained operator unit",
                "fine_grained_operator_units": ["unit1"],
                "unit_identity": "stable id",
                "variant_or_signature": "v1",
                "native_operator_symbols": ["sym1"],
                "kernel_functions": ["kernel1"],
                "kernel_launch_sites": ["launch1"],
                "public_entry_mapping": "api1",
                "source_evidence": "evidence1",
                "inventory_granularity": "fine_grained",
                "out_of_scope_source_groups": "none",
            },
            "required_report_paths": [
                str(reports_dir / "operator_inventory.json"),
                str(reports_dir / "migration_manifest.json"),
                str(reports_dir / "preflight.json"),
                str(reports_dir / "baseline.json"),
                str(reports_dir / "runtime_coverage.json"),
                str(reports_dir / "performance.json"),
                str(reports_dir / "build.json"),
                str(reports_dir / "implementation_resolution.json"),
                str(reports_dir / "custom_op_final_gate.json"),
                str(reports_dir / "evidence_validation.json"),
                str(reports_dir / "summary.json"),
            ],
            "required_checks": [
                "inventory_manifest_equality",
                "closed_pass_count_equals_manifest_entries",
                "remaining_entries_zero",
                "full_migration_status_full_pass",
                "fine_grained_operator_unit_inventory",
                "kernel_launch_site_inventory",
                "public_entry_mapping",
                "inventory_granularity_fine",
                "per_entry_opp_custom_op_artifact_evidence",
                "per_entry_adapter_evidence",
                "per_entry_parity_evidence",
                "integration_e2e_evidence",
                "same_run_runtime_coverage",
                "performance_evidence",
                "complete_performance_report",
                "overall_speedup_report",
                "no_fallback_no_zero_call_no_builtin_contamination",
                "native_operator_symbol_inventory",
            ],
            "validation_obligations": [
                "project_local_artifact",
                "runtime_project_api",
                "numeric_performance",
                "complete_speedup_report",
                "overall_speedup_report",
                "no_fallback",
            ],
            "phase5_entry_script_revision_allowed": True,
        },
        {
            "project_dir": str(project_dir),
            "container_workdir": "/workspace",
        },
        {"previous_outputs": {}},
    )

    assert normalized["entry_script_path"] == str(script)
    assert normalized["reports_dir"] == str(reports_dir)

    validation = runner.validator.validate("entry_script", normalized)
    assert validation.passed is True, f"validation failed: {validation.errors}"


# ── WorkflowExecutor: _rewrite_container_to_host_path ──────────────────


def test_workflow_executor_rewrites_entry_script_container_path():
    # pylint: disable-next=import-outside-toplevel,reimported; silent
    from core.workflow_executor import _rewrite_container_to_host_path

    result = _rewrite_container_to_host_path(
        "/workspace/validate_fwi.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/srv/projects/my-project/validate_fwi.py"


def test_workflow_executor_rewrites_reports_dir_container_path():
    # pylint: disable-next=import-outside-toplevel,reimported; silent
    from core.workflow_executor import _rewrite_container_to_host_path

    result = _rewrite_container_to_host_path(
        "/workspace/migration_reports",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/srv/projects/my-project/migration_reports"


def test_workflow_executor_rewrite_passthrough_for_non_matching_paths():
    # pylint: disable-next=import-outside-toplevel,reimported; silent
    from core.workflow_executor import _rewrite_container_to_host_path

    result = _rewrite_container_to_host_path(
        "/home/user/outside.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/home/user/outside.py"


def test_workflow_executor_rewrite_prefix_boundary_no_false_match():
    # pylint: disable-next=import-outside-toplevel,reimported; silent
    from core.workflow_executor import _rewrite_container_to_host_path

    result = _rewrite_container_to_host_path(
        "/workspace2/file.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/workspace2/file.py"


def test_workflow_executor_rewrite_prefix_boundary_deeper_no_match():
    # pylint: disable-next=import-outside-toplevel,reimported; silent
    from core.workflow_executor import _rewrite_container_to_host_path

    result = _rewrite_container_to_host_path(
        "/workspace_backup/deep/file.py",
        "/srv/projects/my-project",
        "/workspace",
    )
    assert result == "/workspace_backup/deep/file.py"


def test_workflow_executor_rewrite_trailing_slash_boundary_still_matches():
    # pylint: disable-next=import-outside-toplevel,reimported; silent
    from core.workflow_executor import _rewrite_container_to_host_path

    result = _rewrite_container_to_host_path(
        "/workspace/validate.py",
        "/srv/projects/my-project",
        "/workspace/",
    )
    assert result == "/srv/projects/my-project/validate.py"


# ── WorkflowExecutor: _normalize_phase3_container_paths ───────────────


def _make_minimal_executor(project_dir: str):
    """Create a WorkflowExecutor with minimal mocks for _normalize_llm_output testing."""
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_executor import WorkflowExecutor

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.project_dir = project_dir
    executor._container_context = {}  # pylint: disable=protected-access; silent
    return executor


def test_workflow_executor_normalize_phase3_normalizes_entry_script():
    executor = _make_minimal_executor("/srv/projects/test-project")

    phase = _phase_def("phase_3_entry_script")
    output = {
        "entry_script_path": "/workspace/validate_fwi.py",
        "run_command": "python3 /workspace/validate_fwi.py",
    }
    context = {
        "project_dir": "/srv/projects/test-project",
        "container_workdir": "/workspace",
    }
    state = {"phase_1_project_analysis": {}}

    # pylint: disable-next=protected-access; silent
    normalized = executor._normalize_llm_output(phase, output, context, state)

    assert normalized["entry_script_path"] == "/srv/projects/test-project/validate_fwi.py"
    assert normalized["run_command"] == "python3 /workspace/validate_fwi.py"


def test_workflow_executor_normalize_phase3_normalizes_reports_dir():
    executor = _make_minimal_executor("/srv/projects/test-project")

    phase = _phase_def("phase_3_entry_script")
    output = {
        "entry_script_path": "/srv/projects/test-project/validate.py",
        "reports_dir": "/workspace/migration_reports",
        "run_command": "python3 /workspace/validate.py",
        "entry_script_kind": "custom_op_full_validation",
    }
    context = {
        "project_dir": "/srv/projects/test-project",
        "container_workdir": "/workspace",
    }
    state = {"phase_1_project_analysis": {}}

    # pylint: disable-next=protected-access; silent
    normalized = executor._normalize_llm_output(phase, output, context, state)

    assert normalized["reports_dir"] == "/srv/projects/test-project/migration_reports"
    assert normalized["run_command"] == "python3 /workspace/validate.py"


def test_workflow_executor_normalize_phase3_does_not_rewrite_run_command():
    executor = _make_minimal_executor("/srv/projects/test-project")

    phase = _phase_def("phase_3_entry_script")
    output = {
        "entry_script_path": "/workspace/v.py",
        "run_command": "python3 /workspace/deep/nested/script.py",
    }
    context = {
        "project_dir": "/srv/projects/test-project",
        "container_workdir": "/workspace",
    }
    state = {"phase_1_project_analysis": {}}

    # pylint: disable-next=protected-access; silent
    normalized = executor._normalize_llm_output(phase, output, context, state)

    assert normalized["run_command"] == "python3 /workspace/deep/nested/script.py"


def test_workflow_executor_normalize_phase3_skips_without_container_context():
    executor = _make_minimal_executor("/srv/projects/test-project")

    phase = _phase_def("phase_3_entry_script")
    output = {
        "entry_script_path": "/workspace/v.py",
    }
    context = {
        "project_dir": "/srv/projects/test-project",
    }
    state = {"phase_1_project_analysis": {}}

    # pylint: disable-next=protected-access; silent
    normalized = executor._normalize_llm_output(phase, output, context, state)

    assert normalized["entry_script_path"] == "/workspace/v.py"


def test_workflow_executor_normalize_phase3_preserves_host_paths():
    executor = _make_minimal_executor("/srv/projects/test-project")

    phase = _phase_def("phase_3_entry_script")
    output = {
        "entry_script_path": "/srv/projects/test-project/already_host.py",
    }
    context = {
        "project_dir": "/srv/projects/test-project",
        "container_workdir": "/workspace",
    }
    state = {"phase_1_project_analysis": {}}

    # pylint: disable-next=protected-access; silent
    normalized = executor._normalize_llm_output(phase, output, context, state)

    assert normalized["entry_script_path"] == "/srv/projects/test-project/already_host.py"


# ── container_workdir == "/" root-workdir guard ────────────────────────


@pytest.mark.parametrize(
    "path_str",
    [
        "/any/absolute/path.py",
        "/workspace/file.py",
        "/file.py",
        "/",
    ],
)
def test_rewrite_container_root_workdir_does_not_rewrite_arbitrary_paths(path_str):
    """When container_workdir is '/', no absolute path should be rewritten."""
    result = _rewrite_container_to_host_path(
        path_str,
        "/srv/projects/my-project",
        "/",
    )
    assert result == path_str


@pytest.mark.parametrize(
    "path_str",
    [
        "/any/absolute/path.py",
        "/workspace/file.py",
        "/file.py",
        "/",
    ],
)
def test_workflow_executor_rewrite_root_workdir_does_not_rewrite(path_str):
    """WorkflowExecutor copy of the helper must also guard against root workdir."""
    # pylint: disable-next=import-outside-toplevel; silent
    from core.workflow_executor import _rewrite_container_to_host_path as _we_rewrite

    result = _we_rewrite(
        path_str,
        "/srv/projects/my-project",
        "/",
    )
    assert result == path_str


# ── Helpers ────────────────────────────────────────────────────────────


def _phase_def(phase_id: str):
    """Minimal PhaseDefinition for normalization testing."""
    from core.types import PhaseDefinition  # pylint: disable=import-outside-toplevel; silent

    return PhaseDefinition(
        id=phase_id,
        name=phase_id,
        prompt_template=phase_id,
        output_schema={},
        validator="entry_script",
        transitions={},
        type="llm",
    )


class _NoopSessionManager:
    def get_or_create(self, role: str, lifecycle: str) -> str:
        return f"{role}-{lifecycle}"

    def send_command(self, session_id: str, command: str, timeout=None) -> str:
        raise AssertionError("Unexpected send_command")


def _noop_session_mgr():
    return _NoopSessionManager()


def _null_store():
    # pylint: disable-next=import-outside-toplevel; silent
    from core.artifact_store import ArtifactStore

    return ArtifactStore("/tmp", "null-run")
