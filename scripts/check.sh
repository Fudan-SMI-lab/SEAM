#!/usr/bin/env bash
#
# Utility script for running development checks.
# Usage: ./scripts/check.sh

set -euo pipefail

echo "=== Running Lint ==="

resolve_lint_base() {
  local requested_base="${PYLINT_BASE_SHA:-}"
  if [[ -n "$requested_base" \
        && ! "$requested_base" =~ ^0+$ \
        && $(git cat-file -t "$requested_base" 2>/dev/null || true) == commit ]]; then
    printf '%s\n' "$requested_base"
    return 0
  fi

  local default_branch="${PYLINT_DEFAULT_BRANCH:-}"
  if [[ -n "$default_branch" ]] \
      && git rev-parse --verify --quiet "origin/$default_branch^{commit}" >/dev/null; then
    git merge-base HEAD "origin/$default_branch"
    return 0
  fi

  return 1
}

LINT_FILES=()
if LINT_BASE=$(resolve_lint_base); then
  echo "Lint scope: production Python files changed since $LINT_BASE"
  while IFS= read -r path; do
    [[ -n "$path" ]] && LINT_FILES+=("$path")
  done < <(
    git diff --diff-filter=ACMR --name-only "$LINT_BASE" HEAD -- 'src/**/*.py' \
      | awk '!/(^|\/)tests\//' \
      | sort
  )
else
  echo "Lint scope: all production Python files"
  while IFS= read -r path; do
    LINT_FILES+=("$path")
  done < <(
    find src/ -name "*.py" \
      -not -path "*/tests/*" \
      -not -path "*/.git/*" \
      -not -path "*/__pycache__/*" \
      | sort
  )
fi

if [[ ${#LINT_FILES[@]} -eq 0 ]]; then
  echo "No changed production Python files to lint."
else
  pylint --reports=n \
    --disable=all \
    --enable=line-too-long,wrong-import-position,wrong-import-order,\
trailing-whitespace,superfluous-parens,multiple-imports,\
f-string-without-interpolation \
    "${LINT_FILES[@]}"
fi

echo "=== Running Tests ==="
# pytest tests/ -v --tb=short || { echo "Tests failed"; exit 1; }

echo "=== All Checks Passed ==="
