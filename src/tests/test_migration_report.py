from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.migration_report import (
    ensure_phase6_unified_report,
    generate_migration_report,
    report_structure_errors,
)
from core.migration_report_evidence import collect_report_evidence, redact_report_text
from core.prompt_loader import PromptLoader
from harness.run.finalizer import finalize_run
from harness.run.report_publication import publish_unified_report
from .run_finalizer_test_support import (
    FinalizerScenario,
    failed_finalizer_outcome,
    finalization_request,
)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def bundle(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write_json(
        root / "summary.json",
        {
            "run_id": "run-1",
            "overall_status": "FAIL",
            "total_duration_seconds": 21.345,
            "workflow_path": "npu_ascend_general.yaml",
        },
    )
    artifacts = root / ".sm-artifacts" / "run-1"
    write_json(
        artifacts / "validated" / "phase_0_env_detect_canonical.json",
        {
            "platform": "npu",
            "python_version": "3.11.9",
            "device_name": "Observed accelerator",
        },
    )
    write_json(
        artifacts / "validated" / "phase_1_project_analysis_canonical.json",
        {
            "model_name": "ExampleModel",
            "config": {"hidden_size": 512, "num_hidden_layers": 8},
        },
    )
    write_json(
        artifacts / "validated" / "phase_5_validation_canonical.json",
        {
            "success": True,
            "iteration_count": 2,
            "metrics": {
                "inference_time_seconds": 0.014227636158466339,
                "tokens_per_second": 125.75,
            },
        },
    )
    write_json(
        root / "run_timeline.json",
        {
            "run_started_at": "2026-09-16T00:00:00+00:00",
            "run_ended_at": "2026-09-16T00:00:21+00:00",
            "phases": [{"phase_id": "phase_5_validation", "duration_seconds": 10.25}],
        },
    )
    (root / "SUMMARY_REPORT.md").write_text(
        "# Summary\nOld summary claims PASS.\n", encoding="utf-8"
    )
    (root / "seam_run.log").write_text(
        'inference_time=0.014227636158466339 s\napi_key="real-test-secret"\n'
        "tokens_per_second=125.75\nRuntimeError: final gate rejected\n",
        encoding="utf-8",
    )
    return artifacts


def test_consolidates_evidence_without_promoting_old_success(tmp_path: Path) -> None:
    bundle(tmp_path)
    original = (tmp_path / "SUMMARY_REPORT.md").read_bytes()
    result = generate_migration_report(tmp_path)
    text = result.read_text(encoding="utf-8")
    assert not report_structure_errors(text)
    assert "❌ 失败（框架最终终态）" in text
    assert "Phase 5 success 与最终失败终态不同" in text
    assert "0.014227636158466339" in text
    assert "125.75" in text
    assert "Observed accelerator" in text
    assert "real-test-secret" not in text
    assert "RuntimeError: final gate rejected" in text
    assert "2026-09-16T00:00:00+00:00" in text
    assert (tmp_path / "SUMMARY_REPORT.md").read_bytes() == original
    manifest = json.loads((tmp_path / "migration_report_manifest.json").read_text())
    assert manifest["report_sha256"] == hashlib.sha256(result.read_bytes()).hexdigest()
    facts = json.loads((tmp_path / "migration_report_facts.json").read_text())
    assert facts["phases"]["phase_5_validation"]["metrics"]["tokens_per_second"] == 125.75
    assert facts["log_measurements"][0]["value"] == "0.014227636158466339"


def test_missing_data_is_not_fabricated_and_regeneration_is_stable(tmp_path: Path) -> None:
    (tmp_path / "TOOLS_EXECUTION_REPORT.md").write_text("# Tools\nNo validation occurred.")
    result = generate_migration_report(tmp_path)
    first = result.read_bytes()
    assert "未验证（证据不足）" in first.decode()
    assert "910B2C" not in first.decode()
    assert "实时" not in first.decode()
    generate_migration_report(tmp_path)
    assert result.read_bytes() == first
    assert (
        len(json.loads((tmp_path / "migration_report_manifest.json").read_text())["sources"]) == 1
    )


def test_multiple_runs_require_selection_and_other_run_is_not_read(tmp_path: Path) -> None:
    for run in ("a", "b"):
        write_json(
            tmp_path / ".sm-artifacts" / run / "validated" / "phase_5_validation_canonical.json",
            {"success": run == "a", "unique_marker": "from-" + run},
        )
    with pytest.raises(ValueError, match="Multiple artifact runs"):
        generate_migration_report(tmp_path)
    path = generate_migration_report(tmp_path, run_id="a")
    assert "from-a" in path.read_text()
    assert "from-b" not in path.read_text()


def test_does_not_mix_multiple_output_directories(tmp_path: Path) -> None:
    for run in ("a", "b"):
        write_json(tmp_path / run / "summary.json", {"run_id": run, "overall_status": "PASS"})
    with pytest.raises(ValueError, match="another run"):
        generate_migration_report(tmp_path)


def test_limits_are_visible_symlinks_not_followed_and_tail_is_preserved(tmp_path: Path) -> None:
    secret = tmp_path.parent / (tmp_path.name + "-outside.txt")
    secret.write_text("outside-secret-data")
    (tmp_path / "link.log").symlink_to(secret)
    (tmp_path / "long.log").write_text("start\n" + "x" * 3000 + "\nRuntimeError: tail failure")
    evidence = collect_report_evidence(tmp_path, max_file_bytes=300)
    assert len(evidence.sources) == 1
    assert evidence.sources[0].truncated
    assert "tail failure" in evidence.sources[0].text
    assert evidence.warnings
    output = generate_migration_report(tmp_path, max_file_bytes=300)
    assert "outside-secret-data" not in output.read_text()
    manifest = json.loads((tmp_path / "migration_report_manifest.json").read_text())
    assert manifest["sources"][0]["hash_scope"] == "read_fragments"


def test_invalid_json_and_markdown_fences_do_not_break_report(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text('{"unfinished":')
    (tmp_path / "report.md").write_text("# Existing\n````\n## Fake heading\n````\n")
    path = generate_migration_report(tmp_path)
    assert not report_structure_errors(path.read_text())
    assert "JSON 无法解析" in path.read_text()


def test_rejects_overwriting_source_or_link(tmp_path: Path) -> None:
    source = tmp_path / "SUMMARY_REPORT.md"
    source.write_text("original")
    with pytest.raises(ValueError, match="overwrite"):
        generate_migration_report(tmp_path, output=source)
    (tmp_path / "MIGRATION_REPORT.md").symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        generate_migration_report(tmp_path)
    assert source.read_text() == "original"


@pytest.mark.parametrize("prompt", ["phase_6_report", "phase_6_report_ppu", "phase_6_report_musa"])
def test_all_platform_agents_receive_skill(prompt: str) -> None:
    text = PromptLoader().load_prompt(
        prompt,
        {
            "phase_name": "phase_6_report",
            "project_dir": "/project",
            "report_dir": "/reports",
            "previous_outputs": "{}",
            "run_timeline": "{}",
            "execution_environment_context": "test",
        },
    )
    assert "name: seam-migration-report" in text
    assert "MIGRATION_REPORT.md" in text
    assert "Shape/dtype consistency is not numerical accuracy" in text


def test_phase6_preserves_valid_skill_report_and_recovers_invalid_report(tmp_path: Path) -> None:
    generated = generate_migration_report(tmp_path)
    original = generated.read_text()
    report = ensure_phase6_unified_report(
        {"report_paths": []},
        report_dir=str(tmp_path),
        project_dir="model",
        prior_outputs={},
    )
    assert report["unified_report_mode"] == "skill"
    assert generated.read_text() == original
    generated.write_text("# incomplete")
    report = ensure_phase6_unified_report(
        {"report_paths": []},
        report_dir=str(tmp_path),
        project_dir="model",
        prior_outputs={"phase_5_validation": {"success": False}},
    )
    assert report["unified_report_mode"] == "deterministic"
    assert "Phase 5 验证失败" in generated.read_text()
    assert str(generated) in report["report_paths"]


def test_finalizer_writes_failed_run_report_without_changing_outcome(tmp_path: Path) -> None:
    result = finalize_run(
        finalization_request(
            tmp_path,
            FinalizerScenario(
                authoritative_outcome=failed_finalizer_outcome(),
            ),
        )
    )
    assert result.outcome.value == "failed"
    assert not result.finalization_failed
    assert "❌ 失败" in (tmp_path / "MIGRATION_REPORT.md").read_text()


def test_report_error_is_a_diagnostic_not_a_changed_migration_outcome(tmp_path: Path) -> None:
    protected = tmp_path / "protected.txt"
    protected.write_text("unchanged")
    (tmp_path / "MIGRATION_REPORT.md").symlink_to(protected)
    result = finalize_run(finalization_request(tmp_path, FinalizerScenario()))
    assert result.outcome.value == "passed"
    assert not result.finalization_failed
    assert any(d.stage.value == "unified_report" for d in result.diagnostics)
    assert protected.read_text() == "unchanged"


def test_published_report_and_sidecars_have_matching_checksums(tmp_path: Path) -> None:
    run = tmp_path / "run"
    bundle(run)
    report = generate_migration_report(run)
    project = tmp_path / "project"
    dest = project / "migration_reports"
    dest.mkdir(parents=True)
    write_json(
        dest / "report_manifest.json",
        {"schema_version": "1.0", "reports": [{"name": "SUMMARY_REPORT.md"}]},
    )
    publish_unified_report(report, project)
    manifest = json.loads((dest / "report_manifest.json").read_text())
    assert manifest["reports"][0]["name"] == "SUMMARY_REPORT.md"
    for record in manifest["reports"][1:]:
        assert hashlib.sha256((dest / record["name"]).read_bytes()).hexdigest() == record["sha256"]
    assert (dest / "MIGRATION_REPORT.md").read_bytes() == report.read_bytes()


@pytest.mark.parametrize("overall,runtime", [("PASS", "unavailable"), ("FAIL", "passed")])
def test_final_summary_is_not_overridden_by_runtime_projection(
    tmp_path: Path,
    overall: str,
    runtime: str,
) -> None:
    write_json(
        tmp_path / "summary.json",
        {
            "run_id": "r",
            "overall_status": overall,
            "runtime": {"outcome_status": runtime},
        },
    )
    report = generate_migration_report(tmp_path).read_text()
    expected = "✅ 成功（框架最终终态）" if overall == "PASS" else "❌ 失败（框架最终终态）"
    assert expected in report


def test_phase6_fallback_collects_logs_outside_reports_folder(tmp_path: Path) -> None:
    artifacts = bundle(tmp_path)
    reports = artifacts / "reports"
    reports.mkdir()
    (artifacts / "validation.log").write_text("inference_time=0.123456789 s\n")
    result = ensure_phase6_unified_report(
        {"report_paths": [], "fallback_reason": "timeout"},
        report_dir=str(reports),
        project_dir="model",
        prior_outputs={},
    )
    assert "0.123456789" in (reports / "MIGRATION_REPORT.md").read_text()
    assert result["unified_report_mode"] == "deterministic"
    ensure_phase6_unified_report(
        result,
        report_dir=str(reports),
        project_dir="model",
        prior_outputs={},
    )
    assert result["unified_report_mode"] == "deterministic"


def test_redaction_preserves_many_numeric_token_measurements() -> None:
    text = "\n".join(f"tokens_per_second={index}.125" for index in range(25))
    assert redact_report_text(text) == text
    assert "secret-value" not in redact_report_text('tokens_per_second="secret-value"')


def test_nested_summary_does_not_claim_framework_success(tmp_path: Path) -> None:
    write_json(tmp_path / "reports" / "summary.json", {"overall_status": "PASS"})
    report = generate_migration_report(tmp_path).read_text()
    assert "未验证（证据不足）" in report.split("## 附录")[0]


def test_list_valued_model_and_shape_fields_appear_in_main_report(tmp_path: Path) -> None:
    artifacts = bundle(tmp_path)
    write_json(
        artifacts / "validated" / "phase_1_project_analysis_canonical.json",
        {"model_name": "Example", "config": {"architectures": ["ObservedArchitecture"]}},
    )
    write_json(
        artifacts / "validated" / "phase_5_validation_canonical.json",
        {"success": True, "output_shape": [1, 32, 128]},
    )
    main = generate_migration_report(tmp_path).read_text().split("## 附录")[0]
    assert "ObservedArchitecture" in main
    assert "[1, 32, 128]" in main


def test_optional_report_failure_does_not_abort_phase6(tmp_path: Path) -> None:
    original = tmp_path / "SUMMARY_REPORT.md"
    original.write_text("original summary")
    (tmp_path / "MIGRATION_REPORT.md").symlink_to(original)
    manifest = {"report_paths": [str(original)], "migration_summary": {"files_migrated": 1}}
    result = ensure_phase6_unified_report(
        manifest,
        report_dir=str(tmp_path),
        project_dir="model",
        prior_outputs={},
    )
    assert result["unified_report_mode"] == "failed"
    assert "symlink" in result["unified_report_error"]
    assert result["report_paths"] == [str(original)]
    assert original.read_text() == "original summary"
