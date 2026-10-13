#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_OPERATOR="$SCRIPT_DIR/run_operator.sh"
COUNT=5
SERVER_URL="http://127.0.0.1:4096"
TARGET_SOC=""
AGENT=""
POLICY_PATH="$REPO_ROOT/docs/operator_constraints.example.json"
OUTPUT_ROOT=""
TASK_ROOT=""
LEVELS=""
MAX_ITER=5

usage() {
    cat <<'USAGE'
Usage:
  bash src/scripts/run_operator_levels.sh <KERNELBENCH_ROOT> \
    --target-soc SOC [options]

Options:
  --task-root DIR             KernelBench task root (default: ROOT/data/kernel_generator)
  --levels LIST               Comma-separated levels, e.g. level1,level2 (default: all)
  --count N                   Tasks per level (default: 5)
  --output-root DIR           Batch output/report directory
  --operator-constraints PATH JSON API policy
  --server-url URL            OpenCode server (default: http://127.0.0.1:4096)
  --agent NAME                OpenCode agent override
  --max-iter N                Maximum repair iterations per task (default: 5)
  --help                      Show this help
USAGE
}

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then usage; exit 0; fi
if [[ $# -lt 1 ]]; then usage >&2; exit 2; fi
KERNELBENCH_ROOT="$1"; shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --task-root) [[ $# -ge 2 ]] || { echo "--task-root requires a path" >&2; exit 2; }; TASK_ROOT="$2"; shift 2 ;;
        --levels) [[ $# -ge 2 ]] || { echo "--levels requires a list" >&2; exit 2; }; LEVELS="$2"; shift 2 ;;
        --count) [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--count requires a positive integer" >&2; exit 2; }; COUNT="$2"; shift 2 ;;
        --output-root) [[ $# -ge 2 ]] || { echo "--output-root requires a path" >&2; exit 2; }; OUTPUT_ROOT="$2"; shift 2 ;;
        --target-soc) [[ $# -ge 2 ]] || { echo "--target-soc requires a value" >&2; exit 2; }; TARGET_SOC="$2"; shift 2 ;;
        --operator-constraints) [[ $# -ge 2 ]] || { echo "--operator-constraints requires a path" >&2; exit 2; }; POLICY_PATH="$2"; shift 2 ;;
        --server-url) [[ $# -ge 2 ]] || { echo "--server-url requires a URL" >&2; exit 2; }; SERVER_URL="$2"; shift 2 ;;
        --agent) [[ $# -ge 2 ]] || { echo "--agent requires a name" >&2; exit 2; }; AGENT="$2"; shift 2 ;;
        --max-iter) [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--max-iter requires a positive integer" >&2; exit 2; }; MAX_ITER="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -n "$TARGET_SOC" ]] || { echo "--target-soc is required" >&2; exit 2; }
KERNELBENCH_ROOT="$(cd "$KERNELBENCH_ROOT" 2>/dev/null && pwd -P)" || { echo "KernelBench root not found" >&2; exit 2; }
[[ -f "$KERNELBENCH_ROOT/pyproject.toml" ]] || { echo "KernelBench root must contain pyproject.toml" >&2; exit 2; }
if [[ -z "$TASK_ROOT" ]]; then TASK_ROOT="$KERNELBENCH_ROOT/data/kernel_generator"; fi
TASK_ROOT="$(cd "$TASK_ROOT" 2>/dev/null && pwd -P)" || { echo "Task root not found" >&2; exit 2; }
[[ -f "$POLICY_PATH" ]] || { echo "API policy not found: $POLICY_PATH" >&2; exit 2; }
if [[ -z "$OUTPUT_ROOT" ]]; then OUTPUT_ROOT="$REPO_ROOT/operator_runs/batch_$(date '+%Y%m%d_%H%M%S')"; fi
mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd -P)"

level_dirs=()
if [[ -n "$LEVELS" ]]; then
    IFS=',' read -r -a requested_levels <<< "$LEVELS"
    for level in "${requested_levels[@]}"; do
        [[ "$level" == level* ]] || level="level$level"
        level_dir="$TASK_ROOT/$level"
        [[ -d "$level_dir" ]] || { echo "Level not found: $level_dir" >&2; exit 2; }
        level_dirs+=("$level_dir")
    done
else
    while IFS= read -r level_dir; do level_dirs+=("$level_dir"); done < <(find "$TASK_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'level*' | sort)
fi
((${#level_dirs[@]} > 0)) || { echo "No level directories found under $TASK_ROOT" >&2; exit 2; }

report="$OUTPUT_ROOT/operator_batch_report.md"
{
    echo "# SEAM 算子批量迁移报告"
    echo
    echo "- KernelBench: \`$KERNELBENCH_ROOT\`"
    echo "- Task root: \`$TASK_ROOT\`"
    echo "- Target SoC: \`$TARGET_SOC\`"
    echo "- 每个 level: 前 $COUNT 个 task"
    echo "- OpenCode: \`$SERVER_URL\`"
    echo
    echo "| Level | Operator | Exit code | Log |"
    echo "|---|---|---:|---|"
} > "$report"

failed=0
for level_dir in "${level_dirs[@]}"; do
    while IFS= read -r task; do
        [[ -n "$task" ]] || continue
        operator="$(basename "$task")"
        task_output="$OUTPUT_ROOT/$(basename "$level_dir")/$operator"
        mkdir -p "$task_output"
        log="$task_output/launcher.log"
        args=("$task" "$KERNELBENCH_ROOT" --target-soc "$TARGET_SOC" --operator-output-dir "$task_output" --operator-constraints "$POLICY_PATH" --server-url "$SERVER_URL" --max-iter "$MAX_ITER")
        [[ -n "$AGENT" ]] && args+=(--agent "$AGENT")
        echo "Running $(basename "$level_dir")/$operator"
        if bash "$RUN_OPERATOR" "${args[@]}" >"$log" 2>&1; then code=0; else code=$?; fi
        [[ "$code" -eq 0 ]] || failed=$((failed + 1))
        printf '| `%s` | `%s` | `%s` | `%s` |\n' "$(basename "$level_dir")" "$operator" "$code" "${log#$OUTPUT_ROOT/}" >> "$report"
    done < <(find "$level_dir" -mindepth 2 -maxdepth 2 -type f -name definition.json -print | sed 's#/definition.json$##' | sort | head -n "$COUNT")
done

{
    echo
    echo "- Total failed launchers: $failed"
    echo "- Detailed logs and KernelBench outputs are under: \`$OUTPUT_ROOT\`"
} >> "$report"
echo "Report: $report"
((failed == 0))
