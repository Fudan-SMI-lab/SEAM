#!/usr/bin/env bash
set -euo pipefail

# verify_improvements.sh — Automated verification of 4 framework improvements.
#
# Usage:
#   ./verify_improvements.sh --output-dir <dir> --repo-root <dir>
#
# Checks:
#   1. repair_code_adapter.md contains monkeypatch/composing strategy keywords
#   2. Dependency/code repair prompts and operator_fixer use the current runtime-artifact contract
#   3. phase_error_recovery.md contains artifact_base_path AND raw_attempt_files
#   4. phase_5_review.md contains last_artifact_path AND "Available Runtime Evidence"
#
# Exit codes:
#   0 — all 4 checks pass
#   1 — one or more checks fail

PASS=0
FAIL=0

check() {
    local name="$1"
    local result="$2"  # 0 = pass, non-zero = fail
    if [ "$result" -eq 0 ]; then
        echo "✅ PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "❌ FAIL: $name"
        FAIL=$((FAIL + 1))
    fi
}

# ── Parse arguments ──────────────────────────────────────────────────────
OUTPUT_DIR=""
REPO_ROOT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --repo-root)  REPO_ROOT="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$OUTPUT_DIR" ] || [ -z "$REPO_ROOT" ]; then
    echo "Usage: $0 --output-dir <dir> --repo-root <dir>"
    exit 1
fi

REPO_ROOT="${REPO_ROOT%/}"
if [ -d "${REPO_ROOT}/prompts" ]; then
    PROMPTS_DIR="${REPO_ROOT}/prompts"
elif [ -d "${REPO_ROOT}/src/prompts" ]; then
    PROMPTS_DIR="${REPO_ROOT}/src/prompts"
else
    echo "Could not locate prompts directory under ${REPO_ROOT}" >&2
    PROMPTS_DIR="${REPO_ROOT}/prompts"
fi

# ── Check 1: Code adapter has monkeypatch/composing strategy ─────────────
CODE_ADAPTER="${PROMPTS_DIR}/repair_code_adapter.md"
if [ -f "$CODE_ADAPTER" ]; then
    if grep -qEi "composing|C-Level.*Operators" "$CODE_ADAPTER" 2>/dev/null; then
        check "Check 1: Code adapter has monkeypatch/composing strategy" 0
    else
        check "Check 1: Code adapter has monkeypatch/composing strategy" 1
    fi
else
    check "Check 1: Code adapter has monkeypatch/composing strategy (file missing)" 1
fi

# ── Check 2: Repair prompts expose the expected diagnostics contract ───────
ADAPT_MISSING=0
DEP_PROMPT="${PROMPTS_DIR}/repair_dependency_fixer.md"
if [ ! -f "$DEP_PROMPT" ] \
    || ! grep -q "runtime_error_artifact_path" "$DEP_PROMPT" 2>/dev/null \
    || ! grep -q "runtime_card_artifact_path" "$DEP_PROMPT" 2>/dev/null; then
    ADAPT_MISSING=1
fi
CODE_PROMPT="${PROMPTS_DIR}/repair_code_adapter.md"
if [ ! -f "$CODE_PROMPT" ] \
    || ! grep -q "agent_diagnostics" "$CODE_PROMPT" 2>/dev/null; then
    ADAPT_MISSING=1
fi
OP_PROMPT="${PROMPTS_DIR}/repair_operator_fixer.md"
if [ ! -f "$OP_PROMPT" ] \
    || ! grep -q "runtime_error_artifact_path" "$OP_PROMPT" 2>/dev/null \
    || ! grep -q "runtime_card_artifact_path" "$OP_PROMPT" 2>/dev/null \
    || ! grep -q "operator_custom_op_guidance" "$OP_PROMPT" 2>/dev/null \
    || grep -q "operator_repair_context_artifact_path" "$OP_PROMPT" 2>/dev/null \
    || grep -q "cuda_custom_op_skill_test_prompt" "$OP_PROMPT" 2>/dev/null \
    || grep -q "全部8个要求" "$OP_PROMPT" 2>/dev/null; then
    ADAPT_MISSING=1
fi
check "Check 2: Repair prompt diagnostics contract is current" "$ADAPT_MISSING"

# ── Check 3: Error analyzer prompt has artifact paths ────────────────────
ERROR_RECOVERY="${PROMPTS_DIR}/phase_error_recovery.md"
if [ -f "$ERROR_RECOVERY" ]; then
    HAS_BASE=0
    HAS_RAW=0
    grep -q "artifact_base_path" "$ERROR_RECOVERY" 2>/dev/null && HAS_BASE=1
    grep -q "raw_attempt_files" "$ERROR_RECOVERY" 2>/dev/null && HAS_RAW=1
    if [ "$HAS_BASE" -eq 1 ] && [ "$HAS_RAW" -eq 1 ]; then
        check "Check 3: Error analyzer prompt has artifact paths" 0
    else
        check "Check 3: Error analyzer prompt has artifact paths" 1
    fi
else
    check "Check 3: Error analyzer prompt has artifact paths (file missing)" 1
fi

# ── Check 4: Review prompt has runtime evidence paths ───────────────────
REVIEW_PROMPT="${PROMPTS_DIR}/phase_5_review.md"
if [ -f "$REVIEW_PROMPT" ]; then
    HAS_ARTIFACT=0
    HAS_EVIDENCE=0
    grep -q "last_artifact_path" "$REVIEW_PROMPT" 2>/dev/null && HAS_ARTIFACT=1
    grep -q "Available Runtime Evidence" "$REVIEW_PROMPT" 2>/dev/null && HAS_EVIDENCE=1
    if [ "$HAS_ARTIFACT" -eq 1 ] && [ "$HAS_EVIDENCE" -eq 1 ]; then
        check "Check 4: Review prompt has runtime evidence paths" 0
    else
        check "Check 4: Review prompt has runtime evidence paths" 1
    fi
else
    check "Check 4: Review prompt has runtime evidence paths (file missing)" 1
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then
    echo "✅ All 4 improvements verified successfully!"
    exit 0
else
    echo "❌ $FAIL improvement(s) not applied correctly"
    exit 1
fi
