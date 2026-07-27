from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Literal, final

import pytest

from harness.session.opencode_contract import JsonObject
from harness.session.trace_export_models import TraceExportError
from harness.session.trace_exporter import TraceExportRequest, TraceExporter
from tests.opencode_contract_test_helpers import object_list_member, object_member
from tests.trace_export_assertions import (
    CAPTURED_AT,
    artifact_path,
    export_root,
    read_object,
    string_list_member,
    string_member,
)
from tests.trace_export_part_fixtures import reasoning_part
from tests.trace_export_part_fixtures import overflow_part
from tests.trace_export_test_support import (
    FakeTraceClient,
    graph,
    seed,
    session_info_capture,
)


def test_root_session_info_is_exact_and_required_for_complete(tmp_path: Path) -> None:
    root = graph("ses_root")
    client = FakeTraceClient({"ses_root": root.retrieval})

    result = export_root(tmp_path, client)

    manifest = read_object(result.manifest_path)
    session = object_list_member(manifest, "sessions")[0]
    payload = read_object(artifact_path(tmp_path / "trace", session))
    assert result.complete is True
    assert client.info_calls == ["ses_root"]
    assert object_member(payload, "session_info")["futureRootInfo"] == {
        "exact": [True, None, 9]
    }
    capture = object_member(payload, "session_info_capture")
    assert capture["raw_body"] is not None


def test_unavailable_root_session_info_is_truthfully_incomplete(tmp_path: Path) -> None:
    root = graph("ses_root")
    client = FakeTraceClient(
        {"ses_root": root.retrieval},
        {"ses_root": session_info_capture("ses_root", status=500)},
    )

    result = export_root(tmp_path, client)

    manifest = read_object(result.manifest_path)
    session = object_list_member(manifest, "sessions")[0]
    assert result.complete is False
    assert "root_session_info_http_500" in string_list_member(session, "reasons")
    assert "root_session_info_http_500" in string_list_member(session, "errors")


def test_fallback_capture_survives_without_typed_fallback_children(
    tmp_path: Path,
) -> None:
    root = graph("ses_root", child_status=404, fallback_capture_only=True)
    client = FakeTraceClient({"ses_root": root.retrieval})

    result = export_root(tmp_path, client)

    manifest = read_object(result.manifest_path)
    session = object_list_member(manifest, "sessions")[0]
    payload = read_object(artifact_path(tmp_path / "trace", session))
    children = object_member(payload, "children")
    fallback = object_member(children, "fallback")
    server = object_member(manifest, "server")
    capabilities = object_member(server, "capabilities_by_session")
    root_capabilities = object_member(capabilities, "ses_root")
    assert result.complete is False
    assert children["traversal_source"] == "none"
    assert fallback["typed_children"] is None
    assert fallback["raw_capture"] is not None
    assert root_capabilities["direct_vs_fallback"] == "fallback_evidence_only"


def test_atomic_write_error_is_a_per_session_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_io

    real_replace = trace_export_io.atomic_replace

    def interrupt_session(source: Path, destination: Path) -> None:
        if destination.parent.name == "sessions":
            raise OSError("session replace interrupted")
        real_replace(source, destination)

    monkeypatch.setattr(trace_export_io, "atomic_replace", interrupt_session)
    result = export_root(
        tmp_path,
        FakeTraceClient({"ses_root": graph("ses_root").retrieval}),
    )

    session = object_list_member(read_object(result.manifest_path), "sessions")[0]
    assert "artifact_write_interrupted" in string_list_member(session, "errors")


def test_cross_edge_cycle_is_classified_as_cycle_not_only_duplicate(
    tmp_path: Path,
) -> None:
    graphs = {
        "ses_root": graph("ses_root", child_ids=("ses_a", "ses_b")).retrieval,
        "ses_a": graph("ses_a", child_ids=("ses_c",)).retrieval,
        "ses_b": graph("ses_b", child_ids=("ses_c",)).retrieval,
        "ses_c": graph("ses_c", child_ids=("ses_b",)).retrieval,
    }

    result = export_root(tmp_path, FakeTraceClient(graphs))

    errors = object_list_member(read_object(result.manifest_path), "errors")
    assert any(
        string_member(error, "code") == "cycle_detected"
        and error["session_id"] == "ses_c"
        for error in errors
    )


def test_surrogate_raw_json_is_persisted_without_abort(tmp_path: Path) -> None:
    part: JsonObject = reasoning_part("ses_root")
    part["text"] = "persisted-\ud800-surrogate"
    root = graph("ses_root", parts=(part,))

    result = export_root(tmp_path, FakeTraceClient({"ses_root": root.retrieval}))

    session = object_list_member(read_object(result.manifest_path), "sessions")[0]
    payload = read_object(artifact_path(tmp_path / "trace", session))
    messages = payload["messages"]
    assert result.complete is True
    assert isinstance(messages, list)


def test_relative_destination_is_rejected_before_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    request = TraceExportRequest(
        destination=Path("relative-trace"),
        seeds=(seed("ses_root"),),
        captured_at=CAPTURED_AT,
    )

    with pytest.raises(TraceExportError):
        _ = TraceExporter(
            FakeTraceClient({"ses_root": graph("ses_root").retrieval})
        ).export(request)

    assert not (tmp_path / "relative-trace").exists()


def test_overflow_source_identity_change_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "output.bin"
    _ = source.write_bytes(b"original")
    root = graph(
        "ses_root",
        parts=(overflow_part("ses_root", "prt_swapped", str(source)),),
    )
    from harness.session import trace_export_io

    real_open = trace_export_io.source_open
    swapped = False

    def swap_before_read(path: Path, mode: Literal["rb"]) -> BinaryIO:
        nonlocal swapped
        if path == source and mode == "rb" and not swapped:
            swapped = True
            source.unlink()
            _ = source.write_bytes(b"replacement")
        return real_open(path, mode)

    monkeypatch.setattr(trace_export_io, "source_open", swap_before_read)
    result = TraceExporter(FakeTraceClient({"ses_root": root.retrieval})).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            overflow_roots=(allowed,),
            captured_at=CAPTURED_AT,
        )
    )

    overflow = object_list_member(read_object(result.manifest_path), "overflows")[0]
    assert result.complete is False
    assert overflow["status"] == "unsafe"
    assert overflow["artifact"] is None


@final
class TestTraceExportReviewCases:
    test_atomic_write_error_is_a_per_session_error = staticmethod(
        test_atomic_write_error_is_a_per_session_error
    )
    test_cross_edge_cycle_is_classified_as_cycle_not_only_duplicate = staticmethod(
        test_cross_edge_cycle_is_classified_as_cycle_not_only_duplicate
    )
    test_fallback_capture_survives_without_typed_fallback_children = staticmethod(
        test_fallback_capture_survives_without_typed_fallback_children
    )
    test_overflow_source_identity_change_is_rejected = staticmethod(
        test_overflow_source_identity_change_is_rejected
    )
    test_relative_destination_is_rejected_before_creation = staticmethod(
        test_relative_destination_is_rejected_before_creation
    )
    test_root_session_info_is_exact_and_required_for_complete = staticmethod(
        test_root_session_info_is_exact_and_required_for_complete
    )
    test_surrogate_raw_json_is_persisted_without_abort = staticmethod(
        test_surrogate_raw_json_is_persisted_without_abort
    )
    test_unavailable_root_session_info_is_truthfully_incomplete = staticmethod(
        test_unavailable_root_session_info_is_truthfully_incomplete
    )


__all__ = ["TestTraceExportReviewCases"]
