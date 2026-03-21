"""
Quick script to pull current sportsbook lines for active R2 bets
and compare against model predictions + Kalshi prices.

Usage:
    uv run python forecasting/odds_check.py [--year 2026]
"""

import argparse
import os
from pathlib import Path

import httpx
import pandas as pd

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


def get_odds(api_key: str) -> list[dict]:
    """Fetch NCAA basketball moneylines from The Odds API."""
    url = f"{ODDS_API_BASE}/sports/basketball_ncaab/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal",
        "bookmakers": "draftkings,fanduel,betmgm",
    }
    resp = httpx.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def american_to_implied(american: int) -> float:
    """Convert American odds to implied probability."""
    if american > 0:
        return 100 / (american + 100)
    else:
        return abs(american) / (abs(american) + 100)


def decimal_to_implied(decimal: float) -> float:
    return 1 / decimal


def parse_odds(games: list[dict]) -> pd.DataFrame:
    """Extract moneyline implied probs and spread per game."""
    rows = []
    for game in games:
        home = game["home_team"]
        away = game["away_team"]

        home_ml_probs = []
        away_ml_probs = []
        spreads = []

        for bm in game.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        p = decimal_to_implied(outcome["price"])
                        if outcome["name"] == home:
                            home_ml_probs.append(p)
                        else:
                            away_ml_probs.append(p)
                elif market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home:
                            spreads.append(outcome["point"])

        if not home_ml_probs:
            continue

        # Normalize to remove vig
        raw_home = sum(home_ml_probs) / len(home_ml_probs)
        raw_away = sum(away_ml_probs) / len(away_ml_probs) if away_ml_probs else 1 - raw_home
        total = raw_home + raw_away
        home_prob = raw_home / total
        away_prob = raw_away / total
        spread = sum(spreads) / len(spreads) if spreads else None

        rows.append({
            "home_team": home,
            "away_team": away,
            "home_ml_prob": round(home_prob, 4),
            "away_ml_prob": round(away_prob, 4),
            "home_spread": round(spread, 1) if spread is not None else None,
            "n_books": len(game.get("bookmakers", [])),
        })

    return pd.DataFrame(rows)


def fuzzy_name_match(name_a: str, name_b: str) -> bool:
    """True if two team name strings overlap meaningfully."""
    a, b = name_a.lower(), name_b.lower()
    # strip common suffixes that differ between sources
    for suffix in [" jayhawks", " cyclones", " cornhuskers", " cavaliers",
                   " crimson tide", " huskies", " bruins", " volunteers",
                   " commodores", " red storm", " wolverines", " billikens",
                   " red raiders", " gators", " hawkeyes", " wildcats"]:
        a = a.replace(suffix, "")
        b = b.replace(suffix, "")
    return a in b or b in a


def match_bet_to_game(bet_team: str, matchup: str, odds_df: pd.DataFrame) -> dict | None:
    """Match a bet to a game using both teams in the matchup string."""
    teams = [t.strip() for t in matchup.split(" vs ")]
    for _, row in odds_df.iterrows():
        home, away = row["home_team"], row["away_team"]
        matched_teams = sum(
            1 for t in teams
            if fuzzy_name_match(t, home) or fuzzy_name_match(t, away)
        )
        if matched_teams >= 2:
            return row.to_dict()
    # fallback: single team match on bet_team
    for _, row in odds_df.iterrows():
        home, away = row["home_team"], row["away_team"]
        if fuzzy_name_match(bet_team, home) or fuzzy_name_match(bet_team, away):
            return row.to_dict()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY not set in environment.")
        return

    bet_path = ARTIFACTS_DIR / f"bet_sheet_{args.year}.csv"
    if not bet_path.exists():
        print(f"No bet sheet at {bet_path}. Run allocate.py first.")
        return

    bets = pd.read_csv(bet_path)

    print("Fetching odds from The Odds API...")
    games = get_odds(api_key)
    odds_df = parse_odds(games)
    print(f"Found {len(odds_df)} NCAAB games with odds.\n")

    print(f"{'='*105}")
    print("BET SHEET vs SPORTSBOOK LINES")
    print(f"{'='*105}")
    print(f"{'Matchup':<30} {'Bet':<12} {'Kalshi':>7} {'Model':>7} {'Book%':>7} {'Spread':>8} {'Agreement':>12} {'$':>7}")
    print(f"{'-'*30} {'-'*12} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*12} {'-'*7}")

    no_match = []
    for _, bet in bets.iterrows():
        game = match_bet_to_game(bet["bet_team"], bet["matchup"], odds_df)

        if game is None:
            no_match.append(bet["bet_team"])
            continue

        # Determine book prob for the bet team
        bet_lower = bet["bet_team"].lower()
        if (bet_lower in game["home_team"].lower() or
                game["home_team"].lower() in bet_lower):
            book_prob = game["home_ml_prob"]
            spread = game["home_spread"]
        else:
            book_prob = game["away_ml_prob"]
            spread = -game["home_spread"] if game["home_spread"] is not None else None

        model_prob = bet["model_prob"]
        kalshi_price = bet["kalshi_price"]
        spread_str = f"{spread:+.1f}" if spread is not None else "N/A"

        # Agreement: does book agree with model direction vs kalshi?
        model_edge = model_prob - kalshi_price
        book_edge = book_prob - kalshi_price
        if model_edge > 0 and book_edge > 0:
            agreement = "✓ both+"
        elif model_edge > 0 and book_edge < -0.05:
            agreement = "✗ book-"
        elif model_edge > 0 and abs(book_edge) <= 0.05:
            agreement = "~ neutral"
        else:
            agreement = "?"

        print(
            f"{bet['matchup']:<30} {bet['bet_team']:<12} "
            f"{kalshi_price:>6.1%} {model_prob:>6.1%} {book_prob:>6.1%} "
            f"{spread_str:>8} {agreement:>12} ${bet['bet_amount']:>6.2f}"
        )

    if no_match:
        print(f"\nNo odds match found for: {', '.join(no_match)}")

    print(f"\nOdds API quota remaining: check https://api.the-odds-api.com/v4/sports")


if __name__ == "__main__":
    main()
