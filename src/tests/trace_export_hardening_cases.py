from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, Literal, final

import pytest

from harness.session.trace_export_models import TraceExportError
from harness.session.trace_exporter import TraceExportRequest, TraceExporter
from tests.opencode_contract_test_helpers import object_list_member, object_member
from tests.trace_export_assertions import (
    CAPTURED_AT,
    export_root,
    read_object,
    string_list_member,
    string_member,
)
from tests.trace_export_part_fixtures import overflow_part
from tests.trace_export_test_support import (
    FakeTraceClient,
    graph,
    seed,
    session_info_capture,
)


def test_graph_edge_limit_bounds_retained_adjacency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_traversal

    monkeypatch.setattr(trace_export_traversal, "MAX_TRACE_EDGES", 1)
    client = FakeTraceClient(
        {
            "ses_root": graph("ses_root", child_ids=("ses_a", "ses_b")).retrieval,
            "ses_a": graph("ses_a").retrieval,
            "ses_b": graph("ses_b").retrieval,
        }
    )

    result = export_root(tmp_path, client)

    manifest = read_object(result.manifest_path)
    assert result.complete is False
    assert client.calls == ["ses_root", "ses_a"]
    assert object_member(manifest, "counts")["child_edge_count"] == 1
    assert any(
        string_member(error, "code") == "trace_edge_limit_exceeded"
        for error in object_list_member(manifest, "errors")
    )


def test_graph_edge_limit_marks_every_affected_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_traversal

    monkeypatch.setattr(trace_export_traversal, "MAX_TRACE_EDGES", 2)
    graphs = {
        "ses_root": graph("ses_root", child_ids=("ses_a", "ses_b")).retrieval,
        "ses_a": graph("ses_a", child_ids=("ses_c",)).retrieval,
        "ses_b": graph("ses_b", child_ids=("ses_d",)).retrieval,
    }

    result = export_root(tmp_path, FakeTraceClient(graphs))

    sessions = object_list_member(read_object(result.manifest_path), "sessions")
    by_id = {string_member(session, "session_id"): session for session in sessions}
    assert result.complete is False
    assert "trace_edge_limit_exceeded" in string_list_member(by_id["ses_a"], "reasons")
    assert "trace_edge_limit_exceeded" in string_list_member(by_id["ses_b"], "reasons")


def test_overflow_reference_limit_bounds_manifest_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_index

    monkeypatch.setattr(trace_export_index, "MAX_OVERFLOW_REFERENCES", 1)
    parts = tuple(
        overflow_part("ses_root", f"prt_{index}", f"https://example.invalid/{index}")
        for index in range(3)
    )

    result = export_root(
        tmp_path,
        FakeTraceClient({"ses_root": graph("ses_root", parts=parts).retrieval}),
    )

    manifest = read_object(result.manifest_path)
    assert result.complete is False
    assert len(object_list_member(manifest, "overflows")) == 1
    assert any(
        string_member(error, "code") == "overflow_reference_limit_exceeded"
        for error in object_list_member(manifest, "errors")
    )


def test_prepare_failure_removes_staging_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_mkdir = Path.mkdir

    def interrupt_sessions_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path.name == "sessions" and path.parent.name.startswith(".trace."):
            raise OSError("sessions mkdir interrupted")
        real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", interrupt_sessions_mkdir)

    with pytest.raises(TraceExportError):
        _ = export_root(
            tmp_path,
            FakeTraceClient({"ses_root": graph("ses_root").retrieval}),
        )

    assert not list(tmp_path.glob(".trace.*.tmp"))


def test_directory_sync_failure_aborts_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_transaction

    def fail_sync(_descriptor: int) -> None:
        raise OSError("directory sync failed")

    monkeypatch.setattr(trace_export_transaction, "DIRECTORY_SYNC_SUPPORTED", True)
    monkeypatch.setattr(trace_export_transaction, "directory_fsync", fail_sync)

    with pytest.raises(TraceExportError):
        _ = export_root(
            tmp_path,
            FakeTraceClient({"ses_root": graph("ses_root").retrieval}),
        )

    assert not (tmp_path / "trace").exists()
    assert not list(tmp_path.glob(".trace.*.tmp"))


