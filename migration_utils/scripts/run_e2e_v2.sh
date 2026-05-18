#!/usr/bin/env bash
#
# E2E Migration Test Launcher (V2 — YAML-driven workflow)
# Usage: run_e2e_v2.sh <project_name> [options]
#
# Examples:
#   ./run_e2e_v2.sh 01_Hallo
#   ./run_e2e_v2.sh 02_ChaiLab --dry-run
#   ./run_e2e_v2.sh 07_IndexTTS --max-iter 10
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATION_UTILS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$MIGRATION_UTILS_DIR/.." && pwd)"
OUTPUT_PROJECTS_DIR="$REPO_ROOT/output_projects"
PROJECT_SEARCH_DIRS=(
    "$REPO_ROOT/original_projects"
    "$REPO_ROOT/cuda_projects"
    "$REPO_ROOT/../original_projects"
    "$REPO_ROOT/../cuda_projects"
)

# ── Defaults (mirroring the V1 successful run pattern) ──
SERVER_URL="http://127.0.0.1:4098"
MAX_ITER=8
KEEP_TEMP=true
REVIEW_GATE=true
DRY_RUN=false
EXTRA_ARGS=""

# ── Color helpers ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Usage ──
usage() {
    cat <<'EOF'
Usage: run_e2e_v2.sh <PROJECT_NAME> [OPTIONS]

PROJECT_NAME must have a corresponding directory under:
  ./original_projects/<PROJECT_NAME>/ or ./cuda_projects/<PROJECT_NAME>/
  Legacy fallback: ../original_projects/<PROJECT_NAME>/ or ../cuda_projects/<PROJECT_NAME>/

Preferred substructure for <PROJECT_NAME>:
  ├── ADAPTATION_REQUIREMENTS.md     ← User constraints
  ├── original_src/                  ← Clean upstream source
  └── test_data_and_scripts/
      └── <entry_script>.py          ← Non-interactive E2E test entry

Flat cuda_projects are also accepted; Phase 3 will discover an entry script.

Options:
  --server-url URL       OpenCode server URL (default: http://127.0.0.1:4098)
  --max-iter N           Max Phase 5 repair iterations (default: 8)
  --review               Enable Review Gate (default: enabled)
  --no-review            Disable Review Gate
  --no-keep-temp         Don't keep output project directory (default: keep)
  --agent NAME           Override auto-detected agent name
  --dry-run              Validate setup without running the test
  --extra 'ARGS...'      Pass extra arguments to e2e_test_v2.py
  --verbose              Enable verbose debug logging
  -h, --help             Show this help message

Examples:
  ./run_e2e_v2.sh 01_Hallo
  ./run_e2e_v2.sh 02_ChaiLab --max-iter 10
  ./run_e2e_v2.sh 07_IndexTTS --no-review --server-url http://10.0.0.1:4096
  ./run_e2e_v2.sh 05_InsectID --dry-run
  ./run_e2e_v2.sh 08_SpeechGPT-2.0-preview --review --verbose
EOF
    exit 0
}

# ── Arg parsing ──
PROJECT_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)              usage ;;
        --server-url)           SERVER_URL="$2"; shift 2 ;;
        --max-iter)             MAX_ITER="$2"; shift 2 ;;
        --review)               REVIEW_GATE=true; shift ;;
        --no-review)            REVIEW_GATE=false; shift ;;
        --no-keep-temp)         KEEP_TEMP=false; shift ;;
        --agent)                EXTRA_ARGS="$EXTRA_ARGS --agent $2"; shift 2 ;;
        --dry-run)              DRY_RUN=true; shift ;;
        --verbose)              EXTRA_ARGS="$EXTRA_ARGS --verbose"; shift ;;
        --extra)                EXTRA_ARGS="$EXTRA_ARGS $2"; shift 2 ;;
        -*)                     echo -e "${RED}Unknown option: $1${NC}" >&2; exit 1 ;;
        *)
            if [[ -z "$PROJECT_NAME" ]]; then
                PROJECT_NAME="$1"; shift
            else
                echo -e "${RED}Unexpected argument: $1${NC}" >&2; exit 1
            fi
            ;;
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
echo -e "${CYAN}║     migration_utils  E2E  Migration  Test  Launcher (V2)    ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${GREEN}Project:${NC}   $PROJECT_NAME"
echo -e "${GREEN}Path:${NC}      $PROJECT_DIR"
echo -e "${GREEN}Server:${NC}    $SERVER_URL"
echo -e "${GREEN}Max iter:${NC}  $MAX_ITER"
echo -e "${GREEN}Review:${NC}    $REVIEW_GATE"
echo -e "${GREEN}Keep tmp:${NC}  $KEEP_TEMP"
echo -e "${GREEN}Root:${NC}      $REPO_ROOT"
echo -e "${GREEN}Output:${NC}    $OUTPUT_PROJECTS_DIR"
echo -e "${GREEN}Extra:    ${NC}  ${EXTRA_ARGS:-(none)}"
echo ""

# Check project directory
if [[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]]; then
    echo -e "${RED}✗ Project directory not found: $PROJECT_NAME${NC}"
    echo -e "${YELLOW}  Searched:${NC}"
    for base in "${PROJECT_SEARCH_DIRS[@]}"; do
        echo -e "${YELLOW}    - $base/$PROJECT_NAME${NC}"
    done
    exit 1
