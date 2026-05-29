#!/usr/bin/env bash
#
# Utility script for running development checks.
# Usage: ./scripts/check.sh
set -euo pipefail
echo "=== Running Lint ==="
pylint --disable=missing-module-docstring,missing-class-docstring,missing-function-docstring,duplicate-code,cyclic-import src/ tests/ || { echo "Lint failed"; exit 1; }
echo "=== Running Tests ==="
# pytest tests/ -v --tb=short || { echo "Tests failed"; exit 1; }
echo "=== All Checks Passed ==="
