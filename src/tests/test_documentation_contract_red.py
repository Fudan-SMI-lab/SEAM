"""RED tests for bug #12 (文档契约 / documentation contract) — T15.

Every artifact that public docs and core code REFERENCE as part of the SEAM
distribution must actually exist at HEAD. This file locks the documented-but-
missing artifacts (pure RED: all fail at HEAD 6018b85/worktree HEAD); T18
(GREEN) must CREATE the missing files to turn this file green.

Missing-items list (referenced somewhere in the repo, ABSENT at HEAD):

1. ``/home/yiding/SEAM/ADAPTATION_REQUIREMENTS.md`` (repo root) — claimed by:
   - README.md L52: "项目根目录下的 ``ADAPTATION_REQUIREMENTS.md`` 会自动加载"
     (auto-load claim; also mirrored in README.en.md / README.zh.md)
   - src/README.md L37 (project layout diagram lists the file)
   - src/scripts/run_e2e.sh L131-135, L178, L201 (existence check +
     ``--user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md`` default)
   - src/scripts/run_e2e_v2.sh L50, L162-168, L234, L258
   - src/scripts/run_e2e_v3.sh L65, L388-394, L552
   - src/scripts/sm_adapt_cli.py L82 (--user-constraints accepts a file path,
     e.g. ``ADAPTATION_REQUIREMENTS.md``)
   - src/docs/e2e_test_guide_deepwave.md L145, L151
   - src/docs/improvement_plan.md L17 (notes the constraints file is not yet
     injected into any LLM prompt — the file itself is absent too)
   - docs/dev_examples/开发时思路示例文档_功能迭代.md L512, L526
   - docs/v1.1.1-dev-change-record.md L158, L170; docs/v1.1.2-dev-change-record.md L167

2. ``src/test_data_and_scripts/`` (cwd contract scripts) — referenced by:
   - src/core/repair_loop.py ``_resolve_script_cwd`` docstring L1139-1147: the
     cwd contract for ``cd /path && python test_data_and_scripts/run_inference.py``;
     after cd-stripping the argv is ``[python, test_data_and_scripts/run_inference.py]``
     and the fix always returns project_dir so the relative script path resolves.
     The referenced script ``test_data_and_scripts/run_inference.py`` is absent.
   - src/README.md L35-41 (documented project layout includes
     ``test_data_and_scripts/run_e2e.py``)
   - src/docs/e2e_test_guide_deepwave.md L42 (optional non-interactive entry
     script, e.g. ``test_data_and_scripts/test_e2e_fwi.py``)
   - src/scripts/run_e2e.sh L139-143 / run_e2e_v2.sh L52, L173-177 /
     run_e2e_v3.sh L67, L399-405 (discover ``test_data_and_scripts/*.py`` as
     Phase-3 entry scripts)
   - src/tests/test_usage_guide_docs.py L43, L50 (usage-guide contract uses
     the string ``python test_data_and_scripts/run_e2e.py`` — file absent)
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

README = ROOT / "README.md"
ADAPTATION_REQUIREMENTS = ROOT / "ADAPTATION_REQUIREMENTS.md"
CWD_SCRIPTS_DIR = ROOT / "src" / "test_data_and_scripts"
REPAIR_LOOP = ROOT / "src" / "core" / "repair_loop.py"


class TestReadmeClaimedAdaptationRequirements:
    """README.md L52 claims ADAPTATION_REQUIREMENTS.md auto-loads at repo root."""

    def test_readme_actually_claims_the_file(self) -> None:
        """Control: the README really makes the auto-load claim (anchor guard).

        If this ever fails, the README claim moved and the RED below must be
        re-anchored instead of T18 creating a file nothing references.
        """
        text = README.read_text(encoding="utf-8")
        assert "ADAPTATION_REQUIREMENTS.md" in text, (
            "README.md no longer mentions ADAPTATION_REQUIREMENTS.md — the "
            "bug #12 anchor drifted; re-anchor before fixing."
        )

    def test_readme_claimed_adaptation_requirements_file_exists(self) -> None:
        """RED: README.md L52 says the repo-root file auto-loads; it is absent."""
        assert ADAPTATION_REQUIREMENTS.is_file(), (
            "README.md L52 claims the project-root ADAPTATION_REQUIREMENTS.md "
            "is auto-loaded, but the file does not exist at HEAD: "
            f"{ADAPTATION_REQUIREMENTS}"
        )


class TestCwdContractScripts:
    """repair_loop.py `_resolve_script_cwd` cwd contract + documented layout."""

    def test_cwd_contract_directory_exists(self) -> None:
        """RED: the cwd-contract scripts directory referenced by docs is absent."""
        assert CWD_SCRIPTS_DIR.is_dir(), (
            "The cwd-contract scripts directory referenced by "
            "src/README.md L35-41 and by repair_loop.py `_resolve_script_cwd` "
            "docstring (L1139-1147) does not exist at HEAD: "
            f"{CWD_SCRIPTS_DIR}"
        )

    def test_cwd_contract_run_inference_script_exists(self) -> None:
        """RED: repair_loop.py's cwd contract names this exact script; missing."""
        script = CWD_SCRIPTS_DIR / "run_inference.py"
        assert script.is_file(), (
            "repair_loop.py `_resolve_script_cwd` docstring (L1139-1147) "
            "documents the cwd contract for "
            "'python test_data_and_scripts/run_inference.py', but the script "
            "does not exist at HEAD: " f"{script}"
        )

    def test_cwd_contract_run_e2e_script_exists(self) -> None:
        """RED: src/README.md documented layout lists this script; missing."""
        script = CWD_SCRIPTS_DIR / "run_e2e.py"
        assert script.is_file(), (
            "src/README.md L35-41 documents the project layout containing "
            "test_data_and_scripts/run_e2e.py (also the usage-guide contract "
            "string in test_usage_guide_docs.py L43/L50), but the script does "
            "not exist at HEAD: " f"{script}"
        )
