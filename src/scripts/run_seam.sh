#!/usr/bin/env bash
#
# SEAM Public Launcher — YAML-driven multi-platform migration entrypoint
# Usage: run_seam.sh <project_path> [options]
#
# Examples:
#   bash src/scripts/run_seam.sh /path/to/cuda/project --server_type opencode --server_url http://127.0.0.1:5000
#   bash src/scripts/run_seam.sh my_project --workflow src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml
#   bash src/scripts/run_seam.sh /path/to/project --max-iter 10 --verbose
#   bash src/scripts/run_seam.sh my_project --dry-run
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/.." && pwd)"

# ── Color helpers ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

SERVER_TYPE="opencode"
SERVER_URL=""
SERVER_CONFLICT_ACTION="prompt"

usage() {
    cat <<'EOF'
SEAM (Self-Evolving Agentic Migration) — Public Launcher

Usage:
  bash src/scripts/run_seam.sh <PROJECT_PATH> [OPTIONS]
  bash src/scripts/run_seam.sh --continue-from <SUMMARY_JSON> [OPTIONS]

PROJECT_PATH can be:
  - A directory name under cuda_projects/ or original_projects/
  - An absolute or relative path to a CUDA-based project

Options:
  --server_type TYPE          Server backend type: opencode (default)
  --continue-from PATH        Continue an explicit terminal V3 summary
  --server_url URL            Server base URL. Defaults to http://127.0.0.1:4098 if unset.
  --server-conflict-action ACTION
                              Port conflict behavior: prompt, start, or error (default: prompt)
  --workflow PATH             Custom workflow YAML path (default: src/workflows/seam_auto_default.yaml)
  --max-iter N                Max Phase 5 repair iterations (default: 8)
  --max-review-iter N         Max logical review rounds (default: workflow/config, then 3)
  --review                    Enable Review Gate (default: disabled)
  --no-review                 Disable Review Gate (kept for compatibility)
  --review-fail-closed        Fail when review rejection exhausts the maximum (default)
  --no-review-fail-closed     Allow reject exhaustion compatibility outcome
  --no-keep-temp              Don't keep output project directory (default: keep)
  --container-retention POLICY
                              Container policy: retain or delete (default: retain)
  --save-agent-trace          Export raw OpenCode agent trace (default: disabled)
  --no-save-agent-trace       Explicitly disable raw OpenCode agent trace
  --agent NAME                Override auto-detected agent name
  --output-dir DIR            Output project root (default: MIGRATION_OUTPUT_PROJECTS_ROOT or ../output_projects)
  --server-no-auto-start       Disable auto-start of OpenCode server
  --opencode-readiness MODE   OpenCode readiness mode: off, basic, or message (default: message)
  --opencode-message-timeout N
                              Timeout for model-backed OpenCode message probe
  --opencode-diagnose-only    Run OpenCode diagnostics and exit before launching E2E
  --dashboard                 Force live terminal dashboard on
  --no-dashboard              Force live terminal dashboard off
  --dashboard-mode MODE       Dashboard mode: auto, on, or off (default: auto)
  --dry-run                   Validate paths without running migration
  --extra 'ARGS...'           Pass extra arguments to the E2E harness
  --verbose                   Enable verbose debug logging
  -h, --help                  Show this help message

Optional Live Dashboard:
  The dashboard extra is optional. Install it first:
    python -m pip install -e "./src[dashboard]"
  --dashboard-mode auto|on|off (default: auto):
    auto  enable on an interactive TTY outside CI; otherwise inert
    on    force on; needs textual (preferred) or rich (fallback), else fails
          before any side effect with the install command above
    off   fully disabled; run is identical to a no-dashboard run
  While active, press `q` to exit the dashboard view; migration and logging
  continue. Event telemetry is written to ui_events.jsonl in the report
  directory only while the dashboard is active; off or inactive auto runs
  create no such file.

Platform Workflows:
  Default Auto-Selector: src/workflows/seam_auto_default.yaml
  PPU Container: src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml
  PPU Auto-mode: src/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml

Multi-Platform Support:
  PPU, Ascend NPU, MUSA, ROCm, MLU — select via --workflow

Quickstart:
  # Clone, install, and start OpenCode server
  python -m pip install -e "./src[dev]"
  opencode serve --port 4098 --hostname 127.0.0.1 &

  # Run a migration
  bash src/scripts/run_seam.sh my_cuda_project --server_url http://127.0.0.1:4098

For advanced usage (container backends, custom-op flows, platform policy), see README.md.
EOF
    exit 0
}

