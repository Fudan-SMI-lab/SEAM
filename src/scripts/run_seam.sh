#!/usr/bin/env bash
#
# E2E Migration Test Launcher (V3 — YAML-driven workflow with custom workflow path)
# Usage: run_e2e_v3.sh <project_name> [options]
#
# Examples:
#   ./run_e2e_v3.sh 01_Hallo
#   ./run_e2e_v3.sh SEAM_PPU_SMOKE --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml --dry-run
#   ./run_e2e_v3.sh 07_IndexTTS --max-iter 10
#   ./run_e2e_v3.sh deepwave --server-port 5001 --max-iter 30
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/.." && pwd)"
OUTPUT_PROJECTS_DIR="${MIGRATION_OUTPUT_PROJECTS_ROOT:-$REPO_ROOT/output_projects}"
PROJECT_SEARCH_DIRS=(
    "$REPO_ROOT/original_projects"
    "$REPO_ROOT/cuda_projects"
    "$REPO_ROOT/../original_projects"
    "$REPO_ROOT/../cuda_projects"
    "$REPO_ROOT/../application_migration_cases"
)

# ── Defaults (mirroring the V1 successful run pattern) ──
SERVER_URL="http://127.0.0.1:4098"
SERVER_PORT=""
MAX_ITER=20
KEEP_TEMP=true
REVIEW_GATE=true
DRY_RUN=false
SERVER_NO_AUTO_START=false
SERVER_TYPE=""
WORKFLOW_PATH=""
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
Usage: run_e2e_v3.sh <PROJECT_NAME> [OPTIONS]

PROJECT_NAME must have a corresponding directory under:
  ./original_projects/<PROJECT_NAME>/ or ./cuda_projects/<PROJECT_NAME>/
  Legacy fallback: ../original_projects/<PROJECT_NAME>/ or ../cuda_projects/<PROJECT_NAME>/
  Application migration cases: ../application_migration_cases/<PROJECT_NAME>/

Preferred substructure for <PROJECT_NAME>:
  ├── ADAPTATION_REQUIREMENTS.md     ← User constraints
  ├── original_src/                  ← Clean upstream source
  └── test_data_and_scripts/
      └── <entry_script>.py          ← Non-interactive E2E test entry

Flat cuda_projects are also accepted; Phase 3 will discover an entry script.

Options:
  --server-url URL       OpenCode server URL (default: http://127.0.0.1:4098)
  --server-port PORT     Auto-start server on this port (overrides --server-url)
  --server-type TYPE     Server type for --server-url smart probe: 'opencode'
                           Port free → auto-start; matching server → reuse; conflict → error
  --max-iter N           Max Phase 5 repair iterations (default: 20)
  --review               Enable Review Gate (default: enabled)
  --no-review            Disable Review Gate
  --no-keep-temp         Don't keep output project directory (default: keep)
  --agent NAME           Override auto-detected agent name
  --workflow PATH        Path to workflow YAML file (overrides default NPU workflow)
  --output-dir PATH      Override output projects directory
  --server-no-auto-start Disable auto-start of OpenCode server
  --dry-run              Validate setup without running the test
  --extra 'ARGS...'      Pass extra arguments to e2e_test_v3.py
  --verbose              Enable verbose debug logging
  -h, --help             Show this help message

Examples:
  ./run_e2e_v3.sh 01_Hallo
  ./run_e2e_v3.sh 02_ChaiLab --max-iter 10
  ./run_e2e_v3.sh 07_IndexTTS --no-review --server-url http://10.0.0.1:4096
  ./run_e2e_v3.sh SEAM_PPU_SMOKE --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml
  ./run_e2e_v3.sh 05_InsectID --dry-run
  ./run_e2e_v3.sh 08_SpeechGPT-2.0-preview --review --verbose
  ./run_e2e_v3.sh deepwave --server-port 5001 --max-iter 30 --output-dir output_projects
EOF
    exit 0
}

# ── Arg parsing ──
PROJECT_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)              usage ;;
        --server-url)           SERVER_URL="$2"; shift 2 ;;
        --server-port)          SERVER_PORT="$2"; shift 2 ;;
        --server-type)          SERVER_TYPE="$2"; shift 2 ;;
        --max-iter)             MAX_ITER="$2"; shift 2 ;;
        --review)               REVIEW_GATE=true; shift ;;
        --no-review)            REVIEW_GATE=false; shift ;;
        --no-keep-temp)         KEEP_TEMP=false; shift ;;
        --agent)                EXTRA_ARGS="$EXTRA_ARGS --agent $2"; shift 2 ;;
        --workflow)             WORKFLOW_PATH="$2"; shift 2 ;;
        --output-dir)           OUTPUT_PROJECTS_DIR="$2"; shift 2 ;;
        --server-no-auto-start)  SERVER_NO_AUTO_START=true; shift ;;
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

# When server-port is given, switch to auto-start mode
if [[ -n "$SERVER_PORT" ]]; then
    USE_AUTO_START=true
    SERVER_NO_AUTO_START=false
else
    USE_AUTO_START=false
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
echo -e "${CYAN}║          src  E2E  Migration  Test  Launcher (V3)           ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${GREEN}Project:${NC}   $PROJECT_NAME"
echo -e "${GREEN}Path:${NC}      $PROJECT_DIR"
if [[ -n "$SERVER_PORT" ]]; then
    echo -e "${GREEN}Server:${NC}    auto-start on port $SERVER_PORT"
elif [[ -n "$SERVER_TYPE" ]]; then
    echo -e "${GREEN}Server:${NC}    $SERVER_URL ($SERVER_TYPE — smart probe)"
