"""
Phase 3A: Generate predictions for current tournament matchups.

Reads Kalshi game markets to identify R1/R2 matchups, joins to KenPom ratings
via the crosswalk, constructs features, and generates model predictions.

Usage:
    uv run python forecasting/predict.py [--round 1] [--year 2026]
"""

import argparse
import pickle
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
MODEL_DIR = Path(__file__).parent.parent / "modeling" / "artifacts"
DB_PATH = Path(__file__).parent.parent / "data" / "madness.duckdb"


def load_model() -> dict:
    """Load the trained model from Phase 2."""
    model_path = MODEL_DIR / "model.pkl"
    if not model_path.exists():
        raise FileNotFoundError(
            f"No trained model found at {model_path}. Run `make model` first."
        )
    with open(model_path, "rb") as f:
        return pickle.load(f)


def build_matchups_from_kalshi(conn: duckdb.DuckDBPyConnection, year: int) -> pd.DataFrame:
    """Build tournament matchups from Kalshi game markets + KenPom ratings.

    Pairs up the two sides of each Kalshi event (game), fuzzy-matches team
    names to the crosswalk, and joins to KenPom ratings + four factors.
    """
    # Get active + recently settled Kalshi game markets for this year
    kalshi_df = conn.execute(f"""
        SELECT
            event_ticker
            , subtitle as team_name
            , implied_prob
            , volume
            , status
            , result
        FROM raw.kalshi_ncaambgame
        WHERE event_ticker LIKE 'KXNCAAMBGAME-{year % 100:02d}MAR%'
          AND status IN ('active', 'finalized', 'settled')
        ORDER BY event_ticker, team_name
    """).fetchdf()

    if kalshi_df.empty:
        raise ValueError(f"No Kalshi tournament markets found for {year}")

    # Pivot: two rows per event -> one row with both teams
    games = []
    for event, group in kalshi_df.groupby("event_ticker"):
        if len(group) != 2:
            continue
        rows = group.sort_values("team_name").reset_index(drop=True)
        games.append({
            "event_ticker": event,
            "team_1_name": rows.loc[0, "team_name"],
            "team_1_implied_prob": rows.loc[0, "implied_prob"],
            "team_2_name": rows.loc[1, "team_name"],
            "team_2_implied_prob": rows.loc[1, "implied_prob"],
            "status": rows.loc[0, "status"],
            "volume": rows["volume"].sum(),
        })
    games_df = pd.DataFrame(games)

    return games_df


