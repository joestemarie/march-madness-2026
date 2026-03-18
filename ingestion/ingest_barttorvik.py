"""Ingest Barttorvik T-Rank data via CBBData API into DuckDB raw schema.

Usage:
    uv run python ingestion/ingest_barttorvik.py [--start-year 2008] [--end-year 2026]

Optionally requires CBBDATA_API_KEY in .env for API access.
Falls back to CSV export from barttorvik.com if API is unavailable.
"""

import argparse
import logging
import os
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

CBBDATA_API_KEY = os.environ.get("CBBDATA_API_KEY", "")
CBBDATA_BASE_URL = "https://cbbdata.aweatherman.com"
BARTTORVIK_CSV_URL = "https://barttorvik.com/trank.php"
REQUEST_DELAY = 1.0  # 1 req/sec to be polite


def cbbdata_get(endpoint: str, params: dict | None = None) -> list[dict] | dict:
    """Make an authenticated GET request to the CBBData API."""
    headers = {}
    if CBBDATA_API_KEY:
        headers["Authorization"] = f"Bearer {CBBDATA_API_KEY}"
    url = f"{CBBDATA_BASE_URL}/{endpoint}"
    logger.info("GET %s params=%s", url, params)
    resp = httpx.get(url, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_ratings_api(seasons: range) -> pd.DataFrame:
    """Fetch team ratings from CBBData API."""
    all_rows = []
    for year in seasons:
        try:
            data = cbbdata_get("api/torvik/team_factors", {"year": year})
            if isinstance(data, list):
                for row in data:
                    row["season"] = year
                all_rows.extend(data)
            elif isinstance(data, dict) and "data" in data:
                for row in data["data"]:
                    row["season"] = year
                all_rows.extend(data["data"])
            else:
                logger.warning("Unexpected response shape for season %d", year)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 401, 403):
                logger.warning(
                    "Cannot fetch ratings for season %d (HTTP %d), skipping",
                    year,
                    e.response.status_code,
                )
            else:
                raise
        time.sleep(REQUEST_DELAY)
    return pd.DataFrame(all_rows)


def fetch_ratings_csv(seasons: range) -> pd.DataFrame:
    """Fallback: fetch ratings from Barttorvik CSV export."""
    all_dfs = []
    for year in seasons:
        try:
            url = f"{BARTTORVIK_CSV_URL}?year={year}&csv=1"
            logger.info("Fetching CSV from %s", url)
            resp = httpx.get(url, timeout=60, follow_redirects=True)
            resp.raise_for_status()

            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            df["season"] = year
            all_dfs.append(df)
        except Exception as e:
            logger.warning("Failed to fetch CSV for season %d: %s", year, e)
        time.sleep(REQUEST_DELAY)

    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()


def fetch_game_predictions(conn, seasons: range) -> pd.DataFrame:
    """Fetch game predictions for historical tournament matchups.

    Requires kaggle_tourney_results to be loaded first to know the matchups.
    """
    # Check if tourney results are available
    try:
        matchups = conn.execute("""
            SELECT DISTINCT Season, WTeamID, LTeamID, DayNum
            FROM raw.kaggle_tourney_results
            WHERE DayNum BETWEEN 134 AND 137
              AND Season >= ?
              AND Season <= ?
        """, [seasons.start, seasons.stop - 1]).fetchdf()
    except Exception as e:
        logger.warning(
            "Cannot read kaggle_tourney_results (run ingest_kaggle.py first): %s", e
        )
        return pd.DataFrame()

    if matchups.empty:
        logger.warning("No tournament matchups found for game predictions")
        return pd.DataFrame()

    # Also need team names from kaggle_teams
    try:
        teams = conn.execute(
            "SELECT TeamID, TeamName FROM raw.kaggle_teams"
        ).fetchdf()
        team_map = dict(zip(teams["TeamID"], teams["TeamName"]))
    except Exception:
        logger.warning("Cannot read kaggle_teams for name mapping")
        team_map = {}

    all_predictions = []
    for _, row in matchups.iterrows():
        season = int(row["Season"])
        w_team = team_map.get(int(row["WTeamID"]), str(row["WTeamID"]))
        l_team = team_map.get(int(row["LTeamID"]), str(row["LTeamID"]))

        try:
            data = cbbdata_get(
                "api/torvik/game_prediction",
                {
                    "team": w_team,
                    "opponent": l_team,
                    "year": season,
                    "location": "N",
                },
            )
            if isinstance(data, dict):
                data["season"] = season
                data["team_name"] = w_team
                data["opponent_name"] = l_team
                all_predictions.append(data)
            elif isinstance(data, list) and data:
                rec = data[0]
                rec["season"] = season
                rec["team_name"] = w_team
                rec["opponent_name"] = l_team
                all_predictions.append(rec)
        except httpx.HTTPStatusError as e:
            logger.debug(
                "Game prediction failed for %s vs %s (%d): HTTP %d",
                w_team,
                l_team,
                season,
                e.response.status_code,
            )
        except Exception as e:
            logger.debug(
                "Game prediction failed for %s vs %s (%d): %s",
                w_team,
                l_team,
                season,
                e,
            )
        time.sleep(REQUEST_DELAY)

    return pd.DataFrame(all_predictions)


def main():
    parser = argparse.ArgumentParser(
        description="Ingest Barttorvik/CBBData data into DuckDB"
    )
    current_year = datetime.now().year
    parser.add_argument("--start-year", type=int, default=2008)
    parser.add_argument("--end-year", type=int, default=current_year)
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument(
        "--skip-predictions",
        action="store_true",
        help="Skip game predictions (which require kaggle data loaded first)",
    )
    args = parser.parse_args()

    seasons = range(args.start_year, args.end_year + 1)
    logger.info(
        "Ingesting Barttorvik data for seasons %d-%d", seasons.start, seasons.stop - 1
    )

    conn = get_connection(args.db_path)

    # Fetch ratings — try API first, fall back to CSV
    logger.info("Fetching team ratings...")
    ratings_df = fetch_ratings_api(seasons)
    if ratings_df.empty:
        logger.info("API returned no data, trying CSV fallback...")
        ratings_df = fetch_ratings_csv(seasons)

    if not ratings_df.empty:
        load_dataframe(conn, ratings_df, "barttorvik_ratings")
    else:
        logger.warning("No Barttorvik ratings data fetched")

    # Fetch game predictions for historical tournament matchups
    if not args.skip_predictions:
        logger.info("Fetching game predictions for tournament matchups...")
        predictions_df = fetch_game_predictions(conn, seasons)
        if not predictions_df.empty:
            load_dataframe(conn, predictions_df, "barttorvik_game_predictions")
        else:
            logger.warning("No game predictions fetched (this is OK for initial setup)")
    else:
        logger.info("Skipping game predictions (--skip-predictions)")

    conn.close()
    logger.info("Barttorvik ingestion complete")


if __name__ == "__main__":
    main()
