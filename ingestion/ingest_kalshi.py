"""Ingest Kalshi March Madness market prices into DuckDB raw schema.

Usage:
    uv run python ingestion/ingest_kalshi.py

No API key needed — market data endpoints are public.

API base: https://api.elections.kalshi.com/trade-api/v2
Docs: https://trading-api.readme.io/reference/getmarkets
"""

import argparse
import logging
import time

import httpx
import pandas as pd

from ingestion.utils import get_connection, load_dataframe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
REQUEST_DELAY = 0.1  # 20 req/sec limit, stay well under


def kalshi_get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the Kalshi public API."""
    url = f"{BASE_URL}{path}"
    logger.debug("GET %s params=%s", url, params)
    resp = httpx.get(url, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_markets(series_ticker: str) -> list[dict]:
    """Fetch all markets for a series, handling pagination."""
    all_markets = []
    cursor = None
    while True:
        params = {"series_ticker": series_ticker, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = kalshi_get("/markets", params)
        markets = data.get("markets", [])
        all_markets.extend(markets)
        cursor = data.get("cursor")
        if not cursor or not markets:
            break
        time.sleep(REQUEST_DELAY)
    logger.info("Fetched %d markets for series %s", len(all_markets), series_ticker)
    return all_markets


def parse_game_markets(markets: list[dict]) -> pd.DataFrame:
    """Parse KXNCAAMBGAME markets into a clean DataFrame.

    Each market is a binary contract on whether a specific team wins a
    specific game. The yes_bid/yes_ask prices are the implied probability
    that team wins.
    """
    rows = []
    for m in markets:
        ticker = m.get("ticker", "")
        event_ticker = m.get("event_ticker", "")
        title = m.get("title", "")
        subtitle = m.get("yes_sub_title", "")

        # Extract team abbreviation from ticker
        # Format: KXNCAAMBGAME-26MAR19SIEDUKE-DUKE
        parts = ticker.split("-")
        team_abbr = parts[-1] if len(parts) >= 3 else ""

        # Price fields are dollar strings like "0.5300"
        yes_bid = _to_float(m.get("yes_bid_dollars"))
        yes_ask = _to_float(m.get("yes_ask_dollars"))
        no_bid = _to_float(m.get("no_bid_dollars"))
        no_ask = _to_float(m.get("no_ask_dollars"))
        last_price = _to_float(m.get("last_price_dollars"))
        volume = _to_float(m.get("volume_fp"))
        open_interest = _to_float(m.get("open_interest_fp"))

        # Midpoint as best implied probability estimate
        if yes_bid is not None and yes_ask is not None:
            implied_prob = (yes_bid + yes_ask) / 2
        elif last_price is not None:
            implied_prob = last_price
        else:
            implied_prob = None

        rows.append({
            "ticker": ticker,
            "event_ticker": event_ticker,
            "series_ticker": m.get("series_ticker", ""),
            "title": title,
            "subtitle": subtitle,
            "team_abbr": team_abbr,
            "status": m.get("status", ""),
            "result": m.get("result", ""),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": last_price,
            "implied_prob": implied_prob,
            "volume": volume,
            "open_interest": open_interest,
            "open_time": m.get("open_time", ""),
            "close_time": m.get("close_time", ""),
        })
    return pd.DataFrame(rows)


def _to_float(val) -> float | None:
    """Convert a dollar string or numeric value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description="Ingest Kalshi March Madness odds")
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument(
        "--series",
        type=str,
        nargs="+",
        default=["KXNCAAMBGAME"],
        help="Kalshi series tickers to fetch (default: game winner markets)",
    )
    args = parser.parse_args()

    conn = get_connection(args.db_path)

    for series in args.series:
        logger.info("Fetching series: %s", series)
        markets = fetch_markets(series)
        if not markets:
            logger.warning("No markets found for series %s", series)
            continue

        df = parse_game_markets(markets)
        if df.empty:
            logger.warning("No parseable markets for series %s", series)
            continue

        # Table name based on series: KXNCAAMBGAME -> kalshi_game_markets
        table_name = f"kalshi_{series.lower().replace('kx', '')}"
        load_dataframe(conn, df, table_name)

    conn.close()
    logger.info("Kalshi ingestion complete")


if __name__ == "__main__":
    main()