# ── Forward args to run_e2e_v3.sh, translating server_type/server_url/server_conflict ──
FORWARD_ARGS=()
HAS_SERVER_URL=false
HAS_WORKFLOW=false
HAS_MAX_REVIEW_ITER=false
REVIEW_FAIL_CLOSED=""
CONTINUE_FROM=""
PROJECT_ARG=""
CONTAINER_RETENTION=""
SAVE_AGENT_TRACE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            ;;
        --server_type|--server-type)
            SERVER_TYPE="$2"
            # opencode agent name: set --agent if not already present
            shift 2
            ;;
        --server_url|--server-url)
            FORWARD_ARGS+=("--server-url" "$2")
            HAS_SERVER_URL=true
            shift 2
            ;;
        --server-conflict-action)
            SERVER_CONFLICT_ACTION="$2"
            shift 2
            ;;
        --workflow)
            FORWARD_ARGS+=("--workflow" "$2")
            HAS_WORKFLOW=true
            shift 2
            ;;
        --max-iter)
            FORWARD_ARGS+=("--max-iter" "$2")
            shift 2
            ;;
        --continue-from)
            if [[ -n "$CONTINUE_FROM" || $# -lt 2 || -z "$2" ]]; then
                echo -e "${RED}Error: --continue-from requires one summary.json path.${NC}" >&2
                exit 1
            fi
            CONTINUE_FROM="$2"
            shift 2
            ;;
        --container-retention)
            if [[ $# -lt 2 || ( "$2" != "retain" && "$2" != "delete" ) ]]; then
                echo -e "${RED}Error: --container-retention requires retain or delete.${NC}" >&2
                exit 1
            fi
            if [[ -n "$CONTAINER_RETENTION" && "$CONTAINER_RETENTION" != "$2" ]]; then
                echo -e "${RED}Error: conflicting container retention options.${NC}" >&2
                exit 1
            fi
            CONTAINER_RETENTION="$2"
            shift 2
            ;;
        --save-agent-trace)
            if [[ -n "$SAVE_AGENT_TRACE" && "$SAVE_AGENT_TRACE" != true ]]; then
                echo -e "${RED}Error: conflicting agent trace options.${NC}" >&2
                exit 1
            fi
            SAVE_AGENT_TRACE=true
            shift
            ;;
        --no-save-agent-trace)
            if [[ -n "$SAVE_AGENT_TRACE" && "$SAVE_AGENT_TRACE" != false ]]; then
                echo -e "${RED}Error: conflicting agent trace options.${NC}" >&2
                exit 1
            fi
            SAVE_AGENT_TRACE=false
            shift
            ;;
        --max-review-iter)
            if [[ "$HAS_MAX_REVIEW_ITER" == true ]]; then
                echo -e "${RED}Error: --max-review-iter may be supplied only once.${NC}" >&2
                exit 1
            fi
            if [[ $# -lt 2 || ! "$2" =~ ^[1-9][0-9]*$ ]]; then
                echo -e "${RED}Error: --max-review-iter requires a positive integer.${NC}" >&2
                exit 1
            fi
            FORWARD_ARGS+=("--max-review-iter" "$2")
            HAS_MAX_REVIEW_ITER=true
            shift 2
            ;;
        --review)
            FORWARD_ARGS+=("--review")
            shift
            ;;
        --no-review)
            FORWARD_ARGS+=("--no-review")
            shift
            ;;
        --review-fail-closed)
            if [[ -n "$REVIEW_FAIL_CLOSED" && "$REVIEW_FAIL_CLOSED" != true ]]; then
                echo -e "${RED}Error: conflicting review fail-closed options.${NC}" >&2
                exit 1
            fi
            if [[ -z "$REVIEW_FAIL_CLOSED" ]]; then
                FORWARD_ARGS+=("--review-fail-closed")
            fi
            REVIEW_FAIL_CLOSED=true
            shift
            ;;
        --no-review-fail-closed)
            if [[ -n "$REVIEW_FAIL_CLOSED" && "$REVIEW_FAIL_CLOSED" != false ]]; then
                echo -e "${RED}Error: conflicting review fail-closed options.${NC}" >&2
                exit 1
            fi
            if [[ -z "$REVIEW_FAIL_CLOSED" ]]; then
                FORWARD_ARGS+=("--no-review-fail-closed")
            fi
            REVIEW_FAIL_CLOSED=false
            shift
            ;;
        --no-keep-temp)
            FORWARD_ARGS+=("--no-keep-temp")
            shift
            ;;
        --agent)
            FORWARD_ARGS+=("--agent" "$2")
            shift 2
            ;;
        --output-dir)
            FORWARD_ARGS+=("--output-dir" "$2")
            shift 2
            ;;
        --server-no-auto-start)
            FORWARD_ARGS+=("--server-no-auto-start")
            shift
            ;;
        --opencode-readiness)
            FORWARD_ARGS+=("--opencode-readiness" "$2")
            shift 2
            ;;
        --opencode-message-timeout)
            FORWARD_ARGS+=("--opencode-message-timeout" "$2")
            shift 2
            ;;
        --opencode-diagnose-only)
            FORWARD_ARGS+=("--opencode-diagnose-only")
            shift
            ;;
        --dashboard)
            FORWARD_ARGS+=("--dashboard")
            shift
            ;;
        --no-dashboard)
            FORWARD_ARGS+=("--no-dashboard")
            shift
            ;;
        --dashboard-mode)
            FORWARD_ARGS+=("--dashboard-mode" "$2")
            shift 2
            ;;
        --dashboard-backend)
            FORWARD_ARGS+=("--dashboard-backend" "$2")
            shift 2
            ;;
        --seal-manifest)
            FORWARD_ARGS+=("--seal-manifest")
            shift
            ;;
        --dry-run)
            FORWARD_ARGS+=("--dry-run")
            shift
            ;;
        --extra)
            FORWARD_ARGS+=("--extra" "$2")
            shift 2
            ;;
        --verbose)
            FORWARD_ARGS+=("--verbose")
            shift
            ;;
        -*)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            usage
            ;;
        *)
            if [[ -n "$PROJECT_ARG" ]]; then
                echo -e "${RED}Unexpected argument: $1${NC}" >&2
                exit 1
            fi
            PROJECT_ARG="$1"
            FORWARD_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$CONTAINER_RETENTION" ]]; then
    CONTAINER_RETENTION="retain"
