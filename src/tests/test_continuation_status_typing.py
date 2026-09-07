from __future__ import annotations

from pathlib import Path

from core.continuation_models import PhasePresentationStatus, SummaryStatus
from core.continuation_paths import read_explicit_summary_snapshot
from tests.terminal_run_continuation_test_support import (
    create_parent_run,
    read_summary,
    write_summary,
)


def test_summary_boundary_parses_persisted_statuses_as_enums(tmp_path: Path) -> None:
    # Given a valid persisted terminal summary.
    parent = create_parent_run(tmp_path)

    # When the summary crosses the continuation parse boundary.
    summary = read_explicit_summary_snapshot(parent.summary_path).document

    # Then summary and phase presentation statuses are distinct typed values.
    assert summary.overall_status is SummaryStatus.PASS
    assert summary.phases[0].status is PhasePresentationStatus.PASSED


def test_summary_boundary_maps_unknown_summary_status_to_enum(tmp_path: Path) -> None:
    # Given a persisted summary containing an arbitrary terminal status token.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    payload["overall_status"] = "CUSTOM"
    write_summary(parent, payload)

    # When the summary crosses the continuation parse boundary.
    summary = read_explicit_summary_snapshot(parent.summary_path).document

    # Then no arbitrary terminal status propagates beyond that boundary.
    assert summary.overall_status is SummaryStatus.UNKNOWN


def test_summary_boundary_maps_unknown_phase_status_to_enum(tmp_path: Path) -> None:
    # Given a persisted summary containing an arbitrary phase status token.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    phases = payload["phases"]
    assert isinstance(phases, list)
    phase = phases[0]
    assert isinstance(phase, dict)
    phase["status"] = "custom"
    write_summary(parent, payload)

    # When the summary crosses the continuation parse boundary.
    summary = read_explicit_summary_snapshot(parent.summary_path).document

    # Then no arbitrary phase status propagates beyond that boundary.
    assert summary.phases[0].status is PhasePresentationStatus.UNKNOWN
