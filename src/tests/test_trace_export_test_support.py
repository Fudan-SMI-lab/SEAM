from __future__ import annotations

from harness.session.opencode_contract import Completeness, Compatibility
from harness.session.opencode_trace_models import TraceCapabilityState

from tests.trace_export_test_support import graph


def test_graph_fixture_mirrors_version_tolerant_client_state() -> None:
    fixture = graph("ses_root", version="1.18.10")
    retrieval = fixture.retrieval
    assert retrieval.state is TraceCapabilityState.COMPATIBLE
    assert retrieval.contract.compatibility is Compatibility.COMPATIBLE
    assert retrieval.contract.completeness is Completeness.COMPLETE
    assert retrieval.contract.server_version == "1.18.10"
    assert retrieval.errors == ()


def test_graph_fixture_rejects_missing_version() -> None:
    fixture = graph("ses_root", version="")
    retrieval = fixture.retrieval
    assert retrieval.state is TraceCapabilityState.ERROR
    assert retrieval.contract.compatibility is Compatibility.INCOMPATIBLE
    assert retrieval.contract.completeness is not Completeness.COMPLETE
