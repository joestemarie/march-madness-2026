# March Madness 2026

## Quick Start
```bash
make setup                    # Install dependencies
# Place Kaggle CSVs in ingestion/kaggle_data/
# Add KENPOM_API_KEY to .env
make ingest-kaggle            # Run first (no API key needed)
make ingest-kenpom            # Requires KENPOM_API_KEY in .env
make ingest-barttorvik        # Requires CBBDATA_API_KEY in .env (optional)
make dbt                      # Seed crosswalk + build dbt models
make check                    # Verify row counts + win rates by seed
```

### Useful Commands
```bash
make dbt-test                 # Run dbt tests only
make crosswalk                # Rebuild team name crosswalk via fuzzy matching
make clean                    # Delete DuckDB + dbt artifacts
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
