# March Madness 2026

## Quick Start
```bash
uv sync
uv run python ingestion/ingest_kaggle.py      # Run first (no API key needed)
uv run python ingestion/ingest_kenpom.py       # Requires KENPOM_API_KEY in .env
uv run python ingestion/ingest_barttorvik.py   # Requires CBBDATA_API_KEY in .env (optional)
cd dbt_project && uv run dbt build             # Transform data
```

## Architecture
- **Database:** DuckDB at `data/madness.duckdb`
- **Ingestion:** Python scripts in `ingestion/` write to `raw` schema
- **Transformation:** dbt models in `dbt_project/` read from `raw`, write to `staging`/`marts`
- **Modeling:** Python scripts in `modeling/` read from `marts.model_features`

## Conventions
- Seasons use spring year (2024 = 2023-24 season)
- Team A is always the higher seed (lower number) in matchups
- All ingestion scripts are idempotent (safe to re-run)
- KenPom is primary data source; Barttorvik is supplementary
