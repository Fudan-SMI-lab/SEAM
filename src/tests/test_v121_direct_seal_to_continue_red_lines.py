"""Failing-first red proof: no public direct-run seal-to-continue scenario.

This characterization test locks the *desired* hardware-free coverage
contract for Wave-4 Todo 6 of the v1.2.1 remote-update remediation
workplan:

    At least one hardware-free test must execute a real direct run with
    ``--seal-manifest`` and then invoke the public ``--continue-from
    <summary.json>`` entrypoint against the produced parent (instead of
    constructing a synthetic sealed parent). The matrix must cover
    terminal PASS, FAIL before Phase 5, FAIL at Phase 5 after environment
    establishment with no accepted attempt, and FAIL after accepted
    Phase 5; plus tamper/sealing-failure refusal cases.

At the c6cbed3 baseline no such test exists: every continuation test
constructs its parent synthetically via ``ResourceManifestStore.seal``
(see ``e2e_v3_runtime_continuation_support.create_runtime_parent``) and
the only references to ``--seal-manifest`` live in the runner itself
(``e2e_test_v3.py``) and in this red-lines module. The assertion below
fails for the intended contract: the public seal-to-continue lifecycle
is uncovered.
"""

from __future__ import annotations

import re
from pathlib import Path

_DIRECT_SEAL_TOKENS = (
    re.compile(r"--seal-manifest", re.MULTILINE),
    re.compile(r"\bseal_manifest\s*=\s*True\b", re.MULTILINE),
)
_CONTINUATION_TOKENS = (
    re.compile(r"--continue-from", re.MULTILINE),
    re.compile(r"\brun_terminal_continuation\b", re.MULTILINE),
)
_TEST_FUNC_RE = re.compile(r"^def\s+test_\w+\s*\(", re.MULTILINE)


def _src_root() -> Path:
    import core  # noqa: PLC0415

    return Path(core.__file__).resolve().parent.parent


def _test_files() -> list[Path]:
    return sorted((_src_root() / "tests").rglob("*.py"))


def _file_matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def test_at_least_one_hardware_free_direct_seal_to_continue_scenario_exists() -> None:
    """A direct-run ``--seal-manifest`` followed by public ``--continue-from``.

    Given the entire hardware-free test corpus under ``src/tests``.
    When the corpus is scanned for any file that BOTH contains at least
    one ``def test_`` function AND exercises a real direct-run
    ``--seal-manifest`` (or ``seal_manifest=True``) AND the public
    ``--continue-from`` entrypoint (or ``run_terminal_continuation``).
    Then at least one such file must exist so the public seal-to-continue
    lifecycle is covered. Files without ``def test_`` (notably the
    ``e2e_test_v3`` runner module) are excluded because they are
    production code rather than test scenarios.
    """
    candidates: list[Path] = []
    for py_file in _test_files():
        if py_file.name.startswith("test_v121_"):
            # Red-lines modules under this Todo 1 must not satisfy the
            # coverage they exist to require.
            continue
        text = py_file.read_text(encoding="utf-8")
        if not _TEST_FUNC_RE.search(text):
            # Production runner modules (e.g. e2e_test_v3.py) declare the
            # flags but do not contain test scenarios.
            continue
        if _file_matches_any(text, _DIRECT_SEAL_TOKENS) and _file_matches_any(
            text, _CONTINUATION_TOKENS
        ):
            candidates.append(py_file)

    assert candidates, (
        "No hardware-free test drives the public direct-run seal-to-continue "
        "lifecycle: every continuation test constructs a synthetic parent "
        "(ResourceManifestStore.seal) and no test invokes the real direct "
        "runner with --seal-manifest and then --continue-from against the "
        "produced summary.json. Wave-4 Todo 6 must add this coverage."
    )
