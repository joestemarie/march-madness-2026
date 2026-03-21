"""Shared DuckDB connection helpers for ingestion scripts."""

import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "madness.duckdb"


def get_connection(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Get a DuckDB connection with the raw schema created."""
    db_path = Path(db_path) if db_path else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
    return conn


def load_dataframe(
    conn: duckdb.DuckDBPyConnection,
    df,
    table_name: str,
    schema: str = "raw",
) -> int:
    """Load a pandas DataFrame into a DuckDB table (CREATE OR REPLACE).

    Returns the number of rows loaded.
    """
    qualified = f"{schema}.{table_name}"
    conn.execute(f"CREATE OR REPLACE TABLE {qualified} AS SELECT * FROM df")
    count = conn.execute(f"SELECT count(*) FROM {qualified}").fetchone()[0]
    logger.info("Loaded %d rows into %s", count, qualified)
    return count