fi
FORWARD_ARGS+=("--container-retention" "$CONTAINER_RETENTION")
if [[ "$SAVE_AGENT_TRACE" == true ]]; then
    FORWARD_ARGS+=("--save-agent-trace")
elif [[ "$SAVE_AGENT_TRACE" == false ]]; then
    FORWARD_ARGS+=("--no-save-agent-trace")
fi

if [[ -n "$PROJECT_ARG" && -n "$CONTINUE_FROM" ]]; then
    echo -e "${RED}Error: PROJECT_PATH and --continue-from are mutually exclusive.${NC}" >&2
    exit 1
fi
if [[ -z "$PROJECT_ARG" && -z "$CONTINUE_FROM" ]]; then
    echo -e "${RED}Error: PROJECT_PATH or --continue-from is required.${NC}" >&2
    exit 1
fi
if [[ -n "$CONTINUE_FROM" && "$HAS_WORKFLOW" == true ]]; then
    echo -e "${RED}Error: --workflow is not valid with --continue-from; the parent workflow is pinned.${NC}" >&2
    exit 1
fi
if [[ -n "$CONTINUE_FROM" ]]; then
    CONTINUE_PARENT="$(cd "$(dirname "$CONTINUE_FROM")" 2>/dev/null && pwd -P)" || {
        echo -e "${RED}Error: --continue-from parent directory is unavailable.${NC}" >&2
        exit 1
    }
    CONTINUE_FROM="$CONTINUE_PARENT/$(basename "$CONTINUE_FROM")"
    FORWARD_ARGS+=("--continue-from" "$CONTINUE_FROM")
fi

# Default server URL if not provided
if [[ "$HAS_SERVER_URL" != true ]]; then
    FORWARD_ARGS+=("--server-url" "http://127.0.0.1:4098")
fi

# Default workflow if not provided
if [[ "$HAS_WORKFLOW" != true && -z "$CONTINUE_FROM" ]]; then
    FORWARD_ARGS+=("--workflow" "src/workflows/seam_auto_default.yaml")
fi

echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     SEAM  Public  Launcher  (src/scripts/run_seam.sh)    ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Server type:${NC} $SERVER_TYPE"
echo -e "${GREEN}Workflow:${NC}   $([ "$HAS_WORKFLOW" = true ] && echo "${FORWARD_ARGS[*]}" || echo "src/workflows/seam_auto_default.yaml (default)")"
echo ""

# Delegate to run_e2e_v3.sh. Invoke through bash so checkouts that lose the
# executable bit can still run from the public launcher.
exec bash "$SRC_DIR/scripts/run_e2e_v3.sh" "${FORWARD_ARGS[@]}"
