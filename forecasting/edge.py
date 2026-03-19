"""
Phase 3B: Edge detection — compare model probabilities to Kalshi market prices.

Reads predictions from predict.py output and flags bets where the model
diverges from the market by more than a configurable threshold.

Usage:
    uv run python forecasting/edge.py [--year 2026] [--threshold 0.05]
"""

import argparse
from pathlib import Path

import pandas as pd

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def compute_edges(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Compute edge (model_prob - kalshi_implied) for each matchup.

    For each game, we evaluate both sides:
    - Betting on team_a: edge = model_prob_a - kalshi_implied_a
    - Betting on team_b: edge = (1 - model_prob_a) - kalshi_implied_b
    We pick the side with the larger absolute edge.
    """
    rows = []
    for _, r in df.iterrows():
        model_a = r["model_prob_a_wins"]
        model_b = 1 - model_a
        kalshi_a = r.get("team_a_implied_prob") or 0
        kalshi_b = r.get("team_b_implied_prob") or 0

        edge_a = model_a - kalshi_a
        edge_b = model_b - kalshi_b

        # Pick the side with the bigger edge
        if abs(edge_a) >= abs(edge_b):
            bet_team = r["team_a_kalshi"]
            bet_side = "team_a"
            model_prob = model_a
            kalshi_prob = kalshi_a
            edge = edge_a
        else:
            bet_team = r["team_b_kalshi"]
            bet_side = "team_b"
            model_prob = model_b
            kalshi_prob = kalshi_b
            edge = edge_b

        # Only flag positive edges (model thinks team is undervalued)
        if edge <= 0:
            confidence = "none"
        elif edge >= 0.15:
            confidence = "high"
        elif edge >= 0.10:
            confidence = "medium"
        elif edge >= threshold:
            confidence = "low"
        else:
            confidence = "none"

        # Seed baseline probability for the bet side
        seed_a = r.get("seed_baseline_prob", None)
        if seed_a is not None and bet_side == "team_b":
            seed_prob = 1 - seed_a
        else:
            seed_prob = seed_a

        rows.append({
            "event_ticker": r["event_ticker"],
            "team_a": r["team_a_kalshi"],
            "team_b": r["team_b_kalshi"],
            "team_a_seed": r.get("team_a_seed"),
            "team_b_seed": r.get("team_b_seed"),
            "model_prob_a": model_a,
            "seed_baseline_prob_a": seed_a,
            "kalshi_prob_a": kalshi_a,
            "kalshi_prob_b": kalshi_b,
            "bet_team": bet_team,
            "bet_side": bet_side,
            "model_prob": model_prob,
            "seed_prob": seed_prob,
            "kalshi_price": kalshi_prob,
            "edge": edge,
            "edge_pct": edge * 100,
            "confidence": confidence,
            "volume": r.get("volume", 0),
        })

    edges = pd.DataFrame(rows)
    return edges


def main():
    parser = argparse.ArgumentParser(description="Detect edges vs Kalshi prices")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Minimum edge (in probability points) to flag")
    args = parser.parse_args()

    pred_path = ARTIFACTS_DIR / f"predictions_{args.year}.csv"
    if not pred_path.exists():
        print(f"No predictions found at {pred_path}. Run predict.py first.")
        return

    df = pd.read_csv(pred_path)
    print(f"Loaded {len(df)} predictions from {pred_path}")

    edges = compute_edges(df, args.threshold)

    # Filter to actionable edges
    actionable = edges[edges["confidence"] != "none"].sort_values("edge", ascending=False)

    # Save all edges
    out_path = ARTIFACTS_DIR / f"edges_{args.year}.csv"
    edges.to_csv(out_path, index=False)
    print(f"Saved {len(edges)} edges to {out_path}")

    # Print summary
    print(f"\n{'='*90}")
    print(f"EDGE DETECTION — {args.year} (threshold: {args.threshold:.0%})")
    print(f"{'='*90}")

    if actionable.empty:
        print("No actionable edges found above threshold.")
    else:
        print(f"Found {len(actionable)} actionable edges:\n")
        print(f"{'Matchup':<35} {'Bet On':<18} {'Model':>6} {'Seed':>6} {'Kalshi':>7} {'Edge':>7} {'Conf':<6}")
        print(f"{'-'*35} {'-'*18} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*6}")
        for _, r in actionable.iterrows():
            matchup = f"{r['team_a']} vs {r['team_b']}"
            seed_str = f"{r['seed_prob']:>5.1%}" if pd.notna(r.get("seed_prob")) else "  N/A"
            print(
                f"{matchup:<35} {r['bet_team']:<18} "
                f"{r['model_prob']:>5.1%} {seed_str} {r['kalshi_price']:>6.1%} "
                f"{r['edge']:>+6.1%} {r['confidence']:<6}"
            )

    # Also show all games sorted by edge
    print(f"\n{'='*97}")
    print("ALL GAMES (sorted by edge)")
    print(f"{'='*97}")
    print(f"{'Matchup':<35} {'Bet On':<18} {'Model':>6} {'Seed':>6} {'Kalshi':>7} {'Edge':>7}")
    print(f"{'-'*35} {'-'*18} {'-'*6} {'-'*6} {'-'*7} {'-'*7}")
    for _, r in edges.sort_values("edge", ascending=False).iterrows():
        matchup = f"{r['team_a']} vs {r['team_b']}"
        seed_str = f"{r['seed_prob']:>5.1%}" if pd.notna(r.get("seed_prob")) else "  N/A"
        print(
            f"{matchup:<35} {r['bet_team']:<18} "
            f"{r['model_prob']:>5.1%} {seed_str} {r['kalshi_price']:>6.1%} "
            f"{r['edge']:>+6.1%}"
        )


if __name__ == "__main__":
    main()
