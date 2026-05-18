#!/usr/bin/env bash
# Live tail of migration_utils E2E execution logs
# Usage: ./monitor_e2e.sh
set -euo pipefail

MIGRATION_UTILS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${MIGRATION_UTILS_DIR}/e2e_run.log"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}━━━ E2E Log Monitor ━━━${NC}"
echo -e "Watching: ${LOG_FILE}"
echo -e "Log from previous run:   tail ${LOG_FILE}"
echo -e ""
echo -e "${GREEN}━━━ Latest Log Entries ━━━${NC}"
tail -50 "$LOG_FILE" 2>/dev/null || echo "(no log yet)"
echo -e ""
echo -e "${CYAN}━━━ Live TAIL - Press Ctrl+C to exit ━━━${NC}"

tail -f "$LOG_FILE" 2>/dev/null | while IFS= read -r line; do
    if [[ "$line" == *"PASSED"* || "$line" == *"PASS"* ]]; then
        echo -e "${GREEN}$line${NC}"
    elif [[ "$line" == *"FAILED"* || "$line" == *"FAIL"* || "$line" == *"ERROR"* || "$line" == *"Error"* ]]; then
        echo -e "${RED}$line${NC}"
    elif [[ "$line" == *"Phase"* || "$line" == *"phase"* ]]; then
        echo -e "${YELLOW}$line${NC}"
    else
        echo "$line"
    fi
done
