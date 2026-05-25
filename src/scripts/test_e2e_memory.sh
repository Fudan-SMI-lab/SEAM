#!/usr/bin/env bash
#
# Experience Memory System E2E Test Launcher
# Tests the complete extraction + retrieval + promotion flow.
#
# Usage: ./test_e2e_memory.sh <project_name> [options]
#
# Phase:
#   Run 1: Full migration → Phase 7 extracts experiences → validates index
#   Run 2: Same project → Phase 5 retrieves Run 1 experiences → validates injection
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATION_UTILS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$MIGRATION_UTILS_DIR/.." && pwd)"
OUTPUT_PROJECTS_DIR="${MIGRATION_OUTPUT_PROJECTS_ROOT:-$(dirname "$REPO_ROOT")/output_projects}"
PROJECT_SEARCH_DIRS=(
    "$REPO_ROOT/original_projects"
    "$REPO_ROOT/cuda_projects"
    "$REPO_ROOT/../original_projects"
    "$REPO_ROOT/../cuda_projects"
)

# ── Defaults ──
SERVER_URL="http://127.0.0.1:4098"
MAX_ITER=8
REVIEW_GATE=false
KEEP_TEMP=true
PROJECT_NAME=""
RUN_ONLY=1  # 1=first run, 2=second run, both=both runs

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Usage ──
usage() {
    cat <<'EOF'
Usage: test_e2e_memory.sh <PROJECT_NAME> [OPTIONS]

Options:
  --server-url URL    OpenCode server URL (default: http://127.0.0.1:4098)
  --max-iter N        Max Phase 5 repair iterations (default: 8)
  --run-only N        Run only 1 (extract) or 2 (retrieve), or 'both' (default)
  --no-review         Disable Review Gate (default)
  --no-keep-temp      Don't keep temp output directory
  -h, --help          Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage ;;
        --server-url) SERVER_URL="$2"; shift 2 ;;
        --max-iter) MAX_ITER="$2"; shift 2 ;;
        --run-only) RUN_ONLY="$2"; shift 2 ;;
        --no-review) REVIEW_GATE=false; shift ;;
        --no-keep-temp) KEEP_TEMP=false; shift ;;
        -*) echo -e "${RED}Unknown option: $1${NC}" >&2; exit 1 ;;
        *)
            if [[ -z "$PROJECT_NAME" ]]; then
                PROJECT_NAME="$1"; shift
            else
                echo -e "${RED}Unexpected argument: $1${NC}" >&2; exit 1
            fi ;;
    esac
done

if [[ -z "$PROJECT_NAME" ]]; then
    echo -e "${RED}Error: PROJECT_NAME is required.${NC}" >&2
    usage
fi

resolve_project_dir() {
    local raw="$1"
    if [[ "$raw" = /* || "$raw" == .* || "$raw" == */* ]]; then
        if [[ -d "$raw" ]]; then
            cd "$raw" && pwd
            return 0
        fi
    fi

    local base
    for base in "${PROJECT_SEARCH_DIRS[@]}"; do
        if [[ -d "$base/$raw" ]]; then
            cd "$base/$raw" && pwd
            return 0
        fi
    done
    return 1
}

PROJECT_DIR="$(resolve_project_dir "$PROJECT_NAME" || true)"

# ── Validation ──
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     Experience Memory System — E2E Test Launcher       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Project:${NC}   $PROJECT_NAME"
echo -e "${GREEN}Path:${NC}      $PROJECT_DIR"
echo -e "${GREEN}Server:${NC}    $SERVER_URL"
echo -e "${GREEN}Run:${NC}       $RUN_ONLY"
echo ""

if [[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]]; then
    echo -e "${RED}✗ Project directory not found: $PROJECT_NAME${NC}"
    echo -e "${YELLOW}  Searched:${NC}"
    for base in "${PROJECT_SEARCH_DIRS[@]}"; do
        echo -e "${YELLOW}    - $base/$PROJECT_NAME${NC}"
    done
    exit 1
fi

if ! curl -fsS -o /dev/null --max-time 5 "$SERVER_URL/agent" 2>/dev/null; then
    echo -e "${RED}✗ Server not reachable at $SERVER_URL${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Project exists"
echo -e "${GREEN}✓${NC} Server reachable"
echo ""

# ── Run unit tests first ──
echo -e "${CYAN}── Running E2E unit tests ──${NC}"
cd "$MIGRATION_UTILS_DIR"
python tests/e2e/test_experience_memory.py
echo ""

