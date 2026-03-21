#!/usr/bin/env bash
set -euo pipefail

DB="data/madness.duckdb"

if [ ! -f "$DB" ]; then
    echo "ERROR: $DB not found. Run ingestion + dbt first."
    exit 1
fi

echo "=== Row Counts ==="
uv run python -c "
import duckdb
conn = duckdb.connect('$DB', read_only=True)
r1 = conn.sql(\"SELECT count(*) FROM marts.model_features WHERE round = 1\").fetchone()[0]
r2 = conn.sql(\"SELECT count(*) FROM marts.model_features WHERE round = 2\").fetchone()[0]
print(f'Round 1: {r1} rows (expect ~736)')
print(f'Round 2: {r2} rows (expect ~368)')
"

echo ""
echo "=== Win Rates by Seed Matchup ==="
uv run python -c "
import duckdb
conn = duckdb.connect('$DB', read_only=True)
conn.sql(\"\"\"
    SELECT
        team_a_seed || 'v' || team_b_seed as matchup,
        round(avg(team_a_won), 3) as win_rate,
        count(*) as n
    FROM marts.model_features
    GROUP BY 1
    ORDER BY 1
\"\"\").show()
"
