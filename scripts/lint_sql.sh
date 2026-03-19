#!/usr/bin/env bash
set -euo pipefail

# Lint dbt SQL files for style violations:
# 1. No trailing commas — all commas must be leading (start of line)
# 2. No terminal CTE pattern — files must not end with "select * from <cte>"

MODELS_DIR="dbt_project/models"
EXIT_CODE=0

for f in $(find "$MODELS_DIR" -name '*.sql'); do
    # --- Check 1: trailing commas ---
    # Match lines that end with a comma (ignoring whitespace), but exclude
    # CTE-closing lines like "),", Jinja lines, and string literals.
    if grep -nP ',\s*$' "$f" | grep -vP '^\d+:\s*\)' | grep -vP '{[{%]' | grep -q .; then
        echo "FAIL [trailing-comma] $f"
        grep -nP ',\s*$' "$f" | grep -vP '^\d+:\s*\)' | grep -vP '{[{%]'
        EXIT_CODE=1
    fi

    # --- Check 2: terminal CTE ---
    # The last non-empty line should not be "select * from <identifier>"
    last_line=$(grep -v '^\s*$' "$f" | tail -1 | sed 's/^[[:space:]]*//' | tr '[:upper:]' '[:lower:]')
    if echo "$last_line" | grep -qP '^select \* from \w+$'; then
        echo "FAIL [terminal-cte] $f"
        echo "  Last line: $last_line"
        EXIT_CODE=1
    fi
done

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "OK — all SQL files pass style checks"
fi

exit $EXIT_CODE