def test_manifest_size_limit_removes_unpublished_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_manifest

    monkeypatch.setattr(trace_export_manifest, "MAX_MANIFEST_BYTES", 1)

    with pytest.raises(TraceExportError):
        _ = export_root(
            tmp_path,
            FakeTraceClient({"ses_root": graph("ses_root").retrieval}),
        )

    assert not (tmp_path / "trace").exists()
    assert not list(tmp_path.glob(".trace.*.tmp"))


def test_source_open_error_is_read_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "output.bin"
    _ = source.write_bytes(b"content")
    root = graph(
        "ses_root",
        parts=(overflow_part("ses_root", "prt_denied", str(source)),),
    )
    from harness.session import trace_export_io

    def deny_source(_path: Path, _mode: Literal["rb"]) -> BinaryIO:
        raise PermissionError("source denied")

    monkeypatch.setattr(trace_export_io, "source_open", deny_source)
    result = TraceExporter(FakeTraceClient({"ses_root": root.retrieval})).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            overflow_roots=(allowed,),
            captured_at=CAPTURED_AT,
        )
    )

    overflow = object_list_member(read_object(result.manifest_path), "overflows")[0]
    assert overflow["status"] == "read_error"


def test_oversized_root_info_has_explicit_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import opencode_contract_json

    monkeypatch.setattr(opencode_contract_json, "MAX_CAPTURE_CHARS", 1)
    capture = session_info_capture("ses_root")
    client = FakeTraceClient(
        {"ses_root": graph("ses_root").retrieval},
        {"ses_root": replace(capture, raw_body="oversized")},
    )

    result = export_root(tmp_path, client)

    session = object_list_member(read_object(result.manifest_path), "sessions")[0]
    assert "root_session_info_size_limit_exceeded" in string_list_member(
        session, "reasons"
    )


def test_unavailable_raw_contract_has_explicit_reason(tmp_path: Path) -> None:
    root = graph("ses_root")
    contract = replace(root.retrieval.contract, raw=None)
    retrieval = replace(root.retrieval, contract=contract)

    result = export_root(tmp_path, FakeTraceClient({"ses_root": retrieval}))

    session = object_list_member(read_object(result.manifest_path), "sessions")[0]
    assert "raw_contract_unavailable" in string_list_member(session, "reasons")


@final
class TestTraceExportHardeningCases:
    test_directory_sync_failure_aborts_before_publication = staticmethod(
        test_directory_sync_failure_aborts_before_publication
    )
    test_graph_edge_limit_bounds_retained_adjacency = staticmethod(
        test_graph_edge_limit_bounds_retained_adjacency
    )
    test_graph_edge_limit_marks_every_affected_parent = staticmethod(
        test_graph_edge_limit_marks_every_affected_parent
    )
    test_manifest_size_limit_removes_unpublished_export = staticmethod(
        test_manifest_size_limit_removes_unpublished_export
    )
    test_overflow_reference_limit_bounds_manifest_records = staticmethod(
        test_overflow_reference_limit_bounds_manifest_records
    )
    test_oversized_root_info_has_explicit_reason = staticmethod(
        test_oversized_root_info_has_explicit_reason
    )
    test_prepare_failure_removes_staging_directory = staticmethod(
        test_prepare_failure_removes_staging_directory
    )
    test_source_open_error_is_read_error = staticmethod(
        test_source_open_error_is_read_error
    )
    test_unavailable_raw_contract_has_explicit_reason = staticmethod(
        test_unavailable_raw_contract_has_explicit_reason
    )


__all__ = ["TestTraceExportHardeningCases"]
