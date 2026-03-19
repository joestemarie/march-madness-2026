.PHONY: setup ingest ingest-kaggle ingest-kenpom ingest-barttorvik crosswalk dbt dbt-seed dbt-build dbt-test clean

# -- Setup --
setup:
	uv sync

# -- Ingestion (run in order) --
ingest: ingest-kaggle ingest-kenpom ingest-barttorvik

ingest-kaggle:
	uv run python ingestion/ingest_kaggle.py

ingest-kenpom:
	uv run python ingestion/ingest_kenpom.py

ingest-barttorvik:
	uv run python ingestion/ingest_barttorvik.py

ingest-barttorvik-no-predictions:
	uv run python ingestion/ingest_barttorvik.py --skip-predictions

crosswalk:
	uv run python ingestion/build_crosswalk.py

# -- dbt --
dbt: dbt-seed dbt-build

dbt-seed:
	cd dbt_project && uv run dbt seed

dbt-build:
	cd dbt_project && uv run dbt build

dbt-test:
	cd dbt_project && uv run dbt test

dbt-run:
	cd dbt_project && uv run dbt run

# -- Full pipeline --
all: setup ingest dbt

# -- Queries --
check-counts:
	uv run python -c "\
	import duckdb; \
	conn = duckdb.connect('data/madness.duckdb'); \
	r1 = conn.sql(\"SELECT count(*) FROM marts.model_features WHERE round = 1\").fetchone()[0]; \
	r2 = conn.sql(\"SELECT count(*) FROM marts.model_features WHERE round = 2\").fetchone()[0]; \
	print(f'Round 1: {r1} rows (expect ~736)'); \
	print(f'Round 2: {r2} rows (expect ~368)')"

check-seeds:
	uv run python -c "\
	import duckdb; \
	conn = duckdb.connect('data/madness.duckdb'); \
	conn.sql(\"SELECT team_a_seed || 'v' || team_b_seed as matchup, round(avg(team_a_won), 3) as win_rate, count(*) as n FROM marts.model_features GROUP BY 1 ORDER BY 1\").show()"

# -- Cleanup --
clean:
	rm -f data/madness.duckdb data/madness.duckdb.wal
	rm -rf dbt_project/target dbt_project/logs dbt_project/dbt_packages
