"""Check status of open Kalshi orders and optionally cancel/reprice.

Usage:
    uv run python forecasting/status.py              # show open orders
    uv run python forecasting/status.py --cancel      # cancel all resting orders
    uv run python forecasting/status.py --reprice     # cancel stale orders and re-place at current bid
    uv run python forecasting/status.py --at-ask      # cancel resting orders and re-place at ask for immediate fill
"""

import argparse
import logging
import time
from pathlib import Path

import httpx
import pandas as pd

from forecasting.kalshi_client import KalshiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def get_current_prices(tickers: list[str]) -> dict[str, dict]:
    """Fetch current market prices for a list of tickers (public, no auth)."""
    prices = {}
    # Batch by event ticker to reduce calls
    for ticker in tickers:
        try:
            resp = httpx.get(
                f"{BASE_URL}/markets/{ticker}", timeout=30
            )
            if resp.status_code == 200:
                m = resp.json().get("market", {})
                yes_bid = m.get("yes_bid_dollars")
                yes_ask = m.get("yes_ask_dollars")
                if yes_bid and yes_ask:
                    prices[ticker] = {
                        "yes_bid": float(yes_bid),
                        "yes_ask": float(yes_ask),
                        "mid": (float(yes_bid) + float(yes_ask)) / 2,
                        "last": float(m.get("last_price_dollars", 0)),
                        "status": m.get("status", ""),
                    }
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", ticker, e)
    return prices


def main():
    parser = argparse.ArgumentParser(description="Check Kalshi order status")
    parser.add_argument("--cancel", action="store_true",
                        help="Cancel all resting orders")
    parser.add_argument("--reprice", action="store_true",
                        help="Cancel stale orders and re-place at current bid")
    parser.add_argument("--at-ask", action="store_true",
                        help="Cancel resting orders and re-place at ask for immediate fill")
    args = parser.parse_args()

    client = KalshiClient()

    # Get balance
    balance = client.get_balance()
    print(f"Balance: ${balance['balance'] / 100:.2f}  |  "
          f"Portfolio: ${balance.get('portfolio_value', 0) / 100:.2f}")

    # Get open orders
    resp = client.get_orders(status="resting")
    orders = resp.get("orders", [])

    if not orders:
        print("\nNo resting orders.")
        # Check for filled orders
        filled = client.get_orders(status="executed")
        filled_orders = filled.get("orders", [])
        if filled_orders:
            print(f"\n{len(filled_orders)} executed orders:")
            for o in filled_orders[-20:]:
                print(f"  {o['ticker']:<45} {o['side']:>3} "
                      f"{o.get('fill_count_fp', '?'):>6} filled @ "
                      f"${float(o.get('yes_price_dollars', 0)):.2f}")
        return

    print(f"\n{len(orders)} resting orders:\n")

    # Get current market prices
    tickers = [o["ticker"] for o in orders]
    current = get_current_prices(tickers)

    print(f"{'Ticker':<45} {'Side':>4} {'Qty':>4} {'Our $':>6} {'Bid':>6} {'Ask':>6} {'Mid':>6} {'Status':<10}")
    print(f"{'-'*45} {'-'*4} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*10}")

    stale = []
    for o in orders:
        ticker = o["ticker"]
        side = o["side"]
        our_price = float(o.get("yes_price_dollars", 0))
        remaining = o.get("remaining_count_fp", "?")
        mkt = current.get(ticker, {})

        bid = mkt.get("yes_bid", 0)
        ask = mkt.get("yes_ask", 0)
        mid = mkt.get("mid", 0)

        # Flag if our price is below the current bid (we're too cheap)
        if our_price < bid:
            flag = "stale ↑"
            stale.append(o)
        elif our_price > ask:
            flag = "stale ↓"
            stale.append(o)
        else:
            flag = "in range"

        print(
            f"{ticker:<45} {side:>4} {remaining:>4} "
            f"${our_price:>5.2f} ${bid:>5.2f} ${ask:>5.2f} ${mid:>5.2f} {flag:<10}"
        )

    if stale:
        print(f"\n{len(stale)} orders are outside the current bid/ask spread.")

    # Determine which orders to act on
    if args.at_ask:
        to_cancel = [o for o in orders if o.get("action") == "buy"]
        to_reprice = [o for o in orders if o.get("action") == "buy"]
        price_key = "yes_ask"
        price_label = "ask"
    elif args.reprice:
        to_cancel = [o for o in stale if o.get("action") == "buy"]
        to_reprice = [o for o in stale if o.get("action") == "buy"]
        price_key = "yes_bid"
        price_label = "bid"
    elif args.cancel:
        to_cancel = orders
        to_reprice = []
    else:
        to_cancel = []
        to_reprice = []

    if to_cancel:
        print(f"\nCancelling {len(to_cancel)} resting orders...")
        for o in to_cancel:
            try:
                client._request("DELETE", f"/portfolio/orders/{o['order_id']}")
                print(f"  Cancelled {o['ticker']}")
            except Exception as e:
                print(f"  Failed to cancel {o['ticker']}: {e}")
            time.sleep(0.1)

    if to_reprice:
        print(f"\nRe-placing {len(to_reprice)} orders at current {price_label}...")
        for o in to_reprice:
            ticker = o["ticker"]
            mkt = current.get(ticker, {})
            new_price = mkt.get(price_key, 0)
            if new_price <= 0:
                continue
            # Only re-place the unfilled remainder — already-filled contracts are ours
            remaining = int(float(o.get("remaining_count_fp", "0")))
            if remaining <= 0:
                print(f"  {ticker}: fully filled, nothing to re-place")
                continue
            time.sleep(0.15)  # stay under 20 req/sec rate limit
            try:
                result = client.create_order(
                    ticker=ticker,
                    side=o["side"],
                    action="buy",
                    count=remaining,
                    yes_price_dollars=f"{new_price:.2f}",
                )
                order_data = result.get("order", {})
                status = order_data.get("status", "?")
                filled = order_data.get("fill_count_fp", "0")
                print(f"  {ticker}: {remaining}x @ ${new_price:.2f} → {status} (filled: {filled})")
            except Exception as e:
                print(f"  Failed {ticker}: {e}")


if __name__ == "__main__":
    main()
