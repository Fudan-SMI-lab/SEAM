"""OpenCode trace documentation contract.

Locks the public OpenCode trace documentation to the verified
capability-by-shape behavior independently confirmed in todos 1-4 of
.opencode-trace-capability-version-tolerance:

* Endpoint HTTP status and response body shape are the sole authority for
  capability. A healthy non-empty product version (including non-reference
  versions) is retained as ``manifest.server.versions`` metadata.
* ``PINNED_VERSION == "1.18.5"`` is the verified reference/deployment baseline
  only; it is publicly exported but is not a runtime equality gate.
* A version string that merely differs from the reference version is
  non-authoritative (non-gating); observed shapes/errors still decide.
* Missing, non-string or malformed version evidence keeps the existing
  fail-closed UNKNOWN/ERROR boundary.

The broad ``tests/test_documented_cli_contracts.py`` regression module stays
unchanged and continues to cover the CLI/artifact-tree/optional-check contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DOC = ROOT / "src" / "docs" / "full_agent_io_logging_design.md"
E2E_DOC = ROOT / "src" / "docs" / "E2E_TESTING.md"

# Stale blanket exact-pin unsupported claims that must not survive the rewrite.
# Chinese and English variants are both rejected so future drift in either
# direction is caught.
STALE_EXACT_PIN_CLAIMS = (
    "OpenCode v1.18.5 feature detection",
    "Pinned OpenCode feature detection",
    "Raw trace 和 OpenCode v1.18.5",
    "非 pinned version 为 unsupported",
    "non-pinned version is unsupported",
    "non-pinned versions are unsupported",
)


def _read(path: Path) -> str:
    assert path.exists(), f"documentation file missing: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "doc",
    [DESIGN_DOC, E2E_DOC],
    ids=["design", "e2e"],
)
def test_opencode_trace_version_capability_comes_from_endpoint_and_body_shape(doc: Path) -> None:
    # Given the public OpenCode trace documentation files.
    text = _read(doc)

    # Then capability is documented as decided by endpoint status and body
    # shape rather than by the product version string alone.
    assert "endpoint" in text
    assert "body" in text
    assert "shape" in text or "schema" in text
    assert "version" in text


@pytest.mark.parametrize(
    "doc",
    [DESIGN_DOC, E2E_DOC],
    ids=["design", "e2e"],
)
def test_opencode_trace_version_metadata_is_documented(doc: Path) -> None:
    text = _read(doc)

    # Then a healthy non-empty product version is documented as retained
    # metadata projected via manifest.server.versions.
    assert "metadata" in text or "元数据" in text
    assert "server.versions" in text or "manifest.server.versions" in text


@pytest.mark.parametrize(
    "doc",
    [DESIGN_DOC, E2E_DOC],
    ids=["design", "e2e"],
)
def test_opencode_trace_reference_version_1_18_5_is_documented_as_baseline(doc: Path) -> None:
    text = _read(doc)

    # Then the verified reference version 1.18.5 / PINNED_VERSION is documented
    # as the reference/deployment baseline only.
    assert "1.18.5" in text
    assert "PINNED_VERSION" in text or "reference" in text or "baseline" in text or "参考" in text


@pytest.mark.parametrize(
    "doc",
    [DESIGN_DOC, E2E_DOC],
    ids=["design", "e2e"],
)
def test_opencode_trace_version_mismatch_alone_is_non_gating(doc: Path) -> None:
    text = _read(doc)

    # Then string inequality alone is documented as non-authoritative /
    # non-gating; observed shapes/errors still decide compatibility.
    assert (
        "non-authoritative" in text
        or "non-gating" in text
        or "不单独" in text
        or "不作为" in text
        or "不等于" in text
        or "非 gating" in text
        or "non-authority" in text
        or "不等" in text
    )


@pytest.mark.parametrize(
    "doc",
    [DESIGN_DOC, E2E_DOC],
    ids=["design", "e2e"],
)
def test_opencode_trace_missing_or_malformed_version_remains_fail_closed(doc: Path) -> None:
    text = _read(doc)

    # Then missing, non-string or malformed version/health evidence remains
    # fail-closed (UNKNOWN/ERROR), never silently promoted.
    assert "fail closed" in text or "fail-closed" in text
    assert "malformed" in text


@pytest.mark.parametrize(
    "claim",
    STALE_EXACT_PIN_CLAIMS,
)
def test_opencode_trace_docs_drop_stale_exact_pin_unsupported_claims(claim: str) -> None:
    # Given the rewritten documentation.
    for doc in (DESIGN_DOC, E2E_DOC):
        text = _read(doc)

        # Then the stale blanket exact-pin unsupported wording is absent.
        assert claim not in text, f"stale exact-pin claim {claim!r} still in {doc}"
