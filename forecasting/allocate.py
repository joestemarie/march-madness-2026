"""
Phase 3C: Bet allocation using fractional Kelly criterion.

Reads edges from edge.py output and sizes bets according to quarter-Kelly,
respecting minimum/maximum constraints and total bankroll.

Usage:
    uv run python forecasting/allocate.py [--year 2026] [--bankroll 250] [--kelly-fraction 0.25]
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def kelly_fraction(model_prob: float, kalshi_price: float) -> float:
    """Compute full Kelly fraction for a binary bet.

    For a binary contract at price p (implied prob), if our model says
    true prob is q:
      - If we buy YES at price p, payout odds b = (1 - p) / p
      - Kelly f* = (b*q - (1-q)) / b = (q - p) / (1 - p)
      - If we buy NO at price (1-p), similar logic with flipped probs

    Returns full Kelly fraction (positive = bet, negative = don't bet).
    """
    if kalshi_price <= 0 or kalshi_price >= 1:
        return 0.0
    edge = model_prob - kalshi_price
    if edge <= 0:
        return 0.0
    return edge / (1 - kalshi_price)


def allocate_bets(
    edges: pd.DataFrame,
    bankroll: float,
    kelly_mult: float,
    min_bet: float,
    max_bet_pct: float,
) -> pd.DataFrame:
    """Size bets using fractional Kelly criterion with constraints."""
    max_bet = bankroll * max_bet_pct

    bets = []
    for _, r in edges.iterrows():
        if r["edge"] <= 0 or r["confidence"] == "none":
            continue

        kf = kelly_fraction(r["model_prob"], r["kalshi_price"])
        fractional_kf = kf * kelly_mult
        raw_bet = fractional_kf * bankroll

        # Apply constraints
        if raw_bet < min_bet:
            continue
        bet_amount = min(raw_bet, max_bet)

        # Expected value
        # If we buy YES at kalshi_price, we win (1 - kalshi_price) with prob model_prob
        # and lose kalshi_price with prob (1 - model_prob)
        ev = bet_amount * (r["model_prob"] * (1 - r["kalshi_price"]) / r["kalshi_price"]
                          - (1 - r["model_prob"]))

        bets.append({
            "event_ticker": r["event_ticker"],
            "matchup": f"{r['team_a']} vs {r['team_b']}",
            "bet_team": r["bet_team"],
            "bet_side": r["bet_side"],
            "model_prob": r["model_prob"],
            "seed_prob": r.get("seed_prob"),
            "kalshi_price": r["kalshi_price"],
            "edge": r["edge"],
            "confidence": r["confidence"],
            "kelly_full": kf,
            "kelly_fraction": fractional_kf,
            "bet_amount": round(bet_amount, 2),
            "expected_profit": round(ev, 2),
        })

    bet_df = pd.DataFrame(bets)

    if bet_df.empty:
        return bet_df

    # Scale down if total exceeds bankroll
    total = bet_df["bet_amount"].sum()
    if total > bankroll:
        scale = bankroll / total
        bet_df["bet_amount"] = (bet_df["bet_amount"] * scale).round(2)
        bet_df["expected_profit"] = (bet_df["expected_profit"] * scale).round(2)
        print(f"Scaled bets by {scale:.2f}x to fit bankroll")

    bet_df = bet_df.sort_values("edge", ascending=False)

    # Round probability columns to 4 decimal places for clean CSV export
    for col in ["model_prob", "seed_prob", "kalshi_price", "edge",
                "kelly_full", "kelly_fraction"]:
        if col in bet_df.columns:
            bet_df[col] = bet_df[col].round(4)

    return bet_df


def simulate_outcomes(bets: pd.DataFrame, n_sims: int = 10000) -> dict:
    """Simple Monte Carlo simulation of bet outcomes."""
    rng = np.random.default_rng(42)
    profits = np.zeros(n_sims)

    for _, bet in bets.iterrows():
        # Each bet wins with probability model_prob
        wins = rng.random(n_sims) < bet["model_prob"]
        # Win: gain (1 - kalshi_price) / kalshi_price * bet_amount
        # Lose: lose bet_amount
        payout_if_win = bet["bet_amount"] * (1 - bet["kalshi_price"]) / bet["kalshi_price"]
        profits += np.where(wins, payout_if_win, -bet["bet_amount"])

    return {
        "mean_profit": float(np.mean(profits)),
        "median_profit": float(np.median(profits)),
        "prob_positive": float(np.mean(profits > 0)),
        "prob_lose_all": float(np.mean(profits <= -bets["bet_amount"].sum())),
        "p5": float(np.percentile(profits, 5)),
        "p95": float(np.percentile(profits, 95)),
    }


def main():
    parser = argparse.ArgumentParser(description="Allocate bets via Kelly criterion")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--bankroll", type=float, default=250.0)
    parser.add_argument("--kelly-fraction", type=float, default=0.25,
                        help="Fraction of full Kelly to use (default: quarter Kelly)")
    parser.add_argument("--min-bet", type=float, default=5.0)
    parser.add_argument("--max-bet-pct", type=float, default=0.15,
                        help="Max single bet as fraction of bankroll")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Minimum edge to consider")
    args = parser.parse_args()

    edge_path = ARTIFACTS_DIR / f"edges_{args.year}.csv"
    if not edge_path.exists():
        print(f"No edges found at {edge_path}. Run edge.py first.")
        return

    edges = pd.read_csv(edge_path)
    actionable = edges[edges["edge"] > args.threshold]
    print(f"Loaded {len(edges)} edges, {len(actionable)} above {args.threshold:.0%} threshold")

    bets = allocate_bets(
        actionable,
        bankroll=args.bankroll,
        kelly_mult=args.kelly_fraction,
        min_bet=args.min_bet,
        max_bet_pct=args.max_bet_pct,
    )

    if bets.empty:
        print("No bets meet the criteria.")
        return

    # Save bet sheet
    out_path = ARTIFACTS_DIR / f"bet_sheet_{args.year}.csv"
    bets.to_csv(out_path, index=False)
    print(f"Saved {len(bets)} bets to {out_path}")

    # Simulate outcomes
    sim = simulate_outcomes(bets)

    # Print bet sheet
    print(f"\n{'='*95}")
    print(f"BET SHEET — {args.year}")
    print(f"Bankroll: ${args.bankroll:.0f} | Kelly: {args.kelly_fraction:.0%} | "
          f"Min: ${args.min_bet:.0f} | Max: ${args.bankroll * args.max_bet_pct:.0f}")
    print(f"{'='*95}")
    print(f"{'Matchup':<30} {'Bet On':<16} {'Model':>6} {'Seed':>6} {'Kalshi':>7} {'Edge':>6} "
          f"{'Kelly':>6} {'Bet':>7} {'E[P]':>7}")
    print(f"{'-'*30} {'-'*16} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*6} {'-'*7} {'-'*7}")
    for _, r in bets.iterrows():
        seed_str = f"{r['seed_prob']:>5.1%}" if pd.notna(r.get("seed_prob")) else "  N/A"
        print(
            f"{r['matchup']:<30} {r['bet_team']:<16} "
            f"{r['model_prob']:>5.1%} {seed_str} {r['kalshi_price']:>6.1%} {r['edge']:>+5.1%} "
            f"{r['kelly_fraction']:>5.1%} ${r['bet_amount']:>6.2f} ${r['expected_profit']:>6.2f}"
        )

    # Summary
    total_deployed = bets["bet_amount"].sum()
    total_ev = bets["expected_profit"].sum()
    print(f"\n{'='*95}")
    print("SUMMARY")
    print(f"{'='*95}")
    print(f"Total bets:           {len(bets)}")
    print(f"Capital deployed:     ${total_deployed:.2f} / ${args.bankroll:.0f}")
    print(f"Expected profit:      ${total_ev:.2f} ({total_ev/total_deployed*100:.1f}% ROI)")
    print(f"Breakeven (KenPom):   need ${20:.0f} profit to cover subscription")
    print(f"\nMonte Carlo simulation ({10000} runs):")
    print(f"  Mean profit:        ${sim['mean_profit']:.2f}")
    print(f"  Median profit:      ${sim['median_profit']:.2f}")
    print(f"  P(profit > 0):      {sim['prob_positive']:.1%}")
    print(f"  5th percentile:     ${sim['p5']:.2f}")
    print(f"  95th percentile:    ${sim['p95']:.2f}")
    print(f"  P(lose all bets):   {sim['prob_lose_all']:.1%}")


if __name__ == "__main__":
    main()
