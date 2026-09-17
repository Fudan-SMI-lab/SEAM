import json
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.review_gate import ReviewGate
from core.run_outcome import ReviewVerdict


def test_review_gate_survives_phase_artifacts_and_checkpoint(tmp_path):
    store = ArtifactStore(str(tmp_path), "review-evidence")
    gate = ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
    output = {"status": "success", "review_gate": gate, "loop_state": {"_review_gate": gate}}
    raw = store.save_phase_output("phase_5_validation", output)
    store.mark_validated("phase_5_validation", output)
    expected = json.loads(Path(raw).read_text())
    assert expected["review_gate"]["rounds"][0]["outcome"] == "accepted"
    assert store.load_phase_output("phase_5_validation") == expected
    store.save_checkpoint({"phase_5_validation": output})
    assert store.load_checkpoint()["phase_5_validation"] == expected
