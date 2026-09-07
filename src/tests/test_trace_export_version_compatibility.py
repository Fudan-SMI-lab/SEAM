from __future__ import annotations

from pathlib import Path

import pytest

from tests.opencode_contract_test_helpers import object_list_member, object_member
from tests.trace_export_assertions import (
    export_root as _export,
    read_object as _read_object,
    string_list_member as _strings,
)
from tests.trace_export_test_support import FakeTraceClient, graph


def test_non_pinned_compatible_server_exports_complete_with_version_metadata(
    tmp_path: Path,
) -> None:
    # Given a structurally compatible 1.18.10 graph whose only deviation from
    # the verified reference version is the observed product version string.
    client = FakeTraceClient({"ses_root": graph("ses_root", version="1.18.10").retrieval})

    # When the typed graph is exported once through the real TraceExporter.
    result = _export(tmp_path, client)
    manifest = _read_object(result.manifest_path)

    # Then result, manifest, and the single session are all complete, the
    # session carries no reasons, the observed version is preserved exactly,
    # and no warnings field was introduced into the manifest schema.
    session = object_list_member(manifest, "sessions")[0]
    assert result.complete is True
    assert manifest["complete"] is True
    assert session["complete"] is True
    assert _strings(session, "reasons") == []
    assert object_member(manifest, "server")["versions"] == ["1.18.10"]
    assert "warnings" not in manifest


def test_malformed_messages_export_remains_incomplete_with_contract_incompatible(
    tmp_path: Path,
) -> None:
    # Given a graph whose typed message history is malformed and therefore
    # yields an incompatible parsed contract.
    malformed = graph("ses_root", malformed_messages=True)
    client = FakeTraceClient({"ses_root": malformed.retrieval})

    # When the incompatible typed graph is exported.
    result = _export(tmp_path, client)
    manifest = _read_object(result.manifest_path)

    # Then the session stays incomplete and the contract_incompatible reason
    # remains, proving the removed version gate was not the only fail-closed path.
    session = object_list_member(manifest, "sessions")[0]
    assert result.complete is False
    assert manifest["complete"] is False
    assert session["complete"] is False
    assert "contract_incompatible" in _strings(session, "reasons")


@pytest.mark.parametrize("status", [404, 405])
def test_fallback_children_for_unavailable_direct_endpoint_remain_partial(
    status: int,
    tmp_path: Path,
) -> None:
    # Given a root whose direct children endpoint is unavailable (404/405) and
    # a fallback listing that still surfaces one child.
    root = graph("ses_root", child_status=status, fallback_ids=("ses_child",))
    child = graph("ses_child")
    client = FakeTraceClient(
        {"ses_root": root.retrieval, "ses_child": child.retrieval}
    )

    # When export traverses the available evidence.
    result = _export(tmp_path, client)
    manifest = _read_object(result.manifest_path)

    # Then the root session remains partial and its reasons name the direct
    # children defects that the fallback traversal cannot override.
    root_record = object_list_member(manifest, "sessions")[0]
    assert result.complete is False
    assert root_record["complete"] is False
    reasons = _strings(root_record, "reasons")
    assert "direct_children_unsupported" in reasons
    assert "direct_children_incomplete" in reasons