# ── Run 1: Extract experiences ──
if [[ "$RUN_ONLY" == "1" || "$RUN_ONLY" == "both" ]]; then
    echo -e "${CYAN}── Run 1: Migration + Experience Extraction ──${NC}"
    REVIEW_FLAG=""
    if [[ "$REVIEW_GATE" == true ]]; then
        REVIEW_FLAG="--review-gate"
    fi
    KEEP_FLAG=""
    if [[ "$KEEP_TEMP" == true ]]; then
        KEEP_FLAG="--keep-temp-dir"
    fi

    cd "$REPO_ROOT"
    python -m tests.e2e.e2e_test_v2 \
        --server-url "$SERVER_URL" \
        --project-dir "$PROJECT_DIR" \
        --output-dir "$OUTPUT_PROJECTS_DIR" \
        --max-phase5-iter "$MAX_ITER" \
        $KEEP_FLAG \
        $REVIEW_FLAG \
        --verbose || true

    echo ""
    echo -e "${YELLOW}── Checking extracted experiences ──${NC}"
    if [[ -d "$REPO_ROOT/memory/index" ]]; then
        INDEX_COUNT=$(wc -l < "$REPO_ROOT/memory/index/cases.jsonl" 2>/dev/null || echo 0)
        echo -e "Index entries: ${CYAN}$INDEX_COUNT${NC}"
        if [[ $INDEX_COUNT -gt 0 ]]; then
            head -3 "$REPO_ROOT/memory/index/cases.jsonl"
        fi
    else
        echo -e "${RED}✗ Index directory not found${NC}"
    fi

    if [[ -d "$REPO_ROOT/memory/staging" ]]; then
        STAGING_RUNS=$(find "$REPO_ROOT/memory/staging" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l)
        echo -e "Staging runs: ${CYAN}$STAGING_RUNS${NC}"
    fi

    if [[ -d "$REPO_ROOT/skills" ]]; then
        SKILL_COUNT=$(find "$REPO_ROOT/skills" -name "SKILL.md" 2>/dev/null | wc -l)
        echo -e "Promoted skills: ${CYAN}$SKILL_COUNT${NC}"
    fi
    echo ""
fi

# ── Run 2: Retrieve experiences ──
if [[ "$RUN_ONLY" == "2" || "$RUN_ONLY" == "both" ]]; then
    echo -e "${CYAN}── Run 2: Migration + Experience Retrieval ──${NC}"
    echo -e "${YELLOW}(Phase 5 analyze_error should retrieve experiences from Run 1)${NC}"
    echo ""
    REVIEW_FLAG=""
    if [[ "$REVIEW_GATE" == true ]]; then
        REVIEW_FLAG="--review-gate"
    fi
    KEEP_FLAG=""
    if [[ "$KEEP_TEMP" == true ]]; then
        KEEP_FLAG="--keep-temp-dir"
    fi

    cd "$REPO_ROOT"
    python -m tests.e2e.e2e_test_v2 \
        --server-url "$SERVER_URL" \
        --project-dir "$PROJECT_DIR" \
        --output-dir "$OUTPUT_PROJECTS_DIR" \
        --max-phase5-iter "$MAX_ITER" \
        $KEEP_FLAG \
        $REVIEW_FLAG \
        --verbose || true

    echo ""
    echo -e "${YELLOW}── Checking index after Run 2 ──${NC}"
    if [[ -f "$REPO_ROOT/memory/index/cases.jsonl" ]]; then
        INDEX_COUNT=$(wc -l < "$REPO_ROOT/memory/index/cases.jsonl")
        CONSUMED=$(grep -c '"consumed"' "$REPO_ROOT/memory/index/cases.jsonl" 2>/dev/null || echo 0)
        PROMOTED=$(grep -c '"promoted"' "$REPO_ROOT/memory/index/cases.jsonl" 2>/dev/null || echo 0)
        echo -e "Total entries: ${CYAN}$INDEX_COUNT${NC}"
        echo -e "Consumed: ${CYAN}$CONSUMED${NC}"
        echo -e "Promoted: ${CYAN}$PROMOTED${NC}"
    fi

    if [[ -d "$REPO_ROOT/skills" ]]; then
        SKILL_COUNT=$(find "$REPO_ROOT/skills" -name "SKILL.md" 2>/dev/null | wc -l)
        echo -e "Promoted skills: ${CYAN}$SKILL_COUNT${NC}"
        if [[ $SKILL_COUNT -gt 0 ]]; then
            find "$REPO_ROOT/skills" -name "SKILL.md" -exec echo "  - {}" \;
        fi
    fi
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  E2E Memory Test Complete${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
