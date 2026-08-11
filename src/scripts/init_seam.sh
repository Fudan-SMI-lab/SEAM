#!/usr/bin/env bash
#
# SEAM Interactive Initializer Launcher — no-argument guided setup entrypoint.
# Usage:
#   bash src/scripts/init_seam.sh                              # interactive setup
#   bash src/scripts/init_seam.sh --non-interactive --answers answers.json
#   bash src/scripts/init_seam.sh -h | --help
#
# This launcher takes NO positional PROJECT_PATH (unlike sibling launchers):
# the initializer acts on this repository. It resolves a Python 3.10+
# interpreter (SEAM_PYTHON wins; PYTHON is a legacy fallback), then execs
# ``python -m seam_init.cli`` so the cli's exit code propagates directly.
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

usage() {
    cat <<'EOF'
SEAM Interactive Initializer — guided setup launcher

Usage:
  bash src/scripts/init_seam.sh                            # interactive guided setup
  bash src/scripts/init_seam.sh --non-interactive --answers PATH
  bash src/scripts/init_seam.sh -h | --help

Options:
  --non-interactive      Run without stdin/getpass prompts. Requires --answers.
  --answers PATH         JSON answers file for --non-interactive mode. Secret
                         values are referenced by environment-variable NAME
                         (e.g. {"api_key_env": "SEAM_INIT_KEY"}); inline secret
                         values are rejected and never accepted on argv.
  -h, --help             Show this help message and exit 0.

Notes:
  * No PROJECT_PATH argument — the initializer configures this repository.
  * SEAM_PYTHON overrides the Python interpreter (PYTHON is a legacy fallback).
  * Missing or Python < 3.10 exits 61 (PYTHON_ENVIRONMENT) with install guidance.
EOF
    exit 0
}

FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            ;;
        --non-interactive)
            FORWARD_ARGS+=("--non-interactive")
            shift
            ;;
        --answers)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo -e "${RED}Error: --answers requires one JSON file path.${NC}" >&2
                exit 2
            fi
            FORWARD_ARGS+=("--answers" "$2")
            shift 2
            ;;
        --answers=*)
            FORWARD_ARGS+=("$1")
            shift
            ;;
        --)
            shift
            for a in "$@"; do FORWARD_ARGS+=("$a"); done
            break
            ;;
        -*)
            echo -e "${RED}Error: unknown option: $1${NC}" >&2
            echo -e "${YELLOW}  Run 'bash src/scripts/init_seam.sh --help' for usage.${NC}" >&2
            exit 2
            ;;
        *)
            echo -e "${RED}Error: unexpected argument: $1${NC}" >&2
            echo -e "${YELLOW}  This launcher takes no positional PROJECT_PATH.${NC}" >&2
            exit 2
            ;;
    esac
done

# ── Python interpreter resolution ──
# SEAM_PYTHON wins; PYTHON is a legacy fallback (matches run_e2e_v3.sh convention
# but reads SEAM_PYTHON first so the test recorder can pin the interpreter).
SEAM_PYTHON="${SEAM_PYTHON:-${PYTHON:-}}"

supports_runtime() {
    "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

resolve_python() {
    if [[ -n "$SEAM_PYTHON" ]]; then
        if (command -v "$SEAM_PYTHON" >/dev/null 2>&1 || [[ -x "$SEAM_PYTHON" ]]) \
            && supports_runtime "$SEAM_PYTHON"; then
            printf '%s\n' "$SEAM_PYTHON"
            return 0
        fi
        echo -e "${RED}Error: SEAM_PYTHON must be an executable Python 3.10+ interpreter.${NC}" >&2
        echo -e "${YELLOW}  Current value: ${SEAM_PYTHON:-(empty)}${NC}" >&2
        echo -e "${YELLOW}  Set SEAM_PYTHON to a Python 3.10+ executable or unset it to auto-probe.${NC}" >&2
        exit 61
    fi
    for candidate in python3.12 python3.11 python3.10 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && supports_runtime "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo -e "${RED}Error: no Python 3.10+ interpreter found.${NC}" >&2
    echo -e "${YELLOW}  Install Python 3.10+ (https://www.python.org/downloads/)${NC}" >&2
    echo -e "${YELLOW}  or set SEAM_PYTHON=/path/to/python3.10+ .${NC}" >&2
    exit 61
}

SEAM_PYTHON="$(resolve_python)"

echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   SEAM  Interactive  Initializer  (src/scripts/init_seam.sh)   ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Python:${NC}   $SEAM_PYTHON"
echo -e "${GREEN}Repo:${NC}     $REPO_ROOT"
echo ""

cd "$SRC_DIR"
exec "$SEAM_PYTHON" -m seam_init.cli "${FORWARD_ARGS[@]}"
