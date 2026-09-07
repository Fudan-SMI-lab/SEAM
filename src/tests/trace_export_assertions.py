from __future__ import annotations

from pathlib import Path

from harness.session.opencode_contract import JsonObject
from harness.session.opencode_contract_json import load_json
from harness.session.trace_export_models import TraceExportResult
from harness.session.trace_exporter import TraceExportRequest, TraceExporter
from tests.opencode_contract_test_helpers import object_member
from tests.trace_export_test_support import FakeTraceClient, seed


CAPTURED_AT = "2026-07-28T00:00:00+00:00"


def export_root(tmp_path: Path, client: FakeTraceClient) -> TraceExportResult:
    return TraceExporter(client).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            captured_at=CAPTURED_AT,
        )
    )


def read_object(path: Path) -> JsonObject:
    value = load_json(path.read_bytes())
    assert isinstance(value, dict)
    return value


def artifact_path(root: Path, record: JsonObject) -> Path:
    return root / string_member(object_member(record, "artifact"), "path")


def string_member(parent: JsonObject, key: str) -> str:
    value = parent.get(key)
    assert isinstance(value, str)
    return value


def string_list_member(parent: JsonObject, key: str) -> list[str]:
    value = parent.get(key)
    assert isinstance(value, list) and all(isinstance(item, str) for item in value)
    return [item for item in value if isinstance(item, str)]
