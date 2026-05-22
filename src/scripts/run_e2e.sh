#!/usr/bin/env bash
#
# E2E Migration Test Launcher (V1 compatibility path)
# Usage: run_e2e.sh <project_name-or-path> [options]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/.." && pwd)"
OUTPUT_PROJECTS_DIR="$REPO_ROOT/output_projects"
PROJECT_SEARCH_DIRS=(
    "$REPO_ROOT/original_projects"
    "$REPO_ROOT/cuda_projects"
    "$REPO_ROOT/../original_projects"
    "$REPO_ROOT/../cuda_projects"
)

SERVER_TYPE="opencode"
SERVER_URL="http://127.0.0.1:4098"
MAX_ITER=""
KEEP_TEMP=true
REVIEW_GATE=true
DRY_RUN=false
EXTRA_ARGS=""
PROJECT_NAME=""

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    cat <<'EOF'
Usage: run_e2e.sh <PROJECT_NAME_OR_PATH> [OPTIONS]

PROJECT_NAME is resolved root-first under:
  ./original_projects/<PROJECT_NAME>/ or ./cuda_projects/<PROJECT_NAME>/
  Legacy fallback: ../original_projects/<PROJECT_NAME>/ or ../cuda_projects/<PROJECT_NAME>/

Options:
  --server_type TYPE     Server backend type (default: opencode)
  --server_url URL       Server base URL (default: http://127.0.0.1:4098)
  --max-iter N           Max Phase 5 repair iterations (default: 10)
  --no-review            Disable Review Gate (default: enabled)
  --no-keep-temp         Don't keep output project directory (default: keep)
  --agent NAME           Override auto-detected agent name
  --dry-run              Validate setup without running the test or checking server
  --extra 'ARGS...'      Pass extra arguments to e2e_test.py
  -h, --help             Show this help message
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage ;;
        --server_type|--server-type) SERVER_TYPE="$2"; shift 2 ;;
        --server_url) SERVER_URL="$2"; shift 2 ;;
        --max-iter) MAX_ITER="$2"; shift 2 ;;
        --no-review) REVIEW_GATE=false; shift ;;
        --no-keep-temp) KEEP_TEMP=false; shift ;;
        --agent) EXTRA_ARGS="$EXTRA_ARGS --agent $2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --extra) EXTRA_ARGS="$EXTRA_ARGS $2"; shift 2 ;;
        -*) echo -e "${RED}Unknown option: $1${NC}" >&2; exit 1 ;;
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

echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║        src  E2E  Migration  Test  Launcher       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Project:${NC}   $PROJECT_NAME"
echo -e "${GREEN}Path:${NC}      $PROJECT_DIR"
if [[ "$SERVER_TYPE" != "opencode" ]]; then
    echo -e "${RED}Unsupported server_type: $SERVER_TYPE${NC}" >&2
    exit 1
fi
echo -e "${GREEN}Server:${NC}    $SERVER_TYPE at $SERVER_URL"
echo -e "${GREEN}Max iter:${NC}  ${MAX_ITER:-10 (default)}"
echo -e "${GREEN}Review:${NC}    $REVIEW_GATE"
echo -e "${GREEN}Keep tmp:${NC}  $KEEP_TEMP"
echo -e "${GREEN}Root:${NC}      $REPO_ROOT"
echo -e "${GREEN}Output:${NC}    $OUTPUT_PROJECTS_DIR"
echo -e "${GREEN}Extra:${NC}     ${EXTRA_ARGS:-(none)}"
echo ""

if [[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]]; then
    echo -e "${RED}✗ Project directory not found: $PROJECT_NAME${NC}"
    echo -e "${YELLOW}  Searched:${NC}"
    for base in "${PROJECT_SEARCH_DIRS[@]}"; do
        echo -e "${YELLOW}    - $base/$PROJECT_NAME${NC}"
    done
    exit 1
fi
echo -e "${GREEN}✓${NC} Project directory exists"

HAS_CONSTRAINTS=false
if [[ -f "$PROJECT_DIR/ADAPTATION_REQUIREMENTS.md" ]]; then
    HAS_CONSTRAINTS=true
    echo -e "${GREEN}✓${NC} ADAPTATION_REQUIREMENTS.md exists"
else
    echo -e "${YELLOW}⚠  ADAPTATION_REQUIREMENTS.md not found (no constraints will be applied)${NC}"
fi

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

if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}⚠  Dry-run mode: skipping server management${NC}"
fi

echo ""
echo -e "${GREEN}════ All checks passed ═════${NC}"

REVIEW_FLAG=""
if [[ "$REVIEW_GATE" == true ]]; then
    REVIEW_FLAG="--review-gate"
fi
KEEP_FLAG=""
if [[ "$KEEP_TEMP" == true ]]; then
    KEEP_FLAG="--keep-temp-dir"
fi
MAX_ITER_FLAG=""
if [[ -n "$MAX_ITER" ]]; then
    MAX_ITER_FLAG="--max-phase5-iter $MAX_ITER"
fi

CONSTRAINTS_FLAG=""
if [[ "$HAS_CONSTRAINTS" == true ]]; then
    CONSTRAINTS_FLAG="--user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md"
fi

if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}── Dry-run mode ──${NC}"
    echo "Would execute:"
    echo "  cd $REPO_ROOT && \\"
    echo "  python src/tests/e2e/e2e_test.py \\"
    echo "    --server_type $SERVER_TYPE \\"
    echo "    --server_url $SERVER_URL \\"
    echo "    --project-dir $PROJECT_DIR \\"
    echo "    --output-project-dir $OUTPUT_PROJECTS_DIR \\"
    if [[ -n "$MAX_ITER" ]]; then
        echo "    --max-phase5-iter $MAX_ITER \\"
    fi
    echo "    $KEEP_FLAG \\"
    if [[ "$REVIEW_GATE" == true ]]; then
        echo "    --review-gate \\"
    fi
    if [[ "$HAS_CONSTRAINTS" == true ]]; then
        echo "    --user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md \\"
    fi
    echo "    $EXTRA_ARGS"
    exit 0
fi

cd "$REPO_ROOT"
python src/tests/e2e/e2e_test.py \
    --server_type "$SERVER_TYPE" \
    --server_url "$SERVER_URL" \
    --project-dir "$PROJECT_DIR" \
    --output-project-dir "$OUTPUT_PROJECTS_DIR" \
    $MAX_ITER_FLAG \
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
echo -e "${CYAN}Reports:${NC}  $REPO_ROOT/e2e-reports/src/$(date +%Y%m%d)_*/"
echo -e "${CYAN}Output:${NC}   $OUTPUT_PROJECTS_DIR/${PROJECT_NAME}_$(date +%Y%m%d)_*/"
echo ""
exit $EXIT_CODE