def match_teams_to_kenpom(
    conn: duckdb.DuckDBPyConnection,
    games_df: pd.DataFrame,
    year: int,
) -> pd.DataFrame:
    """Match Kalshi team names to KenPom via crosswalk and fetch ratings.

    Kalshi uses names like "Duke", "Michigan St." — we need to match these
    to KenPom names via the crosswalk's canonical_name or kenpom_team_name.
    """
    # Load crosswalk and KenPom data
    crosswalk = conn.execute("SELECT * FROM staging.team_crosswalk").fetchdf()
    kenpom = conn.execute(f"""
        SELECT * FROM staging.stg_kenpom_ratings WHERE season = {year}
    """).fetchdf()
    four_factors = conn.execute(f"""
        SELECT * FROM staging.stg_kenpom_four_factors WHERE season = {year}
    """).fetchdf()

    # Build a lookup: various name forms -> canonical_name
    name_lookup = {}
    for _, row in crosswalk.iterrows():
        for col in ["kaggle_team_name", "kenpom_team_name", "barttorvik_team_name", "canonical_name"]:
            name = row.get(col)
            if pd.notna(name) and name:
                name_lookup[name.strip().lower()] = row["kenpom_team_name"]

    # Also add KenPom names directly (for teams not in tournament crosswalk)
    for _, row in kenpom.iterrows():
        kname = row.get("team_name") or row.get("kenpom_team_name")
        if kname:
            name_lookup[kname.strip().lower()] = kname

    # Hardcoded Kalshi -> KenPom name overrides
    kalshi_overrides = {
        "uconn": "Connecticut",
        "hawai'i": "Hawaii",
        "miami (fl)": "Miami FL",
        "miami (oh)": "Miami OH",
        "queens university": "Queens",
        "california baptist": "Cal Baptist",
        "prairie view a&m": "Prairie View A&M",
        "unc wilmington": "UNC Wilmington",
    }
    for k, v in kalshi_overrides.items():
        name_lookup[k] = v

    def resolve_name(kalshi_name: str) -> str | None:
        """Try to find KenPom name for a Kalshi team name."""
        if not kalshi_name:
            return None
        key = kalshi_name.strip().lower()
        if key in name_lookup:
            return name_lookup[key]
        # Try common transformations
        for suffix in [" st.", " st"]:
            if key.endswith(suffix):
                alt = key[:-len(suffix)] + " state"
                if alt in name_lookup:
                    return name_lookup[alt]
        # Try removing parenthetical qualifiers
        if "(" in key:
            base = key.split("(")[0].strip()
            if base in name_lookup:
                return name_lookup[base]
        return None

    # Resolve team names and join KenPom data
    results = []
    unmatched = set()
    for _, game in games_df.iterrows():
        kp1 = resolve_name(game["team_1_name"])
        kp2 = resolve_name(game["team_2_name"])

        if not kp1:
            unmatched.add(game["team_1_name"])
        if not kp2:
            unmatched.add(game["team_2_name"])
        if not kp1 or not kp2:
            continue

        # Look up KenPom ratings
        r1 = kenpom[kenpom["team_name"] == kp1]
        r2 = kenpom[kenpom["team_name"] == kp2]
        if r1.empty or r2.empty:
            if r1.empty:
                unmatched.add(game["team_1_name"])
            if r2.empty:
                unmatched.add(game["team_2_name"])
            continue

        r1 = r1.iloc[0]
        r2 = r2.iloc[0]

        # Look up four factors
        ff1 = four_factors[four_factors["team_name"] == kp1]
        ff2 = four_factors[four_factors["team_name"] == kp2]

        results.append({
            "event_ticker": game["event_ticker"],
            "team_1_kalshi": game["team_1_name"],
            "team_2_kalshi": game["team_2_name"],
            "team_1_kenpom": kp1,
            "team_2_kenpom": kp2,
            "team_1_implied_prob": game["team_1_implied_prob"],
            "team_2_implied_prob": game["team_2_implied_prob"],
            "team_1_kenpom_rank": r1.get("kenpom_rank"),
            "team_2_kenpom_rank": r2.get("kenpom_rank"),
            "team_1_adj_em": r1.get("adj_em"),
            "team_2_adj_em": r2.get("adj_em"),
            "team_1_adj_o": r1.get("adj_o"),
            "team_2_adj_o": r2.get("adj_o"),
            "team_1_adj_d": r1.get("adj_d"),
            "team_2_adj_d": r2.get("adj_d"),
            "team_1_adj_t": r1.get("adj_t"),
            "team_2_adj_t": r2.get("adj_t"),
            "team_1_luck": r1.get("luck"),
            "team_2_luck": r2.get("luck"),
            "team_1_sos_adj_em": r1.get("sos_adj_em"),
            "team_2_sos_adj_em": r2.get("sos_adj_em"),
            # Four factors if available
            "team_1_off_efg_pct": ff1.iloc[0].get("off_efg_pct") if not ff1.empty else None,
            "team_2_off_efg_pct": ff2.iloc[0].get("off_efg_pct") if not ff2.empty else None,
            "team_1_def_efg_pct": ff1.iloc[0].get("def_efg_pct") if not ff1.empty else None,
            "team_2_def_efg_pct": ff2.iloc[0].get("def_efg_pct") if not ff2.empty else None,
            "status": game["status"],
            "volume": game["volume"],
        })

    if unmatched:
        print(f"\nWARNING: Could not match {len(unmatched)} team(s) to KenPom:")
        for name in sorted(unmatched):
            print(f"  - {name}")

    return pd.DataFrame(results)


def load_seeds(conn: duckdb.DuckDBPyConnection, year: int) -> pd.DataFrame:
    """Load tournament seeds from the database (stg_kaggle_seeds).

    Seeds come from Kaggle (historical) or manual_seeds dbt seed (current year).
    """
    seeds = conn.execute(f"""
        SELECT
            kenpom_team_name as team
            , seed_number as seed
            , region
        FROM staging.stg_kaggle_seeds
        WHERE season = {year}
    """).fetchdf()
    if seeds.empty:
        raise ValueError(
            f"No seeds found for {year}. Add rows to "
            "dbt_project/seeds/manual_seeds.csv and run `make dbt`."
        )
    print(f"Loaded {len(seeds)} team seeds from database for {year}")
    return seeds


