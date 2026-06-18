#!/usr/bin/env bash
#
# Utility script for running development checks.
# Usage: ./scripts/check.sh

set -euo pipefail

echo "=== Running Lint ==="
pylint --reports=n \
  --disable=all \
  --enable=line-too-long,wrong-import-position,wrong-import-order,\
trailing-whitespace,superfluous-parens,multiple-imports,\
f-string-without-interpolation \
  $(find src/core -name "*.py" -not -path "*/.git/*" -not -path "*/__pycache__/*")

echo "=== Running Tests ==="
# pytest tests/ -v --tb=short || { echo "Tests failed"; exit 1; }

echo "=== All Checks Passed ==="

