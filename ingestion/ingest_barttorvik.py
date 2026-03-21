"""Ingest Barttorvik T-Rank data via CBBData API into DuckDB raw schema.

Usage:
    uv run python ingestion/ingest_barttorvik.py [--start-year 2008] [--end-year 2026]

Requires CBBDATA_API_KEY in .env for API access.
"""

import argparse
import io
import logging
import os
import time
from datetime import datetime, timedelta

import httpx
import pandas as pd
from dotenv import load_dotenv

from ingestion.utils import get_connection, load_dataframe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress httpx request logging (it leaks the API key in URLs)
logging.getLogger("httpx").setLevel(logging.WARNING)

load_dotenv()

CBBDATA_API_KEY = os.environ.get("CBBDATA_API_KEY", "")
CBBDATA_BASE_URL = "https://www.cbbdata.com"
REQUEST_DELAY = 0.5  # 2 req/sec


def cbbdata_get_parquet(endpoint: str, params: dict | None = None) -> pd.DataFrame:
    """Make an authenticated GET request to the CBBData API, returning a DataFrame.

    The API returns Parquet-encoded responses and expects the API key
    as a ``key`` query parameter.
    """
    if params is None:
        params = {}
    params["key"] = CBBDATA_API_KEY
    url = f"{CBBDATA_BASE_URL}/{endpoint}"
    logger.info("GET %s params=%s", url, {k: v for k, v in params.items() if k != "key"})
    resp = httpx.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return pd.read_parquet(io.BytesIO(resp.content))


def cbbdata_get_json(endpoint: str, params: dict | None = None) -> list | dict:
    """Make an authenticated GET request to the CBBData API, returning JSON."""
    if params is None:
        params = {}
    params["key"] = CBBDATA_API_KEY
    url = f"{CBBDATA_BASE_URL}/{endpoint}"
    logger.debug("GET %s params=%s", url, {k: v for k, v in params.items() if k != "key"})
    resp = httpx.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_ratings(seasons: range) -> pd.DataFrame:
    """Fetch team ratings from CBBData API."""
    all_dfs = []
    consecutive_failures = 0
    for year in seasons:
        try:
            df = cbbdata_get_parquet("api/torvik/ratings", {"year": year})
            if not df.empty:
                df["season"] = year
                all_dfs.append(df)
                consecutive_failures = 0
                logger.info("Fetched %d rows for season %d", len(df), year)
            else:
                logger.warning("Empty response for season %d", year)
                consecutive_failures += 1
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 401, 403):
                logger.warning(
                    "Cannot fetch ratings for season %d (HTTP %d), skipping",
                    year,
                    e.response.status_code,
                )
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    logger.warning(
                        "5+ consecutive API failures, aborting API fetch"
                    )
                    break
            else:
                raise
        except Exception as e:
            logger.warning("Failed to fetch ratings for season %d: %s", year, e)
            consecutive_failures += 1
            if consecutive_failures >= 5:
                logger.warning("5+ consecutive failures, aborting API fetch")
                break
        time.sleep(REQUEST_DELAY)

    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()


def _load_season_day_zero(conn) -> dict[int, datetime]:
    """Load DayZero dates from kaggle CSV to convert DayNum to calendar dates."""
    csv_path = os.path.join(
        os.path.dirname(__file__), "kaggle_data", "MSeasons.csv"
    )
    seasons_df = pd.read_csv(csv_path)
    return {
        int(row["Season"]): datetime.strptime(row["DayZero"], "%m/%d/%Y")
        for _, row in seasons_df.iterrows()
    }