fi
echo -e "${GREEN}✓${NC} Project directory exists"

# Check ADAPTATION_REQUIREMENTS.md
HAS_CONSTRAINTS=false
if [[ -f "$PROJECT_DIR/ADAPTATION_REQUIREMENTS.md" ]]; then
    echo -e "${GREEN}✓${NC} ADAPTATION_REQUIREMENTS.md exists"
    HAS_CONSTRAINTS=true
else
    echo -e "${YELLOW}⚠  ADAPTATION_REQUIREMENTS.md not found (no constraints will be applied)${NC}"
fi

# Check test entry script hints. Some cuda_projects are flat source trees and let Phase 3 discover the entry.
ENTRY_SCRIPTS=""
if [[ -d "$PROJECT_DIR/test_data_and_scripts" ]]; then
    ENTRY_SCRIPTS=$(find "$PROJECT_DIR/test_data_and_scripts" -name "*.py" 2>/dev/null | head -5 || true)
fi
if [[ -z "$ENTRY_SCRIPTS" ]]; then
    echo -e "${YELLOW}⚠  No test_data_and_scripts/*.py found (Phase 3 will discover an entry script)${NC}"
else
    echo -e "${GREEN}✓${NC} Entry scripts found:"
    while IFS= read -r script; do
        echo -e "  - ${CYAN}$(basename "$script")${NC}"
    done <<< "$ENTRY_SCRIPTS"
fi

# Check original_src
if [[ -d "$PROJECT_DIR/original_src" ]]; then
    FILE_COUNT=$(find "$PROJECT_DIR/original_src" -type f 2>/dev/null | wc -l)
    echo -e "${GREEN}✓${NC} original_src/ exists ($FILE_COUNT files)"
else
    echo -e "${YELLOW}⚠  original_src/ not found (will use project root directly)${NC}"
fi

# Check OpenCode server
if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}⚠  Dry-run mode: skipping OpenCode server reachability check${NC}"
else
    echo ""
    echo -e "${CYAN}Checking OpenCode server at $SERVER_URL ...${NC}"
    if curl -fsS -o /dev/null --max-time 5 "$SERVER_URL/agent" 2>/dev/null; then
        AGENT_INFO=$(curl -fsS --max-time 5 "$SERVER_URL/agent" 2>/dev/null | head -c 200 || echo "")
        echo -e "${GREEN}✓${NC} Server reachable: ${AGENT_INFO:-OK}"
    else
        echo -e "${RED}✗ Server not reachable at $SERVER_URL${NC}"
        echo -e "${YELLOW}  Start the server before running E2E tests.${NC}"
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}════ All checks passed ═════${NC}"

# ── Dry-run mode ──
if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}── Dry-run mode ──${NC}"
    echo "Would execute:"
    echo "  cd $REPO_ROOT && \\"
    echo "  python -m tests.e2e.e2e_test_v2 \\"
    echo "    --server-url $SERVER_URL \\"
    echo "    --project-dir $PROJECT_DIR \\"
    echo "    --output-dir $OUTPUT_PROJECTS_DIR \\"
    echo "    --max-phase5-iter $MAX_ITER \\"
    echo "    --keep-temp-dir \\"
    if [[ "$REVIEW_GATE" == true ]]; then
        echo "    --review-gate \\"
    fi
    if [[ "$HAS_CONSTRAINTS" == true ]]; then
        echo "    --user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md \\"
    fi
    echo "    $EXTRA_ARGS"
    exit 0
fi

# ── Launch E2E test ──
echo ""
echo -e "${CYAN}── Launching E2E test (YAML-driven workflow) ──${NC}"
REVIEW_FLAG=""
if [[ "$REVIEW_GATE" == true ]]; then
    REVIEW_FLAG="--review-gate"
fi

KEEP_FLAG=""
if [[ "$KEEP_TEMP" == true ]]; then
    KEEP_FLAG="--keep-temp-dir"
fi

CONSTRAINTS_FLAG=""
if [[ "$HAS_CONSTRAINTS" == true ]]; then
    CONSTRAINTS_FLAG="--user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md"
fi

cd "$REPO_ROOT"

python -m tests.e2e.e2e_test_v2 \
    --server-url "$SERVER_URL" \
    --project-dir "$PROJECT_DIR" \
    --output-dir "$OUTPUT_PROJECTS_DIR" \
    --max-phase5-iter "$MAX_ITER" \
    $KEEP_FLAG \
    $REVIEW_FLAG \
    $CONSTRAINTS_FLAG \
    $EXTRA_ARGS

EXIT_CODE=$?

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  E2E TEST PASSED${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
else
    echo -e "${RED}══════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  E2E TEST FAILED${NC}"
    echo -e "${RED}══════════════════════════════════════════════════════════${NC}"
fi
echo ""
echo -e "${CYAN}Reports:${NC}  $REPO_ROOT/e2e-reports/migration_utils/$(date +%Y%m%d)_*/"
echo -e "${CYAN}Output:${NC}   $OUTPUT_PROJECTS_DIR/${PROJECT_NAME}_$(date +%Y%m%d)_*/"
echo ""

exit $EXIT_CODE
