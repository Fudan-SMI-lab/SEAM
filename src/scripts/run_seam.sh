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

PROJECT_PATH can be:
  - A directory name under cuda_projects/ or original_projects/
  - An absolute or relative path to a CUDA-based project

Options:
  --server_type TYPE          Server backend type: opencode (default)
  --server_url URL            Server base URL. Defaults to http://127.0.0.1:4098 if unset.
  --server-conflict-action ACTION
                              Port conflict behavior: prompt, start, or error (default: prompt)
  --workflow PATH             Custom workflow YAML path (default: src/workflows/seam_auto_default.yaml)
  --max-iter N                Max Phase 5 repair iterations (default: 8)
  --review                    Enable Review Gate (default: enabled)
  --no-review                 Disable Review Gate
  --no-keep-temp              Don't keep output project directory (default: keep)
  --agent NAME                Override auto-detected agent name
  --output-dir DIR            Output project root (default: MIGRATION_OUTPUT_PROJECTS_ROOT or ../output_projects)
  --server-no-auto-start       Disable auto-start of OpenCode server
  --dry-run                   Validate paths without running migration
  --extra 'ARGS...'           Pass extra arguments to the E2E harness
  --verbose                   Enable verbose debug logging
  -h, --help                  Show this help message

Platform Workflows:
  Default Auto-Selector: src/workflows/seam_auto_default.yaml
  PPU Container: src/workflows/ppu_migration_v2_container_vllm018_smoke.yaml
  PPU Auto-mode: src/workflows/ppu_migration_v2_auto_vllm018_smoke_baseaware_entryfix_keep.yaml

Multi-Platform Support:
  PPU, Ascend NPU, MUSA, ROCm, MLU — select via --workflow

Quickstart:
  # Clone, install, and start OpenCode server
  pip install -e ".[dev]"
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
        --review)
            FORWARD_ARGS+=("--review")
            shift
            ;;
        --no-review)
            FORWARD_ARGS+=("--no-review")
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
            FORWARD_ARGS+=("$1")
            shift
            ;;
    esac
done

# Default server URL if not provided
if [[ "$HAS_SERVER_URL" != true ]]; then
    FORWARD_ARGS+=("--server-url" "http://127.0.0.1:4098")
fi

# Default workflow if not provided
if [[ "$HAS_WORKFLOW" != true ]]; then
    FORWARD_ARGS+=("--workflow" "src/workflows/seam_auto_default.yaml")
fi

echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     SEAM  Public  Launcher  (src/scripts/run_seam.sh)    ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Server type:${NC} $SERVER_TYPE"
echo -e "${GREEN}Workflow:${NC}   $([ "$HAS_WORKFLOW" = true ] && echo "${FORWARD_ARGS[*]}" || echo "src/workflows/seam_auto_default.yaml (default)")"
echo ""

# Delegate to run_e2e_v3.sh
exec "$SRC_DIR/scripts/run_e2e_v3.sh" "${FORWARD_ARGS[@]}"