def fetch_game_predictions(conn, seasons: range) -> pd.DataFrame:
    """Fetch Barttorvik game predictions for historical tournament matchups.

    Uses the CBBData game prediction endpoint which accepts team names,
    opponent names, date (YYYYMMDD), and location. Requires kaggle_tourney_results
    and team_crosswalk to be loaded first.
    """
    # Load DayZero lookup for date conversion
    day_zero_map = _load_season_day_zero(conn)

    # Load crosswalk for kaggle_team_id -> barttorvik_team_name
    crosswalk_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "dbt_project", "seeds", "team_crosswalk.csv",
    )
    crosswalk = pd.read_csv(crosswalk_path)
    id_to_bt = dict(
        zip(crosswalk["kaggle_team_id"], crosswalk["barttorvik_team_name"])
    )

    # Get tournament matchups
    min_season = max(seasons.start, 2015)  # predictions available from 2014-15
    try:
        matchups = conn.execute("""
            SELECT Season, DayNum, WTeamID, LTeamID
            FROM raw.kaggle_tourney_results
            WHERE Season >= ?
              AND Season <= ?
              AND DayNum BETWEEN 134 AND 137
            ORDER BY Season, DayNum
        """, [min_season, seasons.stop - 1]).fetchdf()
    except Exception as e:
        logger.warning(
            "Cannot read kaggle_tourney_results (run ingest_kaggle.py first): %s", e
        )
        return pd.DataFrame()

    if matchups.empty:
        logger.warning("No tournament matchups found for game predictions")
        return pd.DataFrame()

    logger.info(
        "Fetching predictions for %d tournament games (%d-%d)...",
        len(matchups), min_season, seasons.stop - 1,
    )

    all_predictions = []
    consecutive_failures = 0
    skipped = 0

    for _, row in matchups.iterrows():
        season = int(row["Season"])
        w_id = int(row["WTeamID"])
        l_id = int(row["LTeamID"])
        day_num = int(row["DayNum"])

        # Map to Barttorvik team names
        team_name = id_to_bt.get(w_id)
        opp_name = id_to_bt.get(l_id)
        if not team_name or not opp_name:
            skipped += 1
            continue

        # Convert DayNum to YYYYMMDD date
        day_zero = day_zero_map.get(season)
        if not day_zero:
            skipped += 1
            continue
        game_date = day_zero + timedelta(days=day_num)
        date_str = game_date.strftime("%Y%m%d")

        try:
            data = cbbdata_get_json(
                "api/torvik/game/prediction",
                {
                    "team": team_name,
                    "opp": opp_name,
                    "date": date_str,
                    "location": "N",
                },
            )
            # Response is a list of 2 dicts (one per team)
            if isinstance(data, list) and len(data) == 2:
                for rec in data:
                    rec["season"] = season
                    rec["game_day_num"] = day_num
                all_predictions.extend(data)
                consecutive_failures = 0
            else:
                logger.debug("Unexpected response shape for %s vs %s", team_name, opp_name)
                consecutive_failures += 1
        except httpx.HTTPStatusError as e:
            logger.debug(
                "Prediction failed for %s vs %s (%d): HTTP %d",
                team_name, opp_name, season, e.response.status_code,
            )
            consecutive_failures += 1
        except Exception as e:
            logger.debug(
                "Prediction failed for %s vs %s (%d): %s",
                team_name, opp_name, season, e,
            )
            consecutive_failures += 1

        if consecutive_failures >= 10:
            logger.warning("10+ consecutive prediction failures, aborting")
            break
        time.sleep(REQUEST_DELAY)

    if skipped:
        logger.info("Skipped %d matchups (missing crosswalk or season date)", skipped)
    logger.info("Fetched predictions for %d team-game rows", len(all_predictions))

    if all_predictions:
        return pd.DataFrame(all_predictions)
    return pd.DataFrame()


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

    if not CBBDATA_API_KEY:
        logger.error(
            "CBBDATA_API_KEY not set in .env — get one at https://www.cbbdata.com"
        )
        return

    seasons = range(args.start_year, args.end_year + 1)
    logger.info(
        "Ingesting Barttorvik data for seasons %d-%d", seasons.start, seasons.stop - 1
    )

    conn = get_connection(args.db_path)

    logger.info("Fetching team ratings...")
    ratings_df = fetch_ratings(seasons)

    if not ratings_df.empty:
        load_dataframe(conn, ratings_df, "barttorvik_ratings")
    else:
        logger.warning("No Barttorvik ratings data fetched")

    if not args.skip_predictions:
        logger.info("Fetching game predictions...")
        predictions_df = fetch_game_predictions(conn, seasons)
        if not predictions_df.empty:
            load_dataframe(conn, predictions_df, "barttorvik_game_predictions")
        else:
            logger.warning("No game predictions fetched")
    else:
        logger.info("Skipping game predictions (--skip-predictions)")

    conn.close()
    logger.info("Barttorvik ingestion complete")


if __name__ == "__main__":
    main()
