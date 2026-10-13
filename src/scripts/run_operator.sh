#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
WORKFLOW_PATH="$SRC_DIR/workflows/npu_kernelbench_custom_op.yaml"
DEFAULT_POLICY="$REPO_ROOT/docs/operator_constraints.example.json"
SERVER_URL="http://127.0.0.1:4096"
TARGET_SOC=""
MAX_ITER=5
AGENT=""
POLICY_PATH="$DEFAULT_POLICY"
OUTPUT_DIR=""

usage() {
    cat <<'USAGE'
Usage:
  bash src/scripts/run_operator.sh <OPERATOR_TASK_DIR> <KERNELBENCH_ROOT> \
    --target-soc SOC [options]

Required:
  OPERATOR_TASK_DIR   One task directory containing definition.json/workload.jsonl
  KERNELBENCH_ROOT    npu-kernelbench package root (contains pyproject.toml)
  --target-soc SOC    Actual NPU SoC, e.g. Ascend910B3

Options:
  --operator-output-dir DIR  Trace/summary output directory
  --operator-constraints PATH  JSON API policy (default: docs/operator_constraints.example.json)
  --server-url URL           OpenCode server (default: http://127.0.0.1:4096)
  --agent NAME               OpenCode agent override
  --max-iter N               Maximum repair iterations (default: 5)
  --help                     Show this help

The launcher copies the task into SEAM's retained output workspace before the
agent writes solution.json, leaving the original benchmark task unchanged.
USAGE
}

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
    usage
    exit 0
fi
if [[ $# -lt 2 ]]; then
    usage >&2
    exit 2
fi
TASK_DIR="$1"
KERNELBENCH_ROOT="$2"
shift 2

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target-soc)
            [[ $# -ge 2 ]] || { echo "--target-soc requires a value" >&2; exit 2; }
            TARGET_SOC="$2"; shift 2 ;;
        --operator-output-dir)
            [[ $# -ge 2 ]] || { echo "--operator-output-dir requires a path" >&2; exit 2; }
            OUTPUT_DIR="$2"; shift 2 ;;
        --operator-constraints)
            [[ $# -ge 2 ]] || { echo "--operator-constraints requires a path" >&2; exit 2; }
            POLICY_PATH="$2"; shift 2 ;;
        --server-url)
            [[ $# -ge 2 ]] || { echo "--server-url requires a URL" >&2; exit 2; }
            SERVER_URL="$2"; shift 2 ;;
        --agent)
            [[ $# -ge 2 ]] || { echo "--agent requires a name" >&2; exit 2; }
            AGENT="$2"; shift 2 ;;
        --max-iter)
            [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--max-iter requires a positive integer" >&2; exit 2; }
            MAX_ITER="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

TASK_DIR="$(cd "$TASK_DIR" 2>/dev/null && pwd -P)" || { echo "Task directory not found" >&2; exit 2; }
KERNELBENCH_ROOT="$(cd "$KERNELBENCH_ROOT" 2>/dev/null && pwd -P)" || { echo "KernelBench root not found" >&2; exit 2; }
[[ -n "$TARGET_SOC" ]] || { echo "--target-soc is required" >&2; exit 2; }
[[ -f "$TASK_DIR/definition.json" ]] || { echo "Missing $TASK_DIR/definition.json" >&2; exit 2; }
[[ -f "$TASK_DIR/workload.jsonl" ]] || { echo "Missing $TASK_DIR/workload.jsonl" >&2; exit 2; }
[[ -f "$KERNELBENCH_ROOT/pyproject.toml" ]] || { echo "KernelBench root must contain pyproject.toml" >&2; exit 2; }
[[ -f "$POLICY_PATH" ]] || { echo "API policy not found: $POLICY_PATH" >&2; exit 2; }

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_ROOT="${SEAM_OPERATOR_OUTPUT_ROOT:-$(dirname "$REPO_ROOT")/operator_runs}"
    TASK_NAME="$(basename "$TASK_DIR")"
    TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
    OUTPUT_DIR="$OUTPUT_ROOT/$TASK_NAME/$TIMESTAMP"
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"

add_loopback_to_no_proxy() {
    local current="${NO_PROXY:-},${no_proxy:-}"
    local entry
    local -a entries
    local -a required=(127.0.0.1 localhost ::1)

    IFS=',' read -r -a entries <<< "$current"
    current=""
    for entry in "${entries[@]}"; do
        entry="${entry//[[:space:]]/}"
        [[ -n "$entry" ]] && current+="${current:+,}$entry"
    done
    for entry in "${required[@]}"; do
        local normalized_current
        local normalized_entry
        normalized_current="$(printf '%s' "$current" | tr '[:upper:]' '[:lower:]')"
        normalized_entry="$(printf '%s' "$entry" | tr '[:upper:]' '[:lower:]')"
        case ",$normalized_current," in
            *",$normalized_entry,"*) ;;
            *) current+="${current:+,}$entry" ;;
        esac
    done

    export NO_PROXY="$current"
    export no_proxy="$current"
}

add_loopback_to_no_proxy

cd "$REPO_ROOT"
ARGS=(
    -m tests.e2e.e2e_test_v3
    --project-dir "$TASK_DIR"
    --workflow-path "$WORKFLOW_PATH"
    --operator-kernelbench-root "$KERNELBENCH_ROOT"
    --operator-output-dir "$OUTPUT_DIR"
    --operator-target-soc "$TARGET_SOC"
    --operator-solution-type ascendc
    --operator-constraints "$POLICY_PATH"
    --server-url "$SERVER_URL"
    --server-no-auto-start
    --opencode-readiness message
    --max-phase5-iter "$MAX_ITER"
    --keep-temp-dir
)
if [[ -n "$AGENT" ]]; then
    ARGS+=(--agent "$AGENT")
fi

printf 'Operator task: %s\nKernelBench: %s\nTarget SoC: %s\nOutput: %s\n' \
    "$TASK_DIR" "$KERNELBENCH_ROOT" "$TARGET_SOC" "$OUTPUT_DIR"
PYTHONPATH="$SRC_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "${ARGS[@]}"
