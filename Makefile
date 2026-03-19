.PHONY: setup download-kaggle ingest ingest-kaggle ingest-kenpom ingest-barttorvik ingest-kalshi crosswalk dbt dbt-seed dbt-build dbt-test dbt-run lint check clean train calibrate evaluate model predict edges allocate forecast execute

# -- Setup --
setup:
	uv sync

# -- Download --
download-kaggle:
	set -a && [ -f .env ] && . ./.env; set +a && \
	uv run kaggle competitions download -c march-machine-learning-mania-2025 -p ingestion/kaggle_data/
	unzip -o ingestion/kaggle_data/*.zip -d ingestion/kaggle_data/

# -- Ingestion (run in order) --
ingest: ingest-kaggle ingest-kenpom ingest-barttorvik ingest-kalshi

ingest-kaggle:
	uv run python ingestion/ingest_kaggle.py

ingest-kenpom:
	uv run python ingestion/ingest_kenpom.py

ingest-barttorvik:
	uv run python ingestion/ingest_barttorvik.py

ingest-barttorvik-no-predictions:
	uv run python ingestion/ingest_barttorvik.py --skip-predictions

ingest-kalshi:
	uv run python ingestion/ingest_kalshi.py

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

# -- Modeling --
train:
	uv run python modeling/train.py $(FEATURE_SET)

calibrate:
	uv run python modeling/calibration.py

evaluate:
	uv run python modeling/evaluate.py

model: train calibrate evaluate

# -- Forecasting --
predict:
	uv run python forecasting/predict.py --active-only

edges:
	uv run python forecasting/edge.py

allocate:
	uv run python forecasting/allocate.py

forecast: ingest-kalshi predict edges allocate

execute:
	uv run python forecasting/execute.py

execute-live:
	uv run python forecasting/execute.py --live

# -- Cleanup --
clean:
	rm -f data/madness.duckdb data/madness.duckdb.wal
	rm -rf dbt_project/target dbt_project/logs dbt_project/dbt_packages
