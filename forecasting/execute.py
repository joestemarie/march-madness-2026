"""
Phase 3D: Execute bets on Kalshi from the bet sheet.

Reads the bet sheet from allocate.py and places limit orders on Kalshi.
Supports dry-run mode (default) to preview orders without submitting.

Usage:
    uv run python forecasting/execute.py                    # dry run (default)
    uv run python forecasting/execute.py --live             # place real orders
    uv run python forecasting/execute.py --live --confirm   # skip per-order prompts
    uv run python forecasting/execute.py --breaks-only --live  # place breaks for prior buys
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
        "order_type": "buy",
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


def round_to_nickel(price: float) -> float:
    """Round price to nearest $0.05, capped at $0.75."""
    return min(0.75, round(round(price / 0.05) * 0.05, 2))


def compute_break_orders(buy_order: dict) -> list[dict]:
    """Compute Tier 1 / Tier 2 sell break orders for a given buy order.

    Break tiers by entry price:
      < $0.05:       tier1 = 4x entry,   tier2 = model_prob
      $0.05–$0.15:   tier1 = 3x entry,   tier2 = model_prob
      $0.15–$0.30:   tier1 = 2.5x entry, tier2 = model_prob
      > $0.30:       no breaks

    Collapses to single break if model_prob <= 1.5 * tier1_price.
    Tier 1: 40% of buy count (min 1). Tier 2: 30% of buy count (min 1).
    All prices rounded to nearest $0.05, capped at $0.75.
    """
    entry = float(buy_order["yes_price_dollars"])
    model_prob = float(buy_order["model_prob"])
    count = buy_order["count"]

    if entry > 0.30:
        return []

    if entry < 0.05:
        tier1_raw = 4 * entry
    elif entry <= 0.15:
        tier1_raw = 3 * entry
    else:
        tier1_raw = 2.5 * entry

    tier1_price = round_to_nickel(tier1_raw)
    tier2_price = round_to_nickel(model_prob)

    tier1_count = max(1, math.floor(count * 0.40))
    tier2_count = max(1, math.floor(count * 0.30))

    base = {
        "ticker": buy_order["ticker"],
        "bet_team": buy_order["bet_team"],
        "side": "yes",
        "action": "sell",
        "edge": buy_order["edge"],
        "model_prob": model_prob,
        "entry_price": entry,
    }

    # Collapse to single break if tier2 target is not meaningfully above tier1
    if model_prob <= 1.5 * tier1_price:
        return [{
            **base,
            "order_type": "break_tier1",
            "count": tier1_count,
            "yes_price_dollars": f"{tier1_price:.2f}",
        }]

    return [
        {
            **base,
            "order_type": "break_tier1",
            "count": tier1_count,
            "yes_price_dollars": f"{tier1_price:.2f}",
        },
        {
            **base,
            "order_type": "break_tier2",
            "count": tier2_count,
            "yes_price_dollars": f"{tier2_price:.2f}",
        },
    ]


def print_order_summary(orders: list[dict], balance_cents: int | None = None):
    """Print a summary of planned buy orders."""
    total_cost = sum(o["count"] * float(o["yes_price_dollars"]) for o in orders)
    total_contracts = sum(o["count"] for o in orders)

    print(f"\n{'='*95}")
    print("BUY ORDER SUMMARY")
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


def print_break_summary(break_orders: list[dict]):
    """Print a summary of planned sell break orders."""
    if not break_orders:
        print("\n(No break orders — all entry prices > $0.30)")
        return

    print(f"\n{'='*95}")
    print("SELL BREAK ORDER SUMMARY")
    print(f"{'='*95}")
    print(f"Total break orders: {len(break_orders)}")
    print()
    print(f"{'Ticker':<45} {'Team':<18} {'Type':<12} {'Qty':>4} {'Entry':>7} {'Break':>7}")
    print(f"{'-'*45} {'-'*18} {'-'*12} {'-'*4} {'-'*7} {'-'*7}")
    for o in break_orders:
        print(
            f"{o['ticker']:<45} {o['bet_team']:<18} "
            f"{o['order_type']:<12} {o['count']:>4} "
            f"${o['entry_price']:>5.2f} "
            f"${float(o['yes_price_dollars']):>5.2f}"
        )


def main():
    parser = argparse.ArgumentParser(description="Execute bets on Kalshi")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--live", action="store_true",
                        help="Actually place orders (default: dry run)")
    parser.add_argument("--confirm", action="store_true",
                        help="Skip per-order confirmation prompts (use with --live)")
    parser.add_argument("--breaks-only", action="store_true",
                        help="Skip buy orders; place break orders for prior buys from execution log")
    args = parser.parse_args()

    # ── Breaks-only mode: load tickers from prior execution log ──
    if args.breaks_only:
        log_path = ARTIFACTS_DIR / f"execution_log_{args.year}.csv"
        if not log_path.exists():
            print(f"No execution log at {log_path}. Run --live first.")
            return
        log_df = pd.read_csv(log_path)
        # Only process successfully placed buy orders
        buy_log = log_df[
            (log_df.get("order_type", pd.Series(["buy"] * len(log_df))) == "buy")
            & (log_df["status"] != "error")
        ] if "order_type" in log_df.columns else log_df[log_df["status"] != "error"]

        if buy_log.empty:
            print("No successful buy orders found in execution log.")
            return

        # Reconstruct minimal order dicts for break computation
        # We need ticker, count, yes_price_dollars (entry), model_prob, edge, bet_team
        orders_for_breaks = []
        bet_path = ARTIFACTS_DIR / f"bet_sheet_{args.year}.csv"
        bets = pd.read_csv(bet_path) if bet_path.exists() else None

        for _, row in buy_log.iterrows():
            ticker = row["ticker"]
            count = int(row["count"])
            price = str(row["price"])

            # Look up model_prob and edge from bet sheet if available
            model_prob = 0.5
            edge = 0.0
            bet_team = row.get("bet_team", "")
            if bets is not None:
                match = bets[bets["bet_team"] == bet_team]
                if not match.empty:
                    model_prob = float(match.iloc[0]["model_prob"])
                    edge = float(match.iloc[0]["edge"])

            orders_for_breaks.append({
                "ticker": ticker,
                "bet_team": bet_team,
                "count": count,
                "yes_price_dollars": price,
                "model_prob": model_prob,
                "edge": edge,
                "order_type": "buy",
            })

        all_breaks = []
        for o in orders_for_breaks:
            all_breaks.extend(compute_break_orders(o))

        if not all_breaks:
            print("No break orders to place (all entry prices > $0.30).")
            return

        # Check for existing resting sell orders
        client = KalshiClient()
        resting_sell_tickers = set()
        try:
            resting = client.get_orders(status="resting")
            for o in resting.get("orders", []):
                if o.get("action") == "sell":
                    resting_sell_tickers.add(o["ticker"])
        except Exception as e:
            logger.warning("Could not fetch resting orders: %s", e)

        if resting_sell_tickers:
            skipped = [o for o in all_breaks if o["ticker"] in resting_sell_tickers]
            all_breaks = [o for o in all_breaks if o["ticker"] not in resting_sell_tickers]
            if skipped:
                print(f"\nSkipping {len(skipped)} break orders with existing resting sells:")
                for o in skipped:
                    print(f"  - {o['ticker']} ({o['bet_team']}) {o['order_type']}")

        print_break_summary(all_breaks)

        if not args.live:
            print("\n*** DRY RUN — no orders will be placed ***")
            print("\nTo place these break orders, run:")
            print("  uv run python forecasting/execute.py --breaks-only --live")
            return

        print("\n*** LIVE MODE — break orders will be placed on Kalshi ***")
        if not args.confirm:
            resp = input(f"\nPlace {len(all_breaks)} sell break orders? [y/N] ")
            if resp.lower() != "y":
                print("Aborted.")
                return

        break_results = []
        for i, order in enumerate(all_breaks, 1):
            if not args.confirm:
                resp = input(
                    f"\n[{i}/{len(all_breaks)}] Sell {order['count']}x YES "
                    f"{order['bet_team']} @ ${float(order['yes_price_dollars']):.2f} "
                    f"({order['order_type']})? [y/N/q] "
                )
                if resp.lower() == "q":
                    print("Stopped.")
                    break
                if resp.lower() != "y":
                    print("  Skipped.")
                    continue

            time.sleep(0.15)
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
                order_id = order_data.get("order_id", "")
                print(f"  ✓ {order['order_type']} {order_id}: status={status}")
                break_results.append({
                    "ticker": order["ticker"],
                    "bet_team": order["bet_team"],
                    "order_id": order_id,
                    "status": status,
                    "count": order["count"],
                    "price": order["yes_price_dollars"],
                    "fill_count": "0",
                    "order_type": order["order_type"],
                })
            except Exception as e:
                print(f"  ✗ FAILED: {e}")
                break_results.append({
                    "ticker": order["ticker"],
                    "bet_team": order["bet_team"],
                    "order_id": "",
                    "status": "error",
                    "count": order["count"],
                    "price": order["yes_price_dollars"],
                    "fill_count": "0",
                    "order_type": order["order_type"],
                    "error": str(e),
                })

        if break_results:
            log_path = ARTIFACTS_DIR / f"execution_log_{args.year}.csv"
            existing = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()
            appended = pd.concat([existing, pd.DataFrame(break_results)], ignore_index=True)
            appended.to_csv(log_path, index=False)
            print(f"\nAppended {len(break_results)} break order(s) to {log_path}")
        return

    # ── Normal mode: buy orders + break orders ──
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

    # Compute break orders for each buy
    all_break_orders = []
    for order in orders:
        all_break_orders.extend(compute_break_orders(order))

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

    # Check resting buy orders (already in the book)
    resting_sell_tickers = set()
    try:
        resting = client.get_orders(status="resting")
        for o in resting.get("orders", []):
            if o.get("action") == "sell":
                resting_sell_tickers.add(o["ticker"])
            else:
                held_tickers.add(o["ticker"])
    except Exception as e:
        logger.warning("Could not fetch resting orders: %s", e)

    if held_tickers:
        already = [o for o in orders if o["ticker"] in held_tickers]
        orders = [o for o in orders if o["ticker"] not in held_tickers]
        if already:
            print(f"\nSkipping {len(already)} markets with existing positions/orders:")
            for o in already:
                print(f"  - {o['ticker']} ({o['bet_team']})")
            print(f"Remaining: {len(orders)} new orders")

    # Filter break orders: skip tickers with existing resting sells
    all_break_orders = [o for o in all_break_orders if o["ticker"] not in resting_sell_tickers]
    # Only keep breaks for tickers we're actually buying this run
    buy_tickers_this_run = {o["ticker"] for o in orders}
    break_orders_this_run = [o for o in all_break_orders if o["ticker"] in buy_tickers_this_run]

    if not orders:
        print("\nAll bets already placed. Nothing to do.")
        return

    if not args.live:
        # ── Dry run ──
        print("\n*** DRY RUN — no orders will be placed ***")
        print_order_summary(orders)
        print_break_summary(break_orders_this_run)
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
    print_break_summary(break_orders_this_run)

    if not args.confirm:
        resp = input(f"\nPlace {len(orders)} buy + {len(break_orders_this_run)} break orders? [y/N] ")
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
        buy_succeeded = False
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
                "order_type": "buy",
            })
            buy_succeeded = True
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
                "order_type": "buy",
                "error": str(e),
            })

        # Place break orders immediately after successful buy
        if buy_succeeded:
            ticker_breaks = [b for b in break_orders_this_run if b["ticker"] == order["ticker"]]
            for brk in ticker_breaks:
                time.sleep(0.15)
                try:
                    result = client.create_order(
                        ticker=brk["ticker"],
                        side=brk["side"],
                        action=brk["action"],
                        count=brk["count"],
                        yes_price_dollars=brk["yes_price_dollars"],
                    )
                    brk_data = result.get("order", {})
                    brk_status = brk_data.get("status", "unknown")
                    brk_id = brk_data.get("order_id", "")
                    print(f"    ↳ {brk['order_type']} {brk_id}: sell {brk['count']}x @ ${float(brk['yes_price_dollars']):.2f} status={brk_status}")
                    results.append({
                        "ticker": brk["ticker"],
                        "bet_team": brk["bet_team"],
                        "order_id": brk_id,
                        "status": brk_status,
                        "count": brk["count"],
                        "price": brk["yes_price_dollars"],
                        "fill_count": "0",
                        "order_type": brk["order_type"],
                    })
                except Exception as e:
                    print(f"    ↳ {brk['order_type']} FAILED: {e}")
                    results.append({
                        "ticker": brk["ticker"],
                        "bet_team": brk["bet_team"],
                        "order_id": "",
                        "status": "error",
                        "count": brk["count"],
                        "price": brk["yes_price_dollars"],
                        "fill_count": "0",
                        "order_type": brk["order_type"],
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
