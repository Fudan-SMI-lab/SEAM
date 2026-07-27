from __future__ import annotations

from pathlib import Path
from typing import final

import pytest

from harness.session.trace_export_models import TraceExportError
from tests.opencode_contract_test_helpers import object_list_member
from tests.trace_export_assertions import (
    export_root,
    read_object,
    string_list_member,
    string_member,
)
from tests.trace_export_test_support import FakeTraceClient, graph


def test_graph_session_limit_retains_bounded_partial_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_traversal

    monkeypatch.setattr(trace_export_traversal, "MAX_TRACE_SESSIONS", 2)
    graphs = {
        "ses_root": graph("ses_root", child_ids=("ses_a", "ses_b")).retrieval,
        "ses_a": graph("ses_a").retrieval,
        "ses_b": graph("ses_b").retrieval,
    }
    client = FakeTraceClient(graphs)

    result = export_root(tmp_path, client)

    manifest = read_object(result.manifest_path)
    errors = object_list_member(manifest, "errors")
    assert result.complete is False
    assert client.calls == ["ses_root", "ses_a"]
    assert any(
        string_member(error, "code") == "trace_session_limit_exceeded"
        for error in errors
    )


def test_aggregate_artifact_limit_is_structured_and_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_index

    monkeypatch.setattr(trace_export_index, "MAX_TOTAL_ARTIFACT_BYTES", 1)

    result = export_root(
        tmp_path,
        FakeTraceClient({"ses_root": graph("ses_root").retrieval}),
    )

    manifest = read_object(result.manifest_path)
    session = object_list_member(manifest, "sessions")[0]
    assert result.complete is False
    assert session["artifact"] is None
    assert "artifact_size_limit_exceeded" in string_list_member(session, "errors")


def test_manifest_interruption_leaves_no_visible_partial_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from harness.session import trace_export_io

    real_replace = trace_export_io.atomic_replace

    def interrupt_manifest(source: Path, destination: Path) -> None:
        if destination.name == "manifest.json":
            raise OSError("manifest replace interrupted")
        real_replace(source, destination)

    monkeypatch.setattr(trace_export_io, "atomic_replace", interrupt_manifest)

    with pytest.raises(TraceExportError):
        _ = export_root(
            tmp_path,
            FakeTraceClient({"ses_root": graph("ses_root").retrieval}),
        )

    assert not (tmp_path / "trace").exists()
    assert not list(tmp_path.glob(".trace.*.tmp"))


@final
class TestTraceExportLimitCases:
    test_graph_session_limit_retains_bounded_partial_manifest = staticmethod(
        test_graph_session_limit_retains_bounded_partial_manifest
    )
    test_aggregate_artifact_limit_is_structured_and_incomplete = staticmethod(
        test_aggregate_artifact_limit_is_structured_and_incomplete
    )
    test_manifest_interruption_leaves_no_visible_partial_export = staticmethod(
        test_manifest_interruption_leaves_no_visible_partial_export
    )


__all__ = ["TestTraceExportLimitCases"]
