from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from harness.session.trace_exporter import (
    TraceExportRequest,
    TraceExporter,
)
from tests.trace_export_assertions import (
    CAPTURED_AT,
    artifact_path as _artifact_path,
    export_root as _export,
    read_object as _read_object,
    string_list_member as _strings,
    string_member as _string,
)
from tests.trace_export_part_fixtures import (
    overflow_part,
    reasoning_part,
    task_part,
    terminal_parts,
)
from tests.trace_export_client_cases import TestTraceExportClientCases
from tests.trace_export_hardening_cases import TestTraceExportHardeningCases
from tests.trace_export_limit_cases import TestTraceExportLimitCases
from tests.trace_export_review_cases import TestTraceExportReviewCases
from tests.trace_export_test_support import (
    FakeTraceClient,
    graph,
    seed,
    with_duplicate_child,
)

from tests.opencode_contract_test_helpers import object_list_member, object_member

__all__ = [
    "TestTraceExportClientCases",
    "TestTraceExportHardeningCases",
    "TestTraceExportLimitCases",
    "TestTraceExportReviewCases",
]


def test_recursive_export_preserves_raw_values_and_accessible_overflow(
    tmp_path: Path,
) -> None:
    # Given a root -> child -> grandchild graph with raw parts and overflow bytes.
    overflow_root = tmp_path / "managed"
    overflow_root.mkdir()
    overflow = overflow_root / "tool-output.bin"
    overflow_bytes = b"raw\x00overflow\xffbytes"
    _ = overflow.write_bytes(overflow_bytes)
    root = graph(
        "ses_root",
        child_ids=("ses_child",),
        parts=(
            reasoning_part("ses_root"),
            task_part("ses_root", "ses_child"),
            overflow_part("ses_root", "prt_overflow", str(overflow)),
        ),
    )
    child = graph(
        "ses_child",
        child_ids=("ses_grandchild",),
        parts=(task_part("ses_child", "ses_grandchild"),),
    )
    grandchild = graph(
        "ses_grandchild",
        parts=tuple(terminal_parts("ses_grandchild")),
    )
    client = FakeTraceClient(
        {
            "ses_root": root.retrieval,
            "ses_child": child.retrieval,
            "ses_grandchild": grandchild.retrieval,
        }
    )

    # When the typed graph is exported once.
    result = TraceExporter(client).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            overflow_roots=(overflow_root,),
            captured_at=CAPTURED_AT,
        )
    )

    # Then traversal, payloads, hashes, and exact overflow bytes are complete.
    manifest = _read_object(result.manifest_path)
    assert result.complete is True
    assert client.calls == ["ses_root", "ses_child", "ses_grandchild"]
    assert manifest["session_ids"] == ["ses_root", "ses_child", "ses_grandchild"]
    assert object_member(manifest, "counts") == {
        "seed_count": 1,
        "session_count": 3,
        "message_count": 6,
        "part_count": 8,
        "child_edge_count": 2,
        "overflow_reference_count": 1,
        "overflow_copied_count": 1,
    }
    sessions = object_list_member(manifest, "sessions")
    root_record = sessions[0]
    root_payload = _read_object(_artifact_path(tmp_path / "trace", root_record))
    assert root_payload["messages"] == root.messages
    assert root_payload["raw_contract"] == root.retrieval.contract.to_json_value()
    child_record = sessions[1]
    child_payload = _read_object(_artifact_path(tmp_path / "trace", child_record))
    session_info = object_member(child_payload, "session_info")
    assert session_info["futureSessionInfo"] == {"exact": [True, None, 7]}
    copied = object_list_member(manifest, "overflows")[0]
    copied_path = _artifact_path(tmp_path / "trace", copied)
    assert copied_path.read_bytes() == overflow_bytes
    assert copied["size"] == len(overflow_bytes)
    assert copied["sha256"] == hashlib.sha256(overflow_bytes).hexdigest()


def test_cycles_and_duplicate_children_are_deduplicated_and_incomplete(
    tmp_path: Path,
) -> None:
    # Given duplicate root children and a child edge back to the root.
    root = with_duplicate_child(graph("ses_root"), "ses_root", "ses_child")
    child = graph("ses_child", child_ids=("ses_root",))
    client = FakeTraceClient({"ses_root": root.retrieval, "ses_child": child.retrieval})

    # When the graph is exported.
    result = _export(tmp_path, client)

    # Then each session is fetched once and graph defects remain explicit.
    manifest = _read_object(result.manifest_path)
    assert result.complete is False
    assert client.calls == ["ses_root", "ses_child"]
    assert manifest["session_ids"] == ["ses_root", "ses_child"]
    assert {
        _string(item, "code") for item in object_list_member(manifest, "errors")
    } >= {
        "duplicate_child_id",
        "cycle_detected",
    }


def test_child_http_500_retains_root_and_child_accessible_data(tmp_path: Path) -> None:
    # Given a reachable child whose immediate-child endpoint returns HTTP 500.
    root = graph("ses_root", child_ids=("ses_child",))
    child = graph("ses_child", child_status=500)
    client = FakeTraceClient({"ses_root": root.retrieval, "ses_child": child.retrieval})

    # When export captures the partial graph.
    result = _export(tmp_path, client)

    # Then both session payloads remain and the child error prevents completeness.
    manifest = _read_object(result.manifest_path)
    assert result.complete is False
    assert object_member(manifest, "counts")["session_count"] == 2
    child_record = object_list_member(manifest, "sessions")[1]
    assert child_record["errors"] == ["children:http_500"]
    assert _artifact_path(tmp_path / "trace", child_record).is_file()