def assign_seeds_and_orient(
    matchups_df: pd.DataFrame,
    seeds_df: pd.DataFrame,
    name_lookup: dict[str, str],
) -> pd.DataFrame:
    """Assign actual tournament seeds and orient so team_a is the higher seed.

    Uses the seeds CSV to assign real seeds. Filters out non-tournament games
    (NIT, CBI, etc.) by requiring both teams to be in the seeds file.

    Convention: team_a = higher seed = better team = lower seed number.
    """
    # Build seed lookup: various name forms -> seed
    seed_lookup = {}
    for _, row in seeds_df.iterrows():
        seed_val = int(row["seed"])
        team_lower = row["team"].strip().lower()
        seed_lookup[team_lower] = seed_val
        # Also map via KenPom name from crosswalk
        kp_name = name_lookup.get(team_lower)
        if kp_name:
            seed_lookup[kp_name.strip().lower()] = seed_val

    def get_seed(kalshi_name: str, kenpom_name: str) -> int | None:
        """Look up seed for a team by Kalshi or KenPom name."""
        for name in [kalshi_name, kenpom_name]:
            if name:
                key = name.strip().lower()
                if key in seed_lookup:
                    return seed_lookup[key]
        return None

    df = matchups_df.copy()
    oriented = []
    skipped = []

    for _, row in df.iterrows():
        s1 = get_seed(row["team_1_kalshi"], row["team_1_kenpom"])
        s2 = get_seed(row["team_2_kalshi"], row["team_2_kenpom"])

        if s1 is None or s2 is None:
            skipped.append(f"{row['team_1_kalshi']} vs {row['team_2_kalshi']}")
            continue

        # Orient: team_a = lower seed number (higher seed)
        if s1 <= s2:
            r = _swap_teams(row, "1", "2")
            r["team_a_seed"] = s1
            r["team_b_seed"] = s2
        else:
            r = _swap_teams(row, "2", "1")
            r["team_a_seed"] = s2
            r["team_b_seed"] = s1
        oriented.append(r)

    if skipped:
        print(f"\nFiltered out {len(skipped)} non-tournament games:")
        for s in skipped:
            print(f"  - {s}")

    return pd.DataFrame(oriented)


def _swap_teams(row, a_suffix: str, b_suffix: str) -> dict:
    """Create oriented row where team_a comes from a_suffix, team_b from b_suffix."""
    return {
        "event_ticker": row["event_ticker"],
        "team_a_kalshi": row[f"team_{a_suffix}_kalshi"],
        "team_b_kalshi": row[f"team_{b_suffix}_kalshi"],
        "team_a_kenpom": row[f"team_{a_suffix}_kenpom"],
        "team_b_kenpom": row[f"team_{b_suffix}_kenpom"],
        "team_a_implied_prob": row[f"team_{a_suffix}_implied_prob"],
        "team_b_implied_prob": row[f"team_{b_suffix}_implied_prob"],
        "team_a_kenpom_rank": row[f"team_{a_suffix}_kenpom_rank"],
        "team_b_kenpom_rank": row[f"team_{b_suffix}_kenpom_rank"],
        "team_a_adj_em": row[f"team_{a_suffix}_adj_em"],
        "team_b_adj_em": row[f"team_{b_suffix}_adj_em"],
        "team_a_adj_o": row[f"team_{a_suffix}_adj_o"],
        "team_b_adj_o": row[f"team_{b_suffix}_adj_o"],
        "team_a_adj_d": row[f"team_{a_suffix}_adj_d"],
        "team_b_adj_d": row[f"team_{b_suffix}_adj_d"],
        "team_a_adj_t": row[f"team_{a_suffix}_adj_t"],
        "team_b_adj_t": row[f"team_{b_suffix}_adj_t"],
        "team_a_luck": row[f"team_{a_suffix}_luck"],
        "team_b_luck": row[f"team_{b_suffix}_luck"],
        "team_a_sos_adj_em": row[f"team_{a_suffix}_sos_adj_em"],
        "team_b_sos_adj_em": row[f"team_{b_suffix}_sos_adj_em"],
        "status": row["status"],
        "volume": row["volume"],
    }




def compute_features(df: pd.DataFrame, round_num: int) -> pd.DataFrame:
    """Compute model features (differentials) matching the trained model."""
    df = df.copy()
    df["adj_em_diff"] = df["team_a_adj_em"] - df["team_b_adj_em"]
    df["seed_diff"] = df["team_b_seed"] - df["team_a_seed"]  # positive = team_a has better seed
    df["luck_diff"] = df["team_a_luck"] - df["team_b_luck"]
    df["round_ind"] = 1.0 if round_num == 2 else 0.0
    df["seed_round_ix"] = df["seed_diff"] * df["round_ind"]
    return df


