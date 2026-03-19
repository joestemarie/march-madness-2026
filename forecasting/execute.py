"""
Phase 3D: Execute bets on Kalshi from the bet sheet.

Reads the bet sheet from allocate.py and places limit orders on Kalshi.
Supports dry-run mode (default) to preview orders without submitting.

Usage:
    uv run python forecasting/execute.py                    # dry run (default)
    uv run python forecasting/execute.py --live             # place real orders
    uv run python forecasting/execute.py --live --confirm   # skip per-order prompts
"""

import argparse
import logging
import math
import sys
import time
from pathlib import Path

import pandas as pd

from forecasting.kalshi_client import KalshiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def compute_order_params(row: pd.Series) -> dict:
    """Convert a bet sheet row into Kalshi order parameters.

    The bet sheet has: event_ticker, bet_team, bet_side (team_a/team_b),
    model_prob, kalshi_price, bet_amount.

    We need to find the right market ticker and determine side (yes/no).
    """
    event_ticker = row["event_ticker"]
    bet_amount = row["bet_amount"]
    kalshi_price = row["kalshi_price"]

    # The market ticker for a specific team is: event_ticker + "-" + team_abbr
    # But we stored the full team name, not the abbreviation.
    # We'll need to look up the actual ticker from the ingested data.
    # For now, store enough info for the lookup.

    # Determine contracts: bet_amount / kalshi_price = number of contracts
    # Each contract costs kalshi_price dollars and pays $1 if yes
    count = max(1, math.floor(bet_amount / kalshi_price))

    # We're always buying YES on the team we think is undervalued
    # The kalshi_price IS the yes price for that team's market
    price = f"{kalshi_price:.2f}"

    return {
        "event_ticker": event_ticker,
        "bet_team": row["bet_team"],
        "side": "yes",
        "action": "buy",
        "count": count,
        "yes_price_dollars": price,
        "bet_amount": bet_amount,
        "model_prob": row["model_prob"],
        "edge": row["edge"],
    }


def resolve_tickers(orders: list[dict], conn) -> list[dict]:
    """Resolve event_ticker + bet_team to actual market tickers from the database."""
    # Load Kalshi market data to find tickers
    markets = conn.execute("""
        SELECT event_ticker, ticker, subtitle as team_name
        FROM raw.kalshi_ncaambgame
        WHERE status = 'active'
    """).fetchdf()

    ticker_lookup = {}
    for _, m in markets.iterrows():
        key = (m["event_ticker"], m["team_name"])
        ticker_lookup[key] = m["ticker"]

    resolved = []
    for order in orders:
        key = (order["event_ticker"], order["bet_team"])
        ticker = ticker_lookup.get(key)
        if not ticker:
            # Try fuzzy match on team name
            event_markets = markets[markets["event_ticker"] == order["event_ticker"]]
            for _, em in event_markets.iterrows():
                if (order["bet_team"].lower() in em["team_name"].lower()
                        or em["team_name"].lower() in order["bet_team"].lower()):
                    ticker = em["ticker"]
                    break

        if ticker:
            order["ticker"] = ticker
            resolved.append(order)
        else:
            logger.warning(
                "Could not find market ticker for %s in %s — skipping",
                order["bet_team"], order["event_ticker"],
            )

    return resolved


def print_order_summary(orders: list[dict], balance_cents: int | None = None):
    """Print a summary of planned orders."""
    total_cost = sum(o["count"] * float(o["yes_price_dollars"]) for o in orders)
    total_contracts = sum(o["count"] for o in orders)

    print(f"\n{'='*95}")
    print("ORDER SUMMARY")
    print(f"{'='*95}")
    if balance_cents is not None:
        print(f"Account balance: ${balance_cents / 100:.2f}")
    print(f"Total orders: {len(orders)}")
    print(f"Total contracts: {total_contracts}")
    print(f"Total cost: ${total_cost:.2f}")
    print()
    print(f"{'Ticker':<45} {'Team':<18} {'Side':>4} {'Qty':>4} {'Price':>7} {'Cost':>8} {'Edge':>6}")
    print(f"{'-'*45} {'-'*18} {'-'*4} {'-'*4} {'-'*7} {'-'*8} {'-'*6}")
    for o in orders:
        cost = o["count"] * float(o["yes_price_dollars"])
        print(
            f"{o['ticker']:<45} {o['bet_team']:<18} "
            f"{'YES':>4} {o['count']:>4} "
            f"${float(o['yes_price_dollars']):>5.2f} "
            f"${cost:>7.2f} {o['edge']:>+5.1%}"
        )


