"""Ingest KenPom API data into DuckDB raw schema.

Usage:
    uv run python ingestion/ingest_kenpom.py [--start-year 2002] [--end-year 2026]

Requires KENPOM_API_KEY in .env file.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

import httpx
import pandas as pd
from dotenv import load_dotenv

from ingestion.utils import get_connection, load_dataframe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

KENPOM_API_KEY = os.environ.get("KENPOM_API_KEY", "")
BASE_URL = "https://kenpom.com/api/v1"
REQUEST_DELAY = 0.5  # seconds between requests


def kenpom_get(endpoint: str, params: dict | None = None) -> list[dict] | dict:
    """Make an authenticated GET request to the KenPom API.

    NOTE: The base URL and endpoint paths below are guesses from the KenPom
    registration page. If you get 403 errors, check the API docs that came
    with your key and update BASE_URL / endpoint paths accordingly.
    """
    if not KENPOM_API_KEY:
        raise RuntimeError("KENPOM_API_KEY not set. Add it to your .env file.")
    headers = {"Authorization": f"Bearer {KENPOM_API_KEY}"}
    url = f"{BASE_URL}/{endpoint}"
    logger.info("GET %s params=%s", url, params)
    resp = httpx.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 403:
        logger.error(
            "403 Forbidden from KenPom API. Check that:\n"
            "  1. KENPOM_API_KEY in .env is correct\n"
            "  2. BASE_URL (%s) matches your API docs\n"
            "  3. The endpoint path '%s' is correct\n"
            "  4. Your API subscription is active",
            BASE_URL, endpoint,
        )
    resp.raise_for_status()
    return resp.json()


def fetch_ratings(seasons: range) -> pd.DataFrame:
    """Fetch team ratings for all requested seasons."""
    all_rows = []
    for year in seasons:
        try:
            data = kenpom_get("ratings", {"season": year})
            if isinstance(data, list):
                for row in data:
                    row["season"] = year
                all_rows.extend(data)
            else:
                logger.warning("Unexpected response shape for ratings season %d", year)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("No ratings data for season %d (404), skipping", year)
            else:
                raise
        time.sleep(REQUEST_DELAY)
    return pd.DataFrame(all_rows)


def fetch_four_factors(seasons: range) -> pd.DataFrame:
    """Fetch four factors data for all requested seasons."""
    all_rows = []
    for year in seasons:
        try:
            data = kenpom_get("fourfactors", {"season": year})
            if isinstance(data, list):
                for row in data:
                    row["season"] = year
                all_rows.extend(data)
            else:
                logger.warning(
                    "Unexpected response shape for fourfactors season %d", year
                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(
                    "No four factors data for season %d (404), skipping", year
                )
            else:
                raise
        time.sleep(REQUEST_DELAY)
    return pd.DataFrame(all_rows)


def main():
    parser = argparse.ArgumentParser(description="Ingest KenPom data into DuckDB")
    current_year = datetime.now().year
    parser.add_argument("--start-year", type=int, default=2002)
    parser.add_argument("--end-year", type=int, default=current_year)
    parser.add_argument("--db-path", type=str, default=None)
    args = parser.parse_args()

    seasons = range(args.start_year, args.end_year + 1)
    logger.info("Ingesting KenPom data for seasons %d-%d", seasons.start, seasons.stop - 1)

    conn = get_connection(args.db_path)

    # Fetch and load ratings
    logger.info("Fetching ratings...")
    ratings_df = fetch_ratings(seasons)
    if not ratings_df.empty:
        load_dataframe(conn, ratings_df, "kenpom_ratings")
    else:
        logger.warning("No ratings data fetched")

    # Fetch and load four factors
    logger.info("Fetching four factors...")
    ff_df = fetch_four_factors(seasons)
    if not ff_df.empty:
        load_dataframe(conn, ff_df, "kenpom_four_factors")
    else:
        logger.warning("No four factors data fetched")

    conn.close()
    logger.info("KenPom ingestion complete")


if __name__ == "__main__":
    main()
