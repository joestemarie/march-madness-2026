.PHONY: setup download-kaggle ingest ingest-kaggle ingest-kenpom ingest-barttorvik crosswalk dbt dbt-seed dbt-build dbt-test dbt-run lint check clean

# -- Setup --
setup:
	uv sync

# -- Download --
download-kaggle:
	uv run kaggle competitions download -c march-machine-learning-mania-2025 -p ingestion/kaggle_data/
	unzip -o ingestion/kaggle_data/*.zip -d ingestion/kaggle_data/

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

# -- Lint --
lint:
	uv run sqlfluff lint dbt_project/models/

lint-fix:
	uv run sqlfluff fix dbt_project/models/

# -- Sanity checks --
check:
	./scripts/check.sh

# -- Cleanup --
clean:
	rm -f data/madness.duckdb data/madness.duckdb.wal
	rm -rf dbt_project/target dbt_project/logs dbt_project/dbt_packages
