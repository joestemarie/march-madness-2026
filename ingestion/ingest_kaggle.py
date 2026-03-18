"""Ingest Kaggle March Machine Learning Mania CSVs into DuckDB raw schema.

Usage:
    uv run python ingestion/ingest_kaggle.py [--data-dir ingestion/kaggle_data]

Expected CSV files in data-dir:
    - MNCAATourneyCompactResults.csv
    - MNCAATourneySeeds.csv
    - MTeams.csv
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from ingestion.utils import get_connection, load_dataframe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

REQUIRED_FILES = {
    "tourney_results": "MNCAATourneyCompactResults.csv",
    "seeds": "MNCAATourneySeeds.csv",
    "teams": "MTeams.csv",
}


def main():
    parser = argparse.ArgumentParser(description="Ingest Kaggle MMLM CSVs into DuckDB")
    parser.add_argument(
        "--data-dir",
        type=str,
        default="ingestion/kaggle_data",
        help="Directory containing Kaggle CSV files",
    )
    parser.add_argument("--db-path", type=str, default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        logger.error(
            "Data directory %s does not exist. Download Kaggle MMLM CSVs and place them there.",
            data_dir,
        )
        raise SystemExit(1)

    # Verify all required files exist
    for key, filename in REQUIRED_FILES.items():
        path = data_dir / filename
        if not path.exists():
            logger.error("Missing required file: %s", path)
            raise SystemExit(1)

    conn = get_connection(args.db_path)

    # Load tournament results
    results_df = pd.read_csv(data_dir / REQUIRED_FILES["tourney_results"])
    load_dataframe(conn, results_df, "kaggle_tourney_results")

    # Load seeds
    seeds_df = pd.read_csv(data_dir / REQUIRED_FILES["seeds"])
    load_dataframe(conn, seeds_df, "kaggle_seeds")

    # Load teams
    teams_df = pd.read_csv(data_dir / REQUIRED_FILES["teams"])
    load_dataframe(conn, teams_df, "kaggle_teams")

    conn.close()
    logger.info("Kaggle ingestion complete")


if __name__ == "__main__":
    main()