def seed_baseline_probs(
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
) -> pd.Series:
    """Compute seed-only baseline P(team_a_wins) from historical win rates.

    Uses all historical data (the model was trained on all past seasons,
    so the seed baseline should too for a fair comparison).
    """
    loyo_path = MODEL_DIR / "loyo_predictions.csv"
    if loyo_path.exists():
        hist = pd.read_csv(loyo_path)
    else:
        hist = conn.execute("SELECT * FROM staging.model_features").fetchdf()

    rates = hist.groupby(["team_a_seed", "team_b_seed"])["team_a_won"].mean().to_dict()
    global_rate = hist["team_a_won"].mean()

    return df.apply(
        lambda r: rates.get((int(r["team_a_seed"]), int(r["team_b_seed"])), global_rate),
        axis=1,
    )


def generate_predictions(
    df: pd.DataFrame,
    model_dict: dict,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """Generate P(team_a_wins) for each matchup using the trained model."""
    features = model_dict["features"]
    pipeline = model_dict["pipeline"]

    X = df[features].values
    probs = pipeline.predict_proba(X)[:, 1]
    df = df.copy()
    df["model_prob_a_wins"] = probs
    df["seed_baseline_prob"] = seed_baseline_probs(df, conn)
    return df


def main():
    parser = argparse.ArgumentParser(description="Generate tournament predictions")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--round", type=int, default=1, choices=[1, 2])
    parser.add_argument("--active-only", action="store_true",
                        help="Only include active (unsettled) markets")
    args = parser.parse_args()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating Round {args.round} predictions for {args.year}...")

    # Load model
    model_dict = load_model()
    print(f"Model: {model_dict['feature_set']} features: {model_dict['features']}")

    conn = duckdb.connect(str(DB_PATH), read_only=True)

    # Load seeds from database
    seeds_df = load_seeds(conn, args.year)

    # Build matchups from Kalshi markets
    games_df = build_matchups_from_kalshi(conn, args.year)
    print(f"Found {len(games_df)} Kalshi game events")

    if args.active_only:
        games_df = games_df[games_df["status"] == "active"]
        print(f"Filtered to {len(games_df)} active games")

    # Match to KenPom
    matched = match_teams_to_kenpom(conn, games_df, args.year)
    print(f"Matched {len(matched)} games to KenPom data")

    if matched.empty:
        print("ERROR: No games matched. Check team name crosswalk.")
        return

    # Build the name_lookup used by match_teams_to_kenpom for seed resolution
    crosswalk = conn.execute("SELECT * FROM staging.team_crosswalk").fetchdf()
    name_lookup = {}
    for _, row in crosswalk.iterrows():
        for col in ["kaggle_team_name", "kenpom_team_name", "barttorvik_team_name", "canonical_name"]:
            name = row.get(col)
            if pd.notna(name) and name:
                name_lookup[name.strip().lower()] = row["kenpom_team_name"]

    # Orient matchups and assign seeds (filters to tournament games only)
    seeded = assign_seeds_and_orient(matched, seeds_df, name_lookup)
    print(f"Tournament matchups with seeds: {len(seeded)}")

    # Compute features
    featured = compute_features(seeded, args.round)

    # Generate predictions
    predictions = generate_predictions(featured, model_dict, conn)

    # Select output columns
    output_cols = [
        "event_ticker",
        "team_a_kalshi", "team_b_kalshi",
        "team_a_kenpom", "team_b_kenpom",
        "team_a_seed", "team_b_seed",
        "team_a_kenpom_rank", "team_b_kenpom_rank",
        "team_a_adj_em", "team_b_adj_em",
        "model_prob_a_wins",
        "seed_baseline_prob",
        "team_a_implied_prob", "team_b_implied_prob",
        "status", "volume",
    ]
    output = predictions[[c for c in output_cols if c in predictions.columns]]
    output = output.sort_values("volume", ascending=False)

    # Save
    out_path = ARTIFACTS_DIR / f"predictions_{args.year}.csv"
    output.to_csv(out_path, index=False)
    print(f"\nSaved {len(output)} predictions to {out_path}")

    # Print summary
    print(f"\n{'='*80}")
    print(f"PREDICTIONS — {args.year} Round {args.round}")
    print(f"{'='*80}")
    print(f"{'Team A':<20} {'Team B':<20} {'Model':>6} {'Kalshi':>7} {'Edge':>6}")
    print(f"{'-'*20} {'-'*20} {'-'*6} {'-'*7} {'-'*6}")
    for _, row in output.head(40).iterrows():
        model_p = row["model_prob_a_wins"]
        kalshi_p = row.get("team_a_implied_prob", 0) or 0
        edge = model_p - kalshi_p
        print(
            f"{row['team_a_kalshi']:<20} {row['team_b_kalshi']:<20} "
            f"{model_p:>5.1%} {kalshi_p:>6.1%} {edge:>+5.1%}"
        )

    conn.close()


if __name__ == "__main__":
    main()