def test_malformed_messages_are_preserved_without_becoming_authority(
    tmp_path: Path,
) -> None:
    # Given malformed WithParts data retained by Task 3's raw contract.
    malformed = graph("ses_root", malformed_messages=True)
    client = FakeTraceClient({"ses_root": malformed.retrieval})

    # When export processes the incompatible typed result.
    result = _export(tmp_path, client)

    # Then the exact malformed JSON value is saved and completeness is false.
    manifest = _read_object(result.manifest_path)
    session = object_list_member(manifest, "sessions")[0]
    payload = _read_object(_artifact_path(tmp_path / "trace", session))
    assert result.complete is False
    raw_contract = object_member(payload, "raw_contract")
    raw_messages = object_member(raw_contract, "messages")
    assert raw_messages["body"] == [{"malformed": ["retained"]}]
    assert payload["messages"] == []


def test_fallback_children_are_traversed_but_never_claim_direct_completeness(
    tmp_path: Path,
) -> None:
    # Given direct children are unsupported and listing fallback finds one child.
    root = graph("ses_root", child_status=404, fallback_ids=("ses_child",))
    child = graph("ses_child")
    client = FakeTraceClient({"ses_root": root.retrieval, "ses_child": child.retrieval})

    # When export traverses available evidence.
    result = _export(tmp_path, client)

    # Then fallback evidence is raw, traversed, and explicitly partial.
    manifest = _read_object(result.manifest_path)
    root_record = object_list_member(manifest, "sessions")[0]
    payload = _read_object(_artifact_path(tmp_path / "trace", root_record))
    assert client.calls == ["ses_root", "ses_child"]
    assert result.complete is False
    children = object_member(payload, "children")
    assert children["traversal_source"] == "fallback"
    assert object_member(children, "direct")["capability"] == "unsupported"
    assert object_member(children, "fallback")["raw_capture"] is not None


def test_unsafe_missing_remote_and_oversized_overflow_stay_referenced(
    tmp_path: Path,
) -> None:
    # Given every unsafe or inaccessible outputPath class.
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    oversized = allowed / "oversized.bin"
    _ = oversized.write_bytes(b"12345")
    outside = tmp_path / "outside.bin"
    _ = outside.write_bytes(b"outside")
    refs = (
        "https://example.invalid/output",
        "../relative-escape",
        str(allowed / "nested" / ".." / "traversal.bin"),
        str(allowed / "expired.bin"),
        str(outside),
        str(oversized),
        "bad\x00path",
    )
    parts = tuple(
        overflow_part("ses_root", f"prt_overflow_{index}", path)
        for index, path in enumerate(refs)
    )
    client = FakeTraceClient({"ses_root": graph("ses_root", parts=parts).retrieval})

    # When export applies local-root and size safety.
    result = TraceExporter(client).export(
        TraceExportRequest(
            destination=tmp_path / "trace",
            seeds=(seed("ses_root"),),
            overflow_roots=(allowed,),
            captured_at=CAPTURED_AT,
            max_overflow_bytes=4,
        )
    )

    # Then no unsafe path is opened or copied and reasons remain truthful.
    manifest = _read_object(result.manifest_path)
    assert result.complete is False
    assert object_member(manifest, "counts")["overflow_copied_count"] == 0
    assert {
        _string(item, "status") for item in object_list_member(manifest, "overflows")
    } == {
        "remote",
        "relative",
        "path_traversal",
        "missing",
        "outside_allowed_roots",
        "oversized",
        "malformed",
    }


def test_missing_seed_metadata_is_partial_without_fabricated_correlation(
    tmp_path: Path,
) -> None:
    # Given a SEAM seed without logical role or scope.
    client = FakeTraceClient({"ses_root": graph("ses_root").retrieval})
    request = TraceExportRequest(
        destination=tmp_path / "trace",
        seeds=(seed("ses_root", complete=False),),
        captured_at=CAPTURED_AT,
    )

    # When it is exported.
    result = TraceExporter(client).export(request)

    # Then absent correlation metadata stays null and marks the capture partial.
    manifest = _read_object(result.manifest_path)
    session = object_list_member(manifest, "sessions")[0]
    payload = _read_object(_artifact_path(tmp_path / "trace", session))
    assert result.complete is False
    correlation = object_list_member(payload, "seed_correlations")[0]
    assert correlation["logical_role"] is None
    assert "seed_metadata_partial" in _strings(session, "reasons")


def test_session_atomic_write_interruption_keeps_truthful_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given an interrupted atomic replace for one session payload.
    from harness.session import trace_export_io

    real_replace = trace_export_io.atomic_replace
    interrupted = False

    def interrupt_once(source: Path, destination: Path) -> None:
        nonlocal interrupted
        if destination.parent.name == "sessions" and not interrupted:
            interrupted = True
            raise OSError("injected interruption")
        real_replace(source, destination)

    monkeypatch.setattr(trace_export_io, "atomic_replace", interrupt_once)
    client = FakeTraceClient({"ses_root": graph("ses_root").retrieval})

    # When export continues after the isolated artifact failure.
    result = _export(tmp_path, client)

    # Then an atomic manifest survives, reports the omission, and leaves no temp file.
    manifest = _read_object(result.manifest_path)
    assert result.complete is False
    session = object_list_member(manifest, "sessions")[0]
    assert session["artifact"] is None
    assert "artifact_write_interrupted" in _strings(session, "reasons")
    assert not list((tmp_path / "trace").rglob("*.tmp"))
