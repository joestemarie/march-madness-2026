"""Utility to build/update the team crosswalk CSV using fuzzy matching.

Usage:
    uv run python ingestion/build_crosswalk.py

Reads distinct team names from all ingested sources and attempts to match them.
Outputs unmatched teams that need manual resolution.
"""

import logging
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

from ingestion.utils import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

CROSSWALK_PATH = Path(__file__).resolve().parent.parent / "dbt_project" / "seeds" / "team_crosswalk.csv"

# Manual overrides for Kaggle names that can't be fuzzy-matched reliably.
# Format: kaggle_name -> (kenpom_name, barttorvik_name)
MANUAL_OVERRIDES: dict[str, tuple[str, str]] = {
    "TAM C. Christi": ("Texas A&M Corpus Chris", "Texas A&M Corpus Chris"),
    "FGCU": ("Florida Gulf Coast", "Florida Gulf Coast"),
    "Central Conn": ("Central Connecticut", "Central Connecticut"),
    "ULM": ("Louisiana Monroe", "Louisiana Monroe"),
    "St Mary's CA": ("Saint Mary's", "Saint Mary's"),
    "MS Valley St": ("Mississippi Valley St.", "Mississippi Valley St."),
    "MTSU": ("Middle Tennessee", "Middle Tennessee"),
    "WKU": ("Western Kentucky", "Western Kentucky"),
    "NE Omaha": ("Nebraska Omaha", "Nebraska Omaha"),
    "UT San Antonio": ("UTSA", "UTSA"),
    "ETSU": ("East Tennessee St.", "East Tennessee St."),
    "NC A&T": ("North Carolina A&T", "North Carolina A&T"),
    "SIUE": ("SIU Edwardsville", "SIU Edwardsville"),
}


def get_source_teams(conn) -> dict[str, list[str]]:
    """Pull distinct team names from each ingested source."""
    teams = {}

    try:
        kaggle = conn.execute(
            "SELECT DISTINCT TeamID, TeamName FROM raw.kaggle_teams"
        ).fetchdf()
        teams["kaggle"] = kaggle
        logger.info("Found %d Kaggle teams", len(kaggle))
    except Exception:
        logger.warning("No kaggle_teams table found")

    try:
        kenpom = conn.execute(
            'SELECT DISTINCT "TeamName" FROM raw.kenpom_ratings'
        ).fetchdf()
        teams["kenpom"] = list(kenpom["TeamName"])
        logger.info("Found %d KenPom teams", len(kenpom))
    except Exception:
        logger.warning("No kenpom_ratings table found")

    try:
        torvik = conn.execute(
            "SELECT DISTINCT team FROM raw.barttorvik_ratings"
        ).fetchdf()
        teams["barttorvik"] = list(torvik["team"])
        logger.info("Found %d Barttorvik teams", len(torvik))
    except Exception:
        logger.warning("No barttorvik_ratings table found")

    return teams


def fuzzy_match(name: str, candidates: list[str], threshold: int = 78) -> str | None:
    """Find the best fuzzy match for a name in a list of candidates."""
    if not candidates:
        return None
    # Try exact first, then token_set_ratio (handles abbreviations well),
    # then plain ratio as fallback
    for scorer in [fuzz.token_set_ratio, fuzz.ratio]:
        result = process.extractOne(name, candidates, scorer=scorer)
        if result and result[1] >= threshold:
            return result[0]
    return None


def build_crosswalk():
    """Build or update the team crosswalk by matching names across sources."""
    conn = get_connection()
    source_teams = get_source_teams(conn)

    if "kaggle" not in source_teams:
        logger.error("Kaggle teams must be loaded first. Run ingest_kaggle.py.")
        return

    # Load existing crosswalk if it exists
    existing = None
    if CROSSWALK_PATH.exists():
        existing = pd.read_csv(CROSSWALK_PATH)
        logger.info("Loaded existing crosswalk with %d entries", len(existing))

    kaggle_df = source_teams["kaggle"]
    kenpom_names = source_teams.get("kenpom", [])
    barttorvik_names = source_teams.get("barttorvik", [])

    # Only process tournament teams (those that appear in seeds)
    try:
        tourney_team_ids = conn.execute(
            "SELECT DISTINCT TeamID FROM raw.kaggle_seeds"
        ).fetchdf()["TeamID"].tolist()
        kaggle_df = kaggle_df[kaggle_df["TeamID"].isin(tourney_team_ids)]
        logger.info("Filtered to %d tournament teams", len(kaggle_df))
    except Exception:
        logger.warning("Could not filter to tournament teams")

    rows = []
    unmatched_kenpom = []
    unmatched_barttorvik = []

    for _, row in kaggle_df.iterrows():
        team_id = int(row["TeamID"])
        kaggle_name = row["TeamName"]

        # Check existing crosswalk first
        if existing is not None:
            match = existing[existing["kaggle_team_id"] == team_id]
            if not match.empty:
                rows.append(match.iloc[0].to_dict())
                continue

        # Try manual overrides first, then fuzzy matching
        if kaggle_name in MANUAL_OVERRIDES:
            kenpom_match, barttorvik_match = MANUAL_OVERRIDES[kaggle_name]
        else:
            kenpom_match = fuzzy_match(kaggle_name, kenpom_names)
            barttorvik_match = fuzzy_match(kaggle_name, barttorvik_names)

        if not kenpom_match:
            unmatched_kenpom.append(kaggle_name)
        if not barttorvik_match:
            unmatched_barttorvik.append(kaggle_name)

        rows.append({
            "kaggle_team_id": team_id,
            "kaggle_team_name": kaggle_name,
            "kenpom_team_name": kenpom_match or "",
            "barttorvik_team_name": barttorvik_match or "",
            "canonical_name": kaggle_name,
        })

    result_df = pd.DataFrame(rows)
    result_df.to_csv(CROSSWALK_PATH, index=False)
    logger.info("Wrote crosswalk with %d entries to %s", len(result_df), CROSSWALK_PATH)

    if unmatched_kenpom:
        logger.warning(
            "Unmatched KenPom teams (%d): %s",
            len(unmatched_kenpom),
            unmatched_kenpom[:20],
        )
    if unmatched_barttorvik:
        logger.warning(
            "Unmatched Barttorvik teams (%d): %s",
            len(unmatched_barttorvik),
            unmatched_barttorvik[:20],
        )

    conn.close()


if __name__ == "__main__":
    build_crosswalk()