def main():
    parser = argparse.ArgumentParser(description="Execute bets on Kalshi")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--live", action="store_true",
                        help="Actually place orders (default: dry run)")
    parser.add_argument("--confirm", action="store_true",
                        help="Skip per-order confirmation prompts (use with --live)")
    args = parser.parse_args()

    bet_path = ARTIFACTS_DIR / f"bet_sheet_{args.year}.csv"
    if not bet_path.exists():
        print(f"No bet sheet at {bet_path}. Run allocate.py first.")
        return

    bets = pd.read_csv(bet_path)
    print(f"Loaded {len(bets)} bets from {bet_path}")

    # Compute order params
    orders = [compute_order_params(row) for _, row in bets.iterrows()]

    # Resolve market tickers from database
    import duckdb
    db_path = Path(__file__).parent.parent / "data" / "madness.duckdb"
    conn = duckdb.connect(str(db_path), read_only=True)
    orders = resolve_tickers(orders, conn)
    conn.close()

    if not orders:
        print("No orders could be resolved to market tickers.")
        return

    # Filter out markets where we already have positions or filled orders
    client = KalshiClient()
    held_tickers = set()

    # Check executed orders
    try:
        executed = client.get_orders(status="executed")
        for o in executed.get("orders", []):
            filled = float(o.get("fill_count_fp", "0"))
            if filled > 0:
                held_tickers.add(o["ticker"])
    except Exception as e:
        logger.warning("Could not fetch executed orders: %s", e)

    # Check resting orders (already in the book)
    try:
        resting = client.get_orders(status="resting")
        for o in resting.get("orders", []):
            held_tickers.add(o["ticker"])
    except Exception as e:
        logger.warning("Could not fetch resting orders: %s", e)

    if held_tickers:
        before = len(orders)
        already = [o for o in orders if o["ticker"] in held_tickers]
        orders = [o for o in orders if o["ticker"] not in held_tickers]
        if already:
            print(f"\nSkipping {len(already)} markets with existing positions/orders:")
            for o in already:
                print(f"  - {o['ticker']} ({o['bet_team']})")
            print(f"Remaining: {len(orders)} new orders")

    if not orders:
        print("\nAll bets already placed. Nothing to do.")
        return

    if not args.live:
        # ── Dry run ──
        print("\n*** DRY RUN — no orders will be placed ***")
        print_order_summary(orders)
        print(f"\nTo place these orders for real, run:")
        print(f"  uv run python forecasting/execute.py --live")
        return

    # ── Live execution ──
    print("\n*** LIVE MODE — orders will be placed on Kalshi ***")

    # Check balance
    balance_resp = client.get_balance()
    balance_cents = balance_resp.get("balance", 0)
    print(f"Account balance: ${balance_cents / 100:.2f}")

    total_cost_cents = sum(
        o["count"] * int(float(o["yes_price_dollars"]) * 100)
        for o in orders
    )
    if total_cost_cents > balance_cents:
        print(f"WARNING: Total order cost (${total_cost_cents / 100:.2f}) "
              f"exceeds balance (${balance_cents / 100:.2f})")
        resp = input("Continue anyway? Orders may partially fill. [y/N] ")
        if resp.lower() != "y":
            print("Aborted.")
            return

    print_order_summary(orders, balance_cents)

    if not args.confirm:
        resp = input(f"\nPlace {len(orders)} orders? [y/N] ")
        if resp.lower() != "y":
            print("Aborted.")
            return

    # Place orders
    results = []
    for i, order in enumerate(orders, 1):
        if not args.confirm:
            resp = input(
                f"\n[{i}/{len(orders)}] Buy {order['count']}x YES "
                f"{order['bet_team']} @ ${float(order['yes_price_dollars']):.2f} "
                f"(edge: {order['edge']:+.1%})? [y/N/q] "
            )
            if resp.lower() == "q":
                print("Stopped.")
                break
            if resp.lower() != "y":
                print("  Skipped.")
                continue

        time.sleep(0.15)  # stay under 20 req/sec rate limit
        try:
            result = client.create_order(
                ticker=order["ticker"],
                side=order["side"],
                action=order["action"],
                count=order["count"],
                yes_price_dollars=order["yes_price_dollars"],
            )
            order_data = result.get("order", {})
            status = order_data.get("status", "unknown")
            fill_count = order_data.get("fill_count_fp", "0")
            order_id = order_data.get("order_id", "")
            print(f"  ✓ Order {order_id}: status={status}, filled={fill_count}")
            results.append({
                "ticker": order["ticker"],
                "bet_team": order["bet_team"],
                "order_id": order_id,
                "status": status,
                "count": order["count"],
                "price": order["yes_price_dollars"],
                "fill_count": fill_count,
            })
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            results.append({
                "ticker": order["ticker"],
                "bet_team": order["bet_team"],
                "order_id": "",
                "status": "error",
                "count": order["count"],
                "price": order["yes_price_dollars"],
                "fill_count": "0",
                "error": str(e),
            })

    # Save results
    if results:
        results_df = pd.DataFrame(results)
        out_path = ARTIFACTS_DIR / f"execution_log_{args.year}.csv"
        results_df.to_csv(out_path, index=False)
        print(f"\nSaved execution log to {out_path}")

    # Final balance
    try:
        balance_resp = client.get_balance()
        print(f"Final balance: ${balance_resp.get('balance', 0) / 100:.2f}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
