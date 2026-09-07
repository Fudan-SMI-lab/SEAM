"""Trace client/seed builders for the direct-seal trace lifecycle test.

Kept separate from the continuation support module to stay under the 250
pure-LOC ceiling. Both parent and child trace clients are production-shaped
``OpenCodeTraceClient`` instances backed by ``FakeTraceClient`` graphs; they
are observable inputs to the real trace exporter, not mock outputs.

No production sealing, evidence staging, or artifact-store mutation is
performed here.  The trace directory is written by the real trace exporter
to ``report_dir/trace``; production sealing records only artifact-store
evidence.  The two are independent authorities verified separately by the
trace lifecycle test.
"""

from __future__ import annotations

from tests.trace_export_part_fixtures import task_part
from tests.trace_export_test_support import FakeTraceClient, graph, seed

from .e2e_v3_runtime_fakes import concrete_trace_client
from harness.session.opencode_trace_client import OpenCodeTraceClient
from harness.session.trace_seeds import TraceSeed

TRACE_SENTINEL = "PARENT_TRACE_SENTINEL_B3A7F2E1"
TRACE_SENTINEL_BYTES = TRACE_SENTINEL.encode("utf-8")


def build_parent_trace_client(session_id: str) -> OpenCodeTraceClient:
    """Trace client whose exported session payload contains the sentinel.

    The sentinel text flows through the real trace exporter and lands in
    ``trace/sessions/<sha256>.json`` inside the parent report directory.
    """
    parent_graph = graph(
        session_id,
        parts=({
            "id": "prt_sentinel",
            "sessionID": session_id,
            "messageID": f"msg_{session_id}",
            "type": "text",
            "text": TRACE_SENTINEL,
            "time": {"start": 1, "end": 2},
        },),
    )
    fake = FakeTraceClient({session_id: parent_graph.retrieval})
    return concrete_trace_client(fake, (session_id,))


def build_child_trace_client(
    root_id: str, child_id: str
) -> OpenCodeTraceClient:
    """Trace client for the child continuation: a root + child graph.

    The child graph has no sentinel — the child must not duplicate the
    parent's raw trace payload.  The child's correlation manifest carries
    run-lineage (run ID, parent run ID) but does not reference the parent
    trace hash/size because production does not seal the trace into
    evidence for the direct-seal lifecycle.
    """
    root_graph = graph(
        root_id,
        child_ids=(child_id,),
        parts=(task_part(root_id, child_id),),
    )
    child_graph = graph(child_id)
    fake = FakeTraceClient({
        root_id: root_graph.retrieval,
        child_id: child_graph.retrieval,
    })
    return concrete_trace_client(fake, (root_id, child_id))


def child_trace_seeds(root_id: str) -> tuple[TraceSeed, ...]:
    return (seed(root_id),)
