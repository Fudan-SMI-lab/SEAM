"""T13 RED contract for bug #18 — 报告版本化 / report standardization.

RED tests (each must FAIL on current code with an AssertionError — never an
ImportError) locking the four CURRENT-BEHAVIOR divergences that T16 (GREEN)
must resolve:

1. ``src/schemas/phase_6_reports.json`` declares NO ``version`` /
   ``schema_version`` key at HEAD — the Phase-6 report output schema is
   unversioned. ``$schema`` is only the JSON-Schema meta-schema URL and is
   deliberately NOT treated as a report version. ->
   ``TestPhase6SchemaVersioning::test_schema_declares_version``
2. ``src/validators/validate_reports.py`` performs hand-rolled dict checks and
   never imports ``jsonschema`` / instantiates a draft validator, so a report
   that violates the JSON-Schema types (e.g. ``migration_summary.files_migrated``
   as a string) still passes. ->
   ``TestValidateReportsConsumesSchema::{test_module_references_jsonschema,
   test_rejects_schema_type_violation}``
3. ``V3RuntimeReport.schema_version == "1.0"`` (v3_runtime_report_models.py L78)
   while the Phase-6 schema declares no version — the runtime report version is
   not aligned with the standardized Phase-6 schema version. ->
   ``TestPhase6SchemaVersioning::test_v3_runtime_report_schema_version_matches_schema``
4. ``PhaseRunner.run_phase_6`` hardcodes an empty ``run_timeline``
   (``{"run_started_at": None, "run_ended_at": None, "phases": []}`` at
   phase_runner.py L704-708) while ``WorkflowExecutor._build_run_timeline``
   produces a real timeline (workflow_executor.py L627-645, injected into the
   phase-6 prompt context at L1987). A Phase-6 report built through the
   phase_runner path therefore carries an empty timeline. ->
   ``TestRunTimelineConsistency::test_phase_runner_timeline_not_empty``

Style follows ``src/tests/test_workflow_executor.py``: direct ``core.*`` /
``validators.*`` imports, plain test classes, descriptive failure messages
that cite the current-code fact each assertion locks.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from core import phase_runner  # noqa: F401  (module-under-test; import path per T13)
from core import workflow_executor  # noqa: F401  (module-under-test; import path per T13)
from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner
from core.v3_runtime_report_models import V3RuntimeReport
from validators import validate_reports

_PHASE6_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "phase_6_reports.json"


def _load_phase6_schema() -> dict:
    with open(_PHASE6_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _schema_version() -> str | None:
    """Version declared by the Phase-6 schema, or None when undeclared."""
    schema = _load_phase6_schema()
    return schema.get("version") or schema.get("schema_version")


class TestPhase6SchemaVersioning:
    """Locks divergence 1 (schema version field missing) and 3 (V3 mismatch)."""

    def test_schema_declares_version(self):
        schema = _load_phase6_schema()
        # RED: at HEAD phase_6_reports.json has only the JSON-Schema meta-schema
        # key '$schema' (draft-07 URL) plus title/type/properties/required —
        # NO 'version' / 'schema_version' key. #18 requires a versioned schema.
        assert "version" in schema or "schema_version" in schema, (
            "phase_6_reports.json has no version/schema_version key at HEAD "
            "(keys=%r); #18 requires the Phase-6 report schema to declare a version"
            % sorted(schema)
        )

    def test_v3_runtime_report_schema_version_matches_schema(self):
        declared = _schema_version()
        model_default = V3RuntimeReport.model_fields["schema_version"].default
        # RED: V3RuntimeReport declares schema_version='1.0'
        # (v3_runtime_report_models.py L78), but the Phase-6 schema declares no
        # version at HEAD -> declared is None, so the two cannot match.
        assert model_default == declared, (
            "V3RuntimeReport.schema_version=%r does not match the version "
            "declared in phase_6_reports.json (%r); the schema has no "
            "version/schema_version key at HEAD"
            % (model_default, declared)
        )


class TestValidateReportsConsumesSchema:
    """Locks divergence 2 (validate_reports.py does not consume the schema)."""

    def test_module_references_jsonschema(self):
        module_namespace = validate_reports.__dict__
        jsonschema_markers = {
            "jsonschema",
            "Draft7Validator",
            "JSONSchemaValidator",
            "validate_schema",
        }
        # RED: validators/validate_reports.py only does hand-rolled dict checks
        # (L8-41) and never imports jsonschema nor instantiates a draft validator
        # at HEAD, so none of these names exist in the module namespace.
        assert jsonschema_markers & set(module_namespace), (
            "validators/validate_reports.py never references jsonschema / a "
            "draft validator at HEAD; #18 requires Phase-6 validation to go "
            "through jsonschema against phase_6_reports.json"
        )

    def test_rejects_schema_type_violation(self):
        # migration_summary.files_migrated as a string violates
        # phase_6_reports.json ('type': 'integer'), yet the hand-rolled
        # validator only checks that migration_summary is a dict (L19-20) and
        # does not recurse into its fields — only jsonschema would reject it.
        bad_report = {
            "report_paths": ["/tmp/SUMMARY_REPORT.md"],
            "migration_summary": {
                "files_migrated": "not-an-integer",
                "files_skipped": 0,
            },
        }
        result = validate_reports.validate(bad_report)
        # RED: current code returns {'passed': True, 'errors': [], 'warnings': []}
        assert result["passed"] is False, (
            "validate_reports.validate() accepted a report whose "
            "migration_summary.files_migrated is a string (got %r); only "
            "jsonschema validation against phase_6_reports.json ('type': "
            "'integer') would reject it — the hand-rolled validator passes at HEAD"
            % result
        )


class TestRunTimelineConsistency:
    """Locks divergence 4 (phase_runner hardcodes an empty run_timeline)."""

    def test_phase_runner_timeline_not_empty(self, tmp_path):
        artifact_store = ArtifactStore(str(tmp_path), "testrun")
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = "session_main_engineer"
        session_mgr.send_command.return_value = json.dumps(
            {
                "report_paths": ["/tmp/SUMMARY_REPORT.md"],
                "migration_summary": {"files_migrated": 1, "files_skipped": 0},
            }
        )
        captured: dict = {}
        prompt_loader = MagicMock()
        prompt_loader.load_prompt.side_effect = (
            lambda template, ctx: (captured.update(ctx) or template)
        )
        runner = PhaseRunner(session_mgr, artifact_store, prompt_loader, MagicMock())

        runner.run_phase_6(str(tmp_path), artifact_store, session_mgr)

        timeline = captured["run_timeline"]
        # RED: PhaseRunner.run_phase_6 hardcodes
        #   {'run_started_at': None, 'run_ended_at': None, 'phases': []}
        # at phase_runner.py L704-708, while WorkflowExecutor injects a real
        # timeline from _build_run_timeline() (workflow_executor.py L627-645,
        # wired at L1987). The phase_runner path must carry the same entries.
        assert timeline.get("phases"), (
            "phase_runner path produced an empty run_timeline.phases (%r); "
            "a Phase-6 report must carry the same timeline entries as the "
            "workflow_executor path, not the hardcoded placeholder"
            % timeline
        )
        assert timeline.get("run_started_at") is not None, (
            "phase_runner path produced run_started_at=None (%r); the run "
            "timeline must record when the run actually started, mirroring "
            "WorkflowExecutor._build_run_timeline"
            % timeline
        )