else
    echo -e "${GREEN}Server:${NC}    $SERVER_URL"
fi
echo -e "${GREEN}Max iter:${NC}  $MAX_ITER"
echo -e "${GREEN}Review:${NC}    $REVIEW_GATE"
echo -e "${GREEN}Keep tmp:${NC}  $KEEP_TEMP"
echo -e "${GREEN}Auto-start:${NC} $( [[ "$SERVER_NO_AUTO_START" == true ]] && echo 'false' || echo 'true' )"
echo -e "${GREEN}Root:${NC}      $REPO_ROOT"
echo -e "${GREEN}Output:${NC}    $OUTPUT_PROJECTS_DIR"
if [[ -n "$WORKFLOW_PATH" ]]; then
    echo -e "${GREEN}Workflow:${NC} $WORKFLOW_PATH"
fi
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

# Check OpenCode server (skip if auto-starting with a specific port)
if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}⚠  Dry-run mode: skipping OpenCode server reachability check${NC}"
elif [[ "$USE_AUTO_START" == true ]]; then
    echo ""
    echo -e "${CYAN}Server will auto-start on port $SERVER_PORT (skipping connectivity check)${NC}"
elif [[ -n "$SERVER_TYPE" ]]; then
    echo ""
    echo -e "${CYAN}Server type '$SERVER_TYPE' probe mode — Python will handle start/reuse/error${NC}"
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

NO_AUTO_FLAG=""
if [[ "$SERVER_NO_AUTO_START" == true ]]; then
    NO_AUTO_FLAG="--server-no-auto-start"
fi

SERVER_PORT_FLAG=""
if [[ -n "$SERVER_PORT" ]]; then
    SERVER_PORT_FLAG="--server-port $SERVER_PORT"
fi

SERVER_TYPE_FLAG=""
if [[ -n "$SERVER_TYPE" ]]; then
    SERVER_TYPE_FLAG="--server-type $SERVER_TYPE"
fi

# ── Dry-run mode ──
if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo -e "${YELLOW}── Dry-run mode ──${NC}"
    echo "Would execute:"
    echo "  cd $REPO_ROOT && \\"
    echo "  ${PYTHON:-python3.10} -m tests.e2e.e2e_test_v3 \\"
    if [[ -n "$SERVER_PORT_FLAG" ]]; then
        echo "    $SERVER_PORT_FLAG \\"
    else
        echo "    --server-url $SERVER_URL \\"
    fi
    if [[ -n "$SERVER_TYPE_FLAG" ]]; then
        echo "    $SERVER_TYPE_FLAG \\"
    fi
    echo "    --project-dir $PROJECT_DIR \\"
    echo "    --output-dir $OUTPUT_PROJECTS_DIR \\"
    echo "    --max-phase5-iter $MAX_ITER \\"
    echo "    --keep-temp-dir \\"
    if [[ -n "$WORKFLOW_PATH" ]]; then
        echo "    --workflow-path $WORKFLOW_PATH \\"
    fi
    if [[ "$REVIEW_GATE" == true ]]; then
        echo "    --review-gate \\"
    fi
    if [[ "$HAS_CONSTRAINTS" == true ]]; then
        echo "    --user-constraints $PROJECT_DIR/ADAPTATION_REQUIREMENTS.md \\"
    fi
    if [[ -n "$NO_AUTO_FLAG" ]]; then
        echo "    $NO_AUTO_FLAG \\"
    fi
    echo "    $EXTRA_ARGS"
    exit 0
fi

# ── Launch E2E test ──
echo ""
echo -e "${CYAN}── Launching E2E test (YAML-driven workflow V3) ──${NC}"
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

WORKFLOW_FLAG=""
if [[ -n "$WORKFLOW_PATH" ]]; then
    WORKFLOW_FLAG="--workflow-path $WORKFLOW_PATH"
fi

cd "$REPO_ROOT"

# Ignore external termination signals — the orchestrator is a thin coordinator;
# persistent OpenCode agent sessions do the real work and survive independently.
# SIGHUP:  SSH disconnect / terminal close (most common unintended kill)
# SIGTERM: external process management tools
# SIGPIPE: broken pipe + `set -o pipefail` can cause premature exit
trap '' HUP TERM PIPE

if [[ -n "$SERVER_PORT_FLAG" ]]; then
    ${PYTHON:-python3.10} -m tests.e2e.e2e_test_v3 \
        $SERVER_PORT_FLAG \
        $SERVER_TYPE_FLAG \
        --project-dir "$PROJECT_DIR" \
        --output-dir "$OUTPUT_PROJECTS_DIR" \
        --max-phase5-iter "$MAX_ITER" \
        $KEEP_FLAG \
        $REVIEW_FLAG \
        $CONSTRAINTS_FLAG \
        $NO_AUTO_FLAG \
        $WORKFLOW_FLAG \
        $EXTRA_ARGS
else
    ${PYTHON:-python3.10} -m tests.e2e.e2e_test_v3 \
        --server-url "$SERVER_URL" \
        $SERVER_TYPE_FLAG \
        --project-dir "$PROJECT_DIR" \
        --output-dir "$OUTPUT_PROJECTS_DIR" \
        --max-phase5-iter "$MAX_ITER" \
        $KEEP_FLAG \
        $REVIEW_FLAG \
        $CONSTRAINTS_FLAG \
        $NO_AUTO_FLAG \
        $WORKFLOW_FLAG \
        $EXTRA_ARGS
fi

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
